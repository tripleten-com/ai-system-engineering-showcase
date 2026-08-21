"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/remediation-worker/tests/unit/test_retry_and_dlq.py
Component:          Retry Budget & DLQ Routing Unit Tests
Purpose:            Asserts the canonical 3-delivery budget, the jittered backoff schedule, and
                    that exhaustion still reports rather than going silent.
Interacts With:     None (fake SQS client)

Curriculum Project:  Project 3 — Asynchronous Queues & Cloud Operations
Skills:             Exponential Backoff, Jitter, Dead-Letter Queueing
Tools:              Pytest, Python 3.11
"""

import random

import pytest

from remediation_worker import consumer, retry
from tripleten_contracts import CallbackStatus, RemediationJob, RunbookId, ScenarioId, ToolName


def make_job(job_id: str = "job-00001") -> RemediationJob:
    return RemediationJob(
        incident_id="inc-abcd1234-db",
        thread_id="thread-abc123",
        scenario_id=ScenarioId.DB_POOL_EXHAUSTION,
        job_id=job_id,
        idempotency_key=f"inc-abcd1234-db:{job_id}",
        runbook_id=RunbookId.RB_104,
        tools=[ToolName.FLUSH_CONNECTION_POOL],
    )


class FakeSqs:
    """Records the SQS calls a delivery makes, so the budget can be asserted without LocalStack."""

    def __init__(self, body: str, delivery: int = 1) -> None:
        self.body = body
        self.delivery = delivery
        self.deleted: list[str] = []
        self.visibility: list[int] = []

    def get_queue_url(self, QueueName: str) -> dict:  # noqa: N803 - boto3's parameter name
        return {"QueueUrl": f"http://localstack:4566/000000000000/{QueueName}"}

    def receive_message(self, **_kwargs) -> dict:
        return {
            "Messages": [
                {
                    "Body": self.body,
                    "ReceiptHandle": "receipt-1",
                    "Attributes": {"ApproximateReceiveCount": str(self.delivery)},
                }
            ]
        }

    def delete_message(self, QueueUrl: str, ReceiptHandle: str) -> None:  # noqa: N803
        self.deleted.append(ReceiptHandle)

    def change_message_visibility(self, QueueUrl: str, ReceiptHandle: str, VisibilityTimeout: int) -> None:  # noqa: N803
        self.visibility.append(VisibilityTimeout)


class FakeRedis:
    """An always-granting idempotency store."""

    def __init__(self) -> None:
        self.claims: dict[str, str] = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.claims:
            return None
        self.claims[key] = value
        return True

    def delete(self, key):
        self.claims.pop(key, None)


# ----------------------------------------------------------------------------------
# The schedule itself
# ----------------------------------------------------------------------------------


def test_the_budget_matches_the_queue_redrive_policy():
    """3 deliveries, matching maxReceiveCount=3. The 4th is SQS's redrive, not ours."""
    assert retry.MAX_DELIVERIES == 3
    assert retry.budget_exhausted(1) is False
    assert retry.budget_exhausted(2) is False
    assert retry.budget_exhausted(3) is True


@pytest.mark.parametrize(("delivery", "nominal"), [(1, 2.0), (2, 4.0), (3, 8.0)])
def test_backoff_is_exponential_within_the_jitter_band(delivery: int, nominal: float):
    """2s, 4s, 8s with uniform +/-25%, from coding-standards-and-guide.md §5.3."""
    lower, upper = nominal * 0.75, nominal * 1.25
    for seed in range(50):
        delay = retry.backoff_seconds(delivery, random.Random(seed))
        assert lower <= delay <= upper


def test_jitter_actually_varies():
    """A constant "jitter" would leave a fleet retrying in lockstep — the herd it exists to break."""
    delays = {retry.backoff_seconds(1, random.Random(seed)) for seed in range(20)}
    assert len(delays) > 1


def test_worst_case_elapsed_fits_inside_the_visibility_timeout():
    """The invariant the whole schedule exists to satisfy.

    If the retry budget could outlast the visibility timeout, SQS would redeliver a message that
    is still being worked and two workers would remediate the same incident concurrently.
    """
    assert retry.worst_case_elapsed_seconds() == pytest.approx(17.5)
    assert retry.worst_case_elapsed_seconds() < retry.VISIBILITY_TIMEOUT_SECONDS


def test_backoff_rejects_a_zero_based_delivery():
    with pytest.raises(ValueError, match="1-based"):
        retry.backoff_seconds(0)


# ----------------------------------------------------------------------------------
# How the consumer applies it
# ----------------------------------------------------------------------------------


def test_an_unparseable_body_is_left_for_dlq_redrive(monkeypatch):
    """A poison pill is not hand-moved: SQS's own redrive policy is what routes it.

    Asserted by *absence* of a delete — deleting it would destroy the evidence and the DLQ would
    stay empty, which is the opposite of what RB-312 prescribes.
    """
    sqs = FakeSqs(body="{not json at all", delivery=1)
    monkeypatch.setattr(consumer.callback, "send", lambda *a, **k: consumer.callback.Delivery.ACCEPTED)

    handled = consumer.poll_once(sqs, FakeRedis())

    assert handled == 1
    assert sqs.deleted == [], "a malformed message was deleted instead of being redriven"
    assert sqs.visibility == [0], "the message was not made promptly redeliverable"


def test_a_failing_delivery_within_budget_backs_off_instead_of_reporting(monkeypatch):
    """Attempts 1 and 2 hand the wait to SQS and send no callback."""
    job = make_job()
    sqs = FakeSqs(body=job.model_dump_json(), delivery=1)
    sent: list = []
    monkeypatch.setattr(
        consumer.callback, "send", lambda j, p, **k: sent.append(p) or consumer.callback.Delivery.ACCEPTED
    )
    monkeypatch.setattr(
        consumer, "_execute_handlers", lambda ctx, j: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    consumer.poll_once(sqs, FakeRedis())

    assert sent == [], "a callback was sent while retries remained"
    assert len(sqs.visibility) == 1
    assert 1 <= sqs.visibility[0] <= 3, f"expected the ~2s first backoff, got {sqs.visibility[0]}"
    assert sqs.deleted == []


def test_exhaustion_still_reports_failure(monkeypatch):
    """The property that keeps a run from sitting in EXECUTING forever.

    `AWAITING_APPROVAL` and `EXECUTING` have no timeouts by design, so a worker that dies
    quietly strands the incident with a spinner and no explanation.
    """
    job = make_job()
    sqs = FakeSqs(body=job.model_dump_json(), delivery=retry.MAX_DELIVERIES)
    sent: list = []
    monkeypatch.setattr(
        consumer.callback, "send", lambda j, p, **k: sent.append(p) or consumer.callback.Delivery.ACCEPTED
    )
    monkeypatch.setattr(
        consumer,
        "_execute_handlers",
        lambda ctx, j: (_ for _ in ()).throw(RuntimeError("pg_terminate_backend timed out")),
    )

    consumer.poll_once(sqs, FakeRedis())

    assert len(sent) == 1
    assert sent[0].status is CallbackStatus.FAILED
    assert "pg_terminate_backend timed out" in (sent[0].error or "")
    assert sent[0].postmortem_uri is None


def test_a_failure_releases_the_claim_so_the_retry_can_execute(monkeypatch):
    """A held claim would make the next delivery skip the work while reporting success."""
    job = make_job()
    sqs = FakeSqs(body=job.model_dump_json(), delivery=1)
    redis_client = FakeRedis()
    monkeypatch.setattr(consumer.callback, "send", lambda *a, **k: consumer.callback.Delivery.ACCEPTED)
    monkeypatch.setattr(
        consumer, "_execute_handlers", lambda ctx, j: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    consumer.poll_once(sqs, redis_client)

    assert redis_client.claims == {}, "the idempotency claim survived a failed delivery"


def test_a_duplicate_delivery_is_acknowledged_without_re_executing(monkeypatch):
    """SQS is at-least-once; the second delivery must report, not remediate again."""
    job = make_job()
    sqs = FakeSqs(body=job.model_dump_json(), delivery=2)
    redis_client = FakeRedis()
    redis_client.set(f"worker:job:{job.idempotency_key}", "1")

    executed: list[str] = []
    monkeypatch.setattr(consumer, "_execute_handlers", lambda ctx, j: executed.append("ran") or [])
    monkeypatch.setattr(consumer.callback, "send", lambda *a, **k: consumer.callback.Delivery.ACCEPTED)

    consumer.poll_once(sqs, redis_client)

    assert executed == [], "a duplicate delivery re-ran the remediation"
    assert sqs.deleted == ["receipt-1"]


def test_a_missing_redis_refuses_the_job_rather_than_running_it_unguarded(monkeypatch):
    """The opposite default to the API's idempotency layer, and deliberately so.

    With no lock available, proceeding would run a state-changing remediation with no replay
    protection. Refusing costs a delayed demo; proceeding could double-remediate.
    """
    job = make_job()
    sqs = FakeSqs(body=job.model_dump_json(), delivery=1)
    executed: list[str] = []
    monkeypatch.setattr(consumer, "_execute_handlers", lambda ctx, j: executed.append("ran") or [])
    monkeypatch.setattr(consumer.callback, "send", lambda *a, **k: consumer.callback.Delivery.ACCEPTED)

    consumer.poll_once(sqs, None)

    assert executed == []


def test_a_completed_job_whose_report_is_undelivered_is_redelivered(monkeypatch):
    """Work done but unreported is worse than work repeated: the run cannot escape EXECUTING."""
    job = make_job()
    sqs = FakeSqs(body=job.model_dump_json(), delivery=1)
    redis_client = FakeRedis()
    monkeypatch.setattr(consumer, "_execute_handlers", lambda ctx, j: [])
    monkeypatch.setattr(consumer.postmortem, "upload", lambda *a, **k: "s3://bucket/key.json")
    monkeypatch.setattr(consumer.callback, "send", lambda *a, **k: consumer.callback.Delivery.UNDELIVERED)

    consumer.poll_once(sqs, redis_client)

    assert sqs.deleted == [], "the message was acknowledged despite an unreported outcome"
    assert redis_client.claims == {}, "the claim was not released for the redelivery"
    assert sqs.visibility == [0]


def test_a_successful_job_is_acknowledged_once(monkeypatch):
    job = make_job()
    sqs = FakeSqs(body=job.model_dump_json(), delivery=1)
    monkeypatch.setattr(consumer, "_execute_handlers", lambda ctx, j: [])
    monkeypatch.setattr(consumer.postmortem, "upload", lambda *a, **k: "s3://bucket/key.json")
    monkeypatch.setattr(consumer.callback, "send", lambda *a, **k: consumer.callback.Delivery.ACCEPTED)

    consumer.poll_once(sqs, FakeRedis())

    assert sqs.deleted == ["receipt-1"]
    assert sqs.visibility == []


def test_a_permanently_refused_report_is_acknowledged_rather_than_redriven(monkeypatch):
    """The bug this three-way outcome exists to fix.

    A 409 means the run was reset or was never authorized, so the report can never be accepted.
    Treating that as a transient failure redelivered the job three times and then redrove it to
    `remediation-dlq` — filling the control-plane DLQ with moot reports and burying any genuine
    poison message among them. Observed live: 31 messages from a handful of reset runs.
    """
    job = make_job()
    sqs = FakeSqs(body=job.model_dump_json(), delivery=1)
    redis_client = FakeRedis()
    monkeypatch.setattr(consumer, "_execute_handlers", lambda ctx, j: [])
    monkeypatch.setattr(consumer.postmortem, "upload", lambda *a, **k: "s3://bucket/key.json")
    monkeypatch.setattr(consumer.callback, "send", lambda *a, **k: consumer.callback.Delivery.REFUSED)

    consumer.poll_once(sqs, redis_client)

    assert sqs.deleted == ["receipt-1"], "a permanently refused report was left for redelivery"
    assert sqs.visibility == [], "the message was made redeliverable despite a permanent refusal"


def test_the_three_delivery_outcomes_are_distinct():
    """Guards the enum against being collapsed back into a boolean."""
    from remediation_worker.callback import Delivery

    assert len({Delivery.ACCEPTED, Delivery.REFUSED, Delivery.UNDELIVERED}) == 3
