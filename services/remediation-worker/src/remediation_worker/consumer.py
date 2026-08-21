"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/remediation-worker/src/remediation_worker/consumer.py
Component:          SQS Consumer & Job Executor
Purpose:            Consumes approved remediation jobs, runs their handlers idempotently,
                    archives the postmortem, and reports the outcome on every path.
Interacts With:     localstack (:4566), redis (:6379), incident-agent-api (:8000)

Curriculum Project:  Project 3 — Asynchronous Queues & Cloud Operations
Skills:             SQS Consumer, Idempotent Processing, Dead-Letter Queueing, Retry Budgets
Tools:              LocalStack SQS, Boto3, Redis, Python 3.11

One message's journey, and the ordering is the design:

1. **Parse.** A body that fails `RemediationJob` validation is a poison pill. It is left in place
   with visibility zeroed so SQS's own `maxReceiveCount=3` redrive moves it to `remediation-dlq`
   — letting the queue do what the queue is for, rather than hand-moving it.
2. **Claim.** The idempotency key is claimed in Redis *before* any handler runs. An
   at-least-once redelivery of a job that already executed is acknowledged and deleted without
   re-remediating.
3. **Execute.** Handlers run in the job's declared order.
4. **Archive.** The postmortem goes to S3 once, after all handlers, so it records the whole
   remediation.
5. **Report.** The callback is sent — `succeeded` with the postmortem URI, or `failed` with an
   error once the budget is spent. Never nothing.
6. **Delete.** Only after a successful report, because a deleted message whose report was lost
   is an incident stranded in `EXECUTING` with no way to retry.
"""

import logging
from datetime import UTC, datetime

import boto3
import redis
from pydantic import ValidationError

from remediation_worker import callback, idempotency, postmortem, retry
from remediation_worker.config import get_settings
from remediation_worker.handlers import HandlerResult, ToolContext, UnknownToolError, handler_for
from tripleten_contracts import QueueName, RemediationJob, WorkerLogLevel, WorkerLogSource

logger = logging.getLogger("remediation-worker")

MAX_MESSAGES_PER_POLL = 1
LONG_POLL_WAIT_SECONDS = 1

# Zeroing visibility makes a message immediately redeliverable. Used for a poison pill so its
# receive count reaches maxReceiveCount promptly and SQS redrives it, instead of the message
# sitting invisible for the full 30-second timeout first.
IMMEDIATE_REDELIVERY_SECONDS = 0


def get_sqs_client():
    """Initializes a Boto3 SQS client targeting LocalStack."""
    settings = get_settings()
    return boto3.client(
        "sqs",
        endpoint_url=settings.localstack_endpoint,
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key.get_secret_value(),
    )


def get_redis_client() -> redis.Redis:
    """Initializes the synchronous Redis client used for idempotency claims."""
    return redis.Redis.from_url(get_settings().redis_url, decode_responses=True)


def poll_once(sqs, redis_client: redis.Redis | None = None) -> int:
    """Polls the remediation queue once and processes what it receives.

    Returns the number of messages handled. Queue-URL resolution happens per poll so the worker
    survives LocalStack restarting underneath it.
    """
    queue_url = sqs.get_queue_url(QueueName=get_settings().sqs_remediation_jobs_queue).get("QueueUrl")
    if not queue_url:
        return 0

    response = sqs.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=MAX_MESSAGES_PER_POLL,
        WaitTimeSeconds=LONG_POLL_WAIT_SECONDS,
        AttributeNames=["ApproximateReceiveCount"],
        MessageAttributeNames=["All"],
    )
    messages = response.get("Messages", [])
    for message in messages:
        _process(sqs, queue_url, message, redis_client)
    return len(messages)


def _process(sqs, queue_url: str, message: dict, redis_client: redis.Redis | None) -> None:
    """Runs one message through parse → claim → execute → archive → report → delete."""
    receipt = message["ReceiptHandle"]
    delivery = int(message.get("Attributes", {}).get("ApproximateReceiveCount", "1"))

    try:
        job = RemediationJob.model_validate_json(message.get("Body", ""))
    except ValidationError as err:
        # A malformed control-plane message. Left in place so SQS's redrive policy routes it to
        # remediation-dlq after maxReceiveCount deliveries; there is no incident_id to report
        # against, so no callback is possible or owed.
        logger.error("Unparseable remediation job on delivery %d; leaving for DLQ redrive: %s", delivery, err)
        _make_immediately_redeliverable(sqs, queue_url, receipt)
        return

    logger.info("Received job %s for %s (delivery %d/%d)", job.job_id, job.incident_id, delivery, retry.MAX_DELIVERIES)

    if not idempotency.claim(redis_client, job.idempotency_key):
        # Either a genuine duplicate delivery or a Redis outage. Both mean "do not execute".
        # The message is deleted on a duplicate because the first delivery already reported;
        # leaving it would loop forever against a claim that will not clear.
        logger.info("Job %s already claimed; acknowledging without re-executing", job.idempotency_key)
        _delete(sqs, queue_url, receipt)
        return

    ctx = ToolContext(job=job)
    ctx.log(
        f"Consumed job {job.job_id} from {QueueName.REMEDIATION_JOBS.value} "
        f"(runbook {job.runbook_id.value}, authorized by SRE)",
        source=WorkerLogSource.LOCALSTACK_SQS,
    )

    try:
        results = _execute_handlers(ctx, job)
        # One clock read, threaded into both calls. Defaulting each to its own `datetime.now`
        # let a job finishing at 23:59:59.999 record one date in the body and land under the
        # next day's key — the archive disagreeing with itself in the one artefact whose whole
        # purpose is being the durable record.
        completed_at = datetime.now(UTC)
        body = postmortem.assemble(job, results, ctx.logs, completed_at)
        uri = postmortem.upload(job, body, completed_at)
        ctx.log(f"Postmortem archived to {uri}", source=WorkerLogSource.LOCALSTACK_S3)
        report = callback.build_success(job, uri, ctx.logs)
    except Exception as err:
        _handle_failure(sqs, queue_url, receipt, ctx, job, delivery, err, redis_client)
        return

    outcome = callback.send(job, report)
    if outcome is callback.Delivery.UNDELIVERED:
        # The work is done but the report did not land, and it still could. Releasing the claim
        # and letting SQS redeliver is the only way the run escapes EXECUTING; the handlers are
        # idempotent in effect, so re-running them costs nothing.
        logger.error("Job %s completed but its report is undelivered; releasing for redelivery", job.job_id)
        idempotency.release(redis_client, job.idempotency_key)
        _make_immediately_redeliverable(sqs, queue_url, receipt)
        return

    # ACCEPTED or REFUSED both mean this message is finished with. A refusal is permanent — the
    # run was reset or was never authorized — so redelivering would just refill the
    # control-plane DLQ with reports the API has explicitly declined.
    _delete(sqs, queue_url, receipt)


def _execute_handlers(ctx: ToolContext, job: RemediationJob) -> list[HandlerResult]:
    """Runs each of the job's tools in order, returning their structured results."""
    results: list[HandlerResult] = []
    for tool in job.tools:
        try:
            handler = handler_for(tool)
        except UnknownToolError:
            ctx.log(f"No handler for {tool.value}; aborting remediation", level=WorkerLogLevel.ERROR)
            raise
        results.append(handler(ctx))
    return results


def _handle_failure(
    sqs,
    queue_url: str,
    receipt: str,
    ctx: ToolContext,
    job: RemediationJob,
    delivery: int,
    err: Exception,
    redis_client: redis.Redis | None,
) -> None:
    """Applies the retry budget, and reports failure once it is spent."""
    reason = f"{type(err).__name__}: {err}"
    logger.exception("Job %s failed on delivery %d", job.job_id, delivery)

    # Released either way: on a retry so the next delivery can execute, and on exhaustion so a
    # manual redrive from the DLQ is not silently skipped as a duplicate.
    idempotency.release(redis_client, job.idempotency_key)

    if retry.budget_exhausted(delivery):
        ctx.log(
            f"Retry budget exhausted after {delivery} deliveries: {reason}",
            level=WorkerLogLevel.ERROR,
        )
        # Exhaustion is not silence — without this the run sits in EXECUTING forever.
        callback.send(job, callback.build_failure(job, reason, ctx.logs))
        _make_immediately_redeliverable(sqs, queue_url, receipt)
        return

    delay = retry.backoff_seconds(delivery)
    ctx.log(f"Delivery {delivery} failed ({reason}); retrying in {delay:.1f}s", level=WorkerLogLevel.WARN)
    _set_visibility(sqs, queue_url, receipt, int(round(delay)))


def _set_visibility(sqs, queue_url: str, receipt: str, seconds: int) -> None:
    """Hands the retry wait to SQS instead of blocking the poll loop on it."""
    try:
        sqs.change_message_visibility(QueueUrl=queue_url, ReceiptHandle=receipt, VisibilityTimeout=seconds)
    except Exception:
        logger.exception("Adjusting message visibility to %ds failed", seconds)


def _make_immediately_redeliverable(sqs, queue_url: str, receipt: str) -> None:
    """Zeroes a message's visibility so its next delivery — or DLQ redrive — happens promptly."""
    _set_visibility(sqs, queue_url, receipt, IMMEDIATE_REDELIVERY_SECONDS)


def _delete(sqs, queue_url: str, receipt: str) -> None:
    """Acknowledges a message, removing it from the queue."""
    try:
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
    except Exception:
        logger.exception("Deleting the processed message failed; it will be redelivered")
