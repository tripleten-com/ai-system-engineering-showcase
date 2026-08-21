"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/telemetry/chaos.py
Component:          Per-Scenario Chaos Profiles
Purpose:            Transforms a baseline sample into an incident sample using the per-scenario
                    peak tables and the smoothstep ramp from the telemetry specification §4.
Interacts With:     None (pure math)

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             Chaos Simulation, Telemetry Math, Deterministic Simulation
Tools:              Python 3.11

The chaos engine is a simulation, not real failure injection: Postgres, Redis, and LocalStack
stay healthy throughout an incident so the demo is repeatable and resettable. Every metric
follows exactly one of three modes — ramp, step, or override — declared per scenario below.
"""

import math
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from incident_agent_api.telemetry import baseline
from incident_agent_api.telemetry.baseline import MetricValues
from tripleten_contracts import ScenarioId

# Time for a ramped metric to travel from its nominal to its chaos peak.
RAMP_SECONDS = 2.0

# Queue depth gets a longer ramp: a 1,540-message backlog appearing in 2 seconds reads as a
# data glitch, while 8 seconds reads as a queue genuinely filling behind stalled consumers.
QUEUE_RAMP_SECONDS = 8.0

# Once a metric is at peak it wanders by this fraction. AWAITING_APPROVAL is a hard stop of
# unbounded duration, so a dead-flat line would sit on screen for as long as the operator
# takes to decide; ±1% keeps the sparklines alive without leaving the chaos band.
HOLD_JITTER_FRACTION = 0.01

_LONG_RAMPS: dict[str, float] = {"sqs_active_queue_depth": QUEUE_RAMP_SECONDS}

_STAMPEDE_CENTRE = 120.0
_STAMPEDE_AMPLITUDE = 60.0
_STAMPEDE_FREQUENCY = 2.1


def smoothstep(t: float, ramp_seconds: float) -> float:
    """Returns the eased 0→1 ramp progress at t seconds, clamped outside the ramp window."""
    if ramp_seconds <= 0.0:
        return 1.0
    s = min(1.0, max(0.0, t / ramp_seconds))
    return s * s * (3.0 - 2.0 * s)


def ramp_seconds_for(field_name: str) -> float:
    """Returns the ramp duration for a metric — the long ramp for queue depth, else the default."""
    return _LONG_RAMPS.get(field_name, RAMP_SECONDS)


def _stampede_throughput(t: float) -> float:
    """Returns Scenario 2 throughput: a heavy oscillation between 60 and 180 req/s."""
    raw = _STAMPEDE_CENTRE + _STAMPEDE_AMPLITUDE * math.sin(_STAMPEDE_FREQUENCY * t)
    return min(180.0, max(60.0, raw))


@dataclass(frozen=True)
class ChaosProfile:
    """One scenario's metric transformations, split by mode.

    ramped:           field → peak, eased over its ramp window then held with jitter
    stepped:          field → value, applied instantly at t=0 and held exactly
    overrides:        field → f(t_chaos), replacing the baseline expression outright
    recovery_extra:   additional field → peak pairs to decay from that are not ramped
    recovery_stepped: field → value held for the whole recovery phase
    """

    ramped: Mapping[str, float] = field(default_factory=dict)
    stepped: Mapping[str, float] = field(default_factory=dict)
    overrides: Mapping[str, Callable[[float], float]] = field(default_factory=dict)
    recovery_extra: Mapping[str, float] = field(default_factory=dict)
    recovery_stepped: Mapping[str, float] = field(default_factory=dict)

    @property
    def recovery_from(self) -> dict[str, float]:
        """Returns every field the decay curve drives, mapped to the peak it decays from."""
        return {**self.ramped, **self.recovery_extra}


PROFILES: dict[ScenarioId, ChaosProfile] = {
    # Scenario 1 — connection pool exhaustion. A tail-latency signature: p99 and p95 blow out
    # while p50 stays healthy, because it is the queued requests that time out.
    ScenarioId.DB_POOL_EXHAUSTION: ChaosProfile(
        ramped={
            "db_pool_utilization_pct": 98.5,
            "http_5xx_error_rate_pct": 36.4,
            "latency_p99_ms": 4820.0,
            # Not fixed by the spec's §4 prose; taken from the illustrative snapshot in §6.2,
            # which puts p95 at 0.614 × p99 during this scenario.
            "latency_p95_ms": 2960.0,
            "requests_per_sec": 42.0,
        }
    ),
    # Scenario 2 — cache stampede. Throughput does not fall to a single floor, it thrashes,
    # so it is an override rather than a ramp.
    ScenarioId.CACHE_THUNDERING_HERD: ChaosProfile(
        ramped={
            "redis_memory_utilization_pct": 97.8,
            "cache_hit_ratio_pct": 14.1,
            "latency_p95_ms": 1840.0,
            # Not fixed by §4; preserves the baseline p99/p95 ratio (48.0/34.0) applied to
            # the canonical 1840ms p95, keeping the percentile ordering invariant intact.
            "latency_p99_ms": 2598.0,
        },
        overrides={"requests_per_sec": _stampede_throughput},
        # Recovery pulls throughput back to nominal from the oscillation's centre.
        recovery_extra={"requests_per_sec": _STAMPEDE_CENTRE},
    ),
    # Scenario 3 — worker deadlock. The golden signals stay green: HTTP is fine, it is the
    # asynchronous workload that has stalled. That is the point of the scenario.
    ScenarioId.WORKER_DEADLOCK: ChaosProfile(
        ramped={"sqs_active_queue_depth": 1540.0},
        stepped={"active_workers_count": 0.0, "dlq_message_count": 1.0},
        # reboot_workers has landed by the time the callback arrives, so the pool is back at
        # full strength for the whole drain; the poison message stays in customer-dlq.
        recovery_stepped={"active_workers_count": 4.0, "dlq_message_count": 1.0},
    ),
    # Scenario 4 — prompt injection. Deliberately empty: the attack never becomes an outage,
    # so no infrastructure metric moves and there is nothing to recover from.
    ScenarioId.PROMPT_INJECTION: ChaosProfile(),
}


def normalise(values: MetricValues) -> MetricValues:
    """Clamps percentages into [0, 100] and rounds count metrics to whole messages."""
    for field_name, value in values.items():
        if field_name.endswith("_pct"):
            values[field_name] = min(100.0, max(0.0, value))
    for field_name in baseline.INTEGER_FIELDS:
        if field_name in values:
            values[field_name] = float(round(values[field_name]))
    return values


def apply(values: MetricValues, scenario: ScenarioId, t_chaos: float, rng: random.Random) -> MetricValues:
    """Returns the sample transformed by a scenario's chaos profile at t_chaos seconds."""
    profile = PROFILES[scenario]
    result = dict(values)

    for field_name, peak in profile.ramped.items():
        nominal = baseline.NOMINALS[field_name]
        ramp = ramp_seconds_for(field_name)
        if t_chaos <= ramp:
            # Inclusive upper bound so the end of the ramp lands exactly on the documented
            # peak; jitter only starts once the metric is genuinely holding.
            result[field_name] = nominal + (peak - nominal) * smoothstep(t_chaos, ramp)
        else:
            result[field_name] = peak * (1.0 + rng.uniform(-HOLD_JITTER_FRACTION, HOLD_JITTER_FRACTION))

    for field_name, stepped_value in profile.stepped.items():
        result[field_name] = stepped_value

    for field_name, override in profile.overrides.items():
        result[field_name] = override(t_chaos)

    return normalise(result)
