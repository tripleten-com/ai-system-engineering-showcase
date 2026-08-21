"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/retrieval/rank_fusion.py
Component:          Reciprocal Rank Fusion
Purpose:            Fuses the pgvector cosine ranking and the full-text ranking into one
                    ordering without needing the two score scales to be comparable.
Interacts With:     None (pure functions)

Curriculum Project:  Project 2 — Hybrid RAG & Retrieval Architecture
Skills:             Reciprocal Rank Fusion, Hybrid Retrieval, Score Normalisation
Tools:              Python 3.11

RRF fuses *ranks*, never scores, and that is why it is the right choice here. A cosine
similarity and a `ts_rank` are not on the same scale and no amount of min-max normalising
makes them so — normalising two incomparable scales just produces a confident-looking wrong
answer. Ranks are ordinal and directly comparable.

The consequence to keep in mind when reading a payload: the fused score is bounded by
(0, 2/(k+1)] for two rankers and is **not** a confidence. That is exactly why the RAG_MATCH
contract carries `rrf_rank` and `cosine_similarity` as separate fields — the rank is the
meaningful ordering, and the cosine value is reported alongside it for the human.
"""

from collections.abc import Sequence
from dataclasses import dataclass

# The constant from Cormack et al. (2009). It damps the contribution of top ranks so a single
# ranker cannot dominate the fusion on its own — the reason to fuse at all.
RRF_K = 60


@dataclass(frozen=True)
class FusedResult:
    """One document's position after fusion, with the per-leg ranks that produced it."""

    document_id: str
    rrf_score: float
    rrf_rank: int
    vector_rank: int | None
    fts_rank: int | None


def reciprocal_rank_score(rank: int, k: int = RRF_K) -> float:
    """Returns one ranker's contribution for a document at 1-based `rank`."""
    if rank < 1:
        raise ValueError(f"rank is 1-based, got {rank}")
    return 1.0 / (k + rank)


def fuse(
    vector_ranking: Sequence[str],
    fts_ranking: Sequence[str],
    k: int = RRF_K,
) -> list[FusedResult]:
    """Fuses two ordered id lists into one ranking, best first.

    A document missing from one leg is not penalised beyond simply scoring nothing there, which
    is what lets a strong vector match still win when the query shares no lexemes with the
    document — and vice versa. Ties break on the vector leg's ordering, then on document id, so
    the output is total and reproducible rather than dependent on dict iteration order.
    """
    vector_positions = {doc_id: index + 1 for index, doc_id in enumerate(vector_ranking)}
    fts_positions = {doc_id: index + 1 for index, doc_id in enumerate(fts_ranking)}

    scores: dict[str, float] = {}
    for positions in (vector_positions, fts_positions):
        for doc_id, rank in positions.items():
            scores[doc_id] = scores.get(doc_id, 0.0) + reciprocal_rank_score(rank, k)

    # Deterministic total order: score desc, then vector rank asc (absent sorts last), then id.
    ordered = sorted(
        scores,
        key=lambda doc_id: (
            -scores[doc_id],
            vector_positions.get(doc_id, len(vector_positions) + 1),
            doc_id,
        ),
    )

    return [
        FusedResult(
            document_id=doc_id,
            rrf_score=scores[doc_id],
            rrf_rank=index + 1,
            vector_rank=vector_positions.get(doc_id),
            fts_rank=fts_positions.get(doc_id),
        )
        for index, doc_id in enumerate(ordered)
    ]
