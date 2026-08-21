"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/infra/db.py
Component:          PostgreSQL Connection Lifecycle
Purpose:            Owns the async SQLAlchemy engine created at startup and disposed at shutdown.
Interacts With:     postgres-vector (:5432)

Curriculum Project:  Project 2 — Hybrid RAG & Retrieval Architecture
Skills:             Connection Pooling, Async SQLAlchemy, Resource Lifecycle
Tools:              PostgreSQL 16, pgvector, SQLAlchemy, Python 3.11
"""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

_engine: AsyncEngine | None = None


def init_engine(database_url: str) -> AsyncEngine:
    """Creates the process-wide engine. Called once from the application lifespan."""
    global _engine
    _engine = create_async_engine(database_url, pool_pre_ping=True, pool_size=5, max_overflow=10)
    return _engine


def get_engine() -> AsyncEngine | None:
    """Returns the engine, or None before startup.

    Deliberately nullable rather than raising: /healthz must be able to report
    "postgres: unavailable" during startup instead of returning a 500.
    """
    return _engine


async def dispose_engine() -> None:
    """Disposes the engine on shutdown."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
