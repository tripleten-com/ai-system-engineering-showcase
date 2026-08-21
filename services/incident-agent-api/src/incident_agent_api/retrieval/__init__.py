"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/retrieval/__init__.py
Component:          Hybrid Retrieval Package
Purpose:            Re-exports the Project 2 retrieval surface: embeddings, fusion, and search.
Interacts With:     postgres-vector (:5432)

Curriculum Project:  Project 2 — Hybrid RAG & Retrieval Architecture
Skills:             Vector Search, Reciprocal Rank Fusion, Module Boundaries
Tools:              PostgreSQL 16, pgvector, Python 3.11
"""

from incident_agent_api.retrieval.embeddings import EMBEDDING_DIMENSION, embed, to_pgvector_literal
from incident_agent_api.retrieval.hybrid_search import SOURCE_LABEL, search
from incident_agent_api.retrieval.rank_fusion import RRF_K, FusedResult, fuse, reciprocal_rank_score

__all__ = [
    "EMBEDDING_DIMENSION",
    "RRF_K",
    "SOURCE_LABEL",
    "FusedResult",
    "embed",
    "fuse",
    "reciprocal_rank_score",
    "search",
    "to_pgvector_literal",
]
