"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/retrieval/hybrid_search.py
Component:          Hybrid RAG & Semantic Search Service
Purpose:            Executes hybrid vector similarity (pgvector) and keyword full-text search
                    over the emergency runbooks, fused by Reciprocal Rank Fusion.
Interacts With:     postgres-vector (:5432), incident-agent-api (:8000)

Curriculum Project:  Project 2 — Hybrid RAG & Retrieval Architecture
Skills:             Vector Search, HNSW Cosine Indexing, FTS Fusion, Metadata Filtering
Tools:              PostgreSQL 16, pgvector, SQLAlchemy, Python 3.11
"""

import logging
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from incident_agent_api.retrieval.embeddings import embed, to_pgvector_literal
from incident_agent_api.retrieval.rank_fusion import RRF_K, fuse
from tripleten_contracts import RagMatchPayload, RunbookId

logger = logging.getLogger("incident-agent-api")

# Both legs retrieve the whole corpus. With four documents any limit is theatre, and asking for
# all of them means the fusion sees every candidate instead of whatever survived two
# independent cut-offs. A real corpus would page this; the number is here to be changed then.
CANDIDATE_LIMIT = 8

EXCERPT_LENGTH = 160

# How the payload names its provenance. Contractual: the War Room renders it verbatim.
SOURCE_LABEL = "pgvector (cosine) + FTS, fused via RRF"

# Lexeme extraction for the FTS leg. Only [a-z0-9_] survives, so the OR-joined tsquery below is
# built exclusively from characters that cannot carry tsquery syntax — no escaping to get wrong.
_LEXEME = re.compile(r"[A-Za-z0-9_]+")

# Terms that match every runbook and therefore rank none of them. Dropping them keeps ts_rank
# discriminating instead of flattening toward a constant.
_STOPWORDS = frozenset({"the", "a", "an", "and", "or", "of", "to", "in", "is", "for", "on", "with"})


def _tsquery_terms(query: str) -> str:
    """Renders a natural-language query as an OR-joined tsquery string.

    OR, not AND — and this is the decision that makes the FTS leg useful rather than empty.
    `plainto_tsquery` and `websearch_to_tsquery` both AND their terms, so a seven-word symptom
    description would have to appear in full inside a runbook to match anything at all, and all
    four legs would return nothing. OR-joining gives `ts_rank` a graded signal across the corpus,
    which is precisely the ordinal input RRF wants.
    """
    terms = [
        token.lower()
        for token in _LEXEME.findall(query)
        if len(token) > 1 and token.lower() not in _STOPWORDS
    ]
    return " | ".join(dict.fromkeys(terms))


async def _vector_leg(conn: AsyncConnection, query: str, limit: int) -> list[tuple[str, float]]:
    """Returns (runbook_id, cosine_similarity) ordered by cosine distance, nearest first.

    `<=>` is pgvector's cosine distance operator, and `ORDER BY ... <=> ... LIMIT n` is the
    shape that lets the HNSW index serve the query. Similarity is reported as `1 - distance`
    because that is the number the RAG_MATCH payload carries and a human reads.
    """
    vector_literal = to_pgvector_literal(embed(query))
    result = await conn.execute(
        text(
            """
            SELECT id, 1 - (embedding <=> (:embedding)::vector) AS cosine_similarity
            FROM knowledge_runbooks
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> (:embedding)::vector
            LIMIT :limit
            """
        ),
        {"embedding": vector_literal, "limit": limit},
    )
    return [(row.id, float(row.cosine_similarity)) for row in result]


async def _fts_leg(conn: AsyncConnection, query: str, limit: int) -> list[str]:
    """Returns runbook ids ordered by full-text relevance, best first.

    An empty term list (a query of nothing but stopwords) returns no ids rather than raising:
    RRF handles a missing leg by design, so the vector leg simply decides the ordering alone.
    """
    terms = _tsquery_terms(query)
    if not terms:
        return []

    result = await conn.execute(
        text(
            """
            SELECT id, ts_rank(content_fts, to_tsquery('english', :terms)) AS rank
            FROM knowledge_runbooks
            WHERE content_fts @@ to_tsquery('english', :terms)
            ORDER BY rank DESC, id
            LIMIT :limit
            """
        ),
        {"terms": terms, "limit": limit},
    )
    return [row.id for row in result]


async def search(
    engine: AsyncEngine,
    query: str,
    limit: int = CANDIDATE_LIMIT,
    k: int = RRF_K,
) -> list[RagMatchPayload]:
    """Runs both retrieval legs and returns the fused ranking as RAG_MATCH payloads.

    Deterministic end to end: hash embeddings, a total ordering out of the fusion, and a fixed
    tie-break inside the FTS leg. Re-running the same query returns a byte-identical result,
    which is what makes the demo repeatable and the integration fixtures exact.
    """
    async with engine.connect() as conn:
        vector_hits = await _vector_leg(conn, query, limit)
        fts_ids = await _fts_leg(conn, query, limit)
        documents = await _load_documents(conn, [doc_id for doc_id, _ in vector_hits] + fts_ids)

    similarity_by_id = dict(vector_hits)
    fused = fuse([doc_id for doc_id, _ in vector_hits], fts_ids, k=k)

    payloads: list[RagMatchPayload] = []
    for result in fused:
        document = documents.get(result.document_id)
        if document is None:
            # A fused id with no row behind it means the corpus changed mid-query. Skipping it
            # is right — the alternative is a payload whose title and excerpt are invented.
            logger.warning("Fused runbook %s has no row; skipping", result.document_id)
            continue
        title, content = document
        payloads.append(
            RagMatchPayload(
                runbook_id=RunbookId(result.document_id),
                title=title,
                cosine_similarity=round(similarity_by_id.get(result.document_id, 0.0), 4),
                rrf_rank=result.rrf_rank,
                excerpt=_excerpt(content),
                source=SOURCE_LABEL,
            )
        )
    return payloads


async def _load_documents(conn: AsyncConnection, doc_ids: list[str]) -> dict[str, tuple[str, str]]:
    """Fetches title and content for the fused candidate set in one round trip."""
    unique_ids = list(dict.fromkeys(doc_ids))
    if not unique_ids:
        return {}
    result = await conn.execute(
        text("SELECT id, title, content FROM knowledge_runbooks WHERE id = ANY(:ids)"),
        {"ids": unique_ids},
    )
    return {row.id: (row.title, row.content) for row in result}


def _excerpt(content: str) -> str:
    """Returns the first substantive line of a runbook, trimmed to excerpt length.

    Skips the `### SECTION HEADING` lines and the bullet dashes so the excerpt reads as prose
    in the RAG Inspector rather than as a fragment of markdown scaffolding.
    """
    for line in content.splitlines():
        stripped = line.strip().lstrip("-").strip()
        if stripped and not stripped.startswith("###"):
            return stripped[:EXCERPT_LENGTH]
    return content.strip()[:EXCERPT_LENGTH]
