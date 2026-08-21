"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/tests/integration/test_hitl_gate.py
Component:          HITL Gate Enforcement — Negative Tests
Purpose:            Proves that nothing dispatches, nothing executes, and nothing auto-advances
                    while a run is paused at AWAITING_APPROVAL.
Interacts With:     incident-agent-api (:8000), localstack (:4566), postgres-vector (:5432)

Curriculum Project:  Project 5 — Autonomous Agent & Human-in-the-Loop
Skills:             HITL Checkpoints, Safety Invariants, Negative Assertions
Tools:              Pytest, HTTPX, Boto3, Python 3.11

**The most important module in this repository.** Project 5's entire claim is that no remediation
tool executes before an explicit human click. Every other test in the suite proves something
happened; these prove something did *not*.

Every assertion here is made against observable reality — the real `remediation-jobs` depth read
back from LocalStack, the real state reported by the API — never against application internals.
A test that inspected a Python flag could pass while a job sat in the queue.

This module is a CI gate. It may never be skipped, xfailed, or made advisory.
"""

import time

import boto3
import httpx
import pytest

from tripleten_contracts import IncidentState, QueueName, ScenarioId

pytestmark = pytest.mark.integration

API = "http://localhost:8000"
LOCALSTACK = "http://localhost:4566"

# The reasoning chain runs on a background task with a deliberate inter-step delay so the War
# Room can animate it. Generous on purpose: a tight bound here would flake as timing noise and
# cost far more to diagnose than the seconds it saves.
GATE_TIMEOUT_SECONDS = 25.0
POLL_INTERVAL_SECONDS = 0.5


def sqs_client():
    return boto3.client(
        "sqs",
        endpoint_url=LOCALSTACK,
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


def queue_depth(name: str) -> int:
    """Reads a queue's visible depth, including in-flight messages.

    Both counts, not just the visible one: a message a consumer has already received is still a
    dispatched job, and asserting only on `ApproximateNumberOfMessages` would let a job that had
    been picked up read as zero.
    """
    client = sqs_client()
    url = client.get_queue_url(QueueName=name)["QueueUrl"]
    attributes = client.get_queue_attributes(
        QueueUrl=url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    return int(attributes["ApproximateNumberOfMessages"]) + int(
        attributes["ApproximateNumberOfMessagesNotVisible"]
    )


def drain(name: str) -> None:
    """Empties a queue so a depth assertion measures this test and not the previous one."""
    client = sqs_client()
    url = client.get_queue_url(QueueName=name)["QueueUrl"]
    client.purge_queue(QueueUrl=url)
    time.sleep(1.0)


def snapshot() -> dict:
    with httpx.Client(timeout=10.0) as client:
        return client.get(f"{API}/api/telemetry/current").json()


def reset(incident_id: str | None = None) -> None:
    """Clears whatever run is in flight, so each test starts from baseline."""
    with httpx.Client(timeout=10.0) as client:
        target = incident_id or snapshot().get("incident_id")
        if target:
            client.post(f"{API}/api/incidents/reset", json={"incident_id": target})


def trigger(scenario: ScenarioId) -> dict:
    with httpx.Client(timeout=10.0) as client:
        response = client.post(f"{API}/api/incidents/trigger", json={"scenario_id": scenario.value})
        assert response.status_code == 202, response.text
        return response.json()


def wait_for_state(target: IncidentState, timeout: float = GATE_TIMEOUT_SECONDS) -> dict:
    """Polls the snapshot until the run reports `target`, or fails with what it saw instead."""
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        last = snapshot()
        if last.get("state") == target.value:
            return last
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError(f"run never reached {target.value}; last state was {last.get('state')!r}")


@pytest.fixture(autouse=True)
def clean_slate():
    """Every test starts with no run in flight and an empty control-plane queue."""
    reset()
    drain(QueueName.REMEDIATION_JOBS.value)
    yield
    reset()


@pytest.fixture
def paused_run(request):
    """Triggers a scenario and holds it at the approval gate."""
    scenario = getattr(request, "param", ScenarioId.DB_POOL_EXHAUSTION)
    run = trigger(scenario)
    wait_for_state(IncidentState.AWAITING_APPROVAL)
    return run


# ----------------------------------------------------------------------------------
# 1. No dispatch before approval
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize("paused_run", list(ScenarioId), indirect=True, ids=lambda s: s.value)
def test_no_job_is_dispatched_while_paused(paused_run):
    """Asserted by polling the real queue, not by inspecting application state."""
    assert queue_depth(QueueName.REMEDIATION_JOBS.value) == 0
    assert snapshot()["state"] == IncidentState.AWAITING_APPROVAL.value


def test_the_control_plane_queue_stays_empty_for_a_sustained_hold(paused_run):
    """Held for several seconds: a delayed dispatch is still a dispatch before the click."""
    for _ in range(6):
        assert queue_depth(QueueName.REMEDIATION_JOBS.value) == 0
        time.sleep(0.5)


# ----------------------------------------------------------------------------------
# 2. No state-changing tool executes before approval
# ----------------------------------------------------------------------------------


def test_the_api_process_has_no_remediation_tool_to_execute():
    """The structural half of the guarantee, asserted from outside the module that makes it.

    `READ_ONLY_DISPATCH` is the only tool-execution path in the API, and it covers exactly the
    two read-only diagnostics. There is no function in this process that performs a remediation,
    so no bug or injected instruction can cause one to run early.
    """
    from incident_agent_api.agent.tools import READ_ONLY_DISPATCH
    from tripleten_contracts import READ_ONLY_TOOLS, REMEDIATION_TOOLS

    assert set(READ_ONLY_DISPATCH) == READ_ONLY_TOOLS
    for tool in REMEDIATION_TOOLS:
        assert tool not in READ_ONLY_DISPATCH, f"{tool.value} is executable in the API process"


def test_read_only_diagnostics_remain_available_before_approval():
    """Asserted separately from the negative case, so the distinction stays explicit.

    `check_health` and `read_runbook` are what the agent needs while it is still deciding, and
    both are reads. Conflating them with the seven state-changing tools would either block the
    agent from planning or quietly widen what may run pre-approval.
    """
    import asyncio

    from incident_agent_api.agent.tools import invoke_read_only_tool

    result = asyncio.run(invoke_read_only_tool(None, "check_health", {"component": "platform"}))
    assert "reachable" in result


@pytest.mark.parametrize("paused_run", list(ScenarioId), indirect=True, ids=lambda s: s.value)
def test_no_postmortem_is_archived_while_paused(paused_run):
    """A postmortem exists only after a worker ran, so its absence is external evidence."""
    s3 = boto3.client(
        "s3",
        endpoint_url=LOCALSTACK,
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    listing = s3.list_objects_v2(Bucket="tripleten-cloud-postmortems")
    before = {obj["Key"] for obj in listing.get("Contents", [])}

    time.sleep(2.0)

    listing = s3.list_objects_v2(Bucket="tripleten-cloud-postmortems")
    after = {obj["Key"] for obj in listing.get("Contents", [])}
    assert after == before, f"a postmortem appeared while paused: {after - before}"


# ----------------------------------------------------------------------------------
# 3. No timeout auto-approves
# ----------------------------------------------------------------------------------


@pytest.mark.slow
def test_a_thirty_second_hold_does_not_auto_approve(paused_run):
    """There is no approval timeout, and this test exists to stop anyone adding one.

    Thirty seconds is longer than every other timeout in the stack — the 30s SQS visibility
    window, the 17.5s retry budget, the 10s worker heartbeat TTL — so a timeout hiding in any of
    those layers would surface here.
    """
    time.sleep(31)

    assert snapshot()["state"] == IncidentState.AWAITING_APPROVAL.value
    assert queue_depth(QueueName.REMEDIATION_JOBS.value) == 0


# ----------------------------------------------------------------------------------
# 4. Rejection dispatches nothing
# ----------------------------------------------------------------------------------


def test_rejection_is_terminal_and_dispatches_nothing(paused_run):
    """REJECTED holds the chaos values until reset, and the queue never sees a job."""
    peak = snapshot()
    assert peak["golden_signals"]["latency_p99_ms"] > 4000

    with httpx.Client(timeout=15.0) as client:
        response = client.post(
            f"{API}/api/incidents/authorize",
            json={
                "incident_id": paused_run["incident_id"],
                "thread_id": paused_run["thread_id"],
                "scenario_id": paused_run["scenario_id"],
                "approved": False,
            },
        )
    assert response.status_code == 200
    assert response.json()["state"] == IncidentState.REJECTED.value
    assert response.json()["job_id"] is None

    assert queue_depth(QueueName.REMEDIATION_JOBS.value) == 0

    # Chaos persists: a declined remediation does not fix anything.
    time.sleep(2.0)
    after = snapshot()
    assert after["state"] == IncidentState.REJECTED.value
    assert after["golden_signals"]["latency_p99_ms"] > 4000
    assert after["infrastructure"]["system_health_status"] == 0


# ----------------------------------------------------------------------------------
# 5. An authenticated callback cannot bypass the gate
# ----------------------------------------------------------------------------------


def test_a_valid_callback_cannot_advance_an_unauthorized_run(paused_run, callback_secret):
    """Authentication is necessary and not sufficient.

    A caller holding the real secret still cannot move a run out of AWAITING_APPROVAL, because
    the endpoint requires the run to be EXECUTING — which only `/authorize` can make it.
    """
    with httpx.Client(timeout=15.0) as client:
        response = client.post(
            f"{API}/api/incidents/{paused_run['incident_id']}/callback",
            headers={"Authorization": f"Bearer {callback_secret}"},
            json={
                "status": "succeeded",
                "job_id": "job-forged",
                "idempotency_key": f"{paused_run['incident_id']}:job-forged",
                "postmortem_uri": "s3://tripleten-cloud-postmortems/forged.json",
            },
        )

    assert response.status_code == 409, response.text
    assert snapshot()["state"] == IncidentState.AWAITING_APPROVAL.value
    assert queue_depth(QueueName.REMEDIATION_JOBS.value) == 0


# ----------------------------------------------------------------------------------
# 6. Scenario 4 containment is gated identically
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize("paused_run", [ScenarioId.PROMPT_INJECTION], indirect=True, ids=["prompt_injection"])
def test_scenario_four_containment_is_equally_gated(paused_run):
    """The auto-rejection of the *injected* call is not authorization of the *containment* call.

    Easy to get wrong: the guardrail already refused something, so it can feel as though the
    security path has had its safety check. It has not — `revoke_session`, `block_ip`, and
    `archive_forensics` are state-changing and wait for the same click as every other scenario.
    """
    assert snapshot()["state"] == IncidentState.AWAITING_APPROVAL.value
    assert queue_depth(QueueName.REMEDIATION_JOBS.value) == 0

    infra = snapshot()["infrastructure"]
    assert infra["security_violations_total"] == 1, "the guardrail did not record the intercept"
    # And it never became an outage, which is the other half of Scenario 4's claim.
    assert infra["system_health_status"] == 2
    assert 13.0 <= infra["db_pool_utilization_pct"] <= 17.0
