"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             packages/contracts/src/tripleten_contracts/telemetry.py
Component:          Telemetry Contract — Metric Roster, Bands & Snapshot Schemas
Purpose:            Single source of truth for the Prometheus metric roster, the steady-state
                    value bands, and the metric payload shape shared by the polling snapshot,
                    the SSE METRICS_UPDATE frame, and the War Room.
Interacts With:     incident-agent-api (:8000), incident-war-room (:3000), prometheus (:9090)

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             Golden Signals, Prometheus Metric Naming, Contract-First Design
Tools:              Pydantic 2, Python 3.11
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from tripleten_contracts.identifiers import ScenarioId
from tripleten_contracts.states import IncidentState


class MetricName(StrEnum):
    """The eleven Prometheus metric families exposed by GET /metrics — no more, no fewer."""

    HTTP_REQUESTS_TOTAL = "http_requests_total"
    HTTP_5XX_ERRORS_TOTAL = "http_5xx_errors_total"
    SECURITY_VIOLATIONS_TOTAL = "security_violations_total"
    HTTP_REQUEST_DURATION_MILLISECONDS = "http_request_duration_milliseconds"
    DB_POOL_UTILIZATION_PCT = "db_pool_utilization_pct"
    REDIS_MEMORY_UTILIZATION_PCT = "redis_memory_utilization_pct"
    CACHE_HIT_RATIO_PCT = "cache_hit_ratio_pct"
    SQS_ACTIVE_QUEUE_DEPTH = "sqs_active_queue_depth"
    DLQ_MESSAGE_COUNT = "dlq_message_count"
    ACTIVE_WORKERS_COUNT = "active_workers_count"
    SYSTEM_HEALTH_STATUS = "system_health_status"


class MetricKind(StrEnum):
    """Prometheus type of a metric family. Counters are monotonic; a reset never clears them."""

    COUNTER = "counter"
    GAUGE = "gauge"


class Quantile(StrEnum):
    """Label values on http_request_duration_milliseconds."""

    P50 = "p50"
    P95 = "p95"
    P99 = "p99"


# The three _total families are counters and hold no baseline value, only a baseline increment
# rate. Everything else is a gauge holding the literal simulated value.
METRIC_KINDS: dict[MetricName, MetricKind] = {
    MetricName.HTTP_REQUESTS_TOTAL: MetricKind.COUNTER,
    MetricName.HTTP_5XX_ERRORS_TOTAL: MetricKind.COUNTER,
    MetricName.SECURITY_VIOLATIONS_TOTAL: MetricKind.COUNTER,
    MetricName.HTTP_REQUEST_DURATION_MILLISECONDS: MetricKind.GAUGE,
    MetricName.DB_POOL_UTILIZATION_PCT: MetricKind.GAUGE,
    MetricName.REDIS_MEMORY_UTILIZATION_PCT: MetricKind.GAUGE,
    MetricName.CACHE_HIT_RATIO_PCT: MetricKind.GAUGE,
    MetricName.SQS_ACTIVE_QUEUE_DEPTH: MetricKind.GAUGE,
    MetricName.DLQ_MESSAGE_COUNT: MetricKind.GAUGE,
    MetricName.ACTIVE_WORKERS_COUNT: MetricKind.GAUGE,
    MetricName.SYSTEM_HEALTH_STATUS: MetricKind.GAUGE,
}

# Inclusive steady-state bands, keyed by snapshot field name rather than by Prometheus family:
# the browser payload carries instantaneous rates (requests_per_sec, http_5xx_error_rate_pct)
# that deliberately have no Prometheus series. Read by the chaos unit tests, the smoke suite,
# and the War Room's "inside baseline" badge, so the numbers live in exactly one place.
BASELINE_BANDS: dict[str, tuple[float, float]] = {
    "requests_per_sec": (127.0, 163.0),
    "http_5xx_error_rate_pct": (0.0, 0.0),
    "latency_p50_ms": (16.5, 20.5),
    "latency_p95_ms": (31.0, 37.0),
    "latency_p99_ms": (44.0, 52.0),
    "db_pool_utilization_pct": (13.0, 17.0),
    "redis_memory_utilization_pct": (39.0, 41.0),
    "cache_hit_ratio_pct": (98.5, 99.0),
    "sqs_active_queue_depth": (2.0, 6.0),
    "dlq_message_count": (0.0, 0.0),
    "active_workers_count": (4.0, 4.0),
}

# Short infix in a generated incident_id, e.g. inc-9938-db. Distinct per scenario so an id
# is self-describing in a log line without a lookup.
SCENARIO_SLUG: dict[ScenarioId, str] = {
    ScenarioId.DB_POOL_EXHAUSTION: "db",
    ScenarioId.CACHE_THUNDERING_HERD: "cache",
    ScenarioId.WORKER_DEADLOCK: "worker",
    ScenarioId.PROMPT_INJECTION: "sec",
}

HEALTH_OK = 1
HEALTH_DOWN = 0
HEALTH_DEGRADED = 2

# States whose health value is the same whatever scenario produced them. Every state absent
# from this table is scenario-dependent and resolved in health_status_for below.
_SCENARIO_INDEPENDENT_HEALTH: dict[IncidentState, int] = {
    IncidentState.HEALTHY: HEALTH_OK,
    IncidentState.CRITICAL_OUTAGE: HEALTH_DOWN,
    IncidentState.EXPLOIT_INTERCEPTED: HEALTH_DEGRADED,
    IncidentState.RECOVERING: HEALTH_DEGRADED,
    IncidentState.SECURITY_CONTAINED: HEALTH_DEGRADED,
}


def health_status_for(state: IncidentState, scenario_id: ScenarioId | None) -> int:
    """Maps a run state and its scenario to the 0=Down / 1=OK / 2=Degraded enum."""
    fixed = _SCENARIO_INDEPENDENT_HEALTH.get(state)
    if fixed is not None:
        return fixed
    # AWAITING_APPROVAL, EXECUTING, REJECTED and FAILED all mean different things on the two
    # paths, so none of them can be answered from the state alone. On the security path the
    # infrastructure never broke — no chaos math ever ran and every gauge held baseline — so
    # the platform is Degraded rather than Down for the whole run, terminal branches included.
    #
    # REJECTED and FAILED are the easy ones to get wrong, because "the remediation did not
    # happen" reads like an outage. For Scenarios 1-3 it is one: the chaos values persist until
    # reset. For Scenario 4 there is nothing to persist. Reporting Down there would put
    # `system_health_status` at 0 while every infrastructure gauge in the same frame reads
    # healthy, and would contradict the NO CUSTOMER IMPACT claim the War Room holds up for the
    # whole run — a declined containment plan does not retroactively create an outage.
    if scenario_id is ScenarioId.PROMPT_INJECTION:
        return HEALTH_DEGRADED
    return HEALTH_DOWN


Percentage = Annotated[float, Field(ge=0.0, le=100.0)]
NonNegativeMs = Annotated[float, Field(ge=0.0)]
MessageCount = Annotated[int, Field(ge=0)]


class GoldenSignals(BaseModel):
    """Traffic, errors, and latency — the four golden signals as the browser receives them."""

    requests_per_sec: Annotated[float, Field(ge=0.0)]
    http_5xx_error_rate_pct: Percentage
    latency_p50_ms: NonNegativeMs
    latency_p95_ms: NonNegativeMs
    latency_p99_ms: NonNegativeMs

    @model_validator(mode="after")
    def _percentiles_are_ordered(self) -> "GoldenSignals":
        """Enforces p50 <= p95 <= p99, an invariant no chaos or decay profile may break."""
        if not self.latency_p50_ms <= self.latency_p95_ms <= self.latency_p99_ms:
            raise ValueError(
                "latency percentiles must be ordered p50 <= p95 <= p99, got "
                f"{self.latency_p50_ms} / {self.latency_p95_ms} / {self.latency_p99_ms}"
            )
        return self


class InfrastructureMetrics(BaseModel):
    """Platform gauges plus the security counter, as the browser receives them."""

    system_health_status: Annotated[int, Field(ge=0, le=2)]
    db_pool_utilization_pct: Percentage
    redis_memory_utilization_pct: Percentage
    cache_hit_ratio_pct: Percentage
    sqs_active_queue_depth: MessageCount
    dlq_message_count: MessageCount
    active_workers_count: MessageCount
    security_violations_total: MessageCount


class MetricsSnapshot(BaseModel):
    """The data payload of an SSE METRICS_UPDATE frame."""

    # The telemetry spec names this key "status" inside METRICS_UPDATE and "state" in the
    # polling snapshot below. Both spellings are contractual — the frontend codes against
    # each verbatim — so they are preserved rather than unified.
    status: IncidentState
    golden_signals: GoldenSignals
    infrastructure: InfrastructureMetrics


class TelemetrySnapshotResponse(BaseModel):
    """The GET /api/telemetry/current body: metrics plus the identifiers needed to rehydrate a UI."""

    incident_id: str | None
    thread_id: str | None
    scenario_id: ScenarioId | None
    state: IncidentState
    timestamp: datetime
    golden_signals: GoldenSignals
    infrastructure: InfrastructureMetrics
