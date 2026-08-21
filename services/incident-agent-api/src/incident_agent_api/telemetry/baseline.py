"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/telemetry/baseline.py
Component:          Steady-State Telemetry Generator
Purpose:            Pure sine-wave-plus-jitter sample functions for the healthy baseline profile,
                    one entry per simulated metric, matching the generator table in the
                    telemetry specification §3.
Interacts With:     None (pure math)

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             Golden Signals, Telemetry Math, Deterministic Simulation
Tools:              Python 3.11

Every function here is pure and takes its clock and its generator as arguments. Nothing in
this module reads `time` or the module-level `random`, so a seeded generator reproduces a
telemetry sequence bit for bit.
"""

import math
import random

# Metric values in flight between the generator, the chaos layer, and the engine. Keyed by
# snapshot field name — the same keys as tripleten_contracts.BASELINE_BANDS — because the
# chaos and decay layers transform them generically from profile tables.
MetricValues = dict[str, float]

# Metrics that are counts of things and must never surface as fractions.
INTEGER_FIELDS = frozenset({"sqs_active_queue_depth", "dlq_message_count", "active_workers_count"})

# Nominal (pre-jitter) values. These are both the ramp origin for chaos and the decay target
# for recovery, which is why they are declared once here rather than inlined per profile.
NOMINALS: MetricValues = {
    "requests_per_sec": 145.0,
    "http_5xx_error_rate_pct": 0.0,
    "latency_p50_ms": 18.5,
    "latency_p95_ms": 34.0,
    "latency_p99_ms": 48.0,
    "db_pool_utilization_pct": 15.0,
    "redis_memory_utilization_pct": 40.0,
    "cache_hit_ratio_pct": 99.0,
    "sqs_active_queue_depth": 4.0,
    "dlq_message_count": 0.0,
    "active_workers_count": 4.0,
}

# Additive jitter windows, straight from the §3 generator column. A field absent from this
# table is either constant (error rate, DLQ, workers) or sampled discretely (queue depth).
JITTER: dict[str, tuple[float, float]] = {
    "requests_per_sec": (-3.0, 3.0),
    "latency_p50_ms": (-2.0, 2.0),
    "latency_p95_ms": (-3.0, 3.0),
    "latency_p99_ms": (-4.0, 4.0),
    "db_pool_utilization_pct": (-2.0, 2.0),
    "redis_memory_utilization_pct": (-1.0, 1.0),
    # Asymmetric on purpose: a cache hit ratio above 99% would be a better-than-nominal
    # claim the demo has not earned, so jitter only ever degrades it.
    "cache_hit_ratio_pct": (-0.5, 0.0),
}

QUEUE_DEPTH_RANGE = (2, 6)

_THROUGHPUT_CENTRE = 145.0
_THROUGHPUT_AMPLITUDE = 15.0
_THROUGHPUT_WAVELENGTH = 10.0


def throughput_nominal(t: float) -> float:
    """Returns the jitter-free request rate at t seconds: a slow sine around 145 req/s."""
    return _THROUGHPUT_CENTRE + _THROUGHPUT_AMPLITUDE * math.sin(t / _THROUGHPUT_WAVELENGTH)


def nominals(t: float) -> MetricValues:
    """Returns every metric at its nominal value, with the throughput sine evaluated at t."""
    values = dict(NOMINALS)
    values["requests_per_sec"] = throughput_nominal(t)
    return values


def sample(t: float, rng: random.Random) -> MetricValues:
    """Returns one jittered steady-state sample of every simulated metric at t seconds."""
    values = nominals(t)
    for field, (low, high) in JITTER.items():
        values[field] += rng.uniform(low, high)
    values["sqs_active_queue_depth"] = float(rng.randint(*QUEUE_DEPTH_RANGE))
    return values
