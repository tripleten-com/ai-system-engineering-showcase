"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/tests/integration/test_pgvector_retrieval.py
Component:          Hybrid Retrieval Integration Tests
Purpose:            Asserts real pgvector cosine + FTS retrieval, fused by RRF, returns the
                    right runbook at rank 1 for every scenario, reproducibly.
Interacts With:     postgres-vector (:5432)

Curriculum Project:  Project 2 — Hybrid RAG & Retrieval Architecture
Skills:             Vector Search, HNSW Indexing, Reciprocal Rank Fusion, Determinism
Tools:              Pytest, SQLAlchemy, pgvector, Python 3.11

**Assert rank, not absolute similarity.** The offline path derives embeddings from a seeded hash
function, which produces genuine and reproducible cosine distances — and therefore genuine
retrieval — but nowhere near the 0.94/0.97 figures in incident-scenarios.md. Those are narrative
values for the demo storyline. Top-1 identity and rank ordering are what deterministic embeddings
guarantee bit-for-bit, so those are what this module asserts. The absolute-score floor is checked
only on the optional live-embedding path and skipped when no key is present.
"""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from incident_agent_api import scenarios
from incident_agent_api.retrieval import EMBEDDING_DIMENSION, embed, search
from tripleten_contracts import RunbookId, ScenarioId

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/tripleten_db"
)

EXPECTED_TOP_1: dict[str, RunbookId] = {
    scenarios.RETRIEVAL_QUERY[scenario]: scenario.runbook for scenario in ScenarioId
}


@pytest.fixture
async def engine():
    db = create_async_engine(DATABASE_URL)
    try:
        yield db
    finally:
        await db.dispose()


@pytest.mark.parametrize(("query", "expected"), sorted(EXPECTED_TOP_1.items()), ids=lambda v: str(v)[:28])
async def test_each_scenario_query_retrieves_its_runbook_at_rank_one(engine, query, expected):
    """The assertion the whole retrieval layer exists to satisfy."""
    results = await search(engine, query)

    assert results, f"retrieval returned nothing for {query!r}"
    assert results[0].runbook_id is expected
    assert results[0].rrf_rank == 1


@pytest.mark.parametrize("scenario", list(ScenarioId), ids=lambda s: s.value)
async def test_the_fused_ranking_is_a_strict_total_order(engine, scenario):
    """Ranks are 1..N with no gaps and no ties — RRF must produce one unambiguous ordering."""
    results = await search(engine, scenarios.RETRIEVAL_QUERY[scenario])

    assert [match.rrf_rank for match in results] == list(range(1, len(results) + 1))
    assert len({match.runbook_id for match in results}) == len(results)


@pytest.mark.parametrize("scenario", list(ScenarioId), ids=lambda s: s.value)
async def test_repeating_a_query_returns_a_byte_identical_ranking(engine, scenario):
    """The determinism guarantee that makes the demo repeatable and the fixtures exact."""
    query = scenarios.RETRIEVAL_QUERY[scenario]
    first = await search(engine, query)
    second = await search(engine, query)

    assert [m.model_dump() for m in first] == [m.model_dump() for m in second]


async def test_cosine_similarity_and_rrf_rank_are_reported_separately(engine):
    """Per the RAG_MATCH contract: the fused rank orders, the cosine value informs a human.

    They are not interchangeable — the RRF score is bounded by (0, 2/(k+1)] for two rankers and
    is not a confidence, which is exactly why the payload carries both.
    """
    results = await search(engine, scenarios.RETRIEVAL_QUERY[ScenarioId.DB_POOL_EXHAUSTION])
    top = results[0]

    assert top.rrf_rank == 1
    assert -1.0 <= top.cosine_similarity <= 1.0
    assert top.source == "pgvector (cosine) + FTS, fused via RRF"
    assert top.excerpt and not top.excerpt.startswith("###")


async def test_the_hnsw_index_is_used_rather_than_a_sequential_scan(engine):
    """`ORDER BY embedding <=> $1 LIMIT n` must be an index scan, not a seq scan.

    `enable_seqscan = off` is set for the plan only, and it is necessary rather than cheating:
    the corpus is four rows, so the planner will always prefer a sequential scan on cost alone.
    What is being asserted is that the query *shape* and the index are compatible — which is what
    breaks if someone rewrites the ORDER BY or drops the operator class.
    """
    vector = "[" + ",".join(repr(v) for v in embed("connection pool")) + "]"
    async with engine.connect() as conn:
        await conn.execute(text("SET LOCAL enable_seqscan = off;"))
        plan = await conn.execute(
            text(
                "EXPLAIN SELECT id FROM knowledge_runbooks "
                "ORDER BY embedding <=> (:embedding)::vector LIMIT 4"
            ),
            {"embedding": vector},
        )
        rendered = "\n".join(row[0] for row in plan)

    assert "idx_knowledge_runbooks_embedding" in rendered, rendered
    assert "Index Scan" in rendered, rendered


async def test_the_corpus_is_seeded_with_every_runbook_and_a_full_width_vector(engine):
    """A short vector would make cosine distance meaningless while still returning an order."""
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT id, vector_dims(embedding) AS dims FROM knowledge_runbooks ORDER BY id")
        )
        seeded = {row.id: row.dims for row in rows}

    assert set(seeded) == {runbook.value for runbook in RunbookId}
    assert set(seeded.values()) == {EMBEDDING_DIMENSION}


async def test_the_fts_leg_contributes_rather_than_returning_nothing(engine):
    """Guards the OR-vs-AND decision in the tsquery builder.

    `plainto_tsquery` and `websearch_to_tsquery` both AND their terms, so a seven-word symptom
    description would match no document at all and the FTS leg would silently contribute nothing —
    leaving a "hybrid" retriever that is really vector-only. Asserted by checking that at least
    one document is ranked by the keyword leg.
    """
    from incident_agent_api.retrieval.hybrid_search import _fts_leg, _tsquery_terms

    query = scenarios.RETRIEVAL_QUERY[ScenarioId.WORKER_DEADLOCK]
    assert " | " in _tsquery_terms(query), "terms are not OR-joined"

    async with engine.connect() as conn:
        ranked = await _fts_leg(conn, query, 8)
    assert ranked, "the full-text leg matched nothing; the retriever is effectively vector-only"


async def test_a_stopword_only_query_still_returns_a_ranking(engine):
    """RRF handles a missing leg by design; the vector leg decides alone."""
    results = await search(engine, "the a of and to")
    assert results, "a degenerate query broke retrieval instead of degrading it"
    assert results[0].rrf_rank == 1


@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="live-embedding path only")
@pytest.mark.parametrize(("query", "expected"), sorted(EXPECTED_TOP_1.items()), ids=lambda v: str(v)[:28])
async def test_live_embeddings_clear_the_documented_similarity_floor(engine, query, expected):
    """The 0.85 floor applies only when real embeddings are configured."""
    results = await search(engine, query)
    assert results[0].runbook_id is expected
    assert results[0].cosine_similarity >= 0.85
