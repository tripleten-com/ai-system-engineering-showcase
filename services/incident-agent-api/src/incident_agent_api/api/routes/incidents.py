"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/api/routes/incidents.py
Component:          Incident Lifecycle Endpoints
Purpose:            The four endpoints that drive a run: trigger, authorize, the authenticated
                    worker callback, and reset.
Interacts With:     incident-war-room (:3000), jaeger (:4317)

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             API Contract Design, Idempotency, Distributed Tracing
Tools:              FastAPI, OpenTelemetry, Pydantic 2, Python 3.11
"""

import logging
import secrets

from fastapi import APIRouter, Header, HTTPException, Path, status
from pydantic import BaseModel, Field

from incident_agent_api.agent.orchestrator import RunNotPausedError
from incident_agent_api.api.deps import (
    EngineDep,
    EventBusDep,
    OrchestratorDep,
    RedisDep,
    SettingsDep,
    WorkloadDep,
)
from incident_agent_api.infra import idempotency
from incident_agent_api.infra.otel import get_tracer
from incident_agent_api.telemetry import chaos
from incident_agent_api.telemetry.state_machine import IncidentAlreadyActiveError, UnknownIncidentError
from tripleten_contracts import (
    APPROVAL_PROMPT,
    CallbackStatus,
    IncidentState,
    ScenarioId,
    WorkerCallback,
    WorkerLogLevel,
    WorkerLogPayload,
    WorkerLogSource,
)

logger = logging.getLogger("incident-agent-api")

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


class TriggerRequest(BaseModel):
    """Body of POST /api/incidents/trigger."""

    scenario_id: ScenarioId = Field(description="One of the four canonical demo scenarios")


class TriggerResponse(BaseModel):
    """The identifiers the client must retain for every later call in the run."""

    incident_id: str = Field(description="Run identifier, required by /reset and the SSE stream")
    thread_id: str = Field(description="LangGraph thread identifier, required by /authorize")
    scenario_id: ScenarioId = Field(description="The scenario now in flight")
    state: IncidentState = Field(description="The state the run opened in")


class ResetRequest(BaseModel):
    """Body of POST /api/incidents/reset."""

    incident_id: str = Field(description="The run to clear, as returned by /trigger")


class ResetResponse(BaseModel):
    """Confirmation that the platform is back at baseline."""

    incident_id: str | None = Field(description="Always null: the run has been cleared")
    state: IncidentState = Field(description="Always HEALTHY after a successful reset")


@router.post("/trigger", status_code=status.HTTP_202_ACCEPTED, response_model=TriggerResponse)
async def trigger_incident(
    payload: TriggerRequest,
    engine: EngineDep,
    orchestrator: OrchestratorDep,
    workload: WorkloadDep,
) -> TriggerResponse:
    """Opens an incident run and begins chaos injection for the scenarios that cause an outage."""
    tracer = get_tracer()
    with tracer.start_as_current_span("inject_chaos") as span:
        span.set_attribute("incident.scenario_id", payload.scenario_id.value)
        span.set_attribute("incident.causes_outage", payload.scenario_id.causes_outage)

        try:
            run = engine.trigger(payload.scenario_id)
        except IncidentAlreadyActiveError as err:
            span.set_attribute("incident.rejected_reason", "already_active")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "an incident is already in flight; POST /api/incidents/reset to clear it",
                    "incident_id": err.incident_id,
                },
            ) from err

        span.set_attribute("incident.incident_id", run.incident_id)
        span.set_attribute("incident.thread_id", run.thread_id)
        span.set_attribute("incident.state", run.state.value)

        # One child span per metric family the scenario perturbs. Scenario 4 produces none,
        # which is itself the evidence that the attack never became an outage.
        profile = chaos.PROFILES[payload.scenario_id]
        for field_name, peak in {**profile.ramped, **profile.stepped}.items():
            with tracer.start_as_current_span(f"perturb:{field_name}") as metric_span:
                metric_span.set_attribute("metric.field", field_name)
                metric_span.set_attribute("metric.peak", peak)
                metric_span.set_attribute("metric.ramp_seconds", chaos.ramp_seconds_for(field_name))

        # Publish immediately rather than waiting up to a second for the next tick: Prometheus
        # scrapes at 1s, so a lagging status flip is a visibly stale Grafana panel.
        #
        # Non-fatal on purpose. The run is already open at this point, and the client cannot
        # reset a run whose incident_id it never received — /reset refuses any other id. Letting
        # a publishing failure escape here would strand the incident until the container
        # restarts, so it is logged and the 202 still goes out; the next background tick retries.
        try:
            engine.tick()
        except Exception:
            logger.exception("Publishing the initial sample for %s failed", run.incident_id)

        # Scenario 3's stall is real: the consumer tasks stop draining `customer-jobs` and the
        # producer keeps publishing, so the queue genuinely backs up. The gauge still follows
        # the documented chaos ramp — see infra/workload.py on why those are separate.
        if payload.scenario_id is ScenarioId.WORKER_DEADLOCK:
            await workload.simulate_deadlock()

        # Background, not inline. /trigger owes an immediate 202 carrying CRITICAL_OUTAGE, and
        # the reasoning chain is the thing the viewer is meant to watch arrive over SSE.
        orchestrator.start_run(run)

    return TriggerResponse(
        incident_id=run.incident_id,
        thread_id=run.thread_id,
        scenario_id=run.scenario_id,
        state=run.state,
    )


@router.post("/reset", response_model=ResetResponse)
async def reset_incident(
    payload: ResetRequest,
    engine: EngineDep,
    orchestrator: OrchestratorDep,
    workload: WorkloadDep,
) -> ResetResponse:
    """Clears the run and returns every gauge to baseline. Counters are never reset."""
    # Captured before the reset clears it: the thread id is what identifies the checkpoint to
    # prune, and it is unreachable once the run is gone.
    in_flight = engine.machine.run
    cleared_thread_id = in_flight.thread_id if in_flight else None

    try:
        engine.reset(payload.incident_id)
    except UnknownIncidentError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "that incident is not the run in flight",
                "incident_id": err.incident_id,
            },
        ) from err

    # Guarded for the same reason /trigger guards its tick, and the asymmetry was the bug:
    # the run is already cleared by the time this runs, so a raising tick would return 500 from
    # an operation that fully succeeded. The War Room would show Master Reset as failed while
    # the platform was in fact back at baseline. The next background tick republishes.
    try:
        engine.tick()
    except Exception:
        logger.exception("Publishing the post-reset sample for %s failed", payload.incident_id)

    # After the engine, so a reasoning chain cancelled mid-flight cannot transition a run
    # that no longer exists. The orchestrator re-checks ownership for the same reason.
    await orchestrator.cancel_run(cleared_thread_id)
    await workload.reset()

    logger.info("Incident %s reset to baseline", payload.incident_id)
    return ResetResponse(incident_id=None, state=engine.machine.state)


class AuthorizeRequest(BaseModel):
    """Body of POST /api/incidents/authorize — the one human decision in the whole pipeline."""

    incident_id: str = Field(description="The run being decided, as returned by /trigger")
    thread_id: str = Field(description="The LangGraph thread to resume, as returned by /trigger")
    scenario_id: ScenarioId = Field(description="The scenario in flight; must match the run")
    approved: bool = Field(description="True dispatches the plan; False lands the run in REJECTED")


class AuthorizeResponse(BaseModel):
    """The outcome of the decision, including whether this call was a replay."""

    incident_id: str
    state: IncidentState
    job_id: str | None = Field(description="The dispatched job, or null when rejected")
    duplicate: bool = Field(description="True when this decision had already been recorded")


class CallbackResponse(BaseModel):
    """The outcome of a worker completion callback."""

    incident_id: str
    state: IncidentState
    duplicate: bool = Field(description="True when this delivery had already been applied")


def _require_active_run(engine: EngineDep, incident_id: str):
    """Returns the run in flight, or raises 409 when the id does not own it."""
    run = engine.machine.run
    if run is None or run.incident_id != incident_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "that incident is not the run in flight",
                "incident_id": run.incident_id if run else None,
            },
        )
    return run


@router.post("/authorize", response_model=AuthorizeResponse)
async def authorize_incident(
    payload: AuthorizeRequest,
    engine: EngineDep,
    orchestrator: OrchestratorDep,
    redis_client: RedisDep,
) -> AuthorizeResponse:
    """Resumes the paused agent graph with the SRE's decision.

    This is the only door into `EXECUTING`, and it opens exactly once per run. Everything about
    the endpoint is arranged around that: the identifiers must all three match the run in
    flight, the graph must actually be waiting at its interrupt, and the decision is claimed in
    Redis before it is applied so a double-clicked button cannot enqueue two jobs.
    """
    run = _require_active_run(engine, payload.incident_id)

    # All three identifiers, not just the incident id. A thread_id belonging to another run
    # would resume the wrong graph, and the mismatch is a client bug worth surfacing rather
    # than silently correcting.
    if payload.thread_id != run.thread_id or payload.scenario_id is not run.scenario_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "thread_id or scenario_id does not belong to this incident",
                "incident_id": run.incident_id,
            },
        )

    key = idempotency.authorize_key(run.incident_id, run.thread_id)
    if not await idempotency.claim(redis_client, key):
        logger.info("Duplicate authorize for %s ignored", run.incident_id)
        return AuthorizeResponse(
            incident_id=run.incident_id,
            state=engine.machine.state,
            job_id=None,
            duplicate=True,
        )

    tracer = get_tracer()
    with tracer.start_as_current_span("authorize_remediation") as span:
        span.set_attribute("incident.incident_id", run.incident_id)
        span.set_attribute("incident.approved", payload.approved)
        span.set_attribute("incident.approval_prompt", APPROVAL_PROMPT[run.scenario_id])
        try:
            outcome = await orchestrator.authorize(run, payload.approved)
        except RunNotPausedError as err:
            # Release the claim: nothing happened, so the operator's next click must not be
            # swallowed as a duplicate of an attempt that never took effect.
            await idempotency.release(redis_client, key)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": str(err), "incident_id": run.incident_id},
            ) from err
        except Exception:
            await idempotency.release(redis_client, key)
            raise

        span.set_attribute("incident.state", outcome.state.value)
        if outcome.job_id:
            span.set_attribute("incident.job_id", outcome.job_id)

    # `outcome.state`, not `engine.machine.state`: the worker can complete the job before this
    # response serializes, and reporting RECOVERING from a call whose outcome was EXECUTING
    # would contradict the documented contract and tell the caller about someone else's work.
    return AuthorizeResponse(
        incident_id=run.incident_id,
        state=outcome.state,
        job_id=outcome.job_id,
        duplicate=False,
    )


@router.post("/{incident_id}/callback", response_model=CallbackResponse)
async def worker_callback(
    payload: WorkerCallback,
    engine: EngineDep,
    orchestrator: OrchestratorDep,
    workload: WorkloadDep,
    redis_client: RedisDep,
    settings: SettingsDep,
    bus: EventBusDep,
    incident_id: str = Path(description="The run the worker was remediating"),
    authorization: str | None = Header(default=None),
) -> CallbackResponse:
    """Applies a worker's completion report. The only authenticated endpoint on the service.

    Authenticated because it is the one endpoint that advances state on the strength of a claim
    about work that already happened somewhere else. `/trigger`, `/authorize`, and `/reset` are
    deliberately open — this is a public demo and a visitor has to be able to drive it — but a
    caller who can forge this could move a run to `RECOVERING` without any remediation having
    occurred.

    Authentication is necessary and **not sufficient**: the run must also have been authorized.
    A valid token on a run still sitting at `AWAITING_APPROVAL` is refused, which is the
    "callback cannot bypass the gate" case in the HITL suite.
    """
    _require_callback_token(authorization, settings.callback_secret.get_secret_value())
    run = _require_active_run(engine, incident_id)

    # Idempotency BEFORE the state guard, and the order is the contract. A redelivery arriving
    # after the first one succeeded finds the run already moved on to RECOVERING, so checking
    # the state first would answer 409 to a delivery the telemetry spec says must get 200 with
    # `duplicate: true`. SQS is at-least-once; a duplicate is normal traffic, not an error.
    key = idempotency.callback_key(payload.idempotency_key)
    if not await idempotency.claim(redis_client, key):
        logger.info("Duplicate callback %s ignored", payload.idempotency_key)
        return CallbackResponse(incident_id=run.incident_id, state=engine.machine.state, duplicate=True)

    # A *new* delivery, though, must be for a run that was actually authorized. This is what
    # stops an authenticated caller from advancing a run still sitting at the approval gate —
    # the "callback cannot bypass the gate" case — and it stays after the claim because a
    # first-time delivery for an unauthorized run is a real error rather than a replay.
    if run.state is not IncidentState.EXECUTING:
        # Released so a legitimate later delivery of this same job is not mistaken for a replay
        # of an attempt that was refused.
        await idempotency.release(redis_client, key)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    "this run is not executing; a callback may not advance a run that was "
                    "never authorized"
                ),
                "incident_id": run.incident_id,
                "state": run.state.value,
            },
        )

    # The execution terminal's content. `WorkerCallback.logs` exists precisely so these lines
    # can ride back on the callback rather than needing a worker->API log channel — and without this
    # loop the worker filled the field, the postmortem recorded it, and the War Room never saw
    # any of it. Republished before the state transition so the terminal shows what the worker
    # did *before* the banner announces the outcome.
    _republish_worker_logs(bus, run.incident_id, payload)

    succeeded = payload.status is CallbackStatus.SUCCEEDED
    state = orchestrator.complete(run, succeeded=succeeded, error=payload.error)

    # Scenario 3's real backlog drains only once the approved tools have run, which is what this
    # callback reports. Quarantine first, then restart the consumers — the order RB-312 gives.
    if succeeded and run.scenario_id is ScenarioId.WORKER_DEADLOCK:
        await workload.recover()

    logger.info(
        "Incident %s callback %s -> %s (job %s)",
        run.incident_id,
        payload.status.value,
        state.value,
        payload.job_id,
    )
    return CallbackResponse(incident_id=run.incident_id, state=state, duplicate=False)


def _republish_worker_logs(bus, incident_id: str, payload: WorkerCallback) -> None:
    """Puts each execution-terminal line the worker reported onto the SSE stream.

    Non-fatal, for the same reason every other publish on this path is: the callback has already
    been accepted by the time this runs, and a stream fault must not turn a completed remediation
    into a failed request. A dropped line costs the terminal one row; a raised exception would
    strand the run.
    """
    entries = list(payload.logs)

    # A failure's `error` string is the diagnosis, and the SSE contract has no field for it — so it
    # rides the execution terminal, which is where every other line about this job already goes.
    # Without this the War Room could reach `FAILED` and render a crimson banner with nothing in it,
    # while `ui-wireframe-and-ux.md` §3 asks for the banner *plus the worker error string*. The
    # browser is not entitled to a second endpoint to go and fetch it from.
    if payload.status is CallbackStatus.FAILED and payload.error:
        entries.append(
            WorkerLogPayload(
                source=WorkerLogSource.WORKER,
                level=WorkerLogLevel.ERROR,
                message=payload.error,
            )
        )

    for entry in entries:
        try:
            bus.publish(entry, incident_id)
        except Exception:
            logger.exception("Republishing a worker log line for %s failed", incident_id)


def _require_callback_token(authorization: str | None, expected: str) -> None:
    """Rejects a missing or mismatched bearer token with 401, never 403.

    Indistinguishable on purpose: 403 would confirm that the token was well-formed but wrong,
    which tells an attacker their format is right. `compare_digest` for the same reason — a
    plain `==` short-circuits on the first differing byte and leaks the prefix through timing.
    """
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="a valid Bearer $CALLBACK_SECRET is required",
            headers={"WWW-Authenticate": "Bearer"},
        )
