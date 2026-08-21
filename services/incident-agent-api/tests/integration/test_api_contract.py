"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/tests/integration/test_api_contract.py
Component:          API Route Contract Integration Tests
Purpose:            Covers the endpoint behaviours pinned in telemetry-and-chaos-engine.md §6
                    that no other module exercises.
Interacts With:     incident-agent-api (:8000), localstack (:4566)

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             API Contracts, Idempotency, Schema Conformance
Tools:              Pytest, HTTPX, Boto3, Python 3.11
"""

import time

import boto3
import httpx
import pytest

from tripleten_contracts import EventType, IncidentState, QueueName, ScenarioId

pytestmark = pytest.mark.integration

API = "http://localhost:8000"
LOCALSTACK = "http://localhost:4566"
GATE_TIMEOUT_SECONDS = 25.0


@pytest.fixture
def client():
    with httpx.Client(base_url=API, timeout=25.0) as http_client:
        yield http_client


@pytest.fixture(autouse=True)
def baseline(client):
    """Clears any run left behind by an earlier test, before and after each test."""
    _ensure_baseline(client)
    yield
    _ensure_baseline(client)


def _ensure_baseline(client: httpx.Client) -> None:
    snapshot = client.get("/api/telemetry/current").json()
    if snapshot["incident_id"] is not None:
        client.post("/api/incidents/reset", json={"incident_id": snapshot["incident_id"]})


def _shape(value):
    """Reduces a JSON object to {key: type name}, so structure can be compared without values."""
    return {key: type(inner).__name__ for key, inner in value.items()}


def _wait_for_state(client: httpx.Client, target: IncidentState, timeout: float = GATE_TIMEOUT_SECONDS):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = client.get("/api/telemetry/current").json()
        if last["state"] == target.value:
            return last
        time.sleep(0.5)
    raise AssertionError(f"never reached {target.value}; last was {last['state'] if last else None!r}")


def _remediation_depth() -> int:
    sqs = boto3.client(
        "sqs",
        endpoint_url=LOCALSTACK,
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    url = sqs.get_queue_url(QueueName=QueueName.REMEDIATION_JOBS.value)["QueueUrl"]
    attrs = sqs.get_queue_attributes(
        QueueUrl=url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    return int(attrs["ApproximateNumberOfMessages"]) + int(attrs["ApproximateNumberOfMessagesNotVisible"])


# ----------------------------------------------------------------------------------
# 1. Scenario id validation
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", list(ScenarioId), ids=lambda s: s.value)
def test_every_canonical_scenario_is_accepted(client, scenario):
    response = client.post("/api/incidents/trigger", json={"scenario_id": scenario.value})

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["scenario_id"] == scenario.value
    expected = (
        IncidentState.CRITICAL_OUTAGE if scenario.causes_outage else IncidentState.EXPLOIT_INTERCEPTED
    )
    assert body["state"] == expected.value


@pytest.mark.parametrize("scenario_id", ["", "unknown_scenario", "DB_POOL_EXHAUSTION", None])
def test_a_non_canonical_scenario_is_refused_and_starts_no_run(client, scenario_id):
    """422 from the Pydantic enum, and the platform stays at baseline."""
    response = client.post("/api/incidents/trigger", json={"scenario_id": scenario_id})

    assert response.status_code == 422
    assert client.get("/api/telemetry/current").json()["incident_id"] is None


# ----------------------------------------------------------------------------------
# 2. Duplicate trigger
# ----------------------------------------------------------------------------------


def test_a_second_trigger_is_refused_and_names_the_run_in_flight(client):
    """409 carrying the in-flight id. Only /reset clears it."""
    first = client.post(
        "/api/incidents/trigger", json={"scenario_id": ScenarioId.DB_POOL_EXHAUSTION.value}
    ).json()

    second = client.post(
        "/api/incidents/trigger", json={"scenario_id": ScenarioId.CACHE_THUNDERING_HERD.value}
    )

    assert second.status_code == 409
    assert second.json()["detail"]["incident_id"] == first["incident_id"]
    assert client.get("/api/telemetry/current").json()["scenario_id"] == first["scenario_id"]


# ----------------------------------------------------------------------------------
# 3 & 4. Authorize replay and identifier mismatch
# ----------------------------------------------------------------------------------


def test_a_replayed_authorize_never_enqueues_a_second_job(client):
    """Asserted against the real queue depth, which must not exceed one."""
    run = client.post(
        "/api/incidents/trigger", json={"scenario_id": ScenarioId.WORKER_DEADLOCK.value}
    ).json()
    _wait_for_state(client, IncidentState.AWAITING_APPROVAL)

    payload = {
        "incident_id": run["incident_id"],
        "thread_id": run["thread_id"],
        "scenario_id": run["scenario_id"],
        "approved": True,
    }
    first = client.post("/api/incidents/authorize", json=payload)
    second = client.post("/api/incidents/authorize", json=payload)

    assert first.status_code == 200 and first.json()["duplicate"] is False
    assert second.status_code == 200 and second.json()["duplicate"] is True
    # At most one job: the worker may already have consumed it, which is why in-flight messages
    # are counted too.
    assert _remediation_depth() <= 1


def test_an_authorize_whose_thread_does_not_belong_to_the_run_is_refused(client):
    """A foreign thread_id would resume the wrong graph."""
    run = client.post(
        "/api/incidents/trigger", json={"scenario_id": ScenarioId.DB_POOL_EXHAUSTION.value}
    ).json()
    _wait_for_state(client, IncidentState.AWAITING_APPROVAL)

    response = client.post(
        "/api/incidents/authorize",
        json={
            "incident_id": run["incident_id"],
            "thread_id": "thread-not-ours",
            "scenario_id": run["scenario_id"],
            "approved": True,
        },
    )

    assert response.status_code == 409
    assert client.get("/api/telemetry/current").json()["state"] == IncidentState.AWAITING_APPROVAL.value
    assert _remediation_depth() == 0


# ----------------------------------------------------------------------------------
# 5, 6 & 7. The polling snapshot
# ----------------------------------------------------------------------------------


def test_the_idle_snapshot_reports_nulls_and_live_baseline(client):
    snapshot = client.get("/api/telemetry/current").json()

    assert snapshot["incident_id"] is None
    assert snapshot["thread_id"] is None
    assert snapshot["scenario_id"] is None
    assert snapshot["state"] == IncidentState.HEALTHY.value
    assert 44.0 <= snapshot["golden_signals"]["latency_p99_ms"] <= 52.0


def test_polling_the_snapshot_never_advances_the_state_machine(client):
    """Ten consecutive polls in an incident state leave the run exactly where it was."""
    client.post("/api/incidents/trigger", json={"scenario_id": ScenarioId.DB_POOL_EXHAUSTION.value})
    before = client.get("/api/telemetry/current").json()

    states = [client.get("/api/telemetry/current").json()["state"] for _ in range(10)]

    # The reasoning chain may legitimately move the run from CRITICAL_OUTAGE to
    # AWAITING_APPROVAL while these polls happen; what must not happen is a poll *causing* a
    # transition, so the assertion is that nothing advanced past the approval gate.
    assert set(states) <= {IncidentState.CRITICAL_OUTAGE.value, IncidentState.AWAITING_APPROVAL.value}
    assert client.get("/api/telemetry/current").json()["incident_id"] == before["incident_id"]


def test_snapshot_and_stream_agree_on_shape_during_an_incident(client, read_stream):
    """Rehydration parity (testing-strategy §5.2F case 7).

    A client that loses the stream rebuilds from GET /api/telemetry/current, so the two
    representations of the same sample must be interchangeable. Structure is what is asserted,
    not values: the snapshot and the frame are taken from different ticks, and baseline jitter
    means their numbers legitimately differ by a fraction.
    """
    client.post("/api/incidents/trigger", json={"scenario_id": ScenarioId.DB_POOL_EXHAUSTION.value})

    # Demultiplex. The agent's reasoning chain publishes on this stream too, so reading one frame
    # and assuming it is telemetry only worked while the metrics generator was the sole producer.
    frames = read_stream(count=6).frames
    telemetry = [frame for frame in frames if frame["type"] == EventType.METRICS_UPDATE.value]
    assert telemetry, f"no telemetry frame among {[f['type'] for f in frames]}"

    streamed = telemetry[0]["data"]
    snapshot = client.get("/api/telemetry/current").json()

    assert _shape(streamed["golden_signals"]) == _shape(snapshot["golden_signals"])
    assert _shape(streamed["infrastructure"]) == _shape(snapshot["infrastructure"])
    # The streamed key is `status` and the polled key is `state`; both spellings are contractual
    # and the frontend codes against each verbatim. They must still report the same run state.
    assert streamed["status"] in {snapshot["state"], IncidentState.CRITICAL_OUTAGE.value}


# ----------------------------------------------------------------------------------
# 8. Reset from every state
# ----------------------------------------------------------------------------------


def test_reset_returns_to_baseline_from_the_chaos_states(client):
    """CRITICAL_OUTAGE and AWAITING_APPROVAL, the two states a run passes through unaided."""
    for target in (IncidentState.CRITICAL_OUTAGE, IncidentState.AWAITING_APPROVAL):
        run = client.post(
            "/api/incidents/trigger", json={"scenario_id": ScenarioId.DB_POOL_EXHAUSTION.value}
        ).json()
        if target is IncidentState.AWAITING_APPROVAL:
            _wait_for_state(client, target)

        response = client.post("/api/incidents/reset", json={"incident_id": run["incident_id"]})

        assert response.status_code == 200, f"reset from {target.value} failed: {response.text}"
        assert response.json()["state"] == IncidentState.HEALTHY.value
        snapshot = client.get("/api/telemetry/current").json()
        assert snapshot["incident_id"] is None
        assert snapshot["infrastructure"]["system_health_status"] == 1


def test_reset_returns_to_baseline_from_rejected(client):
    run = client.post(
        "/api/incidents/trigger", json={"scenario_id": ScenarioId.DB_POOL_EXHAUSTION.value}
    ).json()
    _wait_for_state(client, IncidentState.AWAITING_APPROVAL)
    client.post(
        "/api/incidents/authorize",
        json={
            "incident_id": run["incident_id"],
            "thread_id": run["thread_id"],
            "scenario_id": run["scenario_id"],
            "approved": False,
        },
    )
    assert client.get("/api/telemetry/current").json()["state"] == IncidentState.REJECTED.value

    response = client.post("/api/incidents/reset", json={"incident_id": run["incident_id"]})

    assert response.json()["state"] == IncidentState.HEALTHY.value
    snapshot = client.get("/api/telemetry/current").json()
    assert snapshot["infrastructure"]["system_health_status"] == 1
    assert 44.0 <= snapshot["golden_signals"]["latency_p99_ms"] <= 52.0


def test_reset_returns_to_baseline_from_security_contained(client):
    """The Scenario 4 terminal, which holds Degraded until reset returns it to OK."""
    run = client.post(
        "/api/incidents/trigger", json={"scenario_id": ScenarioId.PROMPT_INJECTION.value}
    ).json()
    _wait_for_state(client, IncidentState.AWAITING_APPROVAL)
    client.post(
        "/api/incidents/authorize",
        json={
            "incident_id": run["incident_id"],
            "thread_id": run["thread_id"],
            "scenario_id": run["scenario_id"],
            "approved": True,
        },
    )
    _wait_for_state(client, IncidentState.SECURITY_CONTAINED, timeout=40.0)
    assert client.get("/api/telemetry/current").json()["infrastructure"]["system_health_status"] == 2

    response = client.post("/api/incidents/reset", json={"incident_id": run["incident_id"]})

    assert response.json()["state"] == IncidentState.HEALTHY.value
    assert client.get("/api/telemetry/current").json()["infrastructure"]["system_health_status"] == 1


def test_a_reset_for_a_foreign_incident_is_refused(client):
    run = client.post(
        "/api/incidents/trigger", json={"scenario_id": ScenarioId.DB_POOL_EXHAUSTION.value}
    ).json()

    response = client.post("/api/incidents/reset", json={"incident_id": "inc-00000000-db"})

    assert response.status_code == 409
    assert client.get("/api/telemetry/current").json()["incident_id"] == run["incident_id"]


def test_resetting_twice_is_harmless(client):
    """A double-clicked Master Reset must not error."""
    run = client.post(
        "/api/incidents/trigger", json={"scenario_id": ScenarioId.DB_POOL_EXHAUSTION.value}
    ).json()

    first = client.post("/api/incidents/reset", json={"incident_id": run["incident_id"]})
    second = client.post("/api/incidents/reset", json={"incident_id": run["incident_id"]})

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["state"] == IncidentState.HEALTHY.value
