"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/tests/unit/test_pydantic_guardrails.py
Component:          Tool Firewall Unit Tests
Purpose:            Asserts the closed allowlist, the strict argument schemas, and that no tool
                    callable is invoked while a call is being judged.
Interacts With:     None (pure validation)

Curriculum Project:  Project 4 — Security, PII Redaction & Guardrails
Skills:             Pydantic Guardrails, Prompt Injection Defence, Spy-Based Assertions
Tools:              Pytest, Pydantic 2, Python 3.11

Scope note, from testing-strategy-and-specs.md §5.1C: these are **pure guardrail tests**. They
assert the validator's verdict and that nothing ran. The `EXPLOIT_INTERCEPTED` transition that
follows a rejection is a state-machine concern, asserted in the integration and E2E tiers.
"""

import pytest
from pydantic import ValidationError

from incident_agent_api import scenarios
from incident_agent_api.agent import guardrails, tools
from incident_agent_api.agent.guardrails import (
    ARGUMENT_MODELS,
    GuardrailViolationError,
    is_blocked,
    validate_tool_call,
)
from tripleten_contracts import READ_ONLY_TOOLS, REMEDIATION_TOOLS, ToolName


def test_authorized_tool_invocation_validates():
    """The canonical signature from the AGENT_THOUGHT contract example must be accepted."""
    call = validate_tool_call("flush_connection_pool", {"target": "postgres", "max_idle_seconds": 60})
    assert call.name is ToolName.FLUSH_CONNECTION_POOL
    assert call.args == {"target": "postgres", "max_idle_seconds": 60}


def test_the_documented_payload_shape_validates_without_the_optional_target():
    """telemetry-and-chaos-engine.md §6.1 emits `{"max_idle_seconds": 60}` with no target."""
    call = validate_tool_call("flush_connection_pool", {"max_idle_seconds": 60})
    assert call.args["target"] == "postgres"


@pytest.mark.parametrize(
    "name",
    ["flush_database_tables", "dump_aws_credentials", "execute_raw_sql", "drop_database", ""],
)
def test_tool_outside_the_allowlist_is_refused(name: str):
    """Rejection is by absence from the roster, never by matching a denylist of known-bad names."""
    with pytest.raises(GuardrailViolationError) as exc:
        validate_tool_call(name, {})
    assert exc.value.tool_name == name


# Attack strings, never executed. Each is passed to the validator as data so the test can assert
# it is *refused*; nothing here is evaluated, interpolated into SQL, or handed to a shell. That
# is the whole point of the module under test.
@pytest.mark.parametrize(
    "args",
    [
        {"message_id": "DROP TABLE customers"},
        {"message_id": "msg; rm -rf /"},
        {"message_id": "$(whoami)"},
        {"message_id": "eval(1)"},
        {"message_id": "../../etc/passwd"},
        {"message_id": "a && b"},
        {"message_id": "TRUNCATE TABLE jobs"},
        {"message_id": "DELETE FROM customers"},
        {"message_id": "FLUSHALL"},
    ],
)
def test_destructive_arguments_on_an_allowed_tool_are_refused(args: dict):
    """Passing the name check must not imply passing the argument check."""
    with pytest.raises(ValidationError):
        validate_tool_call("isolate_poison_message", args)


def test_an_unknown_argument_is_refused_rather_than_dropped():
    """extra="forbid" is load-bearing: a silently dropped key validates a payload nobody read."""
    with pytest.raises(ValidationError):
        validate_tool_call("reboot_workers", {"pool_size": 4, "confirm_drop": True})


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("flush_connection_pool", {"max_idle_seconds": 0}),
        ("flush_connection_pool", {"max_idle_seconds": 99999}),
        ("reboot_workers", {"pool_size": 0}),
        ("warm_cache", {"ttl_jitter_pct": 90}),
        ("block_ip", {"ip_address": "not-an-address"}),
        ("block_ip", {"ip_address": "10.0.1.42", "duration_minutes": 0}),
        ("read_runbook", {"runbook_id": "RB-1"}),
        ("isolate_poison_message", {"message_id": "msg", "destination_queue": "remediation-dlq"}),
    ],
)
def test_out_of_contract_argument_values_are_refused(name: str, args: dict):
    """Bounded fields, address shapes, and the DLQ Literal all narrow what a call can ask for.

    The `destination_queue` case is the pointed one: a containment job must not be able to
    quarantine into the control-plane DLQ, so that field is a Literal rather than a free string.
    """
    with pytest.raises(ValidationError):
        validate_tool_call(name, args)


def test_the_adversarial_injection_payload_is_rejected_in_full():
    """The canonical Scenario 4 fixture: both injected calls refused, nothing executed."""
    for name, args in scenarios.INJECTED_TOOL_CALLS:
        assert is_blocked(name, dict(args)) is True
        with pytest.raises(GuardrailViolationError):
            validate_tool_call(name, dict(args))


def test_no_tool_callable_is_invoked_while_judging_a_call(monkeypatch):
    """Asserted with a spy on the dispatch layer, not by trusting a return value.

    The guardrail returns a verdict and invokes nothing. If validation ever gained a "dry run"
    that called the handler, this is what would catch it.
    """
    invoked: list[str] = []

    for tool_name, handler in list(tools.READ_ONLY_DISPATCH.items()):
        async def spy(*_args, _name=tool_name, _handler=handler, **_kwargs):
            invoked.append(_name.value)
            return await _handler(*_args, **_kwargs)

        monkeypatch.setitem(tools.READ_ONLY_DISPATCH, tool_name, spy)

    for name, args in scenarios.INJECTED_TOOL_CALLS:
        assert is_blocked(name, dict(args))
    validate_tool_call("flush_connection_pool", {"max_idle_seconds": 60})

    assert invoked == [], f"the guardrail invoked {invoked}"


def test_every_canonical_tool_has_an_argument_model():
    """A tool with no schema would validate any arguments at all."""
    assert set(ARGUMENT_MODELS) == set(ToolName)


def test_the_read_only_split_matches_the_contract():
    """The HITL gate test asserts against exactly these two sets."""
    assert {t.value for t in READ_ONLY_TOOLS} == {"check_health", "read_runbook"}
    assert len(REMEDIATION_TOOLS) == 7
    assert not (READ_ONLY_TOOLS & REMEDIATION_TOOLS)


@pytest.mark.parametrize("tool", sorted(REMEDIATION_TOOLS, key=lambda t: t.value), ids=lambda t: t.value)
def test_the_api_process_cannot_execute_a_remediation_tool(tool: ToolName):
    """The structural half of the HITL guarantee: there is no implementation to call.

    Not a check that could be removed — `READ_ONLY_DISPATCH` simply has no entry, so no bug,
    refactor, or injected instruction can cause a remediation to run in this process.
    """
    assert tool not in tools.READ_ONLY_DISPATCH


def test_invoking_a_remediation_tool_raises_rather_than_running_it():
    """Guards the boundary in case a future change did wire one up."""
    import asyncio

    with pytest.raises(tools.RemediationToolNotExecutableError):
        asyncio.run(
            tools.invoke_read_only_tool(None, "flush_connection_pool", {"max_idle_seconds": 60})
        )


def test_guardrail_violation_and_validation_error_are_distinguishable():
    """"No such tool" and "wrong arguments for that tool" are different findings.

    Scenario 4's narrative depends on telling them apart, and so does anyone reading a log line.
    """
    with pytest.raises(GuardrailViolationError):
        validate_tool_call("dump_aws_credentials", {})
    with pytest.raises(ValidationError):
        validate_tool_call("block_ip", {"ip_address": "nope"})
    assert not issubclass(guardrails.GuardrailViolationError, ValidationError)
