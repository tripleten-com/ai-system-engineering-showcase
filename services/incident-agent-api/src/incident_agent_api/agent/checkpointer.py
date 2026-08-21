"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/agent/checkpointer.py
Component:          LangGraph Postgres Checkpointer Lifecycle
Purpose:            Owns the AsyncPostgresSaver and its connection pool, so a paused graph
                    survives the approval gap in the database rather than in this process.
Interacts With:     postgres-vector (:5432)

Curriculum Project:  Project 5 — Autonomous Agent & Human-in-the-Loop
Skills:             LangGraph Orchestration, State Persistence, HITL Checkpoints
Tools:              LangGraph, langgraph-checkpoint-postgres, psycopg 3, Python 3.11

**Postgres, not Redis, and the distinction is architectural.** `AWAITING_APPROVAL` has no
timeout — an SRE can take a minute or an afternoon — so the paused graph is durable state, not
a cache. Redis in this stack holds idempotency locks, rate limits, and transient values, all of
which may be evicted without breaking a run. A checkpoint may not.

`setup()` owns the schema. The three checkpointer tables used to be hand-written DDL in
`infra/postgres/init-vector.sql` and `seed/ingest.py`, which approximated LangGraph's layout
closely enough to make `/healthz` pass and would have diverged silently on the first library
upgrade — including the `checkpoint_migrations` table that version-tracks the rest. Letting the
library create its own tables is the only version-correct answer.

This service runs one uvicorn worker (the telemetry engine's in-memory state requires it), so
one pool here is the whole process's checkpoint access.
"""

import logging
from urllib.parse import urlsplit, urlunsplit

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger("incident-agent-api")

# Small on purpose. Checkpoint writes are short and bursty — a handful per run — and this pool
# is separate from the SQLAlchemy engine's, so oversizing it would just hold idle backends
# against the `max_connections` ceiling Scenario 1 is about.
POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 4

_pool: AsyncConnectionPool | None = None
_saver: AsyncPostgresSaver | None = None


def to_psycopg_dsn(database_url: str) -> str:
    """Converts the app's SQLAlchemy URL into the plain DSN psycopg expects.

    `postgresql+asyncpg://...` is a SQLAlchemy dialect URL and psycopg cannot parse the `+driver`
    suffix. Rewritten here rather than adding a second environment variable, so the two clients
    cannot end up pointed at different databases.
    """
    parts = urlsplit(database_url)
    scheme = parts.scheme.split("+", 1)[0]
    return urlunsplit((scheme, parts.netloc, parts.path, parts.query, parts.fragment))


async def setup_checkpointer(database_url: str) -> AsyncPostgresSaver:
    """Opens the pool, creates the checkpointer tables if absent, and returns the saver.

    Idempotent: a second call returns the existing saver rather than opening a second pool.
    `setup()` itself is idempotent in LangGraph, so a restart against an existing schema is a
    no-op rather than an error.
    """
    global _pool, _saver
    if _saver is not None:
        return _saver

    dsn = to_psycopg_dsn(database_url)
    pool = AsyncConnectionPool(
        conninfo=dsn,
        min_size=POOL_MIN_SIZE,
        max_size=POOL_MAX_SIZE,
        # Both are required by AsyncPostgresSaver: it issues DDL and multi-statement writes that
        # must not sit inside an outer transaction, and it reads rows by column name.
        kwargs={"autocommit": True, "row_factory": dict_row},
        open=False,
    )
    await pool.open(wait=True)

    saver = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
    await saver.setup()

    _pool, _saver = pool, saver
    logger.info("LangGraph Postgres checkpointer ready (pool %d-%d)", POOL_MIN_SIZE, POOL_MAX_SIZE)
    return saver


def get_checkpointer() -> AsyncPostgresSaver | None:
    """Returns the saver, or None before setup has completed.

    Nullable for the same reason `get_engine` is: `/healthz` must be able to report
    `checkpointer_ready: false` and a 503 during startup rather than raising a 500.
    """
    return _saver


async def close_checkpointer() -> None:
    """Closes the pool on shutdown."""
    global _pool, _saver
    _saver = None
    if _pool is not None:
        await _pool.close()
        _pool = None
