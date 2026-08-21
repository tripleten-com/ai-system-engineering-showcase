"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/telemetry/state_machine.py
Component:          Incident Run State Machine
Purpose:            Owns the single in-flight incident run and enforces the canonical transition
                    table, so no code path can advance past the human approval gate.
Interacts With:     incident-war-room (:3000), remediation-worker (internal)

Curriculum Project:  Project 5 — Autonomous Agent & Human-in-the-Loop
Skills:             State Machine Design, Safety Invariants, HITL Checkpoints
Tools:              Python 3.11
"""

import secrets
from dataclasses import dataclass, field

from tripleten_contracts import LEGAL_TRANSITIONS, SCENARIO_SLUG, IncidentState, ScenarioId

# 4 bytes, not 2, and the arithmetic is the reason rather than taste. At 2 bytes an id
# carries 16 bits, and test_identifiers_are_unique_across_runs draws 50 of them and asserts
# all 50 differ — a birthday problem with P(collision) = 1 - e^(-50*49/2^17), which is 1.85%.
# That test had therefore been failing roughly one CI run in 54 since Stage 1. 4 bytes takes
# the same draw to 1 in 3.5 million. Do not narrow this again to match the `inc-9938-db` in
# the docs: that id is illustrative, exactly as `thread-42` is beside a 3-byte thread id.
_INCIDENT_ID_BYTES = 4
_THREAD_ID_BYTES = 3


class IllegalTransitionError(RuntimeError):
    """Raised when a caller attempts a transition the canonical table does not permit."""


class IncidentAlreadyActiveError(RuntimeError):
    """Raised when a trigger arrives while a run is still in flight."""

    def __init__(self, incident_id: str) -> None:
        super().__init__(f"incident {incident_id} is already active")
        self.incident_id = incident_id


class UnknownIncidentError(RuntimeError):
    """Raised when an incident id does not match the run currently in flight."""

    def __init__(self, incident_id: str) -> None:
        super().__init__(f"incident {incident_id} is not the active run")
        self.incident_id = incident_id


@dataclass
class IncidentRun:
    """One incident from trigger to reset, including the clocks the chaos engine reads."""

    incident_id: str
    thread_id: str
    scenario_id: ScenarioId
    state: IncidentState
    chaos_started_at: float
    decay_started_at: float | None = None
    error: str | None = field(default=None)


def _new_incident_id(scenario_id: ScenarioId) -> str:
    """Returns an incident id whose suffix names the scenario, e.g. inc-3f9a2b71-db.

    Unique in practice rather than by construction: 32 bits of entropy from `secrets`, which
    is enough that the uniqueness assertion in the unit suite is a real check instead of a
    1-in-54 coin flip. Nothing here dedupes against a table, because the machine holds at most
    one run and every incoming id is compared against that one run. Stage 5 keying durable
    state on an `incident_id` — a Redis idempotency lock, a persisted incident row — should
    revisit the width rather than assume it.
    """
    return f"inc-{secrets.token_hex(_INCIDENT_ID_BYTES)}-{SCENARIO_SLUG[scenario_id]}"


def _new_thread_id() -> str:
    """Returns the LangGraph thread id that will carry this run across the approval gap."""
    return f"thread-{secrets.token_hex(_THREAD_ID_BYTES)}"


class TelemetryStateMachine:
    """Holds at most one incident run and validates every transition against the contract."""

    def __init__(self) -> None:
        self._run: IncidentRun | None = None

    @property
    def run(self) -> IncidentRun | None:
        """Returns the in-flight run, or None when the platform is idle."""
        return self._run

    @property
    def state(self) -> IncidentState:
        """Returns the current run state, or HEALTHY when no run is in flight."""
        return self._run.state if self._run else IncidentState.HEALTHY

    @property
    def scenario_id(self) -> ScenarioId | None:
        """Returns the in-flight scenario, or None when the platform is idle."""
        return self._run.scenario_id if self._run else None

    def trigger(self, scenario_id: ScenarioId, now: float) -> IncidentRun:
        """Opens a new run, refusing to start a second one while the first is unresolved."""
        if self._run is not None:
            raise IncidentAlreadyActiveError(self._run.incident_id)

        # Scenario 4 skips CRITICAL_OUTAGE entirely: the guardrail blocks the injected call,
        # so a security event is recorded but no outage ever occurs.
        opening = (
            IncidentState.CRITICAL_OUTAGE if scenario_id.causes_outage else IncidentState.EXPLOIT_INTERCEPTED
        )
        if opening not in LEGAL_TRANSITIONS[IncidentState.HEALTHY]:  # pragma: no cover - contract guard
            raise IllegalTransitionError(f"HEALTHY may not open into {opening}")

        self._run = IncidentRun(
            incident_id=_new_incident_id(scenario_id),
            thread_id=_new_thread_id(),
            scenario_id=scenario_id,
            state=opening,
            chaos_started_at=now,
        )
        return self._run

    def transition(self, to: IncidentState) -> IncidentRun:
        """Advances the run, raising IllegalTransitionError without mutating anything on refusal."""
        if self._run is None:
            raise IllegalTransitionError(f"no active run to transition into {to}")
        if to not in LEGAL_TRANSITIONS[self._run.state]:
            raise IllegalTransitionError(f"{self._run.state} may not transition to {to}")
        self._run.state = to
        return self._run

    def reset(self, incident_id: str | None) -> None:
        """Clears the run, refusing an id that does not own it.

        Three cases, all deliberate. With no run in flight this is a no-op rather than an error,
        so a double-click on Master Reset is harmless. With a matching id the run is cleared.
        With a mismatched id it raises. Passing None **force-clears** whatever is in flight,
        skipping the ownership check — that is for the engine's own recovery-completion path
        only, and the parameter is required precisely so no caller reaches it by accident.
        """
        if self._run is None:
            return
        if incident_id is not None and incident_id != self._run.incident_id:
            raise UnknownIncidentError(incident_id)
        self._run = None
