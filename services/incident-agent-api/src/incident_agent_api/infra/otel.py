"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/infra/otel.py
Component:          OpenTelemetry Tracing Exporter
Purpose:            Configures the tracer provider, auto-instruments FastAPI, httpx, SQLAlchemy,
                    and Redis, and streams spans over OTLP/gRPC to Jaeger.
Interacts With:     jaeger (:4317), postgres-vector (:5432), redis (:6379)

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             Distributed Tracing, OTLP Export, Auto-Instrumentation
Tools:              OpenTelemetry SDK, Jaeger, FastAPI, Python 3.11
"""

import contextlib
import logging

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from incident_agent_api.config import Settings
from incident_agent_api.infra.db import get_engine

logger = logging.getLogger("incident-agent-api")

# Matches the container name, the Prometheus job label, and the Jaeger service name, so one
# identifier follows a request across all three tools.
SERVICE_NAME = "incident-agent-api"
SERVICE_VERSION = "1.0.0"
TRACER_NAME = "incident_agent_api"

# Prometheus scrapes /metrics every second and Compose probes /healthz every three. Tracing
# either would add roughly 115,000 spans a day of pure noise and bury the incident waterfall
# this demo exists to show, so both are excluded at the instrumentor.
#
# /api/stream is excluded for the opposite reason: it is not noisy, it is *unbounded*. A server
# span covers a request for its whole duration, and an SSE connection stays open for as long as
# the War Room tab does — so tracing it would produce one multi-minute span, exported only when
# the browser disconnects, sitting next to the incident waterfall it would visually dwarf.
EXCLUDED_URLS = "healthz,metrics,stream"

_provider: TracerProvider | None = None


def get_tracer() -> trace.Tracer:
    """Returns the service tracer, which is a no-op proxy until init_tracing runs."""
    return trace.get_tracer(TRACER_NAME)


def instrument_http(app: FastAPI) -> None:
    """Installs the ASGI middleware that opens a server span per request.

    Called from create_app(), not from lifespan, and the distinction is load-bearing rather
    than stylistic. FastAPIInstrumentor works by adding middleware, and Starlette builds its
    middleware stack before it dispatches lifespan startup — so an instrument_app() call from
    inside lifespan arrives too late, is silently discarded, and logs nothing. The symptom is
    a Jaeger with no HTTP operations at all, where every manual span is its own root and the
    request waterfall this demo exists to show has no trunk.

    Runs before init_tracing installs the provider, which is fine: the instrumentor holds a
    proxy tracer that resolves the global provider on first use, not at instrumentation time.
    """
    FastAPIInstrumentor.instrument_app(app, excluded_urls=EXCLUDED_URLS)


def init_tracing(settings: Settings) -> None:
    """Installs the OTLP tracer provider and instruments the outbound integrations.

    Safe to run after the app has started, and it has to: SQLAlchemy is instrumented against
    a live engine. The inbound request middleware cannot wait that long — see instrument_http.
    """
    global _provider
    if _provider is not None:
        return

    resource = Resource.create(
        {
            "service.name": SERVICE_NAME,
            "service.version": SERVICE_VERSION,
            "deployment.environment": "demo",
        }
    )
    _provider = TracerProvider(resource=resource)
    # BatchSpanProcessor exports off the request path and drops batches when the collector is
    # unreachable. Tracing is diagnostics, never a dependency: a missing Jaeger must not fail a
    # request or block startup.
    _provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint, insecure=True))
    )
    trace.set_tracer_provider(_provider)

    HTTPXClientInstrumentor().instrument()
    RedisInstrumentor().instrument()

    db_engine = get_engine()
    if db_engine is not None:
        # The async engine wraps a sync core; the instrumentor hooks the latter.
        SQLAlchemyInstrumentor().instrument(engine=db_engine.sync_engine)

    logger.info("OTLP tracing initialized, exporting to %s", settings.otel_exporter_otlp_endpoint)


def shutdown_tracing() -> None:
    """Flushes pending spans, uninstruments the integrations, and tears the provider down.

    Uninstrumenting matters because init_tracing guards on `_provider`: clearing that without
    unpatching would let a second init re-instrument libraries that are already patched, so a
    process entering the lifespan twice would emit every outbound span twice.
    """
    global _provider
    if _provider is None:
        return

    for instrumentor in (HTTPXClientInstrumentor(), RedisInstrumentor(), SQLAlchemyInstrumentor()):
        with contextlib.suppress(Exception):
            instrumentor.uninstrument()

    _provider.shutdown()
    _provider = None
