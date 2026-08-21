"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/tests/unit/test_agent_graph_logic.py
Component:          Agent Graph & Planner Unit Tests
Purpose:            Asserts the graph's node order, its hard stop before dispatch, and that
                    every scenario's plan is guardrail-valid.
Interacts With:     None (in-memory graph, no containers)

Curriculum Project:  Project 5 — Autonomous Agent & Human-in-the-Loop
Skills:             LangGraph Orchestration, HITL Checkpoints, Deterministic Mock Execution
Tools:              Pytest, LangGraph, Python 3.11

Runs the real graph against an in-memory checkpointer. The Postgres checkpointer needs a
database and belongs in the integration tier; the *shape* of the run — what order nodes fire in,
what stops at the gate, what dispatches — does not, and this is the tier that can assert it for
all four scenarios in under a second.
"""

import pytest
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel

from incident_agent_api import scenarios
from incident_agent_api.agent import mock_llm
from incident_agent_api.agent.graph import AgentRuntime, build_graph, resume_command, thread_config
from tripleten_contracts import (
    APPROVAL_PROMPT,
    SCENARIO_TOOLS,
    AgentPhase,
    AgentThoughtPayload,
    GuardrailVerdict,
    LogStreamPayload,
    RagMatchPayload,
    RemediationJob,
    ScenarioId,
    WorkerLogPayload,
)


class Recorder:
    """Collects everything the graph publishes, standing in for the SSE bus."""

    def __init__(self) -> None:
        self.events: list[BaseModel] = []
        self.jobs: list[RemediationJob] = []

    def publish(self, payload: BaseModel, incident_id: str | None = None) -> None:
        self.events.append(payload)

    async def dispatch(self, job: RemediationJob) -> str:
        self.jobs.append(job)
        return "sqs-message-1"

    def of(self, model: type[BaseModel]) -> list:
        return [event for event in self.events if isinstance(event, model)]

    @property
    def thoughts(self) -> list[AgentThoughtPayload]:
        return self.of(AgentThoughtPayload)


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def compiled(recorder: Recorder):
    """The real graph with a fake retriever, an in-memory checkpointer, and no step delay."""

    async def fake_search(engine, query, **kwargs):
        # Returns the runbook the scenario is meant to match. The *real* retrieval is asserted
        # against pgvector in the integration tier; here it is stubbed so this tier stays
        # container-free and fast.
        scenario = next(s for s in ScenarioId if mock_llm.retrieval_query_for(s) == query)
        return [
            RagMatchPayload(
                runbook_id=scenario.runbook,
                title=f"Runbook for {scenario.value}",
                cosine_similarity=0.5,
                rrf_rank=1,
                excerpt="Terminate orphaned idle connections > 60 seconds...",
                source="pgvector (cosine) + FTS, fused via RRF",
            )
        ]

    import incident_agent_api.agent.graph as graph_module

    original = graph_module.search
    graph_module.search = fake_search
    runtime = AgentRuntime(
        publish=recorder.publish,
        engine=object(),  # type: ignore[arg-type]
        dispatch_job=recorder.dispatch,
        step_delay=0.0,
    )
    try:
        yield build_graph(runtime).compile(checkpointer=MemorySaver())
    finally:
        graph_module.search = original


def initial_state(scenario: ScenarioId) -> dict:
    return {
        "incident_id": f"inc-abcd1234-{scenario.value[:3]}",
        "thread_id": f"thread-{scenario.value[:6]}",
        "scenario_id": scenario.value,
        "step": 0,
    }


@pytest.mark.parametrize("scenario", list(ScenarioId), ids=lambda s: s.value)
async def test_the_graph_stops_at_the_gate_and_dispatches_nothing(compiled, recorder, scenario):
    """The invariant Project 5 exists for: the run pauses and no job is published."""
    state = initial_state(scenario)
    await compiled.ainvoke(state, thread_config(state["thread_id"]))

    snapshot = await compiled.aget_state(thread_config(state["thread_id"]))
    assert "await_approval" in tuple(snapshot.next)
    assert recorder.jobs == [], "a job was dispatched before approval"
    assert recorder.of(WorkerLogPayload) == [], "a worker log was emitted before approval"


@pytest.mark.parametrize("scenario", list(ScenarioId), ids=lambda s: s.value)
async def test_the_reasoning_chain_reaches_the_approval_phase(compiled, recorder, scenario):
    """Phases fire in order and the last thought before the gate names the approval prompt."""
    state = initial_state(scenario)
    await compiled.ainvoke(state, thread_config(state["thread_id"]))

    phases = [thought.phase for thought in recorder.thoughts]
    assert phases[0] is AgentPhase.ANALYZING
    assert AgentPhase.RETRIEVING in phases
    assert AgentPhase.PLANNING in phases
    assert phases[-1] is AgentPhase.AWAITING_APPROVAL

    # Strictly increasing, not merely sorted. `step` is what the War Room renders as the ordered
    # chain and what a React list key would use, so two entries sharing an index means one of
    # them is dropped. Scenario 4 emitted both of its blocked calls at the same step until this
    # assertion was tightened.
    steps = [thought.step for thought in recorder.thoughts]
    assert steps == sorted(set(steps)), f"reasoning steps are not strictly increasing: {steps}"
    assert steps[0] >= 1

    assert APPROVAL_PROMPT[scenario] in recorder.thoughts[-1].text


@pytest.mark.parametrize("scenario", list(ScenarioId), ids=lambda s: s.value)
async def test_logs_are_sanitized_before_they_are_published(compiled, recorder, scenario):
    """The masking is inbound: what reaches the bus is already masked, on every scenario."""
    state = initial_state(scenario)
    await compiled.ainvoke(state, thread_config(state["thread_id"]))

    published = " ".join(event.message for event in recorder.of(LogStreamPayload))
    for secret in scenarios.SCENARIO_SECRETS[scenario]:
        assert secret not in published, f"{secret!r} reached the event bus unmasked"


@pytest.mark.parametrize("scenario", list(ScenarioId), ids=lambda s: s.value)
async def test_approval_dispatches_exactly_the_planned_tools(compiled, recorder, scenario):
    """Resuming with approval publishes one job carrying the scenario's tools, in order."""
    state = initial_state(scenario)
    config = thread_config(state["thread_id"])
    await compiled.ainvoke(state, config)
    await compiled.ainvoke(resume_command(approved=True), config)

    assert len(recorder.jobs) == 1
    job = recorder.jobs[0]
    assert tuple(job.tools) == SCENARIO_TOOLS[scenario]
    assert job.runbook_id is scenario.runbook
    assert job.incident_id == state["incident_id"]
    assert job.idempotency_key == f"{job.incident_id}:{job.job_id}"


@pytest.mark.parametrize("scenario", list(ScenarioId), ids=lambda s: s.value)
async def test_rejection_dispatches_nothing(compiled, recorder, scenario):
    """The other half of the gate: a declined plan publishes no job at all."""
    state = initial_state(scenario)
    config = thread_config(state["thread_id"])
    await compiled.ainvoke(state, config)
    await compiled.ainvoke(resume_command(approved=False), config)

    assert recorder.jobs == []
    worker_logs = recorder.of(WorkerLogPayload)
    assert worker_logs and "rejected" in worker_logs[-1].message.lower()


async def test_the_gate_emits_no_duplicate_events_when_resumed(compiled, recorder):
    """LangGraph re-runs a node on resume, so the gate node must have no side effects.

    Guards the specific bug of emitting the AWAITING_APPROVAL thought inside `await_approval`:
    it would fire once on the way in and again on the way out, and the War Room would render
    the approval step twice.
    """
    state = initial_state(ScenarioId.DB_POOL_EXHAUSTION)
    config = thread_config(state["thread_id"])
    await compiled.ainvoke(state, config)
    before = len(recorder.thoughts)

    await compiled.ainvoke(resume_command(approved=True), config)
    after_approval_thoughts = [
        thought for thought in recorder.thoughts[before:] if thought.phase is AgentPhase.AWAITING_APPROVAL
    ]
    assert after_approval_thoughts == [], "the approval thought was re-emitted on resume"


async def test_scenario_four_blocks_both_injected_calls_with_the_call_intact(compiled, recorder):
    """The rejected calls must survive in the payload — they are what the UI strikes through."""
    state = initial_state(ScenarioId.PROMPT_INJECTION)
    await compiled.ainvoke(state, thread_config(state["thread_id"]))

    blocked = [t for t in recorder.thoughts if t.guardrail is GuardrailVerdict.BLOCKED]
    assert len(blocked) == 2
    names = {thought.tool_call.name for thought in blocked if thought.tool_call}
    assert names == {"flush_database_tables", "dump_aws_credentials"}
    for thought in blocked:
        assert thought.tool_call is not None
        assert thought.tool_call.is_canonical is False
        assert "INSPECTION_HALTED_MALICIOUS_PAYLOAD" in thought.text


@pytest.mark.parametrize("scenario", list(ScenarioId), ids=lambda s: s.value)
async def test_no_scenario_blocks_a_canonical_tool(compiled, recorder, scenario):
    """A PASSED verdict on every proposed canonical call; only injected names are BLOCKED."""
    state = initial_state(scenario)
    await compiled.ainvoke(state, thread_config(state["thread_id"]))

    for thought in recorder.thoughts:
        if thought.tool_call and thought.tool_call.is_canonical:
            assert thought.guardrail is GuardrailVerdict.PASSED


# ----------------------------------------------------------------------------------
# Planner
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", list(ScenarioId), ids=lambda s: s.value)
def test_every_scenario_plan_synthesizes_and_validates(scenario):
    """Guards the bug this suite was extended to catch.

    `archive_forensics` requires an incident id and has no default, so proposing it from a static
    argument table raised a ValidationError inside the planner — correctly, since the guardrail
    must refuse an unvalidatable plan. The result was that Scenario 4 never reached its gate at
    all. A container-free assertion that all four plans build is what would have caught it.
    """
    runbook = RagMatchPayload(
        runbook_id=scenario.runbook,
        title="T",
        cosine_similarity=0.5,
        rrf_rank=1,
        excerpt="e",
        source="s",
    )
    plan = mock_llm.synthesize_plan(scenario, runbook, "inc-abcd1234-x")

    assert plan.tools == SCENARIO_TOOLS[scenario]
    assert plan.approval_prompt == APPROVAL_PROMPT[scenario]
    assert scenario.runbook.value in plan.summary
    for call in plan.tool_calls:
        assert call.is_read_only is False, "a plan proposed a read-only diagnostic as remediation"


def test_the_forensics_tool_receives_the_run_it_is_archiving():
    """The run-scoped argument, asserted explicitly so the fix cannot regress silently."""
    runbook = RagMatchPayload(
        runbook_id=ScenarioId.PROMPT_INJECTION.runbook,
        title="T",
        cosine_similarity=0.5,
        rrf_rank=1,
        excerpt="e",
        source="s",
    )
    plan = mock_llm.synthesize_plan(ScenarioId.PROMPT_INJECTION, runbook, "inc-deadbeef-sec")
    forensics = next(call for call in plan.tool_calls if call.name.value == "archive_forensics")
    assert forensics.args["incident_id"] == "inc-deadbeef-sec"


async def test_a_bypassed_guardrail_fails_loudly_rather_than_misreporting(compiled, recorder, monkeypatch):
    """If an injected call ever validated, the run must break — not claim it was blocked.

    The original code fell through to the same BLOCKED emit whether validation raised or not, so
    a widened `ToolName` or a loosened schema would have had the screen assert that the firewall
    fired over a call it had in fact accepted.
    """
    import incident_agent_api.agent.graph as graph_module

    monkeypatch.setattr(graph_module, "validate_tool_call", lambda name, args=None: None)

    state = initial_state(ScenarioId.PROMPT_INJECTION)
    with pytest.raises(graph_module.GuardrailBypassedError):
        await compiled.ainvoke(state, thread_config(state["thread_id"]))

    blocked = [t for t in recorder.thoughts if t.guardrail is GuardrailVerdict.BLOCKED]
    assert blocked == [], "a validated call was reported as blocked"
