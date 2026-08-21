"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/api/routes/telemetry.py
Component:          Telemetry Snapshot & Service Root
Purpose:            GET /api/telemetry/current — single live snapshot, the SSE polling fallback.
Interacts With:     incident-war-room (:3000)

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             Telemetry Math, Polling Fallback, API Architecture
Tools:              FastAPI, Pydantic 2, Python 3.11
"""

from fastapi import APIRouter

from incident_agent_api.api.deps import EngineDep
from tripleten_contracts import TelemetrySnapshotResponse

router = APIRouter(tags=["telemetry"])


@router.get("/api/telemetry/current", response_model=TelemetrySnapshotResponse)
async def current_telemetry(engine: EngineDep) -> TelemetrySnapshotResponse:
    """Returns the latest generated sample. Read-only: it never advances the state machine."""
    return engine.snapshot()


@router.get("/")
async def root() -> dict[str, str]:
    """Root endpoint for basic service check."""
    return {"service": "incident-agent-api", "status": "running"}
