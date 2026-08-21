"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/tests/unit/test_orchestrator_races.py
Component:          Run Orchestrator — Concurrency Edges
Purpose:            Pins the behaviour of `/authorize` when the worker finishes before the
                    dispatch call returns.
Interacts With:     incident-agent-api (:8000)

Curriculum Project:  Project 5 — Autonomous Agent & Human-in-the-Loop
Skills:             Concurrency Reasoning, State Machine Invariants
Tools:              pytest, pytest-asyncio, Python 3.11

The window these tests describe is small and real. `dispatch` publishes to `remediation-jobs`
*inside* `ainvoke`, and the worker polls every two seconds — so it can consume the job, execute it,
and call back before `ainvoke` returns. Stage 5 already fixed one side of this by transitioning to
`EXECUTING` before resuming the graph. This is the other side: what happens when `ainvoke` raises
*after* the worker has already reported.

`FAILED` is reachable only from `EXECUTING`. The unguarded transition therefore raised
`IllegalTransitionError` from inside an `except` block — replacing the real exception with a
misleading one, and answering `/authorize` with a 500 for a run that had actually succeeded.

A fake graph rather than the real one: the race is in the orchestrator's error handling, and
reproducing it against a real LangGraph would mean orchestrating an actual worker to win a
millisecond-wide race on every run.
"""

import pytest

from incident_agent_api.agent.orchestrator import Orchestrator
from incident_agent_api.telemetry.engine import TelemetryEngine
from tripleten_contracts import IncidentState, ScenarioId

pytestmark = pytest.mark.asyncio


class FakeSnapshot:
    """Stands in for a LangGraph state snapshot."""

    def __init__(self, next_nodes: tuple[str, ...], values: dict | None = None) -> None:
        self.next = next_nodes
        self.values = values or {}


class FakeGraph:
    """A graph that is parked at the gate and does whatever the test tells it to on resume."""

    def __init__(self, on_resume=None, result: dict | None = None) -> None:
        self._on_resume = on_resume
        self._result = result or {"job_id": "job-00001"}
        self.resumed = 0

    async def aget_state(self, _config):
        return FakeSnapshot(("await_approval",))

    async def ainvoke(self, _payload, _config):
        self.resumed += 1
        if self._on_resume is not None:
            # Runs while the orchestrator is "inside" the dispatch, which is exactly where the
            # worker's callback lands in the real system.
            self._on_resume()
        return self._result


def _engine_at_the_gate(scenario_id: ScenarioId = ScenarioId.DB_POOL_EXHAUSTION):
    """Returns an engine holding a run parked at AWAITING_APPROVAL, and that run."""
    engine = TelemetryEngine()
    run = engine.machine.trigger(scenario_id, now=0.0)
    engine.machine.transition(IncidentState.AWAITING_APPROVAL)
    return engine, run


async def test_a_clean_dispatch_reports_executing():
    engine, run = _engine_at_the_gate()
    orchestrator = Orchestrator(engine, FakeGraph())  # type: ignore[arg-type]

    outcome = await orchestrator.authorize(run, approved=True)

    assert outcome.state is IncidentState.EXECUTING
    assert outcome.job_id == "job-00001"
    assert engine.machine.state is IncidentState.EXECUTING


async def test_a_dispatch_failure_marks_the_run_failed():
    engine, run = _engine_at_the_gate()

    def explode():
        raise RuntimeError("SQS unreachable")

    orchestrator = Orchestrator(engine, FakeGraph(on_resume=explode))  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="SQS unreachable"):
        await orchestrator.authorize(run, approved=True)

    assert engine.machine.state is IncidentState.FAILED
    assert run.error is not None and "SQS unreachable" in run.error


async def test_a_dispatch_failure_after_the_worker_succeeded_leaves_the_run_recovering():
    """The race. The worker's report is the more authoritative account, and it already landed."""
    engine, run = _engine_at_the_gate()

    def worker_finishes_then_dispatch_fails():
        # Exactly what the callback route does on a successful report for an outage scenario.
        engine.begin_recovery()
        raise RuntimeError("checkpoint write failed after publish")

    orchestrator = Orchestrator(engine, FakeGraph(on_resume=worker_finishes_then_dispatch_fails))  # type: ignore[arg-type]

    # The original exception surfaces, not an IllegalTransitionError masking it.
    with pytest.raises(RuntimeError, match="checkpoint write failed after publish"):
        await orchestrator.authorize(run, approved=True)

    assert engine.machine.state is IncidentState.RECOVERING
    assert run.error is None, "a run the worker completed must not be annotated with a dispatch error"


async def test_a_dispatch_failure_after_containment_leaves_the_run_contained():
    """Same race on the Scenario 4 path, where the terminal state is SECURITY_CONTAINED."""
    engine, run = _engine_at_the_gate(ScenarioId.PROMPT_INJECTION)

    def containment_completes_then_dispatch_fails():
        engine.machine.transition(IncidentState.SECURITY_CONTAINED)
        raise RuntimeError("checkpoint write failed after publish")

    orchestrator = Orchestrator(engine, FakeGraph(on_resume=containment_completes_then_dispatch_fails))  # type: ignore[arg-type]

    # `match` is not decoration. `IllegalTransitionError` subclasses `RuntimeError`, so a bare
    # `pytest.raises(RuntimeError)` here passed with or without the guard — it caught the masking
    # exception instead of the real one and looked like a green test.
    with pytest.raises(RuntimeError, match="checkpoint write failed after publish"):
        await orchestrator.authorize(run, approved=True)

    assert engine.machine.state is IncidentState.SECURITY_CONTAINED
    assert run.error is None


async def test_a_dispatch_failure_after_a_reset_touches_nothing():
    """A Master Reset during the dispatch clears the run; the error must not resurrect it."""
    engine, run = _engine_at_the_gate()

    def reset_then_dispatch_fails():
        engine.reset(run.incident_id)
        raise RuntimeError("checkpoint write failed after publish")

    orchestrator = Orchestrator(engine, FakeGraph(on_resume=reset_then_dispatch_fails))  # type: ignore[arg-type]

    # Matched for the same reason as above: without it this passed on the masking
    # "no active run to transition into FAILED" and proved nothing.
    with pytest.raises(RuntimeError, match="checkpoint write failed after publish"):
        await orchestrator.authorize(run, approved=True)

    assert engine.machine.run is None
    assert engine.machine.state is IncidentState.HEALTHY


async def test_rejection_dispatches_nothing_and_reports_rejected():
    engine, run = _engine_at_the_gate()
    graph = FakeGraph()
    orchestrator = Orchestrator(engine, graph)  # type: ignore[arg-type]

    outcome = await orchestrator.authorize(run, approved=False)

    assert outcome.state is IncidentState.REJECTED
    assert outcome.job_id is None, "nothing was dispatched, so there is no job to name"
    assert engine.machine.state is IncidentState.REJECTED
