"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             packages/contracts/tests/test_identifier_completeness.py
Component:          Canonical Identifier Conformance Tests
Purpose:            Asserts every canonical identifier from the project charter is present
                    exactly once, and that the state machine forbids auto-advancing the HITL gate.
Interacts With:     None (pure contract assertions)

Curriculum Project:  Cross-cutting — Modular Ports & Contract Design
Skills:             Contract Testing, Enum Modelling, Drift Prevention
Tools:              Pytest, Python 3.11
"""

from tripleten_contracts import (
    LEGAL_TRANSITIONS,
    BucketName,
    IncidentState,
    QueueName,
    RunbookId,
    ScenarioId,
    ToolName,
)


def test_scenario_ids_are_exactly_the_four_canonical_values():
    assert {s.value for s in ScenarioId} == {
        "db_pool_exhaustion",
        "cache_thundering_herd",
        "worker_deadlock",
        "prompt_injection",
    }


def test_runbook_ids_are_exactly_the_four_canonical_values():
    assert {r.value for r in RunbookId} == {"RB-104", "RB-208", "RB-312", "SEC-501"}


def test_queue_names_are_exactly_the_four_canonical_values():
    assert {q.value for q in QueueName} == {
        "customer-jobs",
        "customer-dlq",
        "remediation-jobs",
        "remediation-dlq",
    }


def test_bucket_name_is_the_canonical_value():
    assert BucketName.POSTMORTEMS.value == "tripleten-cloud-postmortems"


def test_tool_names_are_exactly_the_nine_canonical_values():
    assert {t.value for t in ToolName} == {
        "check_health",
        "read_runbook",
        "flush_connection_pool",
        "warm_cache",
        "isolate_poison_message",
        "reboot_workers",
        "revoke_session",
        "block_ip",
        "archive_forensics",
    }


def test_every_scenario_maps_to_its_runbook():
    assert ScenarioId.DB_POOL_EXHAUSTION.runbook is RunbookId.RB_104
    assert ScenarioId.CACHE_THUNDERING_HERD.runbook is RunbookId.RB_208
    assert ScenarioId.WORKER_DEADLOCK.runbook is RunbookId.RB_312
    assert ScenarioId.PROMPT_INJECTION.runbook is RunbookId.SEC_501


def test_only_scenario_four_is_outage_free():
    """Scenario 4 is containment-only: no chaos math, no infrastructure spike, no decay."""
    assert ScenarioId.PROMPT_INJECTION.causes_outage is False
    for scenario in ScenarioId:
        if scenario is not ScenarioId.PROMPT_INJECTION:
            assert scenario.causes_outage is True


def test_awaiting_approval_never_auto_advances_to_executing_without_authorization():
    """AWAITING_APPROVAL may only reach EXECUTING or REJECTED. There is no timeout path."""
    assert LEGAL_TRANSITIONS[IncidentState.AWAITING_APPROVAL] == frozenset(
        {IncidentState.EXECUTING, IncidentState.REJECTED}
    )


def test_executing_is_reachable_only_from_awaiting_approval():
    """The HITL gate is the single door into tool execution."""
    sources = {state for state, targets in LEGAL_TRANSITIONS.items() if IncidentState.EXECUTING in targets}
    assert sources == {IncidentState.AWAITING_APPROVAL}


def test_scenario_four_path_never_enters_critical_outage():
    assert IncidentState.CRITICAL_OUTAGE not in LEGAL_TRANSITIONS[IncidentState.EXPLOIT_INTERCEPTED]


def test_terminal_states_only_leave_via_reset():
    for terminal in (IncidentState.REJECTED, IncidentState.FAILED, IncidentState.SECURITY_CONTAINED):
        assert LEGAL_TRANSITIONS[terminal] == frozenset({IncidentState.HEALTHY})


def test_every_state_has_a_transition_entry():
    assert set(LEGAL_TRANSITIONS) == set(IncidentState)
