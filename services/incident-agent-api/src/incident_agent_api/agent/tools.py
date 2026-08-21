"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/agent/tools.py
Component:          Canonical Agent Tool Registry
Purpose:            Declares the nine canonical tools and implements only the two that are
                    safe to run before a human approves anything.
Interacts With:     postgres-vector (:5432), localstack (:4566)

Curriculum Project:  Project 5 — Autonomous Agent & Human-in-the-Loop
Skills:             Tool Design, HITL Checkpoints, Least Privilege
Tools:              Python 3.11, Pydantic 2, pgvector

**The seven state-changing tools have no implementation in this service, and that is the
design.** `flush_connection_pool`, `warm_cache`, `isolate_poison_message`, `reboot_workers`,
`revoke_session`, `block_ip`, and `archive_forensics` live in
`services/remediation-worker/src/remediation_worker/handlers/`, reachable only by consuming a
job off `remediation-jobs` — and a job only lands there after `POST /api/incidents/authorize`.

So the HITL guarantee is not a check that could be forgotten or a flag that could be flipped.
There is no function in the API process that performs a remediation, which means no bug, no
refactor, and no injected instruction can cause one to run early. `test_hitl_gate.py` asserts
this from the outside with a spy; `READ_ONLY_DISPATCH` below is what makes it true from the
inside.

`check_health` and `read_runbook` are different in kind: both are reads, both are what the
agent needs while it is still deciding, and neither changes anything. They are implemented
here and are deliberately callable pre-approval — the spec asks for that distinction to be
asserted separately, precisely so it stays visible.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from incident_agent_api.agent.guardrails import ARGUMENT_MODELS, ValidatedToolCall, validate_tool_call
from tripleten_contracts import READ_ONLY_TOOLS, REMEDIATION_TOOLS, ToolName

logger = logging.getLogger("incident-agent-api")


class RemediationToolNotExecutableError(RuntimeError):
    """Raised if anything asks this service to execute a state-changing tool.

    Unreachable by design — `READ_ONLY_DISPATCH` has no entry for these — and it exists so that
    a future change which *did* wire one up fails loudly at the boundary instead of quietly
    remediating before a human clicked.
    """

    def __init__(self, tool: ToolName) -> None:
        super().__init__(
            f"{tool.value} is a remediation tool; it executes in remediation-worker after "
            f"POST /api/incidents/authorize, never in the API process"
        )
        self.tool = tool


class ToolResult(dict[str, Any]):
    """A read-only tool's return value. A plain dict subclass so it serializes as itself."""


async def check_health(engine: AsyncEngine | None, component: str = "platform") -> ToolResult:
    """Reads back whether a named platform component is reachable. Changes nothing."""
    if component in {"postgres", "platform"} and engine is not None:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1;"))
            return ToolResult(component=component, reachable=True)
        except Exception as err:  # noqa: BLE001 - a probe reports, it does not raise
            logger.debug("check_health(%s) failed: %s", component, err)
            return ToolResult(component=component, reachable=False)
    return ToolResult(component=component, reachable=engine is not None)


async def read_runbook(engine: AsyncEngine | None, runbook_id: str) -> ToolResult:
    """Fetches one runbook's title and summary by canonical id. Changes nothing."""
    if engine is None:
        return ToolResult(runbook_id=runbook_id, found=False)
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT id, title, summary FROM knowledge_runbooks WHERE id = :id"),
            {"id": runbook_id},
        )
        row = result.first()
    if row is None:
        return ToolResult(runbook_id=runbook_id, found=False)
    return ToolResult(runbook_id=row.id, title=row.title, summary=row.summary, found=True)


# The only tools this process can execute. Keyed by ToolName so the mapping cannot drift from
# the canonical roster, and asserted complete-and-exclusive by the unit suite.
READ_ONLY_DISPATCH: dict[ToolName, Callable[..., Awaitable[ToolResult]]] = {
    ToolName.CHECK_HEALTH: check_health,
    ToolName.READ_RUNBOOK: read_runbook,
}


async def invoke_read_only_tool(
    engine: AsyncEngine | None,
    name: str,
    args: dict[str, Any] | None = None,
) -> ToolResult:
    """Validates and runs a read-only tool, refusing anything state-changing.

    The single entry point for tool execution inside the API, which is what makes it the single
    place a test needs to spy on to prove nothing ran early.
    """
    call = validate_tool_call(name, args)
    handler = READ_ONLY_DISPATCH.get(call.name)
    if handler is None:
        raise RemediationToolNotExecutableError(call.name)
    return await handler(engine, **call.args)


def canonical_signature(tool: ToolName) -> dict[str, Any]:
    """Returns a tool's default arguments — the signature the agent proposes calls against."""
    return ARGUMENT_MODELS[tool]().model_dump()


def proposed_call(tool: ToolName, **overrides: Any) -> ValidatedToolCall:
    """Builds a validated call for a canonical tool, defaults filled in.

    Used by the planner so a proposed call is guardrail-checked at the moment it is drafted,
    not merely before it is dispatched. A plan that cannot be validated is never shown to a
    human as something to approve.
    """
    return validate_tool_call(tool.value, overrides)


# Guards the module's own central claim. Kept at import time rather than in a test so a mapping
# that wired up a remediation tool could not even be imported, let alone dispatched.
assert set(READ_ONLY_DISPATCH) == READ_ONLY_TOOLS, "READ_ONLY_DISPATCH must cover exactly the read-only tools"
assert not (set(READ_ONLY_DISPATCH) & REMEDIATION_TOOLS), "a remediation tool is executable in the API process"
