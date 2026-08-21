"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/retrieval/embeddings.py
Component:          Deterministic Embedding Provider
Purpose:            Produces the 384-dimensional unit vectors used for both ingestion and
                    query, offline, reproducibly, and with real lexical signal.
Interacts With:     postgres-vector (:5432)

Curriculum Project:  Project 2 — Hybrid RAG & Retrieval Architecture
Skills:             Vector Similarity, Feature Hashing, Offline-First Design
Tools:              pgvector, Python 3.11

**One function, two callers, and that is why this module exists.** The seed pipeline embeds
documents and the retriever embeds queries. If those ever used different functions the cosine
distances would be noise while still returning a plausible-looking ordering. This was previously
a private helper inside `seed/ingest.py` with no way for the query side to reach it.

**Signed feature hashing, not a hash of the whole string.** The first implementation seeded a PRNG
from `sha256(entire_document)` and drew a random vector. Those are real vectors with real cosine
distances — but two documents about the same subject land as far apart as two unrelated ones,
because a single byte of difference in the input re-rolls the whole vector. The vector leg was
therefore contributing *noise* to the fusion, and RRF was diluting the FTS leg, which was the only
leg doing real work. It ranked the wrong runbook first for the cache-stampede query, and would
have flipped others as the corpus grew.

Feature hashing fixes it properly and stays entirely offline: each token is hashed into one of the
384 buckets with a sign, occurrences accumulate, and the result is L2-normalised. Documents that
share vocabulary now have genuinely high cosine similarity, so pgvector, the HNSW index, and the
RRF fusion all perform real retrieval — which is what seed-runbooks.md §3 claims.

The vectors still carry no *learned* semantics: "Postgres" and "database" remain orthogonal, which
is why `hybrid_search` pairs this with a full-text leg and why the tests assert rank rather than an
absolute similarity floor. The 0.94/0.97 figures in the scenario docs are narrative.
"""

import hashlib
import re

EMBEDDING_DIMENSION = 384

# Bumped whenever the function below changes shape. Stored alongside each row so the seed can tell
# a stale vector from a current one and re-embed — without this, an embedding change leaves the
# corpus holding vectors from the previous scheme while queries use the new one, and retrieval
# degrades silently rather than failing.
EMBEDDING_VERSION = "feature-hash-v2"

_TOKEN = re.compile(r"[A-Za-z0-9_]+")

# Terms carrying no discriminating signal. They appear in every runbook, so leaving them in adds
# the same component to every vector and pushes all four toward each other.
_STOPWORDS = frozenset(
    {
        "the", "a", "an", "and", "or", "of", "to", "in", "is", "for", "on", "with", "by", "at",
        "from", "as", "be", "are", "was", "it", "this", "that", "if", "not", "all", "any", "no",
    }
)


def tokenize(text_content: str) -> list[str]:
    """Splits text into lowercase alphanumeric tokens, dropping stopwords and single characters."""
    return [
        token.lower()
        for token in _TOKEN.findall(text_content)
        if len(token) > 1 and token.lower() not in _STOPWORDS
    ]


def embed(text_content: str, dimension: int = EMBEDDING_DIMENSION) -> list[float]:
    """Returns a reproducible unit vector whose direction reflects the text's vocabulary.

    Signed feature hashing: each token picks a bucket from the first four bytes of its SHA-256
    and a sign from the fifth. The sign is what keeps collisions from systematically inflating
    similarity — two unrelated tokens landing in the same bucket cancel about half the time
    instead of always adding.
    """
    vector = [0.0] * dimension
    for token in tokenize(text_content):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], byteorder="big") % dimension
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[bucket] += sign

    norm = sum(value * value for value in vector) ** 0.5
    if norm > 0:
        vector = [value / norm for value in vector]
    return vector


def to_pgvector_literal(vector: list[float]) -> str:
    """Renders a vector as the bracketed text form pgvector casts from.

    asyncpg has no native binding for the `vector` type, so the value crosses as text and is cast
    in SQL. Formatted here rather than by `json.dumps` at each call site so ingestion and query
    produce byte-identical literals for the same vector.
    """
    return "[" + ",".join(repr(value) for value in vector) + "]"
