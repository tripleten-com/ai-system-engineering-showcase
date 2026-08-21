"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/seed/__init__.py
Component:          Seed Package Public Surface
Purpose:            Re-exports the ingestion entrypoint consumed by the application lifespan.
Interacts With:     postgres-vector (:5432)

Curriculum Project:  Project 2 — Hybrid RAG & Retrieval Architecture
Skills:             API Surface Design
Tools:              PostgreSQL 16, pgvector, SQLAlchemy, Python 3.11
"""

# The embedding function moved to retrieval/embeddings.py, where the query side can reach it
# too. Re-exported from there rather than duplicated, so `from ...seed import embed` still
# resolves to the one function both ingestion and retrieval use.
from incident_agent_api.retrieval.embeddings import embed
from incident_agent_api.seed.ingest import seed_knowledge_base
from incident_agent_api.seed.runbooks import SEED_RUNBOOKS

__all__ = ["SEED_RUNBOOKS", "embed", "seed_knowledge_base"]
