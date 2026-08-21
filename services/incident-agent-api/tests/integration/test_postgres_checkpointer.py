"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/tests/integration/test_postgres_checkpointer.py
Component:          LangGraph Postgres Checkpointer Integration Tests
Purpose:            Asserts the paused graph lives in Postgres and resumes from it, so the
                    approval gap survives an arbitrary delay.
Interacts With:     postgres-vector (:5432), incident-agent-api (:8000)

Curriculum Project:  Project 5 — Autonomous Agent & Human-in-the-Loop
Skills:             LangGraph Orchestration, State Persistence, HITL Checkpoints
Tools:              Pytest, SQLAlchemy, LangGraph, Python 3.11

Driven through HTTP rather than by opening a checkpointer in-process, and that is not incidental:
psycopg 3's async mode refuses Python's default `ProactorEventLoop` on Windows, which is the
primary development machine here. Going through the API exercises the checkpointer inside the
container where it belongs, and inspects the result with SQLAlchemy — which works on every
platform. It is also the more honest test: it asserts the checkpoint the *running service* wrote.
"""

import asyncio
import os
import time

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tripleten_contracts import IncidentState, ScenarioId

pytestmark = pytest.mark.integration

API = "http://localhost:8000"
DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/tripleten_db"
)

GATE_TIMEOUT_SECONDS = 25.0

# The tables AsyncPostgresSaver.setup() owns. `checkpoint_migrations` is the one the hand-written
# DDL this replaced never had — it version-tracks the others, so its absence was how that schema
# would have diverged silently on the first dependency bump.
CHECKPOINTER_TABLES = {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}


@pytest.fixture
async def engine():
    db = create_async_engine(DATABASE_URL)
    try:
        yield db
    finally:
        await db.dispose()


def snapshot() -> dict:
    with httpx.Client(timeout=10.0) as client:
        return client.get(f"{API}/api/telemetry/current").json()


def reset() -> None:
    incident_id = snapshot().get("incident_id")
    if incident_id:
        with httpx.Client(timeout=10.0) as client:
            client.post(f"{API}/api/incidents/reset", json={"incident_id": incident_id})


def wait_for_state(target: IncidentState, timeout: float = GATE_TIMEOUT_SECONDS) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        last = snapshot()
        if last.get("state") == target.value:
            return last
        time.sleep(0.5)
    raise AssertionError(f"never reached {target.value}; last was {last.get('state')!r}")


@pytest.fixture(autouse=True)
def clean_slate():
    reset()
    yield
    reset()


@pytest.fixture
def paused_run():
    with httpx.Client(timeout=10.0) as client:
        response = client.post(
            f"{API}/api/incidents/trigger",
            json={"scenario_id": ScenarioId.DB_POOL_EXHAUSTION.value},
        )
    run = response.json()
    wait_for_state(IncidentState.AWAITING_APPROVAL)
    return run


async def test_the_library_owns_its_own_schema(engine):
    """All four tables, created by `setup()` rather than by hand-written DDL."""
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = current_schema() AND table_name LIKE 'checkpoint%'
                """
            )
        )
        present = {row.table_name for row in rows}

    assert CHECKPOINTER_TABLES <= present, f"missing {CHECKPOINTER_TABLES - present}"


async def test_the_paused_run_has_a_checkpoint_in_postgres(engine, paused_run):
    """The gate's durability, asserted in the database rather than in process memory."""
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT count(*) FROM checkpoints WHERE thread_id = :thread"),
            {"thread": paused_run["thread_id"]},
        )
        checkpoints = rows.scalar() or 0

    assert checkpoints > 0, "the paused graph wrote no checkpoint"


async def test_the_checkpoint_survives_a_delayed_decision(engine, paused_run):
    """`AWAITING_APPROVAL` has no timeout, so the checkpoint must not have one either."""
    async with engine.connect() as conn:
        before = (
            await conn.execute(
                text("SELECT count(*) FROM checkpoints WHERE thread_id = :thread"),
                {"thread": paused_run["thread_id"]},
            )
        ).scalar()

    # asyncio.sleep, not time.sleep: this coroutine shares an event loop with the engine's
    # connection pool, and blocking it for ten seconds would stall the pool's own upkeep.
    await asyncio.sleep(10)

    assert snapshot()["state"] == IncidentState.AWAITING_APPROVAL.value
    async with engine.connect() as conn:
        after = (
            await conn.execute(
                text("SELECT count(*) FROM checkpoints WHERE thread_id = :thread"),
                {"thread": paused_run["thread_id"]},
            )
        ).scalar()
    assert after == before, "the checkpoint changed while nobody was deciding"


def test_the_graph_resumes_from_its_checkpoint_into_executing(paused_run):
    """Steps 6 and 7 of the spec: authorize resumes the saved thread, not a fresh run."""
    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            f"{API}/api/incidents/authorize",
            json={
                "incident_id": paused_run["incident_id"],
                "thread_id": paused_run["thread_id"],
                "scenario_id": paused_run["scenario_id"],
                "approved": True,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == IncidentState.EXECUTING.value
    assert body["job_id"], "resuming produced no dispatched job"


def test_resuming_a_thread_that_never_paused_is_refused():
    """A resume against an unpaused thread could dispatch a second job."""
    with httpx.Client(timeout=10.0) as client:
        response = client.post(
            f"{API}/api/incidents/authorize",
            json={
                "incident_id": "inc-00000000-db",
                "thread_id": "thread-000000",
                "scenario_id": ScenarioId.DB_POOL_EXHAUSTION.value,
                "approved": True,
            },
        )
    assert response.status_code == 409


def test_a_second_authorize_for_the_same_run_is_idempotent(paused_run):
    """Held in Redis by `(incident_id, thread_id)`: a double click must not enqueue twice."""
    payload = {
        "incident_id": paused_run["incident_id"],
        "thread_id": paused_run["thread_id"],
        "scenario_id": paused_run["scenario_id"],
        "approved": True,
    }
    with httpx.Client(timeout=20.0) as client:
        first = client.post(f"{API}/api/incidents/authorize", json=payload)
        second = client.post(f"{API}/api/incidents/authorize", json=payload)

    assert first.status_code == 200
    assert first.json()["duplicate"] is False
    assert second.status_code == 200
    assert second.json()["duplicate"] is True, "a replayed authorize was executed again"


async def test_each_run_gets_its_own_thread(engine):
    """Two runs must not share a checkpoint, or the second would resume the first's state.

    Asserted while each run is still live. `/reset` now prunes the thread it clears — without
    that, every trigger left rows behind forever — so checking after the reset would assert the
    opposite of the intended behaviour. The property under test is that the ids differ and each
    has its own checkpoint, not that either outlives its run.
    """
    threads: list[str] = []
    for _ in range(2):
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{API}/api/incidents/trigger",
                json={"scenario_id": ScenarioId.CACHE_THUNDERING_HERD.value},
            )
        run = response.json()
        wait_for_state(IncidentState.AWAITING_APPROVAL)
        threads.append(run["thread_id"])

        async with engine.connect() as conn:
            live = (
                await conn.execute(
                    text("SELECT count(*) FROM checkpoints WHERE thread_id = :thread"),
                    {"thread": run["thread_id"]},
                )
            ).scalar()
        assert live and live > 0, f"{run['thread_id']} wrote no checkpoint while paused"

        reset()

    assert threads[0] != threads[1], "two runs shared a thread id"


async def test_resetting_prunes_the_thread_it_cleared(engine, paused_run):
    """Checkpoint rows are bounded by the runs in flight, not by the deployment's lifetime."""
    thread_id = paused_run["thread_id"]

    async with engine.connect() as conn:
        before = (
            await conn.execute(
                text("SELECT count(*) FROM checkpoints WHERE thread_id = :thread"),
                {"thread": thread_id},
            )
        ).scalar()
    assert before and before > 0

    reset()
    # asyncio.sleep: this coroutine shares an event loop with the engine's pool, and the prune
    # is a best-effort call inside the API, so it needs a moment to land.
    await asyncio.sleep(1.0)

    async with engine.connect() as conn:
        after = (
            await conn.execute(
                text("SELECT count(*) FROM checkpoints WHERE thread_id = :thread"),
                {"thread": thread_id},
            )
        ).scalar()
    assert after == 0, f"{after} checkpoint row(s) survived the reset that cleared their run"
