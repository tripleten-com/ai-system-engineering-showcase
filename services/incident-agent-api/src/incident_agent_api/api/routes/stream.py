"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/api/routes/stream.py
Component:          Server-Sent Events Transport
Purpose:            GET /api/stream — the single multiplexed channel the War Room codes against.
                    One connection carries all five event types; the browser demultiplexes on
                    the envelope's `type`.
Interacts With:     incident-war-room (:3000)

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             Event Streaming, Backpressure, API Contract Design
Tools:              FastAPI, Starlette, Python 3.11
"""

import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from incident_agent_api.api.deps import EngineDep, EventBusDep
from incident_agent_api.infra.eventbus import EventBus
from incident_agent_api.telemetry.engine import TelemetryEngine
from tripleten_contracts import sse_format, sse_retry_preamble

logger = logging.getLogger("incident-agent-api")

router = APIRouter(tags=["stream"])

SSE_MEDIA_TYPE = "text/event-stream"

STREAM_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # A reverse proxy that buffers this response destroys the only property it has. nginx and
    # its derivatives honour this header; it is inert everywhere else.
    "X-Accel-Buffering": "no",
}


async def _frames(bus: EventBus, incident_id: str | None) -> AsyncIterator[str]:
    """Yields the retry preamble, then one SSE frame per published event until the stream ends.

    The subscription is attached inside the generator rather than in the route body so that
    client disconnect, buffer overflow, and application shutdown all unwind through the same
    context manager — Starlette closes this generator when the client goes away.

    A scoped stream ends the moment a frame arrives that does not belong to the run it named.
    Checking ownership only at connect time would make the guarantee last exactly one instant:
    a tab already streaming when the operator hits Master Reset would carry straight on into
    the next incident's telemetry, which is the failure the `409` exists to prevent. Ending the
    response instead sends that client down the reconnect-and-rehydrate path, where it either
    reattaches unscoped or learns its run is over.
    """
    async with bus.subscribe() as subscription:
        yield sse_retry_preamble()
        async for event in subscription:
            if incident_id is not None and event.incident_id != incident_id:
                logger.info(
                    "Ending stream scoped to %s: frame belongs to %s",
                    incident_id,
                    event.incident_id,
                )
                return
            yield sse_format(event)


@router.get("/api/stream")
async def stream_incident(
    engine: EngineDep,
    bus: EventBusDep,
    incident_id: str | None = Query(
        default=None,
        description="Restrict the stream to one run. Omit to follow the platform, including baseline.",
    ),
) -> StreamingResponse:
    """Opens the multiplexed SSE channel.

    `incident_id` is optional, and omitting it is the normal case for a freshly loaded War Room:
    the dashboard renders live baseline charts before anyone picks a scenario, so the stream has
    to exist before a run does.

    Supplying it is a claim of ownership, enforced at both ends: a mismatch at connect time is
    refused with `409`, and a stream that outlives its run is closed rather than allowed to
    drift onto the next one. Both halves guard the same failure — a browser tab left open
    across a Master Reset still holds the previous `incident_id`, and either without the check
    or without the close it would silently render the *next* incident's telemetry under the old
    run's identity.
    """
    if incident_id is not None:
        _require_active_run(engine, incident_id)

    return StreamingResponse(
        _frames(bus, incident_id),
        media_type=SSE_MEDIA_TYPE,
        headers=STREAM_HEADERS,
    )


def _require_active_run(engine: TelemetryEngine, incident_id: str) -> None:
    """Refuses a stream request whose incident_id is not the run in flight."""
    run = engine.machine.run
    if run is not None and run.incident_id == incident_id:
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "message": "that incident is not the run in flight; omit incident_id to follow the platform",
            "incident_id": run.incident_id if run else None,
        },
    )
