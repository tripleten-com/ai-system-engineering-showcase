"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/tests/unit/test_incident_routes.py
Component:          Incident & Telemetry Route Tests
Purpose:            Verifies the trigger/reset contract, the polling snapshot, and the Prometheus
                    exposition endpoint without requiring any container to be running.
Interacts With:     incident-agent-api (:8000)

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             API Contract Testing, Idempotency, Prometheus Exposition
Tools:              Pytest, FastAPI, HTTPX, Python 3.11
"""

import httpx
import pytest

from incident_agent_api.main import create_app
from incident_agent_api.telemetry.state_machine import IncidentRun
from tripleten_contracts import IncidentState, MetricName, ScenarioId, WorkerLogLevel


def callback_secret() -> str:
    """Reads the secret from the same place the app reads it.

    Not a literal. The conftest sets `CALLBACK_SECRET` with `os.environ.setdefault` so a clean
    offline checkout works, but CI sets it at the job level — so `setdefault` does not win and a
    hardcoded value here sends a token the app does not accept. Every callback assertion then
    fails with 401 instead of exercising the branch it names. Resolved lazily rather than at
    import, because `get_settings` is lru_cached and other modules clear that cache.
    """
    from incident_agent_api.config import get_settings

    return get_settings().callback_secret.get_secret_value()


class StubOrchestrator:
    """Records what the routes asked of the control plane, and advances the engine itself.

    A stub rather than the real orchestrator because these are *route* tests: the HTTP contract
    is what is under test here, and the graph's behaviour is asserted against a real graph in
    test_agent_graph_logic.py. Using the real one would drag a database and a checkpointer into
    the unit tier for no additional coverage.

    It does advance the engine's state machine, because the routes' responses report that state
    and a stub that left it alone would let a broken transition pass unnoticed.
    """

    def __init__(self, engine) -> None:
        self.engine = engine
        self.started: list[str] = []
        self.authorized: list[tuple[str, bool]] = []
        self.completed: list[tuple[str, bool]] = []
        self.cancelled = 0
        self.pruned: list[str | None] = []
        self.paused = True

    def start_run(self, run: IncidentRun) -> None:
        """Records the launch and returns, exactly as the real one does.

        Deliberately does *not* transition: the real orchestrator drives the graph on a
        background task and reaches the gate after `/trigger` has already answered. A stub that
        advanced synchronously here would make `/trigger` report AWAITING_APPROVAL and quietly
        break the contract these tests exist to pin.
        """
        self.started.append(run.incident_id)

    async def authorize(self, run: IncidentRun, approved: bool):
        from incident_agent_api.agent.orchestrator import AuthorizationOutcome, RunNotPausedError

        if not self.paused:
            raise RunNotPausedError(run.incident_id, ())
        # By the time an operator can click, the graph has reached its gate. Modelled here so
        # the authorize tests do not each have to sleep for a background task.
        if run.state is not IncidentState.AWAITING_APPROVAL:
            self.engine.machine.transition(IncidentState.AWAITING_APPROVAL)
        self.authorized.append((run.incident_id, approved))
        self.paused = False
        if approved:
            self.engine.machine.transition(IncidentState.EXECUTING)
            return AuthorizationOutcome(state=IncidentState.EXECUTING, job_id="job-00001")
        self.engine.machine.transition(IncidentState.REJECTED)
        return AuthorizationOutcome(state=IncidentState.REJECTED, job_id=None)

    def complete(self, run: IncidentRun, succeeded: bool, error: str | None = None) -> IncidentState:
        self.completed.append((run.incident_id, succeeded))
        if not succeeded:
            run.error = error
            self.engine.machine.transition(IncidentState.FAILED)
        elif run.scenario_id is ScenarioId.PROMPT_INJECTION:
            self.engine.machine.transition(IncidentState.SECURITY_CONTAINED)
        else:
            self.engine.begin_recovery()
        return self.engine.machine.state

    async def cancel_run(self, thread_id: str | None = None) -> None:
        self.cancelled += 1
        self.pruned.append(thread_id)


class StubWorkload:
    """Records the Scenario 3 control-surface calls without touching LocalStack."""

    def __init__(self) -> None:
        self.deadlocked = 0
        self.recovered = 0
        self.reset_count = 0

    async def simulate_deadlock(self) -> None:
        self.deadlocked += 1

    async def recover(self) -> None:
        self.recovered += 1

    async def reset(self) -> None:
        self.reset_count += 1


@pytest.fixture
def app():
    """A fresh app per test, so each one gets its own engine and state machine."""
    application = create_app()
    application.state.orchestrator = StubOrchestrator(application.state.engine)
    application.state.workload = StubWorkload()
    return application


@pytest.fixture
def orchestrator(app):
    return app.state.orchestrator


@pytest.fixture
def workload(app):
    return app.state.workload


async def authorize(
    client: httpx.AsyncClient,
    incident_id: str,
    thread_id: str,
    scenario: ScenarioId,
    approved: bool = True,
) -> httpx.Response:
    return await client.post(
        "/api/incidents/authorize",
        json={
            "incident_id": incident_id,
            "thread_id": thread_id,
            "scenario_id": scenario.value,
            "approved": approved,
        },
    )


# Sentinel: `None` means "send no header at all", which is a case under test, so it cannot
# double as "use the real secret".
_REAL_SECRET = object()


async def callback(
    client: httpx.AsyncClient,
    incident_id: str,
    body: dict,
    token: str | None | object = _REAL_SECRET,
) -> httpx.Response:
    resolved = callback_secret() if token is _REAL_SECRET else token
    headers = {"Authorization": f"Bearer {resolved}"} if resolved is not None else {}
    return await client.post(f"/api/incidents/{incident_id}/callback", json=body, headers=headers)


def success_body(job_id: str = "job-00001", incident_id: str = "inc") -> dict:
    return {
        "status": "succeeded",
        "job_id": job_id,
        "idempotency_key": f"{incident_id}:{job_id}",
        "postmortem_uri": "s3://tripleten-cloud-postmortems/2026-08-20-db-pool-exhaustion.json",
    }


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client


async def trigger(client: httpx.AsyncClient, scenario: ScenarioId) -> httpx.Response:
    return await client.post("/api/incidents/trigger", json={"scenario_id": scenario.value})


def health_gauge(body: str) -> float:
    """Reads system_health_status out of a Prometheus exposition body."""
    for line in body.splitlines():
        if line.startswith(f"{MetricName.SYSTEM_HEALTH_STATUS.value} "):
            return float(line.split(" ", 1)[1])
    raise AssertionError("system_health_status missing from the exposition")


# ---------------------------------------------------------------------------
# Route surface
# ---------------------------------------------------------------------------


def test_openapi_declares_the_whole_incident_surface(app):
    """The four incident endpoints from telemetry-and-chaos-engine.md §6, and no others."""
    declared = {path for path in app.openapi()["paths"] if path.startswith("/api/incidents")}
    assert declared == {
        "/api/incidents/trigger",
        "/api/incidents/authorize",
        "/api/incidents/{incident_id}/callback",
        "/api/incidents/reset",
    }


# ---------------------------------------------------------------------------
# POST /api/incidents/trigger
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", [s for s in ScenarioId if s.causes_outage])
async def test_triggering_an_outage_scenario_opens_in_critical_outage(client, scenario):
    response = await trigger(client, scenario)
    assert response.status_code == 202
    body = response.json()
    assert body["state"] == IncidentState.CRITICAL_OUTAGE.value
    assert body["scenario_id"] == scenario.value
    assert body["incident_id"].startswith("inc-")
    assert body["thread_id"].startswith("thread-")


async def test_triggering_the_injection_scenario_opens_in_exploit_intercepted(client):
    response = await trigger(client, ScenarioId.PROMPT_INJECTION)
    assert response.status_code == 202
    assert response.json()["state"] == IncidentState.EXPLOIT_INTERCEPTED.value


async def test_an_unknown_scenario_is_rejected_by_the_schema(client):
    response = await client.post("/api/incidents/trigger", json={"scenario_id": "meltdown"})
    assert response.status_code == 422


async def test_a_missing_scenario_is_rejected_by_the_schema(client):
    response = await client.post("/api/incidents/trigger", json={})
    assert response.status_code == 422


async def test_a_second_trigger_conflicts_and_names_the_run_in_flight(client):
    first = await trigger(client, ScenarioId.DB_POOL_EXHAUSTION)
    second = await trigger(client, ScenarioId.WORKER_DEADLOCK)
    assert second.status_code == 409
    assert first.json()["incident_id"] in second.text


async def test_a_conflicting_trigger_does_not_change_the_active_scenario(client):
    await trigger(client, ScenarioId.DB_POOL_EXHAUSTION)
    await trigger(client, ScenarioId.WORKER_DEADLOCK)
    snapshot = (await client.get("/api/telemetry/current")).json()
    assert snapshot["scenario_id"] == ScenarioId.DB_POOL_EXHAUSTION.value


# ---------------------------------------------------------------------------
# POST /api/incidents/reset
# ---------------------------------------------------------------------------


async def test_reset_returns_the_platform_to_healthy(client):
    incident_id = (await trigger(client, ScenarioId.DB_POOL_EXHAUSTION)).json()["incident_id"]
    response = await client.post("/api/incidents/reset", json={"incident_id": incident_id})
    assert response.status_code == 200
    assert response.json()["state"] == IncidentState.HEALTHY.value


async def test_reset_with_no_active_run_is_an_idempotent_no_op(client):
    """A double-click on Master Reset must not produce an error banner."""
    response = await client.post("/api/incidents/reset", json={"incident_id": "inc-abcd-db"})
    assert response.status_code == 200
    assert response.json()["state"] == IncidentState.HEALTHY.value


async def test_reset_rejects_an_identifier_that_is_not_the_active_run(client):
    await trigger(client, ScenarioId.DB_POOL_EXHAUSTION)
    response = await client.post("/api/incidents/reset", json={"incident_id": "inc-9999-db"})
    assert response.status_code == 409


async def test_a_refused_reset_leaves_the_incident_running(client):
    await trigger(client, ScenarioId.DB_POOL_EXHAUSTION)
    await client.post("/api/incidents/reset", json={"incident_id": "inc-9999-db"})
    snapshot = (await client.get("/api/telemetry/current")).json()
    assert snapshot["state"] == IncidentState.CRITICAL_OUTAGE.value


async def test_reset_requires_an_incident_identifier(client):
    response = await client.post("/api/incidents/reset", json={})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/telemetry/current
# ---------------------------------------------------------------------------


async def test_idle_snapshot_reports_no_run(client):
    body = (await client.get("/api/telemetry/current")).json()
    assert body["incident_id"] is None
    assert body["thread_id"] is None
    assert body["scenario_id"] is None
    assert body["state"] == IncidentState.HEALTHY.value
    assert body["infrastructure"]["system_health_status"] == 1


async def test_snapshot_carries_the_documented_field_structure(client):
    body = (await client.get("/api/telemetry/current")).json()
    assert set(body) == {
        "incident_id",
        "thread_id",
        "scenario_id",
        "state",
        "timestamp",
        "golden_signals",
        "infrastructure",
    }
    assert set(body["golden_signals"]) == {
        "requests_per_sec",
        "http_5xx_error_rate_pct",
        "latency_p50_ms",
        "latency_p95_ms",
        "latency_p99_ms",
    }
    assert set(body["infrastructure"]) == {
        "system_health_status",
        "db_pool_utilization_pct",
        "redis_memory_utilization_pct",
        "cache_hit_ratio_pct",
        "sqs_active_queue_depth",
        "dlq_message_count",
        "active_workers_count",
        "security_violations_total",
    }


async def test_snapshot_reflects_the_triggered_run(client):
    triggered = (await trigger(client, ScenarioId.CACHE_THUNDERING_HERD)).json()
    body = (await client.get("/api/telemetry/current")).json()
    assert body["incident_id"] == triggered["incident_id"]
    assert body["thread_id"] == triggered["thread_id"]
    assert body["state"] == IncidentState.CRITICAL_OUTAGE.value


async def test_polling_the_snapshot_never_advances_the_state_machine(client):
    await trigger(client, ScenarioId.DB_POOL_EXHAUSTION)
    states = [(await client.get("/api/telemetry/current")).json()["state"] for _ in range(10)]
    assert set(states) == {IncidentState.CRITICAL_OUTAGE.value}


async def test_injection_run_reports_degraded_not_down(client):
    await trigger(client, ScenarioId.PROMPT_INJECTION)
    body = (await client.get("/api/telemetry/current")).json()
    assert body["infrastructure"]["system_health_status"] == 2
    assert body["infrastructure"]["security_violations_total"] == 1


# ---------------------------------------------------------------------------
# GET /metrics
# ---------------------------------------------------------------------------


async def test_metrics_endpoint_serves_the_prometheus_text_format(client):
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


async def test_metrics_endpoint_exposes_every_canonical_family(client):
    body = (await client.get("/metrics")).text
    for metric in MetricName:
        assert f"# TYPE {metric.value} " in body


async def test_triggering_publishes_the_new_health_status_without_waiting_for_the_next_tick(client):
    """Prometheus scrapes every second; a status flip must not lag a scrape."""
    assert health_gauge((await client.get("/metrics")).text) == 1
    await trigger(client, ScenarioId.DB_POOL_EXHAUSTION)
    assert health_gauge((await client.get("/metrics")).text) == 0


async def test_resetting_publishes_the_restored_health_status_immediately(client):
    incident_id = (await trigger(client, ScenarioId.DB_POOL_EXHAUSTION)).json()["incident_id"]
    await client.post("/api/incidents/reset", json={"incident_id": incident_id})
    assert health_gauge((await client.get("/metrics")).text) == 1


async def test_injection_trigger_publishes_degraded_rather_than_down(client):
    await trigger(client, ScenarioId.PROMPT_INJECTION)
    assert health_gauge((await client.get("/metrics")).text) == 2


# ---------------------------------------------------------------------------
# POST /api/incidents/authorize
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", list(ScenarioId), ids=lambda s: s.value)
async def test_authorizing_moves_the_run_into_executing(client, orchestrator, scenario):
    body = (await trigger(client, scenario)).json()
    response = await authorize(client, body["incident_id"], body["thread_id"], scenario)

    assert response.status_code == 200
    assert response.json()["state"] == IncidentState.EXECUTING.value
    assert response.json()["job_id"] == "job-00001"
    assert response.json()["duplicate"] is False
    assert orchestrator.authorized == [(body["incident_id"], True)]


async def test_rejecting_lands_in_rejected_and_dispatches_nothing(client, orchestrator):
    body = (await trigger(client, ScenarioId.DB_POOL_EXHAUSTION)).json()
    response = await authorize(
        client, body["incident_id"], body["thread_id"], ScenarioId.DB_POOL_EXHAUSTION, approved=False
    )

    assert response.status_code == 200
    assert response.json()["state"] == IncidentState.REJECTED.value
    assert response.json()["job_id"] is None


async def test_authorizing_an_unknown_incident_is_refused(client):
    await trigger(client, ScenarioId.DB_POOL_EXHAUSTION)
    response = await authorize(client, "inc-0000-db", "thread-abc", ScenarioId.DB_POOL_EXHAUSTION)
    assert response.status_code == 409


async def test_authorizing_with_a_foreign_thread_id_is_refused(client, orchestrator):
    """A thread_id from another run would resume the wrong graph."""
    body = (await trigger(client, ScenarioId.DB_POOL_EXHAUSTION)).json()
    response = await authorize(
        client, body["incident_id"], "thread-not-ours", ScenarioId.DB_POOL_EXHAUSTION
    )
    assert response.status_code == 409
    assert orchestrator.authorized == []


async def test_authorizing_with_a_mismatched_scenario_is_refused(client, orchestrator):
    body = (await trigger(client, ScenarioId.DB_POOL_EXHAUSTION)).json()
    response = await authorize(
        client, body["incident_id"], body["thread_id"], ScenarioId.WORKER_DEADLOCK
    )
    assert response.status_code == 409
    assert orchestrator.authorized == []


async def test_authorizing_a_run_that_is_not_paused_is_refused(client, orchestrator):
    """Guards a second dispatch: the graph must actually be waiting at its interrupt."""
    body = (await trigger(client, ScenarioId.DB_POOL_EXHAUSTION)).json()
    orchestrator.paused = False
    response = await authorize(client, body["incident_id"], body["thread_id"], ScenarioId.DB_POOL_EXHAUSTION)
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# POST /api/incidents/{incident_id}/callback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token", [None, "wrong-secret", "", "Bearer-ish"])
async def test_callback_without_a_valid_bearer_token_is_401(client, orchestrator, token):
    """Missing and mismatched are deliberately indistinguishable — both 401, never 403."""
    body = (await trigger(client, ScenarioId.DB_POOL_EXHAUSTION)).json()
    await authorize(client, body["incident_id"], body["thread_id"], ScenarioId.DB_POOL_EXHAUSTION)

    response = await callback(
        client, body["incident_id"], success_body(incident_id=body["incident_id"]), token=token
    )
    assert response.status_code == 401
    assert orchestrator.completed == [], "an unauthenticated callback advanced the run"


async def test_successful_callback_starts_recovery_for_an_outage_scenario(client, orchestrator):
    body = (await trigger(client, ScenarioId.DB_POOL_EXHAUSTION)).json()
    await authorize(client, body["incident_id"], body["thread_id"], ScenarioId.DB_POOL_EXHAUSTION)

    response = await callback(client, body["incident_id"], success_body(incident_id=body["incident_id"]))
    assert response.status_code == 200
    assert response.json()["state"] == IncidentState.RECOVERING.value


async def test_successful_callback_contains_the_security_scenario_without_decay(client):
    body = (await trigger(client, ScenarioId.PROMPT_INJECTION)).json()
    await authorize(client, body["incident_id"], body["thread_id"], ScenarioId.PROMPT_INJECTION)

    response = await callback(client, body["incident_id"], success_body(incident_id=body["incident_id"]))
    assert response.json()["state"] == IncidentState.SECURITY_CONTAINED.value


async def test_failed_callback_lands_in_failed(client):
    body = (await trigger(client, ScenarioId.DB_POOL_EXHAUSTION)).json()
    await authorize(client, body["incident_id"], body["thread_id"], ScenarioId.DB_POOL_EXHAUSTION)

    response = await callback(
        client,
        body["incident_id"],
        {
            "status": "failed",
            "job_id": "job-00001",
            "idempotency_key": f"{body['incident_id']}:job-00001",
            "error": "pg_terminate_backend timed out after 3 retries",
        },
    )
    assert response.status_code == 200
    assert response.json()["state"] == IncidentState.FAILED.value


async def test_callback_cannot_advance_a_run_that_was_never_authorized(client, orchestrator):
    """Authentication is necessary and not sufficient — the HITL bypass case."""
    body = (await trigger(client, ScenarioId.DB_POOL_EXHAUSTION)).json()

    response = await callback(client, body["incident_id"], success_body(incident_id=body["incident_id"]))
    assert response.status_code == 409
    assert orchestrator.completed == []


async def test_a_half_filled_callback_body_is_rejected(client):
    """A succeeded report with no postmortem URI is a contract violation, not a default."""
    body = (await trigger(client, ScenarioId.DB_POOL_EXHAUSTION)).json()
    await authorize(client, body["incident_id"], body["thread_id"], ScenarioId.DB_POOL_EXHAUSTION)

    response = await callback(
        client,
        body["incident_id"],
        {"status": "succeeded", "job_id": "job-00001", "idempotency_key": "k"},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Scenario 3 workload coupling and reset
# ---------------------------------------------------------------------------


async def test_only_the_deadlock_scenario_stalls_the_workload(client, workload):
    """The real consumer pause is Scenario 3's alone; the others leave the queue draining."""
    first = (await trigger(client, ScenarioId.DB_POOL_EXHAUSTION)).json()
    assert workload.deadlocked == 0

    await client.post("/api/incidents/reset", json={"incident_id": first["incident_id"]})
    await trigger(client, ScenarioId.WORKER_DEADLOCK)
    assert workload.deadlocked == 1


async def test_resetting_cancels_the_agent_run_and_restores_the_workload(client, orchestrator, workload):
    body = (await trigger(client, ScenarioId.WORKER_DEADLOCK)).json()
    response = await client.post("/api/incidents/reset", json={"incident_id": body["incident_id"]})

    assert response.status_code == 200
    assert orchestrator.cancelled == 1
    assert workload.reset_count == 1


async def test_resetting_prunes_the_cleared_run_s_checkpoint(client, orchestrator):
    """The thread id must be captured before the reset clears it, or there is nothing to prune.

    Every trigger mints a fresh thread and nothing else deletes its rows, so a reset that
    forgot to pass the id would leave the checkpointer tables growing for the life of the
    deployment.
    """
    body = (await trigger(client, ScenarioId.DB_POOL_EXHAUSTION)).json()

    await client.post("/api/incidents/reset", json={"incident_id": body["incident_id"]})

    assert orchestrator.pruned == [body["thread_id"]]


async def test_the_worker_s_execution_log_is_republished_to_the_stream(client, app):
    """The callback carries the terminal's content; the route has to put it on the bus.

    Without this the worker filled `WorkerCallback.logs`, the postmortem recorded it, and the
    War Room's execution terminal showed only the single dispatch line the graph emitted — the
    scenario-1 E2E assertion on `pg_terminate_backend()` would have failed.
    """
    published: list = []
    app.state.event_bus.publish = lambda payload, incident_id=None: published.append(payload)

    body = (await trigger(client, ScenarioId.DB_POOL_EXHAUSTION)).json()
    await authorize(client, body["incident_id"], body["thread_id"], ScenarioId.DB_POOL_EXHAUSTION)

    payload = success_body(incident_id=body["incident_id"])
    payload["logs"] = [
        {"source": "LocalStack SQS", "level": "INFO", "message": "Consumed job job-00001"},
        {"source": "Worker", "level": "INFO", "message": "pg_terminate_backend() reclaimed 84"},
        {"source": "LocalStack S3", "level": "INFO", "message": "Postmortem archived to s3://b/k.json"},
    ]
    response = await callback(client, body["incident_id"], payload)

    assert response.status_code == 200, response.text
    messages = [entry.message for entry in published if hasattr(entry, "message")]
    assert "pg_terminate_backend() reclaimed 84" in messages
    assert "Postmortem archived to s3://b/k.json" in messages


async def test_a_callback_with_no_logs_is_still_accepted(client, app):
    """`logs` is optional; an empty list must not break the republish loop."""
    body = (await trigger(client, ScenarioId.DB_POOL_EXHAUSTION)).json()
    await authorize(client, body["incident_id"], body["thread_id"], ScenarioId.DB_POOL_EXHAUSTION)

    response = await callback(client, body["incident_id"], success_body(incident_id=body["incident_id"]))
    assert response.status_code == 200


async def test_a_failed_callback_puts_its_error_on_the_execution_terminal(client, app):
    """The worker's error string has to reach the browser, and this is the only channel it can use.

    `ui-wireframe-and-ux.md` §3 asks the FAILED banner to show the state *plus the worker error
    string*, and the SSE contract has no field for it — the snapshot does not carry it either. So a
    failed callback republishes `error` as an ERROR-level worker log line, which is where every other
    line about that job already goes. Without this the War Room reached FAILED and rendered a crimson
    banner with nothing in it.
    """
    published: list = []
    app.state.event_bus.publish = lambda payload, incident_id=None: published.append(payload)

    body = (await trigger(client, ScenarioId.DB_POOL_EXHAUSTION)).json()
    await authorize(client, body["incident_id"], body["thread_id"], ScenarioId.DB_POOL_EXHAUSTION)

    error = "pg_terminate_backend refused: role lacks privileges"
    response = await callback(
        client,
        body["incident_id"],
        {
            "status": "failed",
            "job_id": "job-00009",
            "idempotency_key": f"{body['incident_id']}:job-00009",
            "error": error,
        },
    )

    assert response.status_code == 200, response.text
    errors = [
        entry.message
        for entry in published
        if getattr(entry, "level", None) is WorkerLogLevel.ERROR
    ]
    assert error in errors


async def test_a_successful_callback_publishes_no_error_line(client, app):
    """No spurious ERROR row on the happy path.

    The War Room derives its failure reason from the last ERROR-level worker line, so an error row
    emitted on success would leave a stale diagnosis attached to a run that worked.
    """
    published: list = []
    app.state.event_bus.publish = lambda payload, incident_id=None: published.append(payload)

    body = (await trigger(client, ScenarioId.DB_POOL_EXHAUSTION)).json()
    await authorize(client, body["incident_id"], body["thread_id"], ScenarioId.DB_POOL_EXHAUSTION)

    response = await callback(client, body["incident_id"], success_body(incident_id=body["incident_id"]))

    assert response.status_code == 200
    assert not [entry for entry in published if getattr(entry, "level", None) is WorkerLogLevel.ERROR]
