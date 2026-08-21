"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             packages/contracts/src/tripleten_contracts/states.py
Component:          Incident State Machine Contract
Purpose:            Defines the canonical incident states and the only legal transitions
                    between them, including the hard human-in-the-loop approval gate.
Interacts With:     incident-agent-api (:8000), remediation-worker (internal)

Curriculum Project:  Project 5 — Autonomous Agent & HITL Checkpoint
Skills:             State Machine Design, Safety Invariants, Contract-First Design
Tools:              Python 3.11
"""

from enum import StrEnum


class IncidentState(StrEnum):
    """Canonical incident lifecycle states.

    Scenarios 1–3: HEALTHY → CRITICAL_OUTAGE → AWAITING_APPROVAL → EXECUTING → RECOVERING → HEALTHY
    Scenario 4:    HEALTHY → EXPLOIT_INTERCEPTED → AWAITING_APPROVAL → EXECUTING → SECURITY_CONTAINED
    """

    HEALTHY = "HEALTHY"
    CRITICAL_OUTAGE = "CRITICAL_OUTAGE"
    EXPLOIT_INTERCEPTED = "EXPLOIT_INTERCEPTED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    RECOVERING = "RECOVERING"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    SECURITY_CONTAINED = "SECURITY_CONTAINED"


# The single door into EXECUTING is AWAITING_APPROVAL, and it opens only on an explicit
# POST /api/incidents/authorize. No timeout approves, nothing auto-advances. This table is
# the machine-checkable form of that guarantee.
LEGAL_TRANSITIONS: dict[IncidentState, frozenset[IncidentState]] = {
    IncidentState.HEALTHY: frozenset(
        {IncidentState.CRITICAL_OUTAGE, IncidentState.EXPLOIT_INTERCEPTED}
    ),
    IncidentState.CRITICAL_OUTAGE: frozenset({IncidentState.AWAITING_APPROVAL}),
    # EXPLOIT_INTERCEPTED is a phase, not a terminal state, and never spikes the metrics.
    IncidentState.EXPLOIT_INTERCEPTED: frozenset({IncidentState.AWAITING_APPROVAL}),
    IncidentState.AWAITING_APPROVAL: frozenset({IncidentState.EXECUTING, IncidentState.REJECTED}),
    IncidentState.EXECUTING: frozenset(
        {IncidentState.RECOVERING, IncidentState.FAILED, IncidentState.SECURITY_CONTAINED}
    ),
    IncidentState.RECOVERING: frozenset({IncidentState.HEALTHY}),
    # The three terminal states hold their metric values until POST /api/incidents/reset.
    IncidentState.REJECTED: frozenset({IncidentState.HEALTHY}),
    IncidentState.FAILED: frozenset({IncidentState.HEALTHY}),
    IncidentState.SECURITY_CONTAINED: frozenset({IncidentState.HEALTHY}),
}

TERMINAL_STATES: frozenset[IncidentState] = frozenset(
    {IncidentState.REJECTED, IncidentState.FAILED, IncidentState.SECURITY_CONTAINED}
)
