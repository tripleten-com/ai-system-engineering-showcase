"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/main.py
Component:          Application Factory & Lifespan Wiring
Purpose:            Builds the FastAPI app, registers routers, and owns startup/shutdown.
                    Wiring only — business logic belongs in the project packages.
Interacts With:     postgres-vector (:5432), redis (:6379), localstack (:4566), jaeger (:4317)

Curriculum Project:  Cross-cutting — Clean Code & Modular Ports
Skills:             Application Factory, Lifespan Management, Router Composition
Tools:              FastAPI, Uvicorn, LangGraph, Python 3.11
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from incident_agent_api.agent.checkpointer import close_checkpointer, setup_checkpointer
from incident_agent_api.agent.graph import AgentRuntime, build_graph
from incident_agent_api.agent.orchestrator import Orchestrator
from incident_agent_api.api.routes import health, incidents, metrics, retrieval, stream, telemetry
from incident_agent_api.config import get_settings
from incident_agent_api.infra.db import dispose_engine, get_engine, init_engine
from incident_agent_api.infra.eventbus import EventBus
from incident_agent_api.infra.otel import init_tracing, instrument_http, shutdown_tracing
from incident_agent_api.infra.redis import close_client, init_client
from incident_agent_api.infra.sqs import publish_remediation_job
from incident_agent_api.infra.workload import CustomerWorkload
from incident_agent_api.seed import seed_knowledge_base
from incident_agent_api.telemetry.engine import TelemetryEngine, stop_task

logger = logging.getLogger("incident-agent-api")
logging.basicConfig(level=get_settings().log_level)

SEED_BACKOFF_INITIAL_SECONDS = 1.0
SEED_BACKOFF_MAX_SECONDS = 5.0
SEED_BACKOFF_FACTOR = 1.5


async def _init_persistence(app: FastAPI) -> None:
    """Seeds the knowledge base and creates the checkpointer schema, retrying until Postgres is up.

    Both halves live in one retrying task because both need the same database and both gate
    readiness. `/healthz` reports `runbooks_seeded` and `checkpointer_ready` and returns 503
    until each has landed, which is what makes `depends_on: service_healthy` mean the stack is
    genuinely usable rather than merely listening.

    The checkpointer runs *after* the seed on purpose: `setup()` is the cheaper of the two and
    ordering it second means a database that accepts writes at all has already proved it by
    ingesting four runbooks.
    """
    settings = get_settings()
    attempt = 0
    backoff = SEED_BACKOFF_INITIAL_SECONDS
    while True:
        attempt += 1
        try:
            count = await seed_knowledge_base(settings.database_url)
            saver = await setup_checkpointer(settings.database_url)
            app.state.graph = build_graph(_agent_runtime(app)).compile(checkpointer=saver)
            app.state.orchestrator = Orchestrator(app.state.engine, app.state.graph)
            logger.info("Persistence ready on attempt %d: %d runbooks, checkpointer schema applied", attempt, count)
            return
        except asyncio.CancelledError:
            logger.info("Persistence initialization cancelled during shutdown.")
            return
        except Exception as err:
            logger.warning(
                "Persistence attempt %d waiting on Postgres: %s. Retrying in %.1fs...", attempt, err, backoff
            )
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                return
            backoff = min(backoff * SEED_BACKOFF_FACTOR, SEED_BACKOFF_MAX_SECONDS)


def _agent_runtime(app: FastAPI) -> AgentRuntime:
    """Binds the graph's collaborators to live application state.

    `engine` is resolved lazily through `get_engine()` rather than captured, so a graph compiled
    once at startup keeps working across an engine dispose/recreate instead of holding a stale
    handle.
    """
    return AgentRuntime(
        publish=app.state.event_bus.publish,
        engine=get_engine(),
        dispatch_job=publish_remediation_job,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initializes connections, starts the background tasks, and unwinds them in reverse."""
    logger.info("Initializing incident-agent-api...")
    settings = get_settings()
    init_engine(settings.database_url)
    init_client(settings.redis_url)

    # After init_engine, so SQLAlchemy instrumentation has a live engine to bind to. The
    # inbound request middleware is not installed here — create_app() does that, because by the
    # time lifespan runs Starlette has already built its middleware stack.
    init_tracing(settings)

    app.state.persistence_task = asyncio.create_task(_init_persistence(app))
    app.state.telemetry_task = asyncio.create_task(app.state.engine.run_forever())
    app.state.workload.start()

    yield

    logger.info("Shutting down incident-agent-api...")
    # Before stopping the generator: an idle stream sits blocked on an empty queue, and only
    # the bus can wake it. Leaving it blocked would hold shutdown open. Tolerant of a missing
    # bus for the same reason as the task lookups below — an app assembled without the factory
    # must still unwind the rest of its teardown.
    event_bus = getattr(app.state, "event_bus", None)
    if event_bus is not None:
        event_bus.close_all()

    workload = getattr(app.state, "workload", None)
    if workload is not None:
        await workload.stop()

    await stop_task(getattr(app.state, "telemetry_task", None))

    persistence_task = getattr(app.state, "persistence_task", None)
    if persistence_task and not persistence_task.done():
        persistence_task.cancel()
        try:
            await persistence_task
        except asyncio.CancelledError:
            pass

    await close_checkpointer()
    shutdown_tracing()
    await close_client()
    await dispose_engine()


def create_app() -> FastAPI:
    """Builds the application. Kept separate from module scope so tests can build isolated apps."""
    application = FastAPI(
        title="TripleTen Cloud Platform — Autonomous Incident Defense API",
        version="1.0.0",
        lifespan=lifespan,
    )
    # Here rather than in lifespan, and that is not a style choice: Starlette freezes its
    # middleware stack before it dispatches lifespan startup, so instrumenting from there is
    # silently dropped and the service exports no request spans at all. See instrument_http.
    instrument_http(application)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # The engine is built here rather than in lifespan so /metrics and the polling snapshot
    # answer truthfully the moment the process is importable — including under a test client
    # that never enters lifespan. Lifespan only owns the background task that ticks it.
    application.state.event_bus = EventBus()
    application.state.engine = TelemetryEngine(publisher=application.state.event_bus)
    application.state.workload = CustomerWorkload()
    # The graph needs a checkpointer and therefore a live database, so it is compiled in
    # `_init_persistence` once Postgres answers. Until then the orchestrator is absent and
    # `/healthz` reports 503 — a trigger arriving in that window gets a clean 503 from the
    # dependency rather than a half-initialized run.
    application.state.graph = None
    application.state.orchestrator = None

    # One synchronous tick, so a scrape arriving before the first background tick sees a real
    # baseline sample rather than an empty registry.
    application.state.engine.tick()

    application.include_router(health.router)
    application.include_router(metrics.router)
    application.include_router(telemetry.router)
    application.include_router(stream.router)
    application.include_router(incidents.router)
    application.include_router(retrieval.router)
    return application


# Module-scope instance: the Dockerfile runs `uvicorn incident_agent_api.main:app`.
app = create_app()
