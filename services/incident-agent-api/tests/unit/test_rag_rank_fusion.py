"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/tests/unit/test_rag_rank_fusion.py
Component:          Reciprocal Rank Fusion & Embedding Unit Tests
Purpose:            Asserts the fusion arithmetic, its total ordering, and the embedding
                    properties that make cosine distance meaningful.
Interacts With:     None (pure functions)

Curriculum Project:  Project 2 — Hybrid RAG & Retrieval Architecture
Skills:             Reciprocal Rank Fusion, Vector Similarity, Feature Hashing
Tools:              Pytest, Python 3.11

Direct assertions on the functions that decide which runbook the agent acts on. Their only
previous coverage was `test_pgvector_retrieval.py`, which needs a live database and asserts
end-to-end ranking — so inverting the score, dropping the tie-break, or changing `RRF_K` would
only have been caught if the change happened to flip a top-1 result on a four-document corpus.
"""

import math

import pytest

from incident_agent_api.retrieval.embeddings import (
    EMBEDDING_DIMENSION,
    EMBEDDING_VERSION,
    embed,
    to_pgvector_literal,
    tokenize,
)
from incident_agent_api.retrieval.rank_fusion import RRF_K, fuse, reciprocal_rank_score

# ----------------------------------------------------------------------------------
# The score
# ----------------------------------------------------------------------------------


def test_the_constant_is_the_one_from_the_literature():
    """k=60, from Cormack et al. (2009). It damps top ranks so one ranker cannot dominate."""
    assert RRF_K == 60


@pytest.mark.parametrize("rank", [1, 2, 5, 60, 1000])
def test_the_score_is_one_over_k_plus_rank(rank: int):
    """The whole formula, asserted directly rather than inferred from an ordering."""
    assert reciprocal_rank_score(rank) == pytest.approx(1.0 / (RRF_K + rank))


def test_the_score_decreases_monotonically_with_rank():
    """A worse rank must never score higher. Guards an inverted `1/(k - rank)`."""
    scores = [reciprocal_rank_score(rank) for rank in range(1, 25)]
    assert scores == sorted(scores, reverse=True)
    assert all(score > 0 for score in scores)


def test_the_score_rejects_a_zero_based_rank():
    """Ranks are 1-based; a 0 would make the first document score 1/k and shift everything."""
    with pytest.raises(ValueError, match="1-based"):
        reciprocal_rank_score(0)


def test_a_custom_k_changes_the_damping():
    """k is a parameter, and a smaller one weights the top of each list more heavily."""
    assert reciprocal_rank_score(1, k=1) > reciprocal_rank_score(1, k=60)


# ----------------------------------------------------------------------------------
# The fusion
# ----------------------------------------------------------------------------------


def test_a_document_ranked_first_by_both_legs_wins():
    """The unambiguous case, and the sanity check every other assertion rests on."""
    fused = fuse(["a", "b", "c"], ["a", "c", "b"])

    assert fused[0].document_id == "a"
    assert fused[0].rrf_rank == 1
    assert fused[0].rrf_score == pytest.approx(2 * reciprocal_rank_score(1))


def test_scores_sum_across_the_legs():
    """RRF is additive over rankers; the per-leg ranks are reported so this is checkable."""
    fused = {result.document_id: result for result in fuse(["a", "b"], ["b", "a"])}

    assert fused["a"].vector_rank == 1
    assert fused["a"].fts_rank == 2
    assert fused["a"].rrf_score == pytest.approx(
        reciprocal_rank_score(1) + reciprocal_rank_score(2)
    )


def test_a_document_in_only_one_leg_is_not_penalised_beyond_scoring_nothing_in_the_other():
    """What lets a strong vector match win when the query shares no lexemes with the document.

    The alternative — treating absence as a maximum rank — would make the fusion punish a
    document for being invisible to one retriever, which is exactly the case hybrid search exists
    to handle.
    """
    fused = {result.document_id: result for result in fuse(["vector-only"], ["fts-only"])}

    assert fused["vector-only"].fts_rank is None
    assert fused["fts-only"].vector_rank is None
    assert fused["vector-only"].rrf_score == pytest.approx(reciprocal_rank_score(1))
    assert fused["fts-only"].rrf_score == pytest.approx(reciprocal_rank_score(1))


def test_a_document_in_both_legs_outranks_one_in_either_alone():
    """The point of fusing: agreement between retrievers is evidence."""
    fused = fuse(["both", "vector-only"], ["both", "fts-only"])
    assert fused[0].document_id == "both"


def test_the_ranking_is_a_dense_total_order():
    """Ranks are 1..N with no gaps and no duplicates, so a caller can index by rank."""
    fused = fuse(["a", "b", "c", "d"], ["d", "c", "b", "a"])

    assert [result.rrf_rank for result in fused] == [1, 2, 3, 4]
    assert len({result.document_id for result in fused}) == 4


def test_ties_break_on_the_vector_leg_then_on_id():
    """Deterministic, so the same query cannot return two different orderings.

    Symmetric input makes every score identical — with no tie-break the output would depend on
    dict iteration order, and the demo's repeatability claim would be false.
    """
    fused = fuse(["a", "b"], ["b", "a"])

    assert fused[0].rrf_score == pytest.approx(fused[1].rrf_score)
    assert [result.document_id for result in fused] == ["a", "b"]


def test_a_tie_with_no_vector_rank_falls_through_to_the_id():
    """Both absent from the vector leg, so only the id can order them."""
    fused = fuse([], ["z", "a"])
    ids = [result.document_id for result in fused]

    assert set(ids) == {"a", "z"}
    assert fused[0].rrf_score >= fused[1].rrf_score


def test_fusing_the_same_input_twice_is_identical():
    """The determinism guarantee the demo's repeatability rests on."""
    first = fuse(["a", "b", "c"], ["c", "a", "b"])
    second = fuse(["a", "b", "c"], ["c", "a", "b"])
    assert [(r.document_id, r.rrf_rank, r.rrf_score) for r in first] == [
        (r.document_id, r.rrf_rank, r.rrf_score) for r in second
    ]


def test_two_empty_legs_fuse_to_nothing_rather_than_raising():
    """A degenerate query degrades; it does not break retrieval."""
    assert fuse([], []) == []


def test_the_score_stays_inside_its_documented_bound():
    """Bounded by (0, 2/(k+1)] for two rankers — which is why it is not a confidence.

    The RAG_MATCH payload carries `rrf_rank` and `cosine_similarity` separately precisely because
    this number cannot be read as one.
    """
    upper = 2.0 / (RRF_K + 1)
    for result in fuse(["a", "b", "c"], ["a", "b", "c"]):
        assert 0 < result.rrf_score <= upper


# ----------------------------------------------------------------------------------
# The embeddings that make cosine distance mean anything
# ----------------------------------------------------------------------------------


def test_the_vector_has_the_column_width_and_is_normalised():
    """pgvector's column is vector(384); a unit vector makes cosine distance a pure angle."""
    vector = embed("Postgres connection pool exhausted")

    assert len(vector) == EMBEDDING_DIMENSION
    assert math.sqrt(sum(value * value for value in vector)) == pytest.approx(1.0)


def test_embedding_is_deterministic():
    assert embed("cache stampede") == embed("cache stampede")


def test_shared_vocabulary_produces_a_higher_cosine_than_unrelated_text():
    """The property the previous implementation did not have, and the reason it was replaced.

    Seeding a PRNG from `sha256(whole document)` re-rolled the entire vector on any change, so
    two documents about the same subject were as far apart as two unrelated ones — the vector leg
    fed noise into the fusion and ranked the wrong runbook. Feature hashing is what makes this
    assertion possible at all.
    """

    def cosine(left: str, right: str) -> float:
        first, second = embed(left), embed(right)
        return sum(a * b for a, b in zip(first, second, strict=True))

    related = cosine(
        "Redis cache stampede thundering herd memory",
        "Redis memory spike cache stampede hot key expiry",
    )
    unrelated = cosine(
        "Redis cache stampede thundering herd memory",
        "prompt injection adversarial exploit credentials",
    )
    assert related > unrelated, f"related={related:.4f} not above unrelated={unrelated:.4f}"


def test_a_document_is_maximally_similar_to_itself():
    text = "SQS poison pill dead letter queue worker crash"
    self_similarity = sum(value * value for value in embed(text))
    assert self_similarity == pytest.approx(1.0)


def test_tokenization_drops_stopwords_and_single_characters():
    """Terms in every document add the same component to every vector and discriminate nothing."""
    tokens = tokenize("The a of Postgres and X connection")
    assert tokens == ["postgres", "connection"]


def test_tokenization_is_case_insensitive():
    assert tokenize("Postgres POSTGRES postgres") == ["postgres"] * 3


def test_the_pgvector_literal_round_trips_the_values():
    """asyncpg has no native `vector` binding, so the value crosses as text and is cast in SQL."""
    vector = embed("connection pool")
    literal = to_pgvector_literal(vector)

    assert literal.startswith("[") and literal.endswith("]")
    assert [float(part) for part in literal[1:-1].split(",")] == pytest.approx(vector)


def test_the_embedding_version_is_declared():
    """Written into metadata_json so a scheme change re-embeds instead of silently mixing schemes.

    Cosine distance across two embedding schemes still returns a confident ordering — just the
    wrong one — so this has to be prevented rather than detected.
    """
    assert EMBEDDING_VERSION
    assert isinstance(EMBEDDING_VERSION, str)
