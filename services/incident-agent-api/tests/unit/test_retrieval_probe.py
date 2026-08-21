"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/tests/unit/test_retrieval_probe.py
Component:          Live Runbook Retrieval Probe — Request Contract
Purpose:            Pins the validation boundary of POST /api/retrieval/search, the disclosure
                    endpoint the RAG Inspector's query box drives.
Interacts With:     incident-agent-api (:8000)

Curriculum Project:  Project 2 — Hybrid RAG & Retrieval Architecture
Skills:             Request Validation, API Contract Testing
Tools:              pytest, Pydantic 2, Python 3.11

The endpoint takes free text from anyone who loads the page, so its request model is the only thing
between a visitor and the retrieval layer. These are unit tests of that model plus the route's
behaviour with no database attached; `tests/integration/test_pgvector_retrieval.py` covers what it
actually returns.
"""

import pytest
from pydantic import ValidationError

from incident_agent_api.api.routes.retrieval import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MAX_QUERY_LENGTH,
    SearchRequest,
)


def test_a_plain_query_is_accepted_with_the_default_limit() -> None:
    request = SearchRequest(query="redis keys expiring at once")

    assert request.query == "redis keys expiring at once"
    assert request.limit == DEFAULT_LIMIT


def test_an_empty_query_is_refused() -> None:
    # There is nothing to rank. Accepting it would return the corpus in index order and present
    # that as a retrieval result.
    with pytest.raises(ValidationError):
        SearchRequest(query="")


def test_a_query_longer_than_the_bound_is_refused() -> None:
    with pytest.raises(ValidationError):
        SearchRequest(query="x" * (MAX_QUERY_LENGTH + 1))


def test_a_query_at_the_bound_is_accepted() -> None:
    assert len(SearchRequest(query="x" * MAX_QUERY_LENGTH).query) == MAX_QUERY_LENGTH


@pytest.mark.parametrize("limit", [0, -1, MAX_LIMIT + 1])
def test_a_limit_outside_the_corpus_is_refused(limit: int) -> None:
    # The corpus is four documents. A limit of 40 is not a bigger answer, it is a caller who has
    # misunderstood the endpoint.
    with pytest.raises(ValidationError):
        SearchRequest(query="pool", limit=limit)


def test_unknown_fields_are_refused() -> None:
    # `extra="forbid"`, matching the guardrail models. A typo'd field name that is silently ignored
    # is how a caller ends up believing it filtered something.
    with pytest.raises(ValidationError):
        SearchRequest(query="pool", threshold=0.9)  # type: ignore[call-arg]


def test_the_probe_reports_service_unavailable_before_the_database_is_up(monkeypatch) -> None:
    """503, not 500, during the startup window.

    The retrieval layer needs Postgres, and `/healthz` is already answering "not ready" at that
    moment. A 503 tells the caller to retry; a 500 says the endpoint is broken.
    """
    import asyncio

    from fastapi import HTTPException

    from incident_agent_api.api.routes import retrieval

    monkeypatch.setattr(retrieval, "get_engine", lambda: None)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(retrieval.search_runbooks(SearchRequest(query="pool")))

    assert caught.value.status_code == 503


def test_the_probe_truncates_to_the_requested_limit(monkeypatch) -> None:
    """The route asks the search layer for the whole corpus and trims afterwards.

    Deliberate: RRF ranks the candidates it is given, so cutting the candidate set before fusion
    would change the ordering rather than just shorten it.
    """
    import asyncio

    from incident_agent_api.api.routes import retrieval
    from tripleten_contracts import RagMatchPayload, RunbookId

    ranked = [
        RagMatchPayload(
            runbook_id=runbook_id,
            title=f"{runbook_id.value} title",
            cosine_similarity=0.5,
            rrf_rank=rank,
            excerpt="excerpt",
            source="pgvector (cosine) + FTS, fused via RRF",
        )
        for rank, runbook_id in enumerate(RunbookId, start=1)
    ]

    captured: dict[str, object] = {}

    async def fake_search(engine, query, limit=None, k=None):  # noqa: ANN001, ANN202 - test double
        captured["limit"] = limit
        return ranked

    monkeypatch.setattr(retrieval, "get_engine", lambda: object())
    monkeypatch.setattr(retrieval, "search", fake_search)

    response = asyncio.run(retrieval.search_runbooks(SearchRequest(query="pool", limit=2)))

    assert captured["limit"] == MAX_LIMIT
    assert [match.runbook_id for match in response.results] == [ranked[0].runbook_id, ranked[1].runbook_id]
    assert response.query == "pool", "the query is echoed so a stale response cannot be mistaken for a fresh one"
