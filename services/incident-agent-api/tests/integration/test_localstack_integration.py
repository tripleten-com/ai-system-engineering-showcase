"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/tests/integration/test_localstack_integration.py
Component:          LocalStack SQS & S3 Integration Tests
Purpose:            Asserts the real control-plane round trip: an approved job crosses SQS, the
                    worker runs it, and a postmortem lands in S3 under the canonical key.
Interacts With:     localstack (:4566), incident-agent-api (:8000), remediation-worker (internal)

Curriculum Project:  Project 3 — Asynchronous Queues & Cloud Operations
Skills:             SQS Round Trip, Dead-Letter Queueing, S3 Archival, Redrive Policies
Tools:              Pytest, Boto3, HTTPX, Python 3.11

Everything asserted here is real: real queues, a real redrive policy, a real worker process, and
a real object in a real bucket. The *remediation* those tools describe is simulated — nothing was
broken, so there is nothing to repair — but the plumbing that carries it is not, and this module
is what demonstrates the difference.
"""

import json
import time

import boto3
import httpx
import pytest

from tripleten_contracts import BucketName, QueueName, ScenarioId, postmortem_key

pytestmark = pytest.mark.integration

API = "http://localhost:8000"
LOCALSTACK = "http://localhost:4566"

GATE_TIMEOUT_SECONDS = 25.0
WORKER_TIMEOUT_SECONDS = 40.0


def aws(service: str):
    return boto3.client(
        service,
        endpoint_url=LOCALSTACK,
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


def queue_url(name: str) -> str:
    return aws("sqs").get_queue_url(QueueName=name)["QueueUrl"]


def attributes(name: str) -> dict:
    return aws("sqs").get_queue_attributes(QueueUrl=queue_url(name), AttributeNames=["All"])["Attributes"]


def snapshot() -> dict:
    with httpx.Client(timeout=10.0) as client:
        return client.get(f"{API}/api/telemetry/current").json()


def reset() -> None:
    incident_id = snapshot().get("incident_id")
    if incident_id:
        with httpx.Client(timeout=10.0) as client:
            client.post(f"{API}/api/incidents/reset", json={"incident_id": incident_id})


def wait_for(predicate, timeout: float, description: str):
    """Polls until `predicate` returns a truthy value, or fails naming what was waited for."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.5)
    raise AssertionError(f"timed out after {timeout}s waiting for {description}")


@pytest.fixture(autouse=True)
def clean_slate():
    reset()
    yield
    reset()


# ----------------------------------------------------------------------------------
# 1. Queue provisioning & redrive policy
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "dlq"),
    [
        (QueueName.CUSTOMER_JOBS, QueueName.CUSTOMER_DLQ),
        (QueueName.REMEDIATION_JOBS, QueueName.REMEDIATION_DLQ),
    ],
    ids=lambda q: q.value,
)
def test_each_source_queue_redrives_to_its_own_dlq(source: QueueName, dlq: QueueName):
    """The workload and the control plane each have their own DLQ and never cross."""
    attrs = attributes(source.value)
    policy = json.loads(attrs["RedrivePolicy"])

    assert policy["maxReceiveCount"] == 3 or policy["maxReceiveCount"] == "3"
    assert policy["deadLetterTargetArn"].endswith(f":{dlq.value}")


@pytest.mark.parametrize(
    "source", [QueueName.CUSTOMER_JOBS, QueueName.REMEDIATION_JOBS], ids=lambda q: q.value
)
def test_the_visibility_timeout_exceeds_the_worker_retry_budget(source: QueueName):
    """The invariant that stops SQS redelivering a message that is still being worked."""
    from remediation_worker import retry

    visibility = int(attributes(source.value)["VisibilityTimeout"])
    assert visibility == retry.VISIBILITY_TIMEOUT_SECONDS
    assert visibility > retry.worst_case_elapsed_seconds()


def test_the_postmortem_bucket_exists():
    buckets = {bucket["Name"] for bucket in aws("s3").list_buckets()["Buckets"]}
    assert BucketName.POSTMORTEMS.value in buckets


# ----------------------------------------------------------------------------------
# 2. The full worker round trip
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scenario",
    [ScenarioId.DB_POOL_EXHAUSTION, ScenarioId.PROMPT_INJECTION],
    ids=lambda s: s.value,
)
def test_an_approved_job_crosses_sqs_and_archives_a_postmortem(scenario: ScenarioId):
    """Trigger → gate → authorize → worker → S3, against the live stack.

    Both an outage scenario and the security scenario, because their completion paths diverge:
    one decays back to baseline and the other lands in a terminal state with no decay, and the
    archival step has to work identically on both.
    """
    s3 = aws("s3")

    with httpx.Client(timeout=25.0) as client:
        run = client.post(
            f"{API}/api/incidents/trigger", json={"scenario_id": scenario.value}
        ).json()
        wait_for(
            lambda: snapshot()["state"] == "AWAITING_APPROVAL",
            GATE_TIMEOUT_SECONDS,
            "the run to reach the approval gate",
        )

        # Nothing archived yet: the gate has not opened.
        before = {
            obj["Key"]
            for obj in s3.list_objects_v2(Bucket=BucketName.POSTMORTEMS.value).get("Contents", [])
        }

        authorized = client.post(
            f"{API}/api/incidents/authorize",
            json={
                "incident_id": run["incident_id"],
                "thread_id": run["thread_id"],
                "scenario_id": run["scenario_id"],
                "approved": True,
            },
        )
    assert authorized.status_code == 200, authorized.text
    assert authorized.json()["job_id"], "approval produced no job"

    body = wait_for(
        lambda: _find_postmortem(s3, run["incident_id"]),
        WORKER_TIMEOUT_SECONDS,
        "the worker to archive a postmortem",
    )

    assert body["incident_id"] == run["incident_id"]
    assert body["scenario_id"] == scenario.value
    assert body["runbook_id"] == scenario.runbook.value
    assert body["authorized_by_human"] is True
    assert body["tools_executed"], "the archive records no tools"
    assert body["execution_log"], "the archive records no execution log"

    # The key convention is contractual, and the archive is where it is observable.
    expected_key = postmortem_key(scenario, _today())
    listing = {
        obj["Key"]
        for obj in s3.list_objects_v2(Bucket=BucketName.POSTMORTEMS.value).get("Contents", [])
    }
    assert expected_key in listing, f"expected {expected_key}, bucket holds {sorted(listing - before)}"


def _today():
    from datetime import UTC, datetime

    return datetime.now(UTC).date()


def _find_postmortem(s3, incident_id: str) -> dict | None:
    """Returns the postmortem body for a run, or None if it has not been archived yet.

    Searched by content rather than by key, because the key is date-scoped and shared across runs
    of the same scenario on the same day — a previous run's object would otherwise satisfy the
    wait immediately.
    """
    listing = s3.list_objects_v2(Bucket=BucketName.POSTMORTEMS.value).get("Contents", [])
    for obj in listing:
        raw = s3.get_object(Bucket=BucketName.POSTMORTEMS.value, Key=obj["Key"])["Body"].read()
        try:
            body = json.loads(raw)
        except ValueError:
            continue
        if body.get("incident_id") == incident_id:
            return body
    return None


def test_the_archive_never_contains_an_unredacted_secret():
    """Durable storage, so a leak here outlives the run. Checked across every object present."""
    from incident_agent_api.scenarios import SCENARIO_SECRETS

    s3 = aws("s3")
    listing = s3.list_objects_v2(Bucket=BucketName.POSTMORTEMS.value).get("Contents", [])
    if not listing:
        pytest.skip("no postmortems archived yet on this stack")

    for obj in listing:
        raw = s3.get_object(Bucket=BucketName.POSTMORTEMS.value, Key=obj["Key"])["Body"].read().decode()
        for secrets in SCENARIO_SECRETS.values():
            for secret in secrets:
                assert secret not in raw, f"{secret!r} is stored in {obj['Key']}"


def test_a_healthy_run_adds_nothing_to_the_control_plane_dlq():
    """Measured as a delta, not an absolute depth.

    An absolute assertion would fail on any stack that had ever seen a deliberate failure test,
    and it would also mask the thing worth catching: whether *this* run redrives. The delta is
    the real claim — a successful remediation must add nothing to the DLQ.

    The bug this guards found 31 messages in the control-plane DLQ. The worker treated a
    permanently-refused callback (409, run already reset) as a transient failure and redelivered
    the job until SQS redrove it, so every reset run left three messages behind.
    """
    dlq = QueueName.REMEDIATION_DLQ.value
    before = int(attributes(dlq)["ApproximateNumberOfMessages"])

    with httpx.Client(timeout=25.0) as client:
        run = client.post(
            f"{API}/api/incidents/trigger",
            json={"scenario_id": ScenarioId.DB_POOL_EXHAUSTION.value},
        ).json()
        wait_for(
            lambda: snapshot()["state"] == "AWAITING_APPROVAL",
            GATE_TIMEOUT_SECONDS,
            "the run to reach the approval gate",
        )
        client.post(
            f"{API}/api/incidents/authorize",
            json={
                "incident_id": run["incident_id"],
                "thread_id": run["thread_id"],
                "scenario_id": run["scenario_id"],
                "approved": True,
            },
        )

    wait_for(
        lambda: _find_postmortem(aws("s3"), run["incident_id"]),
        WORKER_TIMEOUT_SECONDS,
        "the worker to finish the job",
    )
    time.sleep(3.0)

    after = int(attributes(dlq)["ApproximateNumberOfMessages"])
    assert after == before, f"a healthy run added {after - before} message(s) to {dlq}"
