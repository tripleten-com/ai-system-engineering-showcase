"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/api/routes/retrieval.py
Component:          Live Runbook Retrieval Probe
Purpose:            POST /api/retrieval/search — runs the real hybrid search against an
                    arbitrary visitor query so the RAG Inspector can prove the retrieval layer
                    is not scripted.
Interacts With:     incident-war-room (:3000), postgres-vector (:5432)

Curriculum Project:  Project 2 — Hybrid RAG & Retrieval Architecture
Skills:             Vector Search, API Architecture, Honest Disclosure
Tools:              FastAPI, Pydantic 2, pgvector, Python 3.11

This endpoint exists for one reason, spelled out in spa-design-guidelines.md §9: a visitor typing
their own query and getting a real runbook back with a real cosine score is the single strongest
proof that the retrieval layer is not scripted. Everything else in the demo is triggered by a
button and could, in principle, be a recording.

It is deliberately read-only and stateless. It never touches the telemetry engine, never advances
the state machine, and never runs during an incident on the incident's behalf — the agent has its
own retrieval call inside the graph. So it is safe to hit at any time, including from `HEALTHY`.
"""

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from incident_agent_api.infra.db import get_engine
from incident_agent_api.retrieval.hybrid_search import search
from tripleten_contracts import RagMatchPayload

logger = logging.getLogger("incident-agent-api")

router = APIRouter(prefix="/api/retrieval", tags=["retrieval"])

# Long enough for a natural symptom description, short enough that nobody pastes a log file into
# the tsquery builder. The FTS leg reduces the query to `[A-Za-z0-9_]+` lexemes regardless, so this
# is a resource bound rather than a safety one.
MAX_QUERY_LENGTH = 500

# The corpus is four documents. Returning three of them ranked is a demonstration; returning all
# four is a table dump that shows nothing about ordering.
DEFAULT_LIMIT = 3
MAX_LIMIT = 4


class SearchRequest(BaseModel):
    """A visitor's free-text query."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=MAX_QUERY_LENGTH)
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)


class SearchResponse(BaseModel):
    """The fused ranking, plus the query echoed back so a stale response cannot be mistaken
    for a fresh one when a visitor types faster than the round trip."""

    query: str
    results: list[RagMatchPayload]


@router.post("/search", response_model=SearchResponse)
async def search_runbooks(request: SearchRequest) -> SearchResponse:
    """Runs both retrieval legs over the visitor's query and returns the RRF-fused ranking.

    503 rather than 500 when the database is not yet up: during the startup window this is the
    same answer `/healthz` is giving, and it tells the caller to retry rather than that the
    endpoint is broken.
    """
    engine = get_engine()
    if engine is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="the retrieval layer is still initializing; retry once /healthz returns 200",
        )

    results = await search(engine, request.query, limit=MAX_LIMIT)
    return SearchResponse(query=request.query, results=results[: request.limit])
