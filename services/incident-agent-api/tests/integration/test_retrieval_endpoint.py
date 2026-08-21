"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/tests/integration/test_retrieval_endpoint.py
Component:          Live Runbook Retrieval Probe — End to End
Purpose:            Drives POST /api/retrieval/search against the real seeded corpus, the way the
                    RAG Inspector's query box does.
Interacts With:     incident-agent-api (:8000), postgres-vector (:5432)

Curriculum Project:  Project 2 — Hybrid RAG & Retrieval Architecture
Skills:             Vector Search, API Integration Testing, Determinism
Tools:              Pytest, httpx, pgvector, Python 3.11

This endpoint exists so a sceptical visitor can prove the retrieval layer is not scripted. That
claim is only worth making if it holds for queries the demo never wrote down — so the assertions
here use paraphrases rather than the canonical `RETRIEVAL_QUERY` strings, which
`test_pgvector_retrieval.py` already covers.

**Rank, never an absolute similarity floor.** The offline path uses signed feature hashing, so a
short query against a long runbook lands nowhere near the 0.94 in the storyline. Nor is the winner
always the highest cosine: for two of the four paraphrases below another document scores higher on
the vector leg, and RRF still puts the right one first because the keyword leg agrees with it. That
is the fusion earning its place, and it is what `test_fusion_does_work_the_vector_leg_alone_would_not`
pins — a cosine-margin assertion would have quietly required the vector leg to always dominate,
which would make the FTS leg ceremony.
"""

import os

import httpx
import pytest

pytestmark = pytest.mark.integration

API_BASE = os.environ.get("TEST_API_BASE_URL", "http://localhost:8000")

SEARCH = f"{API_BASE}/api/retrieval/search"

# Paraphrases, deliberately not the canonical scenario queries. An SRE typing their own words is
# the scenario this endpoint is for.
PARAPHRASES: list[tuple[str, str]] = [
    ("too many idle postgres sessions holding connections open", "RB-104"),
    ("redis is full and every cache key expired at the same time", "RB-208"),
    ("a bad queue message keeps crashing the consumers", "RB-312"),
    ("someone tried to override the agent instructions to steal credentials", "SEC-501"),
]


@pytest.fixture
async def client():
    async with httpx.AsyncClient(timeout=15.0) as http:
        yield http


@pytest.mark.parametrize(("query", "expected"), PARAPHRASES)
async def test_a_paraphrase_still_ranks_the_right_runbook_first(client, query: str, expected: str) -> None:
    response = await client.post(SEARCH, json={"query": query})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["query"] == query, "the query is echoed so a stale response is distinguishable"
    assert body["results"], "retrieval returned nothing for a query about the seeded corpus"
    assert body["results"][0]["runbook_id"] == expected


@pytest.mark.parametrize(("query", "expected"), PARAPHRASES)
async def test_the_ranking_is_a_strict_sequence(client, query: str, expected: str) -> None:
    """RRF ranks are 1..n with no gaps and no ties.

    The fusion imposes a total order — score descending, then vector rank, then id — so a repeated
    or skipped rank would mean two documents were treated as interchangeable, and the "rank 1"
    every other assertion in this suite relies on would stop meaning anything.
    """
    response = await client.post(SEARCH, json={"query": query, "limit": 3})
    results = response.json()["results"]

    assert results[0]["runbook_id"] == expected
    assert [match["rrf_rank"] for match in results] == list(range(1, len(results) + 1))


async def test_fusion_does_work_the_vector_leg_alone_would_not(client) -> None:
    """At least one paraphrase is won by the keyword leg rather than by cosine.

    This is the assertion that shows the hybrid is load-bearing rather than decorative. If the
    vector leg always dominated, the FTS leg and the fusion would be ceremony — the ranking would be
    a cosine sort wearing an RRF hat. Instead, `RB-312` and `SEC-501` are both ranked first on
    paraphrases where another document scores *higher* on cosine, and RRF still puts the right one
    on top because the keyword leg agrees with it.

    Asserted as "at least one" rather than per-query on purpose: which leg wins a given query is a
    property of the corpus, and pinning it per-query would turn an ordinary runbook edit into a test
    failure. That the two legs disagree somewhere, and that fusion resolves it correctly, is the
    durable claim.
    """
    fts_carried = 0

    for query, expected in PARAPHRASES:
        results = (await client.post(SEARCH, json={"query": query, "limit": 3})).json()["results"]
        assert results[0]["runbook_id"] == expected, f"fusion must be right for {query!r}"

        best_cosine = max(match["cosine_similarity"] for match in results)
        if results[0]["cosine_similarity"] < best_cosine:
            fts_carried += 1

    assert fts_carried > 0, (
        "every query was won on cosine alone, so the FTS leg and the RRF fusion are contributing "
        "nothing to the ranking"
    )


async def test_the_payload_carries_the_full_rag_match_contract(client) -> None:
    response = await client.post(SEARCH, json={"query": "connection pool saturated"})
    match = response.json()["results"][0]

    # The same shape the RAG_MATCH SSE payload uses, so the panel renders a probe result and an
    # incident match with one component instead of two.
    assert set(match) == {"runbook_id", "title", "cosine_similarity", "rrf_rank", "excerpt", "source"}
    assert match["rrf_rank"] == 1
    assert match["source"] == "pgvector (cosine) + FTS, fused via RRF"
    assert match["excerpt"] and not match["excerpt"].startswith("###"), "the excerpt reads as prose"


async def test_the_same_query_returns_a_byte_identical_ranking(client) -> None:
    """Determinism, which is what makes the demo repeatable and this suite exact."""
    query = "postgres pool exhausted with idle transactions"
    first = await client.post(SEARCH, json={"query": query, "limit": 3})
    second = await client.post(SEARCH, json={"query": query, "limit": 3})

    assert first.json() == second.json()


async def test_the_limit_is_honoured(client) -> None:
    response = await client.post(SEARCH, json={"query": "redis cache", "limit": 1})

    assert len(response.json()["results"]) == 1


async def test_a_malformed_body_is_refused_without_touching_the_corpus(client) -> None:
    for body in ({"query": ""}, {"query": "pool", "limit": 99}, {"query": "pool", "threshold": 0.9}):
        response = await client.post(SEARCH, json=body)
        assert response.status_code == 422, f"{body} should not have been accepted"


async def test_the_probe_is_read_only(client) -> None:
    """Querying the corpus must not advance the state machine.

    The probe is offered as a disclosure control that is safe to press at any time, including from
    HEALTHY. If it could move the run, that offer would be false.
    """
    before = (await client.get(f"{API_BASE}/api/telemetry/current")).json()
    await client.post(SEARCH, json={"query": "prompt injection attempt"})
    after = (await client.get(f"{API_BASE}/api/telemetry/current")).json()

    assert before["state"] == after["state"]
    assert before["incident_id"] == after["incident_id"]
    assert after["infrastructure"]["security_violations_total"] == (
        before["infrastructure"]["security_violations_total"]
    ), "a query mentioning an exploit is not an exploit"
