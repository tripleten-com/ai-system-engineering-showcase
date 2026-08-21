"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/agent/mock_llm.py
Component:          Deterministic Offline Planner
Purpose:            Synthesizes the remediation plan and the reasoning chain the War Room
                    renders, with no network call and no API key.
Interacts With:     None (pure functions)

Curriculum Project:  Project 5 — Autonomous Agent & Human-in-the-Loop
Skills:             Deterministic Mock Execution, Offline-First Design, Plan Synthesis
Tools:              Python 3.11, Pydantic 2

The demo's default path has no LLM in it, and that is a feature rather than a compromise:
`OPENAI_API_KEY` is an optional upgrade, so the reasoning chain has to be reproducible without
one. Everything here is a pure function of `(scenario_id, retrieved runbook)`, which is what
makes the E2E specs able to assert exact on-screen text and the demo able to run on a laptop
with no egress.

What is *not* mocked is everything that would make mocking a lie: the retrieval really queries
pgvector, the guardrail really validates, the checkpoint really persists in Postgres, the job
really crosses SQS, and the worker really writes to S3. The planner is the one seam where a
model would sit, and it is deliberately the only one.
"""

from dataclasses import dataclass

from incident_agent_api import scenarios
from incident_agent_api.agent.guardrails import ValidatedToolCall
from incident_agent_api.agent.tools import proposed_call
from tripleten_contracts import APPROVAL_PROMPT, SCENARIO_TOOLS, RagMatchPayload, ScenarioId, ToolName

# Per-scenario arguments for the tools a plan proposes. Defaults come from each tool's schema;
# these are the values the narrative fixes — RB-104's 60-second idle threshold, RB-208's top-500
# key warm-up, RB-312's poison message id, SEC-501's session and source address.
TOOL_ARGUMENTS: dict[ToolName, dict[str, object]] = {
    ToolName.FLUSH_CONNECTION_POOL: {"max_idle_seconds": 60},
    ToolName.WARM_CACHE: {"key_count": 500, "ttl_jitter_pct": 15},
    ToolName.ISOLATE_POISON_MESSAGE: {"message_id": "msg-98234-corrupt"},
    ToolName.REBOOT_WORKERS: {"pool_size": 4},
    ToolName.REVOKE_SESSION: {"session_id": "sess-7c41d9a2"},
    # The private address the injected payload originated from. Masked in every log line the UI
    # renders; carried here because blocking it is the point of the containment.
    ToolName.BLOCK_IP: {"ip_address": "10.0.7.31", "duration_minutes": 1440},
    # archive_forensics is absent on purpose: its only required argument is the incident id,
    # which is per-run and cannot live in a static table. See RUN_SCOPED_ARGUMENTS.
}

# Tools whose arguments depend on the run rather than the scenario. `archive_forensics` takes the
# incident it is archiving, and there is no sensible default for that — which is why
# `ArchiveForensicsArgs.incident_id` has none, and why leaving it out made the guardrail refuse
# the whole Scenario 4 plan rather than draft one with a placeholder. That refusal was correct;
# the planner was what needed fixing.
RUN_SCOPED_ARGUMENTS: frozenset[ToolName] = frozenset({ToolName.ARCHIVE_FORENSICS})


def _arguments_for(tool: ToolName, incident_id: str) -> dict[str, object]:
    """Returns a tool's proposed arguments, filling in any that are scoped to this run."""
    args = dict(TOOL_ARGUMENTS.get(tool, {}))
    if tool in RUN_SCOPED_ARGUMENTS:
        args["incident_id"] = incident_id
    return args


@dataclass(frozen=True)
class RemediationPlan:
    """The plan an SRE is asked to approve, and the reasoning that produced it."""

    scenario_id: ScenarioId
    summary: str
    approval_prompt: str
    tool_calls: tuple[ValidatedToolCall, ...]

    @property
    def tools(self) -> tuple[ToolName, ...]:
        """The tools this plan dispatches, in execution order."""
        return tuple(call.name for call in self.tool_calls)


def synthesize_plan(scenario_id: ScenarioId, runbook: RagMatchPayload, incident_id: str) -> RemediationPlan:
    """Drafts the remediation plan for a scenario from its retrieved runbook.

    Every proposed call goes through `proposed_call`, so the plan is guardrail-validated at the
    moment it is drafted rather than only before dispatch. A plan that would not validate is
    never put in front of a human as something to approve — which is exactly what happened when
    `archive_forensics` was proposed without an incident id, and is why this takes one.
    """
    tools = SCENARIO_TOOLS[scenario_id]
    calls = tuple(proposed_call(tool, **_arguments_for(tool, incident_id)) for tool in tools)

    verb = "Containment" if scenario_id is ScenarioId.PROMPT_INJECTION else "Remediation"
    steps = ", ".join(call.name.value for call in calls)
    summary = (
        f"{verb} plan drafted from {runbook.runbook_id.value} ({runbook.title}). "
        f"{scenarios.TOOL_RATIONALE[scenario_id]} "
        f"Proposed tool sequence: {steps}. Awaiting human authorization."
    )

    return RemediationPlan(
        scenario_id=scenario_id,
        summary=summary,
        approval_prompt=APPROVAL_PROMPT[scenario_id],
        tool_calls=calls,
    )


def diagnosis_for(scenario_id: ScenarioId) -> str:
    """Returns the one-line ANALYZING observation for a scenario."""
    return scenarios.DIAGNOSIS[scenario_id]


def retrieval_query_for(scenario_id: ScenarioId) -> str:
    """Returns the natural-language query this scenario puts to the hybrid retriever."""
    return scenarios.RETRIEVAL_QUERY[scenario_id]
