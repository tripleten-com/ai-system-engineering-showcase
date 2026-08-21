"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/remediation-worker/src/remediation_worker/handlers/__init__.py
Component:          Tool-Name → Handler Registry
Purpose:            Maps each of the seven state-changing tools to the function that performs
                    it, and refuses anything not on the roster.
Interacts With:     localstack (:4566), redis (:6379)

Curriculum Project:  Project 3 — Asynchronous Queues & Cloud Operations
Skills:             Handler Registries, Least Privilege, Closed Allowlists
Tools:              Python 3.11

**This is the only place in the system where a remediation tool can be executed**, and it is
reachable only by consuming a message off `remediation-jobs` — a queue nothing writes to
without a `POST /api/incidents/authorize` first. The API process has no implementation of these
seven functions at all, which is what makes the human-in-the-loop guarantee structural rather
than a check someone could remove.

The registry is closed the same way the guardrail's allowlist is: a tool absent from it cannot
run, so a job carrying an unknown name fails rather than falling through to a default.
"""

from collections.abc import Callable

from remediation_worker.handlers.cache import warm_cache
from remediation_worker.handlers.connection_pool import flush_connection_pool
from remediation_worker.handlers.queue import isolate_poison_message, reboot_workers
from remediation_worker.handlers.security import archive_forensics, block_ip, revoke_session
from remediation_worker.handlers.types import HandlerResult, ToolContext
from tripleten_contracts import READ_ONLY_TOOLS, REMEDIATION_TOOLS, ToolName

ToolHandler = Callable[[ToolContext], HandlerResult]

HANDLERS: dict[ToolName, ToolHandler] = {
    ToolName.FLUSH_CONNECTION_POOL: flush_connection_pool,
    ToolName.WARM_CACHE: warm_cache,
    ToolName.ISOLATE_POISON_MESSAGE: isolate_poison_message,
    ToolName.REBOOT_WORKERS: reboot_workers,
    ToolName.REVOKE_SESSION: revoke_session,
    ToolName.BLOCK_IP: block_ip,
    ToolName.ARCHIVE_FORENSICS: archive_forensics,
}


class UnknownToolError(RuntimeError):
    """Raised when a job names a tool this worker has no handler for."""

    def __init__(self, tool: ToolName) -> None:
        super().__init__(f"no handler registered for {tool.value}")
        self.tool = tool


def handler_for(tool: ToolName) -> ToolHandler:
    """Returns the handler for a tool, raising rather than returning None."""
    try:
        return HANDLERS[tool]
    except KeyError as err:
        raise UnknownToolError(tool) from err


# Guards the registry's central claim at import time, mirroring the assertion in the API's
# tools.py from the other side: every remediation tool is executable here, and neither of the
# two read-only diagnostics is. A job carrying `check_health` is rejected by the RemediationJob
# contract before it is ever published, and would find no handler here either.
assert set(HANDLERS) == REMEDIATION_TOOLS, "HANDLERS must cover exactly the seven remediation tools"
assert not (set(HANDLERS) & READ_ONLY_TOOLS), "a read-only diagnostic is registered as remediation work"

__all__ = ["HANDLERS", "HandlerResult", "ToolContext", "ToolHandler", "UnknownToolError", "handler_for"]
