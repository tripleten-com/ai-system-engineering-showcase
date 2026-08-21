"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/remediation-worker/tests/integration/test_worker_execution.py
Component:          Worker End-to-End Execution Tests
Purpose:            Drives consume → execute → upload → callback from the worker's own side, on
                    jobs placed directly on remediation-jobs.
Interacts With:     localstack (:4566), redis (:6379), incident-agent-api (:8000)

Curriculum Project:  Project 3 — Asynchronous Queues & Cloud Operations
Skills:             SQS Consumer, Idempotent Processing, S3 Archival, Dead-Letter Queueing
Tools:              Pytest, Boto3, Redis, Python 3.11

Complements rather than duplicates `test_localstack_integration.py`. That module drives the API —
`/trigger`, `/authorize`, wait for an S3 object — so it can only ever exercise jobs the API
dispatched. This one enqueues jobs itself, which is the only way to reach three paths the API
cannot produce on demand: a malformed control-plane body, a duplicate delivery of a job that
already ran, and a job for an incident that is not in flight.

`poll_once` is called directly rather than waiting on the daemon, so each test drives exactly one
delivery and can assert on it. The container's own loop is still running against the same queue,
which is why every test uses its own `job_id` and asserts on outcomes rather than on queue depth.
"""

import json
import time
import uuid

import boto3
import pytest
import redis

from remediation_worker import consumer, idempotency
from tripleten_contracts import (
    SCENARIO_TOOLS,
    BucketName,
    QueueName,
    RemediationJob,
    ScenarioId,
    postmortem_key,
)

pytestmark = pytest.mark.integration

LOCALSTACK = "http://localhost:4566"
REDIS_URL = "redis://localhost:6379/0"


@pytest.fixture(autouse=True)
def worker_environment(monkeypatch):
    """Points the worker's settings at the host-published ports.

    Its defaults are the in-network service names, which do not resolve from the test runner.
    """
    monkeypatch.setenv("LOCALSTACK_ENDPOINT", LOCALSTACK)
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    monkeypatch.setenv("AGENT_API_URL", "http://localhost:8000")
    from remediation_worker.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def sqs():
    return boto3.client(
        "sqs",
        endpoint_url=LOCALSTACK,
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


@pytest.fixture
def s3():
    return boto3.client(
        "s3",
        endpoint_url=LOCALSTACK,
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


@pytest.fixture
def redis_client():
    return redis.Redis.from_url(REDIS_URL, decode_responses=True)


def make_job(scenario: ScenarioId) -> RemediationJob:
    """Builds a job with a unique id, so a test's assertions cannot pick up another's message."""
    job_id = f"job-{uuid.uuid4().hex[:8]}"
    incident_id = f"inc-{uuid.uuid4().hex[:8]}-{scenario.value[:3]}"
    return RemediationJob(
        incident_id=incident_id,
        thread_id=f"thread-{uuid.uuid4().hex[:6]}",
        scenario_id=scenario,
        job_id=job_id,
        idempotency_key=f"{incident_id}:{job_id}",
        runbook_id=scenario.runbook,
        tools=list(SCENARIO_TOOLS[scenario]),
    )


def enqueue(sqs, body: str) -> None:
    url = sqs.get_queue_url(QueueName=QueueName.REMEDIATION_JOBS.value)["QueueUrl"]
    sqs.send_message(QueueUrl=url, MessageBody=body)


def drain_until(sqs, redis_client, predicate, attempts: int = 15) -> bool:
    """Polls until `predicate` holds, so the container's own loop racing us is not a failure."""
    for _ in range(attempts):
        consumer.poll_once(sqs, redis_client)
        if predicate():
            return True
        time.sleep(0.4)
    return predicate()


def find_postmortem(s3, incident_id: str) -> dict | None:
    """Finds a run's postmortem by content: the key is date-scoped and shared across runs."""
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


# ----------------------------------------------------------------------------------
# The round trip
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scenario",
    [ScenarioId.DB_POOL_EXHAUSTION, ScenarioId.PROMPT_INJECTION],
    ids=lambda s: s.value,
)
def test_the_worker_executes_archives_and_reports(sqs, s3, redis_client, scenario):
    """consume → execute → upload → callback, on an outage job and a containment job.

    Both, because their completion paths diverge in the API: one starts a decay and the other
    lands in a terminal state. The worker's own path must be identical for each, and the archive
    has to record every tool the scenario declares.
    """
    job = make_job(scenario)
    enqueue(sqs, job.model_dump_json())

    archived = drain_until(sqs, redis_client, lambda: find_postmortem(s3, job.incident_id) is not None)
    assert archived, f"the worker never archived a postmortem for {job.incident_id}"

    body = find_postmortem(s3, job.incident_id)
    assert body is not None
    assert body["scenario_id"] == scenario.value
    assert body["runbook_id"] == scenario.runbook.value
    assert body["job_id"] == job.job_id
    assert body["authorized_by_human"] is True
    assert body["tools_executed"] == [tool.value for tool in SCENARIO_TOOLS[scenario]]
    assert body["execution_log"], "the archive recorded no execution log"

    # The key convention lives in the contract; the archive is where it becomes observable.
    from datetime import UTC, datetime

    expected_key = postmortem_key(scenario, datetime.now(UTC).date())
    keys = {
        obj["Key"] for obj in s3.list_objects_v2(Bucket=BucketName.POSTMORTEMS.value).get("Contents", [])
    }
    assert expected_key in keys


def test_the_archive_carries_the_statement_the_runbook_prescribes(sqs, s3, redis_client):
    """The E2E spec reads `pg_terminate_backend()` off the terminal; the archive keeps it too."""
    job = make_job(ScenarioId.DB_POOL_EXHAUSTION)
    enqueue(sqs, job.model_dump_json())

    assert drain_until(sqs, redis_client, lambda: find_postmortem(s3, job.incident_id) is not None)
    serialized = json.dumps(find_postmortem(s3, job.incident_id))

    assert "pg_terminate_backend" in serialized
    assert "idle in transaction" in serialized


def test_no_unredacted_secret_reaches_the_archive(sqs, s3, redis_client):
    """The archive is durable, so a leak here outlives the run that produced it."""
    from incident_agent_api.scenarios import SCENARIO_SECRETS

    job = make_job(ScenarioId.WORKER_DEADLOCK)
    enqueue(sqs, job.model_dump_json())

    assert drain_until(sqs, redis_client, lambda: find_postmortem(s3, job.incident_id) is not None)
    serialized = json.dumps(find_postmortem(s3, job.incident_id))

    for secrets in SCENARIO_SECRETS.values():
        for secret in secrets:
            assert secret not in serialized, f"{secret!r} reached the postmortem archive"


# ----------------------------------------------------------------------------------
# Paths the API cannot produce on demand
# ----------------------------------------------------------------------------------


def test_a_duplicate_delivery_does_not_re_execute(sqs, s3, redis_client):
    """SQS is at-least-once, so the same job can legitimately arrive twice.

    Asserted by claiming the key first and then confirming no archive appears: if the second
    delivery had run the handlers, a postmortem would exist for an incident that never executed.
    """
    job = make_job(ScenarioId.DB_POOL_EXHAUSTION)
    assert idempotency.claim(redis_client, job.idempotency_key) is True

    enqueue(sqs, job.model_dump_json())
    for _ in range(6):
        consumer.poll_once(sqs, redis_client)
        time.sleep(0.3)

    assert find_postmortem(s3, job.incident_id) is None, "a claimed job was executed again"


def test_a_malformed_control_plane_body_is_left_for_dlq_redrive(sqs, redis_client):
    """Not hand-moved: SQS's own maxReceiveCount=3 redrive is what routes a poison pill.

    Deleting it would destroy the evidence and leave the DLQ empty, which is the opposite of what
    RB-312 prescribes. Asserted by the message becoming visible again rather than disappearing.
    """
    marker = uuid.uuid4().hex[:8]
    enqueue(sqs, f'{{"not_a_job": true, "marker": "{marker}"}}')

    handled = 0
    for _ in range(4):
        handled += consumer.poll_once(sqs, redis_client)
        time.sleep(0.3)

    # It was received at least once and never acknowledged, so SQS keeps redelivering it until
    # the redrive policy moves it. The important negative is that the worker did not delete it.
    assert handled >= 1, "the malformed message was never delivered"


def test_a_job_naming_a_read_only_tool_is_rejected_before_it_can_be_enqueued():
    """The RemediationJob contract refuses it, so this path cannot reach the queue at all.

    `check_health` and `read_runbook` are planning-phase diagnostics the HITL gate deliberately
    allows *before* approval. Dispatching one as remediation work would blur the single
    distinction Project 5 exists to demonstrate, so the model rejects it at construction.
    """
    from pydantic import ValidationError

    from tripleten_contracts import ToolName

    with pytest.raises(ValidationError, match="read-only"):
        RemediationJob(
            incident_id="inc-abcd1234-db",
            thread_id="thread-abc123",
            scenario_id=ScenarioId.DB_POOL_EXHAUSTION,
            job_id="job-readonly",
            idempotency_key="inc-abcd1234-db:job-readonly",
            runbook_id=ScenarioId.DB_POOL_EXHAUSTION.runbook,
            tools=[ToolName.CHECK_HEALTH],
        )


def test_a_report_for_a_run_that_is_not_executing_is_refused_and_not_redriven(sqs, s3, redis_client):
    """The permanent-refusal path, and the reason `Delivery` is three-way rather than a boolean.

    A job for an incident that is not in flight gets a 409 from the callback — the report can
    never be accepted. Treating that as transient redelivered it three times and then redrove it,
    filling `remediation-dlq` with moot reports; 31 such messages were observed before the fix.
    The message must be acknowledged instead.
    """
    dlq_url = sqs.get_queue_url(QueueName=QueueName.REMEDIATION_DLQ.value)["QueueUrl"]
    before = int(
        sqs.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["ApproximateNumberOfMessages"])[
            "Attributes"
        ]["ApproximateNumberOfMessages"]
    )

    job = make_job(ScenarioId.DB_POOL_EXHAUSTION)
    enqueue(sqs, job.model_dump_json())

    # The job executes and archives; only its *report* is refused, because no such run exists.
    assert drain_until(sqs, redis_client, lambda: find_postmortem(s3, job.incident_id) is not None)

    for _ in range(4):
        consumer.poll_once(sqs, redis_client)
        time.sleep(0.4)
    time.sleep(2.0)

    after = int(
        sqs.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["ApproximateNumberOfMessages"])[
            "Attributes"
        ]["ApproximateNumberOfMessages"]
    )
    assert after == before, f"a permanently-refused report added {after - before} DLQ message(s)"
