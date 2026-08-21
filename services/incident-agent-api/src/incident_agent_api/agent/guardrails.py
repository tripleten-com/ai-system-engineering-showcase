"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/agent/guardrails.py
Component:          Pydantic Tool-Argument Firewall
Purpose:            Validates every proposed tool call against a closed allowlist and a strict
                    per-tool argument schema before anything can be dispatched.
Interacts With:     None (pure validation)

Curriculum Project:  Project 4 — Security, PII Redaction & Guardrails
Skills:             Pydantic Guardrails, Prompt Injection Defence, Closed Allowlists
Tools:              Pydantic 2, Python 3.11

Two independent checks, and the order matters:

1. **Is this a real tool?** Rejection is by *absence from the roster*, never by matching a
   denylist of known-bad names. A denylist can only stop the attacks someone already thought
   of; `ToolName` is closed, so `flush_database_tables` fails for the same structural reason
   any other invented name does.
2. **Are these arguments safe for that tool?** Passing the name check must not imply passing
   the argument check. An attacker who discovers a real tool name still has to get destructive
   arguments past a strict schema.

This module invokes nothing. It returns a verdict, and the caller decides. That separation is
what lets the HITL test assert "zero tool callables were invoked" against a spy on the dispatch
layer rather than trusting a return value.
"""

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from tripleten_contracts import READ_ONLY_TOOLS, QueueName, ToolName

# Substrings that make an argument value unacceptable on *any* tool. This is a second line of
# defence, not the primary one — the primary one is that each tool below accepts only the
# arguments its schema names, with the types its schema names.
#
# The patterns cover the three families the spec calls out: destructive SQL, code evaluation,
# and shell metacharacters that would matter if a value ever reached a subprocess.
_DESTRUCTIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bdrop\s+(?:table|database|schema)\b", re.IGNORECASE),
    re.compile(r"\btruncate\s+table\b", re.IGNORECASE),
    re.compile(r"\bdelete\s+from\b", re.IGNORECASE),
    re.compile(r"\bflushall\b|\bflushdb\b", re.IGNORECASE),
    re.compile(r"\b(?:eval|exec|system|popen)\s*\(", re.IGNORECASE),
    re.compile(r"[;&|`]|\$\(|\|\|"),
    re.compile(r"\.\./"),
)


class GuardrailViolationError(Exception):
    """Raised when a proposed call names a tool outside the canonical roster.

    Distinct from `ValidationError` on purpose. This one means "there is no such tool", which
    is an attempt to reach outside the system; a `ValidationError` means "that tool exists but
    not with those arguments". The Scenario 4 narrative needs to tell those apart, and so does
    anyone reading a log line.
    """

    def __init__(self, tool_name: str) -> None:
        super().__init__(f"{tool_name!r} is not one of the {len(ToolName)} canonical agent tools")
        self.tool_name = tool_name


def _reject_destructive(value: str) -> str:
    """Raises if a string argument carries a destructive SQL, eval, or shell construct."""
    for pattern in _DESTRUCTIVE_PATTERNS:
        if pattern.search(value):
            raise ValueError(f"argument contains a prohibited construct: {pattern.pattern}")
    return value


class _SafeArgs(BaseModel):
    """Base for every tool's argument model: closed, immutable, and destructive-value screened.

    `extra="forbid"` is the load-bearing setting. Without it a call could carry
    `{"max_idle_seconds": 60, "confirm_drop": true}` and the extra key would be silently
    dropped rather than rejected — the schema would validate a payload it had not understood.
    """

    model_config = {"extra": "forbid", "frozen": True, "str_strip_whitespace": True}

    @field_validator("*", mode="after")
    @classmethod
    def _screen_strings(cls, value: Any) -> Any:
        """Screens every string field, wherever it appears, against the destructive patterns."""
        if isinstance(value, str):
            return _reject_destructive(value)
        if isinstance(value, list):
            return [_reject_destructive(item) if isinstance(item, str) else item for item in value]
        return value


class CheckHealthArgs(_SafeArgs):
    """`check_health` — read-only probe of a named platform component."""

    component: Literal["postgres", "redis", "sqs", "platform"] = "platform"


class ReadRunbookArgs(_SafeArgs):
    """`read_runbook` — read-only fetch of a runbook by canonical id."""

    runbook_id: str = Field(pattern=r"^(?:RB-\d{3}|SEC-\d{3})$")


class FlushConnectionPoolArgs(_SafeArgs):
    """`flush_connection_pool` — terminate orphaned idle-in-transaction sessions.

    Argument names match the call the AGENT_THOUGHT contract example emits
    (`{"max_idle_seconds": 60}`), which is what the E2E specs read off the DOM.
    """

    target: Literal["postgres"] = "postgres"
    # RB-104 fixes 60 seconds. Bounded so a call cannot ask for 0 and terminate live sessions.
    max_idle_seconds: Annotated[int, Field(ge=30, le=3600)] = 60


class WarmCacheArgs(_SafeArgs):
    """`warm_cache` — batch-repopulate hot keys with jittered TTLs."""

    key_count: Annotated[int, Field(ge=1, le=5000)] = 500
    ttl_jitter_pct: Annotated[int, Field(ge=0, le=50)] = 15


class IsolatePoisonMessageArgs(_SafeArgs):
    """`isolate_poison_message` — route one malformed payload to the workload DLQ."""

    message_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9\-_]+$")
    # The workload DLQ, never the control-plane one, and pinned to the contract rather than a
    # repeated literal. Expressed as a validator instead of `Literal[QueueName.CUSTOMER_DLQ]`
    # because mypy cannot narrow a Literal over an enum member imported across packages, and a
    # type checker that cannot see the constraint is worse than one clear runtime check.
    destination_queue: QueueName = QueueName.CUSTOMER_DLQ

    @field_validator("destination_queue", mode="after")
    @classmethod
    def _only_the_workload_dlq(cls, value: QueueName) -> QueueName:
        """Refuses any queue but `customer-dlq`.

        A containment job must not be able to quarantine into the control-plane DLQ: that would
        cross the workload and the control plane, which the two-queue-pair design exists to keep
        apart.
        """
        if value is not QueueName.CUSTOMER_DLQ:
            raise ValueError(f"a poison payload may only be quarantined to {QueueName.CUSTOMER_DLQ.value}")
        return value


class RebootWorkersArgs(_SafeArgs):
    """`reboot_workers` — restart the stalled consumer pool."""

    pool_size: Annotated[int, Field(ge=1, le=16)] = 4


class RevokeSessionArgs(_SafeArgs):
    """`revoke_session` — invalidate the session that carried the injection."""

    session_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9\-_:]+$")


class BlockIpArgs(_SafeArgs):
    """`block_ip` — add the source address to the edge denylist."""

    # An address shape, not a free string: an argument reaching a firewall rule is the last
    # place to accept arbitrary text.
    ip_address: str = Field(pattern=r"^\d{1,3}(?:\.\d{1,3}){3}$")
    duration_minutes: Annotated[int, Field(ge=1, le=10080)] = 1440


class ArchiveForensicsArgs(_SafeArgs):
    """`archive_forensics` — write the forensic snapshot to the postmortem bucket."""

    incident_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9\-_]+$")
    include_payload: bool = True


ARGUMENT_MODELS: dict[ToolName, type[_SafeArgs]] = {
    ToolName.CHECK_HEALTH: CheckHealthArgs,
    ToolName.READ_RUNBOOK: ReadRunbookArgs,
    ToolName.FLUSH_CONNECTION_POOL: FlushConnectionPoolArgs,
    ToolName.WARM_CACHE: WarmCacheArgs,
    ToolName.ISOLATE_POISON_MESSAGE: IsolatePoisonMessageArgs,
    ToolName.REBOOT_WORKERS: RebootWorkersArgs,
    ToolName.REVOKE_SESSION: RevokeSessionArgs,
    ToolName.BLOCK_IP: BlockIpArgs,
    ToolName.ARCHIVE_FORENSICS: ArchiveForensicsArgs,
}


class ValidatedToolCall(BaseModel):
    """A call that cleared both checks. Nothing downstream accepts anything else."""

    model_config = {"frozen": True}

    name: ToolName
    args: dict[str, Any]

    @property
    def is_read_only(self) -> bool:
        """True for the two planning-phase diagnostics that may run before approval."""
        return self.name in READ_ONLY_TOOLS


def validate_tool_call(name: str, args: dict[str, Any] | None = None) -> ValidatedToolCall:
    """Validates a proposed tool call, raising rather than returning a verdict object.

    Raises `GuardrailViolationError` when the name is not canonical and `ValidationError` when
    the arguments are wrong for a canonical name. Raising keeps the failure impossible to
    ignore: a boolean return could be dropped at a call site and the call dispatched anyway.
    """
    try:
        tool = ToolName(name)
    except ValueError as err:
        raise GuardrailViolationError(name) from err

    model = ARGUMENT_MODELS[tool]
    validated = model.model_validate(args or {})
    return ValidatedToolCall(name=tool, args=validated.model_dump())


def is_blocked(name: str, args: dict[str, Any] | None = None) -> bool:
    """Returns True when a proposed call would be refused, without raising.

    For the reasoning chain, which reports a verdict per proposed call rather than aborting on
    the first bad one — Scenario 4 has to show *both* injected calls struck through.
    """
    try:
        validate_tool_call(name, args)
    except (GuardrailViolationError, ValidationError):
        return True
    return False
