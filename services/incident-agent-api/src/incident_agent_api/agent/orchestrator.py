"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/agent/orchestrator.py
Component:          Run Orchestrator
Purpose:            Couples the LangGraph run to the telemetry state machine, so the narrative
                    the agent produces and the state the platform reports can never disagree.
Interacts With:     postgres-vector (:5432), localstack (:4566), incident-war-room (:3000)

Curriculum Project:  Project 5 — Autonomous Agent & Human-in-the-Loop
Skills:             LangGraph Orchestration, HITL Checkpoints, State Machine Design
Tools:              LangGraph, FastAPI, Python 3.11

Two state machines exist and they answer different questions, which is why neither absorbed the
other:

* `TelemetryStateMachine` owns what the *platform* reports — `system_health_status`, the chaos
  clock, the gauge values. It is the source of truth for `/metrics` and the UI badge.
* The LangGraph checkpoint owns what the *agent* has done — which nodes ran, what it retrieved,
  what it proposed, and whether it is paused. It is the source of truth for resumability.

This module is the only place that advances both, so every state transition in the system has
exactly one call site. Which one moves first depends on the direction, and both orders are
deliberate:

* **Reaching the gate and rejecting** advance the graph first. The graph is durable and the
  engine is in memory, so a crash between the two leaves a resumable run rather than a run the
  engine believes finished and the graph never started.
* **Approving** advances the engine first, to `EXECUTING`, before the graph resumes. The graph's
  dispatch node publishes to `remediation-jobs` and the worker can consume, execute, and call
  back while `ainvoke` is still running — so transitioning afterwards left a window where a valid
  callback hit a run still reporting `AWAITING_APPROVAL` and was refused. See `authorize`.
"""

import asyncio
import logging
from dataclasses import dataclass

from langgraph.graph.state import CompiledStateGraph

from incident_agent_api.agent.graph import resume_command, thread_config
from incident_agent_api.telemetry.engine import TelemetryEngine
from incident_agent_api.telemetry.state_machine import IncidentRun
from tripleten_contracts import IncidentState, ScenarioId

logger = logging.getLogger("incident-agent-api")


@dataclass(frozen=True)
class AuthorizationOutcome:
    """What one `/authorize` call produced: the state it moved the run to, and the job it sent."""

    state: IncidentState
    job_id: str | None


class RunNotPausedError(RuntimeError):
    """Raised when an authorization arrives for a graph that is not waiting at the gate.

    The failure this prevents is subtle and important: without it, an `/authorize` for a run
    that had already been authorized (or had never reached the gate) would resume a graph from
    whatever checkpoint existed and could dispatch a second job. Authentication on the callback
    guards the other direction; this guards this one.
    """

    def __init__(self, incident_id: str, next_nodes: tuple[str, ...]) -> None:
        super().__init__(f"incident {incident_id} is not awaiting approval (next: {next_nodes or 'nothing'})")
        self.incident_id = incident_id
        self.next_nodes = next_nodes


class Orchestrator:
    """Runs the agent graph for one incident at a time and keeps the engine in step."""

    def __init__(self, engine: TelemetryEngine, graph: CompiledStateGraph) -> None:
        self._engine = engine
        self._graph = graph
        self._run_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Trigger → the gate
    # ------------------------------------------------------------------

    def start_run(self, run: IncidentRun) -> None:
        """Launches the reasoning chain in the background and returns immediately.

        `/trigger` must answer with `CRITICAL_OUTAGE` (or `EXPLOIT_INTERCEPTED`) straight away —
        that is the contract, and it is also the demo: the viewer watches the outage land, then
        watches the agent reason about it over SSE, then sees it stop. Running the graph inline
        would collapse all of that into one silent request and the War Room would jump from
        healthy to awaiting-approval with nothing in between.
        """
        self._run_task = asyncio.create_task(
            self._run_until_gate(run), name=f"agent-run-{run.incident_id}"
        )

    async def _run_until_gate(self, run: IncidentRun) -> None:
        """Drives the graph to its interrupt, then moves the engine to AWAITING_APPROVAL."""
        try:
            await self._graph.ainvoke(
                {
                    "incident_id": run.incident_id,
                    "thread_id": run.thread_id,
                    "scenario_id": run.scenario_id.value,
                    "step": 0,
                },
                thread_config(run.thread_id),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # A reasoning chain that dies must not strand the engine mid-run with no way
            # forward. The run stays where it is and `/reset` remains available, which is the
            # same recovery path every other failure uses.
            logger.exception("Agent run for %s failed before the approval gate", run.incident_id)
            return

        if not await self._is_paused(run.thread_id):  # pragma: no cover - the gate always pauses
            logger.error("Graph for %s completed without pausing at the approval gate", run.incident_id)
            return

        # Only now, and only if the run is still the one in flight: a Master Reset during the
        # reasoning chain must not resurrect the incident it cleared.
        current = self._engine.machine.run
        if current is None or current.incident_id != run.incident_id:
            logger.info("Run %s was cleared before reaching the gate; not transitioning", run.incident_id)
            return

        self._engine.machine.transition(IncidentState.AWAITING_APPROVAL)
        self._engine.tick()
        logger.info("Incident %s paused at AWAITING_APPROVAL", run.incident_id)

    # ------------------------------------------------------------------
    # The gate → dispatch or rejection
    # ------------------------------------------------------------------

    async def authorize(self, run: IncidentRun, approved: bool) -> "AuthorizationOutcome":
        """Resumes the paused graph with a decision and reports what the decision produced.

        Returns the state *this call* moved the run to, not whatever the run's state is by the
        time the response serializes. Those differ now that the engine transitions before the
        graph dispatches: the worker can consume the job, execute it, and call back while this
        coroutine is still inside `ainvoke`, so reading the live state afterwards can report
        `RECOVERING` from a call whose outcome was `EXECUTING`. The telemetry spec documents
        `/authorize` as answering `EXECUTING`, and a caller wants to know what its own request
        did rather than what happened next.

        `job_id` is None on rejection, because nothing was dispatched — the assertion the HITL
        negative tests make against the real queue depth.
        """
        if not await self._is_paused(run.thread_id):
            snapshot = await self._graph.aget_state(thread_config(run.thread_id))
            raise RunNotPausedError(run.incident_id, tuple(snapshot.next))

        if not approved:
            await self._graph.ainvoke(resume_command(False), thread_config(run.thread_id))
            self._engine.machine.transition(IncidentState.REJECTED)
            self._engine.tick()
            return AuthorizationOutcome(state=IncidentState.REJECTED, job_id=None)

        # EXECUTING *before* the graph resumes, and the ordering is a fix for a real race rather
        # than a preference. The `dispatch` node publishes to `remediation-jobs` and the worker
        # polls every two seconds, so it can consume the job, run it, and call back while this
        # coroutine is still inside `ainvoke`. Transitioning afterwards left a window in which a
        # perfectly valid callback arrived at a run still reporting AWAITING_APPROVAL and was
        # refused with 409 — the job was done, the report was dropped, and the run sat in
        # EXECUTING until reset. Observed against the live stack, not hypothesised.
        #
        # The HITL invariant is untouched: this line runs only inside /authorize, after an
        # explicit human decision. The run genuinely *is* executing from the moment that decision
        # is accepted.
        self._engine.machine.transition(IncidentState.EXECUTING)
        self._engine.tick()

        try:
            result = await self._graph.ainvoke(resume_command(True), thread_config(run.thread_id))
        except Exception as err:
            # The authorization was accepted and the dispatch failed, which is a failed run and
            # not a run still awaiting approval. FAILED is reachable from EXECUTING; going back
            # to AWAITING_APPROVAL is not, and pretending the click never happened would be a
            # lie the state machine is built to prevent.
            logger.exception("Dispatch failed for %s after authorization", run.incident_id)
            self._fail_if_still_executing(run, err)
            raise

        return AuthorizationOutcome(state=IncidentState.EXECUTING, job_id=result.get("job_id"))

    def _fail_if_still_executing(self, run: IncidentRun, err: Exception) -> None:
        """Marks a run FAILED, but only while it is still the executing run.

        The guard exists because of the same race the `transition(EXECUTING)` above was moved to
        fix, seen from the other side. `dispatch` publishes to `remediation-jobs` inside `ainvoke`,
        and the worker can consume the job, finish it and call back before `ainvoke` returns — so by
        the time a *later* failure inside `ainvoke` is caught, the run may already be `RECOVERING`,
        `SECURITY_CONTAINED`, or cleared entirely by a completed decay.

        `FAILED` is reachable only from `EXECUTING`. Transitioning unconditionally therefore raised
        `IllegalTransitionError` from inside this `except` block, which replaced the real exception
        with a misleading one and answered `/authorize` with a 500 for a run that had in fact
        succeeded. Skipping the transition is right in that case: the worker's report is the more
        authoritative account of what happened, and it already landed.
        """
        current = self._engine.machine.run
        if current is None or current.incident_id != run.incident_id:
            logger.info("Run %s was already cleared; not marking it failed", run.incident_id)
            return
        if current.state is not IncidentState.EXECUTING:
            logger.info(
                "Run %s already moved to %s before the dispatch error surfaced; leaving it there",
                run.incident_id,
                current.state.value,
            )
            return

        run.error = f"{type(err).__name__}: {err}"
        self._engine.machine.transition(IncidentState.FAILED)
        self._engine.tick()

    # ------------------------------------------------------------------
    # Worker callback → recovery, containment, or failure
    # ------------------------------------------------------------------

    def complete(self, run: IncidentRun, succeeded: bool, error: str | None = None) -> IncidentState:
        """Applies a worker outcome to the engine and returns the resulting state.

        The three-way split the telemetry spec fixes: a successful outage remediation decays
        back to baseline, a successful Scenario 4 containment lands in `SECURITY_CONTAINED` with
        no decay because nothing ever left baseline, and any failure lands in `FAILED`.
        """
        if not succeeded:
            run.error = error
            self._engine.machine.transition(IncidentState.FAILED)
        elif run.scenario_id is ScenarioId.PROMPT_INJECTION:
            self._engine.machine.transition(IncidentState.SECURITY_CONTAINED)
        else:
            self._engine.begin_recovery()

        self._engine.tick()
        return self._engine.machine.state

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    async def _is_paused(self, thread_id: str) -> bool:
        """True when the graph for a thread is stopped at the approval gate."""
        snapshot = await self._graph.aget_state(thread_config(thread_id))
        return "await_approval" in tuple(snapshot.next)

    async def plan_for(self, thread_id: str) -> dict | None:
        """Returns the drafted plan from the checkpoint, for the snapshot endpoint and tests."""
        snapshot = await self._graph.aget_state(thread_config(thread_id))
        values = snapshot.values or {}
        plan = values.get("plan")
        return plan if isinstance(plan, dict) else None

    async def cancel_run(self, thread_id: str | None = None) -> None:
        """Cancels an in-flight reasoning chain and drops its checkpoint, used by `/reset`.

        Pruning matters because every trigger mints a fresh `thread_id` and nothing else ever
        deletes its rows: `checkpoints`, `checkpoint_blobs`, and `checkpoint_writes` would grow
        for the life of the deployment, which on an unattended kiosk cycling scenarios is
        unbounded. A reset is exactly the point at which the run's state stops being needed.
        """
        task = self._run_task
        self._run_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Cancelling the agent run raised")

        if thread_id is None:
            return
        try:
            await self._graph.checkpointer.adelete_thread(thread_id)  # type: ignore[union-attr]
        except Exception:
            # Best-effort. A checkpoint that outlives its run costs disk; a /reset that failed
            # because of it would strand the demo, which costs the demo.
            logger.warning("Could not prune checkpoint thread %s", thread_id, exc_info=True)
