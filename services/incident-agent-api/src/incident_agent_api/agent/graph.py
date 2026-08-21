"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/agent/graph.py
Component:          LangGraph Incident Response State Graph
Purpose:            Drives one incident from telemetry ingest to a hard stop at human approval,
                    then to dispatch — with the pause persisted in Postgres.
Interacts With:     postgres-vector (:5432), localstack (:4566), incident-war-room (:3000)

Curriculum Project:  Project 5 — Autonomous Agent & Human-in-the-Loop
Skills:             LangGraph Orchestration, HITL Checkpoints, State Persistence
Tools:              LangGraph, langgraph-checkpoint-postgres, Pydantic 2, Python 3.11

    analyze → sanitize → screen_injection → retrieve → plan → [ INTERRUPT ] → dispatch

**The interrupt is the whole point.** `await_approval` contains exactly one statement — the
`interrupt()` call — and nothing else. That is deliberate: LangGraph re-executes a node from the
top when it resumes, so any side effect placed before the interrupt would fire twice, once on
the way in and once on the way out. Keeping the node empty makes the gate free of duplicate
events by construction rather than by a de-duplication check somewhere downstream.

**Nothing state-changing happens before `dispatch`.** The nodes ahead of the gate read
telemetry, mask logs, query pgvector, and draft a plan. The `dispatch` node is the only one that
publishes anything, and it sits after the interrupt, so an unresumed graph cannot reach it. The
seven remediation tools have no implementation in this process at all (see `tools.py`), so even
`dispatch` only ever *describes* work — `remediation-worker` performs it.

State is plain JSON. It is serialized into Postgres on every step, so a Pydantic object or a
dataclass in here would either fail to round-trip or silently degrade to a dict on resume.
Models are dumped with `mode="json"` at the boundary and validated back when read.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

from incident_agent_api import scenarios
from incident_agent_api.agent import mock_llm
from incident_agent_api.agent.guardrails import validate_tool_call
from incident_agent_api.retrieval import search
from incident_agent_api.security import sanitize
from tripleten_contracts import (
    AgentPhase,
    AgentThoughtPayload,
    GuardrailVerdict,
    LogStreamPayload,
    RagMatchPayload,
    RemediationJob,
    ScenarioId,
    ToolCall,
    WorkerLogLevel,
    WorkerLogPayload,
    WorkerLogSource,
)

logger = logging.getLogger("incident-agent-api")

# Pause between reasoning steps, so the chain *streams* into the War Room instead of arriving as
# one block. The UI staggers rendering by 120ms (spa-design-guidelines.md §5); frames have to
# arrive staggered for that to have anything to work with. Tests set this to 0.
STEP_DELAY_SECONDS = 0.35


class GuardrailBypassedError(RuntimeError):
    """Raised when an injected tool call passes the firewall that exists to refuse it.

    Unreachable while `ToolName` stays closed and the argument schemas stay strict, and it exists
    so that a change which opened either one fails loudly here instead of quietly rendering
    "rejected by the schema firewall" over a call that was accepted.
    """

    def __init__(self, tool_name: str) -> None:
        super().__init__(
            f"{tool_name!r} passed the guardrail; an injected call must never validate"
        )
        self.tool_name = tool_name


class AgentState(TypedDict, total=False):
    """The checkpointed graph state. JSON-serializable values only — this round-trips Postgres."""

    incident_id: str
    thread_id: str
    scenario_id: str
    step: int
    sanitized_logs: list[dict[str, Any]]
    redacted_count: int
    runbook: dict[str, Any] | None
    blocked_calls: list[dict[str, Any]]
    plan: dict[str, Any] | None
    approved: bool | None
    job_id: str | None


# Publishes one SSE payload for a run. Matches EventBus.publish, narrowed to what nodes need.
Publisher = Callable[[BaseModel, str | None], Any]

# Publishes a validated remediation job to `remediation-jobs`, returning the SQS message id.
JobDispatcher = Callable[[RemediationJob], Awaitable[str]]


@dataclass
class AgentRuntime:
    """The collaborators the graph nodes need, injected rather than imported.

    Held outside `AgentState` because none of it is serializable and none of it belongs in a
    checkpoint — a resumed graph must rebind live clients, not restore stale ones.
    """

    publish: Publisher
    engine: AsyncEngine | None
    dispatch_job: JobDispatcher
    step_delay: float = STEP_DELAY_SECONDS


def _next_step(state: AgentState) -> int:
    """Returns the 1-based index of the next reasoning step."""
    return int(state.get("step", 0)) + 1


def build_graph(runtime: AgentRuntime) -> StateGraph:
    """Assembles the incident-response graph over an injected runtime."""

    async def _emit(payload: BaseModel, state: AgentState) -> None:
        runtime.publish(payload, state.get("incident_id"))
        if runtime.step_delay:
            await asyncio.sleep(runtime.step_delay)

    async def _think(
        state: AgentState,
        phase: AgentPhase,
        text: str,
        step: int,
        tool_call: ToolCall | None = None,
        guardrail: GuardrailVerdict = GuardrailVerdict.PASSED,
    ) -> None:
        await _emit(
            AgentThoughtPayload(step=step, phase=phase, text=text, tool_call=tool_call, guardrail=guardrail),
            state,
        )

    async def analyze(state: AgentState) -> dict[str, Any]:
        """Reports the diagnosis drawn from the telemetry spike (or its pointed absence)."""
        scenario = ScenarioId(state["scenario_id"])
        step = _next_step(state)
        await _think(state, AgentPhase.ANALYZING, mock_llm.diagnosis_for(scenario), step)
        return {"step": step}

    async def sanitize_logs(state: AgentState) -> dict[str, Any]:
        """Masks every raw log line before anything else in the graph can read it.

        Inbound, not cosmetic: the sanitized text is what lands in state, so it is what the
        planner, the checkpoint, and every later node see. There is no code path in which a raw
        secret reaches the model seam or the database.
        """
        scenario = ScenarioId(state["scenario_id"])
        step = _next_step(state)
        results = [sanitize(line) for line in scenarios.RAW_LOGS[scenario]]

        for result in results:
            await _emit(LogStreamPayload(message=result.message, sanitized=result.sanitized), state)

        masked = sum(result.redacted_token_count for result in results)
        await _think(
            state,
            AgentPhase.ANALYZING,
            f"Inbound sanitization complete: {masked} sensitive token(s) masked before model ingest.",
            step,
        )
        return {
            "step": step,
            "sanitized_logs": [{"message": r.message, "sanitized": r.sanitized} for r in results],
            "redacted_count": masked,
        }

    async def screen_injection(state: AgentState) -> dict[str, Any]:
        """Runs the schema firewall over any tool calls the inbound payload tried to force.

        Only Scenario 4 carries any, and its rejection happens here — with no human involved,
        because refusing to act is not an action. That is why blocking does not touch the HITL
        invariant, and why the run is already in `EXPLOIT_INTERCEPTED` by the time this node
        reports it.
        """
        scenario = ScenarioId(state["scenario_id"])
        if scenario is not ScenarioId.PROMPT_INJECTION:
            return {}

        step = _next_step(state)
        blocked: list[dict[str, Any]] = []
        for name, args in scenarios.INJECTED_TOOL_CALLS:
            try:
                validate_tool_call(name, dict(args))
            except Exception as err:  # noqa: BLE001 - a verdict, not control flow
                reason = err.__class__.__name__
            else:
                # An injected call that *validated* must never be reported as blocked. Falling
                # through to the BLOCKED emit below would have the screen claim the firewall
                # fired while the call had in fact been accepted — the single worst thing this
                # panel can misreport, and a guardrail regression rather than a runtime input.
                raise GuardrailBypassedError(name)

            # One step per rejected call, matching how synthesize_plan numbers its own. Sharing
            # a step number across both would give the War Room two distinct reasoning entries
            # with the same index, and any UI keyed on `step` would render only one of the two
            # rejections Scenario 4 exists to show.
            await _think(
                state,
                AgentPhase.TOOL_SELECTION,
                f"INSPECTION_HALTED_MALICIOUS_PAYLOAD — {name} rejected by the schema firewall "
                f"({reason}). Zero unauthorized actions executed.",
                step,
                tool_call=ToolCall(name=name, args=dict(args)),
                guardrail=GuardrailVerdict.BLOCKED,
            )
            blocked.append({"name": name, "args": dict(args), "reason": reason})
            step += 1

        # `step` is one past the last emitted index after the loop, so hand back the last one used.
        return {"step": step - 1, "blocked_calls": blocked}

    async def retrieve_runbook(state: AgentState) -> dict[str, Any]:
        """Runs hybrid pgvector + FTS retrieval and reports the top-ranked runbook."""
        scenario = ScenarioId(state["scenario_id"])
        step = _next_step(state)
        query = mock_llm.retrieval_query_for(scenario)

        if runtime.engine is None:  # pragma: no cover - the engine exists whenever lifespan ran
            logger.warning("Retrieval skipped for %s: no database engine", state.get("incident_id"))
            return {"step": step, "runbook": None}

        matches = await search(runtime.engine, query)
        if not matches:
            logger.warning("Retrieval returned nothing for %r", query)
            return {"step": step, "runbook": None}

        top = matches[0]
        await _emit(top, state)
        await _think(
            state,
            AgentPhase.RETRIEVING,
            f"Hybrid retrieval matched {top.runbook_id.value} at RRF rank {top.rrf_rank} "
            f"(cosine {top.cosine_similarity:.4f}) via {top.source}.",
            step,
        )
        return {"step": step, "runbook": top.model_dump(mode="json")}

    async def synthesize_plan(state: AgentState) -> dict[str, Any]:
        """Drafts the remediation plan and announces the pause. Proposes; never executes."""
        scenario = ScenarioId(state["scenario_id"])
        raw_runbook = state.get("runbook")
        if raw_runbook is None:  # pragma: no cover - retrieval failure is logged upstream
            raise RuntimeError(f"cannot plan for {scenario.value} without a retrieved runbook")

        runbook = RagMatchPayload.model_validate(raw_runbook)
        plan = mock_llm.synthesize_plan(scenario, runbook, state["incident_id"])

        step = _next_step(state)
        await _think(state, AgentPhase.PLANNING, plan.summary, step)

        for call in plan.tool_calls:
            step += 1
            await _think(
                state,
                AgentPhase.TOOL_SELECTION,
                f"Selected {call.name.value} from {runbook.runbook_id.value}; guardrail validated "
                f"its arguments against the canonical schema.",
                step,
                tool_call=ToolCall(name=call.name.value, args=call.args),
                guardrail=GuardrailVerdict.PASSED,
            )

        step += 1
        await _think(
            state,
            AgentPhase.AWAITING_APPROVAL,
            f"Execution halted pending human authorization: [ {plan.approval_prompt} ]. "
            f"No remediation tool has run and none will until an SRE authorizes it.",
            step,
        )

        return {
            "step": step,
            "plan": {
                "summary": plan.summary,
                "approval_prompt": plan.approval_prompt,
                "tools": [call.name.value for call in plan.tool_calls],
                "tool_args": {call.name.value: call.args for call in plan.tool_calls},
                "runbook_id": runbook.runbook_id.value,
            },
        }

    async def await_approval(state: AgentState) -> dict[str, Any]:
        """The hard stop. One statement, no side effects — see the module docstring.

        Everything before this node has run and been checkpointed; nothing after it can run
        until `POST /api/incidents/authorize` resumes the graph with a decision. There is no
        timeout branch, and adding one would defeat Project 5 entirely.
        """
        decision = interrupt({"approval_prompt": (state.get("plan") or {}).get("approval_prompt")})
        return {"approved": bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)}

    async def dispatch(state: AgentState) -> dict[str, Any]:
        """Publishes the approved job to `remediation-jobs`, or records the rejection.

        The first node in the graph that changes anything outside this process, and it is
        unreachable without a resume carrying an explicit decision.
        """
        scenario = ScenarioId(state["scenario_id"])
        plan = state.get("plan") or {}

        if not state.get("approved"):
            await _emit(
                WorkerLogPayload(
                    source=WorkerLogSource.WORKER,
                    level=WorkerLogLevel.WARN,
                    message="Remediation rejected by SRE. Nothing dispatched; chaos values hold until reset.",
                ),
                state,
            )
            return {"job_id": None}

        incident_id = state["incident_id"]
        job_id = f"job-{abs(hash(incident_id)) % 100000:05d}"
        job = RemediationJob(
            incident_id=incident_id,
            thread_id=state["thread_id"],
            scenario_id=scenario,
            job_id=job_id,
            idempotency_key=f"{incident_id}:{job_id}",
            runbook_id=scenario.runbook,
            tools=list(plan.get("tools", [])),
        )
        message_id = await runtime.dispatch_job(job)

        await _emit(
            WorkerLogPayload(
                source=WorkerLogSource.LOCALSTACK_SQS,
                level=WorkerLogLevel.INFO,
                message=(
                    f"Message dispatched to remediation-jobs (job_id: {job_id}, "
                    f"tools: {', '.join(job.tools)}, sqs_message_id: {message_id})"
                ),
            ),
            state,
        )
        return {"job_id": job_id}

    graph = StateGraph(AgentState)
    graph.add_node("analyze", analyze)
    graph.add_node("sanitize_logs", sanitize_logs)
    graph.add_node("screen_injection", screen_injection)
    graph.add_node("retrieve_runbook", retrieve_runbook)
    graph.add_node("synthesize_plan", synthesize_plan)
    graph.add_node("await_approval", await_approval)
    graph.add_node("dispatch", dispatch)

    graph.add_edge(START, "analyze")
    graph.add_edge("analyze", "sanitize_logs")
    graph.add_edge("sanitize_logs", "screen_injection")
    graph.add_edge("screen_injection", "retrieve_runbook")
    graph.add_edge("retrieve_runbook", "synthesize_plan")
    graph.add_edge("synthesize_plan", "await_approval")
    graph.add_edge("await_approval", "dispatch")
    graph.add_edge("dispatch", END)
    return graph


def thread_config(thread_id: str) -> RunnableConfig:
    """Returns the LangGraph config that binds an invocation to one checkpointed thread.

    Typed as `RunnableConfig` rather than a bare dict so mypy checks the call sites: the
    `configurable.thread_id` key is what selects the checkpoint, and a typo there would silently
    start a fresh run instead of resuming the paused one.
    """
    return {"configurable": {"thread_id": thread_id}}


def resume_command(approved: bool) -> Command:
    """Returns the resume payload the approval gate unblocks on."""
    return Command(resume={"approved": approved})
