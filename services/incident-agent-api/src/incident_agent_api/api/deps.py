"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/api/deps.py
Component:          FastAPI Dependency Providers
Purpose:            Resolves shared per-request collaborators out of application state so routes
                    never reach into globals themselves.
Interacts With:     incident-agent-api (:8000), redis (:6379)

Curriculum Project:  Cross-cutting — Clean Code & Modular Ports
Skills:             Dependency Injection, Modular Ports
Tools:              FastAPI, Python 3.11
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from redis.asyncio import Redis

from incident_agent_api.agent.orchestrator import Orchestrator
from incident_agent_api.config import Settings, get_settings
from incident_agent_api.infra.eventbus import EventBus
from incident_agent_api.infra.redis import get_client
from incident_agent_api.infra.workload import CustomerWorkload
from incident_agent_api.telemetry.engine import TelemetryEngine


def get_telemetry_engine(request: Request) -> TelemetryEngine:
    """Returns the process-wide telemetry engine attached to the app at construction time."""
    engine = getattr(request.app.state, "engine", None)
    if engine is None:  # pragma: no cover - create_app always attaches one
        raise RuntimeError("telemetry engine is not attached to application state")
    return engine


def get_event_bus(request: Request) -> EventBus:
    """Returns the process-wide SSE bus attached to the app at construction time."""
    bus = getattr(request.app.state, "event_bus", None)
    if bus is None:  # pragma: no cover - create_app always attaches one
        raise RuntimeError("event bus is not attached to application state")
    return bus


def get_orchestrator(request: Request) -> Orchestrator:
    """Returns the run orchestrator, or 503 while the agent graph is still being compiled.

    Absent only during the startup window before Postgres answers: the graph needs a
    checkpointer, so it is compiled in `_init_persistence` rather than in `create_app`. A 503
    is the honest answer there — the same one `/healthz` is giving at that moment — where a
    RuntimeError would surface as a 500 and read as a defect.
    """
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="the agent control plane is still initializing; retry once /healthz returns 200",
        )
    return orchestrator


def get_workload(request: Request) -> CustomerWorkload:
    """Returns the customer workload task pair."""
    workload = getattr(request.app.state, "workload", None)
    if workload is None:  # pragma: no cover - create_app always attaches one
        raise RuntimeError("customer workload is not attached to application state")
    return workload


def get_redis_client() -> Redis | None:
    """Returns the Redis client, or None before startup.

    Nullable rather than raising, matching `get_engine`: the idempotency layer treats a missing
    Redis as "proceed unguarded" instead of failing the request, so a route must be able to
    receive None here.
    """
    return get_client()


EngineDep = Annotated[TelemetryEngine, Depends(get_telemetry_engine)]
EventBusDep = Annotated[EventBus, Depends(get_event_bus)]
OrchestratorDep = Annotated[Orchestrator, Depends(get_orchestrator)]
WorkloadDep = Annotated[CustomerWorkload, Depends(get_workload)]
RedisDep = Annotated[Redis | None, Depends(get_redis_client)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
