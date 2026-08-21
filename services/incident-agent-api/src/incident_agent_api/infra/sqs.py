"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/infra/sqs.py
Component:          LocalStack SQS Client, Readiness Probe & Control-Plane Producer
Purpose:            Verifies that every required SQS queue and S3 bucket exists, and publishes
                    approved remediation jobs to the control-plane queue.
Interacts With:     localstack (:4566)

Curriculum Project:  Project 3 — Asynchronous Queues & Cloud Operations
Skills:             Boto3 Clients, Bounded Timeouts, Readiness Probing
Tools:              LocalStack, Boto3, Python 3.11
"""

import asyncio
import logging

import boto3
import botocore.config

from incident_agent_api.config import get_settings
from incident_agent_api.constants import REQUIRED_BUCKETS, REQUIRED_QUEUES
from tripleten_contracts import QueueName, RemediationJob

logger = logging.getLogger("incident-agent-api")

# Bounded so a wedged LocalStack cannot stall the health check past its 3s timeout.
BOTO_CONFIG = botocore.config.Config(
    connect_timeout=1,
    read_timeout=2,
    retries={"max_attempts": 1},
)


def client(service: str):
    """Builds a LocalStack-targeted boto3 client with bounded timeouts."""
    settings = get_settings()
    return boto3.client(
        service,
        endpoint_url=settings.localstack_endpoint,
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key.get_secret_value(),
        config=BOTO_CONFIG,
    )


def check_localstack_sync() -> bool:
    """Checks that all required SQS queues and S3 buckets exist in LocalStack.

    Synchronous by design; callers run it via asyncio.to_thread so boto3's blocking
    socket work never occupies the event loop.
    """
    try:
        sqs = client("sqs")
        s3 = client("s3")
        queues = sqs.list_queues().get("QueueUrls", [])
        queue_names = [q.split("/")[-1] for q in queues]
        buckets = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]

        queues_present = all(q in queue_names for q in REQUIRED_QUEUES)
        buckets_present = all(b in buckets for b in REQUIRED_BUCKETS)
        return queues_present and buckets_present
    except Exception as e:
        logger.debug(f"LocalStack sync check exception: {e}")
        return False


def queue_url(sqs, queue_name: str) -> str:
    """Resolves a queue URL by name.

    Resolved per call rather than cached at import: LocalStack can be restarted underneath a
    running API, and a cached URL from a previous container is indistinguishable from a live one
    until a publish silently fails against it.
    """
    return sqs.get_queue_url(QueueName=queue_name)["QueueUrl"]


def publish_remediation_job_sync(job: RemediationJob) -> str:
    """Publishes an approved remediation job to `remediation-jobs`, returning the SQS message id.

    Synchronous, and the async wrapper below is how callers reach it: boto3 blocks on sockets,
    and this runs on the event loop that also serves the SSE stream and the 1-second telemetry
    tick. Blocking it would stall the whole demo's telemetry, not just this request.
    """
    sqs = client("sqs")
    target = queue_url(sqs, QueueName.REMEDIATION_JOBS.value)
    response = sqs.send_message(
        QueueUrl=target,
        MessageBody=job.model_dump_json(),
        MessageAttributes={
            # Readable in the LocalStack console and in a DLQ inspection without parsing the
            # body, which is what makes a stuck message diagnosable.
            "incident_id": {"DataType": "String", "StringValue": job.incident_id},
            "scenario_id": {"DataType": "String", "StringValue": job.scenario_id.value},
            "idempotency_key": {"DataType": "String", "StringValue": job.idempotency_key},
        },
    )
    return str(response["MessageId"])


async def publish_remediation_job(job: RemediationJob) -> str:
    """Publishes an approved remediation job without blocking the event loop."""
    return await asyncio.to_thread(publish_remediation_job_sync, job)


def queue_depth_sync(queue_name: str) -> int:
    """Reads a queue's approximate visible message count.

    "Approximate" is SQS's word and it is honest: the value is eventually consistent, so it is
    right for observing a trend and wrong for asserting an exact number. The contractual gauge
    values come from the chaos profile, not from here.
    """
    sqs = client("sqs")
    attributes = sqs.get_queue_attributes(
        QueueUrl=queue_url(sqs, queue_name),
        AttributeNames=["ApproximateNumberOfMessages"],
    )
    return int(attributes["Attributes"]["ApproximateNumberOfMessages"])


async def queue_depth(queue_name: str) -> int:
    """Reads a queue's approximate visible message count without blocking the event loop."""
    return await asyncio.to_thread(queue_depth_sync, queue_name)
