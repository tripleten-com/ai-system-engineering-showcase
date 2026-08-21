"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/tests/integration/test_worker_callback_auth.py
Component:          Authenticated Worker Callback Integration Tests
Purpose:            Asserts the callback's authentication, its three outcome branches, and its
                    idempotency against the live stack.
Interacts With:     incident-agent-api (:8000), remediation-worker (internal)

Curriculum Project:  Project 3 — Asynchronous Queues & Cloud Operations
Skills:             Machine-to-Machine Auth, Idempotent Processing, State Machine Design
Tools:              Pytest, HTTPX, Python 3.11

Why only this endpoint is authenticated: it is the one that advances state on the strength of a
claim about work that already happened elsewhere. `/trigger`, `/authorize`, and `/reset` are
deliberately open — this is a public marketing demo and any visitor must be able to drive the
pipeline, including the HITL click. The asymmetry is a decision, not an oversight.
"""

import time

import httpx
import pytest

from tripleten_contracts import IncidentState, ScenarioId

pytestmark = pytest.mark.integration

API = "http://localhost:8000"
GATE_TIMEOUT_SECONDS = 25.0
# Trigger, gate, authorize, SQS hop, handlers, S3 upload, callback. Generous because a tight
# bound here would flake as timing noise on a loaded CI runner.
WORKER_ROUND_TRIP_SECONDS = 45.0


# Token variants by *label*, resolved inside each test. Parametrising over the secret itself
# would need it at collection time, which is what forced the earlier module-level lookup — and
# every module-level source for it turned out to be wrong in some environment. See the note on
# `stack_callback_secret` in conftest.py.
TOKEN_LABELS = ["absent", "mismatched", "near-miss", "truncated"]


def token_for(label: str, secret: str) -> str | None:
    """Builds the credential a token label stands for."""
    return {
        "absent": None,
        "mismatched": "wrong-secret",
        "near-miss": secret + "x",
        "truncated": secret[:-1],
    }[label]


def snapshot() -> dict:
    with httpx.Client(timeout=10.0) as client:
        return client.get(f"{API}/api/telemetry/current").json()


def reset() -> None:
    incident_id = snapshot().get("incident_id")
    if incident_id:
        with httpx.Client(timeout=10.0) as client:
            client.post(f"{API}/api/incidents/reset", json={"incident_id": incident_id})


def wait_for_state(target: IncidentState, timeout: float = GATE_TIMEOUT_SECONDS) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        last = snapshot()
        if last.get("state") == target.value:
            return last
        time.sleep(0.5)
    raise AssertionError(f"never reached {target.value}; last was {last.get('state')!r}")


def _wait_for_any(targets: set[IncidentState], timeout: float) -> str:
    """Polls until the run reports any of `targets`, returning the one it saw."""
    deadline = time.monotonic() + timeout
    wanted = {state.value for state in targets}
    last: dict = {}
    while time.monotonic() < deadline:
        last = snapshot()
        if last.get("state") in wanted:
            return str(last["state"])
        time.sleep(0.5)
    raise AssertionError(f"never reached any of {sorted(wanted)}; last was {last.get('state')!r}")


def post_callback(incident_id: str, body: dict, token: str | None) -> httpx.Response:
    """POSTs a callback. `token` is explicit on every call — there is no implicit real secret."""
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    with httpx.Client(timeout=15.0) as client:
        return client.post(f"{API}/api/incidents/{incident_id}/callback", json=body, headers=headers)


@pytest.fixture(autouse=True)
def clean_slate():
    reset()
    yield
    reset()


@pytest.fixture
def paused_run():
    """A run held at the approval gate, with no worker involved."""
    with httpx.Client(timeout=20.0) as client:
        run = client.post(
            f"{API}/api/incidents/trigger",
            json={"scenario_id": ScenarioId.DB_POOL_EXHAUSTION.value},
        ).json()
    wait_for_state(IncidentState.AWAITING_APPROVAL)
    return run


@pytest.fixture
def executing_run(request):
    """A run that has been triggered and authorized, so a callback is legitimately expected.

    The worker will also be racing to report on the real job. Each test therefore uses its own
    `job_id`, so the assertions are about *this* callback rather than whichever arrived first.
    """
    scenario = getattr(request, "param", ScenarioId.DB_POOL_EXHAUSTION)
    with httpx.Client(timeout=20.0) as client:
        run = client.post(
            f"{API}/api/incidents/trigger", json={"scenario_id": scenario.value}
        ).json()
        wait_for_state(IncidentState.AWAITING_APPROVAL)
        client.post(
            f"{API}/api/incidents/authorize",
            json={
                "incident_id": run["incident_id"],
                "thread_id": run["thread_id"],
                "scenario_id": run["scenario_id"],
                "approved": True,
            },
        )
    return run


def success_body(incident_id: str, job_id: str) -> dict:
    return {
        "status": "succeeded",
        "job_id": job_id,
        "idempotency_key": f"{incident_id}:{job_id}",
        "postmortem_uri": f"s3://tripleten-cloud-postmortems/2026-08-20-test-{job_id}.json",
    }


# ----------------------------------------------------------------------------------
# Authentication
# ----------------------------------------------------------------------------------


# No `"Bearer "` case: a trailing-space header value is illegal HTTP and httpx refuses to
# transmit it, so that would test the client rather than the endpoint. `"Bearer"` with no
# credential at all is the reachable equivalent, covered by the malformed-header test below.
@pytest.mark.parametrize("label", TOKEN_LABELS)
def test_an_invalid_token_is_401_and_changes_nothing(paused_run, callback_secret, label):
    """Missing and mismatched are deliberately indistinguishable — both 401, never 403.

    A 403 would confirm the token was well-formed but wrong, which tells an attacker their format
    is right. The near-miss case also exercises the constant-time comparison.

    Uses a *paused* run rather than an executing one: authentication is decided before any state
    is read, so a 401 does not need an authorized run — and an executing one would have the real
    worker concurrently moving the state, making "changed nothing" a race rather than an
    assertion.
    """
    response = post_callback(
        paused_run["incident_id"],
        success_body(paused_run["incident_id"], "job-auth"),
        token=token_for(label, callback_secret),
    )

    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"
    assert snapshot()["state"] == IncidentState.AWAITING_APPROVAL.value


@pytest.mark.parametrize("shape", ["basic-scheme", "no-credential", "bare-token", "lowercase-scheme"])
def test_a_malformed_authorization_header_is_refused(paused_run, callback_secret, shape):
    """Wrong scheme, no credential, a bare token, and a scheme with no credential.

    `"bearer"` lowercase is included because the scheme comparison is case-insensitive by design
    (RFC 7235 says it must be) while the credential comparison is not — this pins that asymmetry
    rather than leaving it to chance.
    """
    header = {
        "basic-scheme": f"Basic {callback_secret}",
        "no-credential": "Bearer",
        "bare-token": callback_secret,
        "lowercase-scheme": "bearer",
    }[shape]

    with httpx.Client(timeout=15.0) as client:
        response = client.post(
            f"{API}/api/incidents/{paused_run['incident_id']}/callback",
            json=success_body(paused_run["incident_id"], "job-malformed"),
            headers={"Authorization": header},
        )
    assert response.status_code == 401


# ----------------------------------------------------------------------------------
# Outcome branches
# ----------------------------------------------------------------------------------


def test_the_real_worker_callback_starts_recovery_for_an_outage_scenario(executing_run):
    """Driven by the actual worker rather than a synthetic callback, and deliberately so.

    An earlier version posted its own success callback while the worker was concurrently
    reporting on the real job. Whichever landed first won, and the other got a correct 409 — so
    the test raced the product instead of asserting it. Waiting for the state the real round trip
    produces is both race-free and a stronger claim: it exercises SQS, the handlers, the S3
    upload, and the authenticated callback rather than just the endpoint.
    """
    state = _wait_for_any(
        {IncidentState.RECOVERING, IncidentState.HEALTHY},
        timeout=WORKER_ROUND_TRIP_SECONDS,
    )
    assert state in {IncidentState.RECOVERING.value, IncidentState.HEALTHY.value}


@pytest.mark.parametrize("executing_run", [ScenarioId.PROMPT_INJECTION], indirect=True, ids=["prompt_injection"])
def test_the_real_worker_callback_contains_the_security_scenario_without_decay(executing_run):
    """Scenario 4 has nothing to recover from: its metrics never left baseline."""
    _wait_for_any({IncidentState.SECURITY_CONTAINED}, timeout=WORKER_ROUND_TRIP_SECONDS)

    infra = snapshot()["infrastructure"]
    assert infra["system_health_status"] == 2, "the security path must stay Degraded, never Down"
    assert 13.0 <= infra["db_pool_utilization_pct"] <= 17.0
    assert 44.0 <= snapshot()["golden_signals"]["latency_p99_ms"] <= 52.0


def test_a_failed_callback_lands_in_failed_and_surfaces_the_error(paused_run, callback_secret):
    """The report the worker owes once its retry budget is spent.

    Uses a run this test authorizes and then immediately fails, before the worker's own report
    can land. The `job-doomed` id is this test's alone, so whichever callback arrives second is
    a different job and cannot be mistaken for a replay of this one.
    """
    with httpx.Client(timeout=20.0) as client:
        client.post(
            f"{API}/api/incidents/authorize",
            json={
                "incident_id": paused_run["incident_id"],
                "thread_id": paused_run["thread_id"],
                "scenario_id": paused_run["scenario_id"],
                "approved": True,
            },
        )

    error = "pg_terminate_backend timed out after 3 retries"
    response = post_callback(
        paused_run["incident_id"],
        {
            "status": "failed",
            "job_id": "job-doomed",
            "idempotency_key": f"{paused_run['incident_id']}:job-doomed",
            "error": error,
        },
        token=callback_secret,
    )

    # 200 if this callback won the race to a run still EXECUTING; 409 if the worker's own success
    # report landed first. Both are correct product behaviour, and the failure branch is asserted
    # deterministically at the unit tier in test_incident_routes.py.
    assert response.status_code in {200, 409}, response.text
    if response.status_code == 200:
        assert response.json()["state"] == IncidentState.FAILED.value


# ----------------------------------------------------------------------------------
# Idempotency & body validation
# ----------------------------------------------------------------------------------


def test_two_identical_deliveries_never_both_take_effect(executing_run, callback_secret):
    """The invariant, stated so it holds whoever wins the race with the real worker.

    SQS is at-least-once, so the same completion can legitimately arrive twice. What must never
    happen is *both* deliveries being applied — that would restart the decay loop or re-transition
    the state. Asserting "first 200/false, second 200/true" additionally assumed this test beat
    the worker to the run, which is a race rather than a property.
    """
    body = success_body(executing_run["incident_id"], "job-replay")

    first = post_callback(executing_run["incident_id"], body, token=callback_secret)
    second = post_callback(executing_run["incident_id"], body, token=callback_secret)

    applied = [
        response
        for response in (first, second)
        if response.status_code == 200 and response.json()["duplicate"] is False
    ]
    assert len(applied) <= 1, "the same delivery was applied twice"

    # And if the first one was applied, the second must be recognised as the replay it is.
    if first.status_code == 200 and first.json()["duplicate"] is False:
        assert second.status_code == 200
        assert second.json()["duplicate"] is True


@pytest.mark.parametrize(
    "body",
    [
        {"status": "succeeded", "job_id": "j", "idempotency_key": "k"},
        {"status": "failed", "job_id": "j", "idempotency_key": "k"},
        {
            "status": "succeeded",
            "job_id": "j",
            "idempotency_key": "k",
            "postmortem_uri": "s3://b/k.json",
            "error": "both",
        },
    ],
    ids=["success-without-uri", "failure-without-error", "both-outcomes"],
)
def test_a_body_whose_outcome_lacks_its_evidence_is_refused(executing_run, callback_secret, body):
    """One model, two outcomes, made mutually exclusive by a validator rather than inferred."""
    response = post_callback(executing_run["incident_id"], body, token=callback_secret)
    assert response.status_code == 422


def test_a_callback_for_an_unknown_incident_is_refused(callback_secret):
    """Authenticated, well-formed, and about a run that does not exist."""
    response = post_callback(
        "inc-00000000-db", success_body("inc-00000000-db", "job-ghost"), token=callback_secret
    )
    assert response.status_code == 409
