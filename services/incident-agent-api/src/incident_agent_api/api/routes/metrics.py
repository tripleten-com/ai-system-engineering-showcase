"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/api/routes/metrics.py
Component:          Prometheus Exposition Endpoint
Purpose:            GET /metrics — Prometheus text exposition of the platform gauges and counters.
Interacts With:     prometheus (:9090)

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             Prometheus Exposition Format, Metric Naming, Observability
Tools:              FastAPI, prometheus-client, Python 3.11
"""

from fastapi import APIRouter, Response

from incident_agent_api.telemetry import registry

router = APIRouter(tags=["observability"])


@router.get("/metrics")
async def metrics() -> Response:
    """Renders the current registry. A scrape is a read: it never advances the simulation."""
    body, content_type = registry.render()
    return Response(content=body, media_type=content_type)
