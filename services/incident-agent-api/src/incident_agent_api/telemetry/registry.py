"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/telemetry/registry.py
Component:          Prometheus Metric Registry
Purpose:            Declares the eleven canonical metric families and renders the text exposition
                    scraped by Prometheus every second.
Interacts With:     prometheus (:9090)

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             Prometheus Exposition Format, Metric Naming, Observability
Tools:              prometheus-client, Python 3.11
"""

import prometheus_client
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, generate_latest

from tripleten_contracts import MetricName, Quantile

# Load-bearing, not tidy-up. Every Counter otherwise emits a companion `<name>_created` gauge
# in the text exposition, which would break the contract that /metrics exposes exactly the
# eleven canonical families and no more.
prometheus_client.disable_created_metrics()

# A dedicated registry rather than the default one. prometheus_client's global REGISTRY ships
# with process_*, python_gc_*, and python_info collectors attached, and those would land in
# the exposition uninvited.
REGISTRY = CollectorRegistry()

HTTP_REQUESTS_TOTAL = Counter(
    MetricName.HTTP_REQUESTS_TOTAL.value,
    "Total number of HTTP requests processed",
    registry=REGISTRY,
)
HTTP_5XX_ERRORS_TOTAL = Counter(
    MetricName.HTTP_5XX_ERRORS_TOTAL.value,
    "Total number of HTTP 5xx error responses",
    registry=REGISTRY,
)
SECURITY_VIOLATIONS_TOTAL = Counter(
    MetricName.SECURITY_VIOLATIONS_TOTAL.value,
    "Total security firewall intercept events",
    registry=REGISTRY,
)

REQUEST_DURATION_MS = Gauge(
    MetricName.HTTP_REQUEST_DURATION_MILLISECONDS.value,
    "Simulated HTTP request duration percentiles",
    ["quantile"],
    registry=REGISTRY,
)
DB_POOL_UTILIZATION_PCT = Gauge(
    MetricName.DB_POOL_UTILIZATION_PCT.value,
    "PostgreSQL connection pool saturation percentage",
    registry=REGISTRY,
)
REDIS_MEMORY_UTILIZATION_PCT = Gauge(
    MetricName.REDIS_MEMORY_UTILIZATION_PCT.value,
    "Redis memory usage percentage",
    registry=REGISTRY,
)
CACHE_HIT_RATIO_PCT = Gauge(
    MetricName.CACHE_HIT_RATIO_PCT.value,
    "Cache hit ratio percentage",
    registry=REGISTRY,
)
SQS_ACTIVE_QUEUE_DEPTH = Gauge(
    MetricName.SQS_ACTIVE_QUEUE_DEPTH.value,
    "Active unhandled message count in primary SQS queue",
    registry=REGISTRY,
)
DLQ_MESSAGE_COUNT = Gauge(
    MetricName.DLQ_MESSAGE_COUNT.value,
    "Messages routed to dead-letter queue",
    registry=REGISTRY,
)
ACTIVE_WORKERS_COUNT = Gauge(
    MetricName.ACTIVE_WORKERS_COUNT.value,
    "Active consumer worker threads",
    registry=REGISTRY,
)
SYSTEM_HEALTH_STATUS = Gauge(
    MetricName.SYSTEM_HEALTH_STATUS.value,
    "Platform operational status (0=Down, 1=OK, 2=Degraded)",
    registry=REGISTRY,
)

# Snapshot field name → the gauge that publishes it. The latency percentiles are excluded:
# they share one labelled family and are published separately.
GAUGES_BY_FIELD: dict[str, Gauge] = {
    "db_pool_utilization_pct": DB_POOL_UTILIZATION_PCT,
    "redis_memory_utilization_pct": REDIS_MEMORY_UTILIZATION_PCT,
    "cache_hit_ratio_pct": CACHE_HIT_RATIO_PCT,
    "sqs_active_queue_depth": SQS_ACTIVE_QUEUE_DEPTH,
    "dlq_message_count": DLQ_MESSAGE_COUNT,
    "active_workers_count": ACTIVE_WORKERS_COUNT,
}

QUANTILE_FIELDS: dict[Quantile, str] = {
    Quantile.P50: "latency_p50_ms",
    Quantile.P95: "latency_p95_ms",
    Quantile.P99: "latency_p99_ms",
}


def render() -> tuple[bytes, str]:
    """Returns the Prometheus text exposition body and its content type."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
