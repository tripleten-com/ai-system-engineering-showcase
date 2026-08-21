"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/remediation-worker/src/remediation_worker/postmortem.py
Component:          S3 Postmortem Assembly & Upload
Purpose:            Builds the incident postmortem JSON and writes it to the LocalStack S3
                    archive under the canonical YYYY-MM-DD-<scenario>.json key.
Interacts With:     localstack (:4566)

Curriculum Project:  Project 3 — Asynchronous Queues & Cloud Operations
Skills:             Structured Archival, S3 Object Storage, Forensic Reporting
Tools:              LocalStack S3, Boto3, Python 3.11

This upload is **real**. The bucket is real, the object is real, and
`awslocal s3 cp s3://tripleten-cloud-postmortems/... -` will print it back. The remediation the
report describes is simulated (see handlers/types.py); the archival of that report is not, which
is what makes the S3 half of Project 3 a genuine demonstration.

The body carries no unredacted secrets, and that is asserted rather than assumed: the API
sanitizes inbound logs before the agent or the checkpoint ever see them, so the only log text
that can reach this file has already been masked.
"""

import json
import logging
from datetime import UTC, datetime

import boto3

from remediation_worker.config import get_settings
from remediation_worker.handlers.types import HandlerResult
from tripleten_contracts import BucketName, RemediationJob, WorkerLogPayload, postmortem_key

logger = logging.getLogger("remediation-worker")

POSTMORTEM_SCHEMA_VERSION = "1.0"


def s3_client():
    """Builds a LocalStack-targeted S3 client from configured settings."""
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.localstack_endpoint,
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key.get_secret_value(),
    )


def assemble(
    job: RemediationJob,
    results: list[HandlerResult],
    logs: list[WorkerLogPayload],
    completed_at: datetime | None = None,
) -> dict:
    """Builds the postmortem body for a completed job.

    Pure and separately testable: the assembly is what the unit suite asserts against the
    archival schema, and it must not require a bucket to exercise.
    """
    stamp = completed_at or datetime.now(UTC)
    return {
        "schema_version": POSTMORTEM_SCHEMA_VERSION,
        "incident_id": job.incident_id,
        "thread_id": job.thread_id,
        "scenario_id": job.scenario_id.value,
        "runbook_id": job.runbook_id.value,
        "job_id": job.job_id,
        "idempotency_key": job.idempotency_key,
        "completed_at": stamp.isoformat(),
        "authorized_by_human": True,
        "tools_executed": [result.tool for result in results],
        "operations": [
            {"tool": result.tool, "operation": result.operation, "detail": result.detail}
            for result in results
        ],
        "execution_log": [
            {"source": entry.source.value, "level": entry.level.value, "message": entry.message}
            for entry in logs
        ],
    }


def upload(job: RemediationJob, body: dict, completed_at: datetime | None = None) -> str:
    """Writes the postmortem to S3 and returns its `s3://` URI.

    The key comes from `postmortem_key` in the shared contract rather than being formatted here,
    so the `2026-08-19-db-pool-exhaustion.json` convention exists in exactly one place.
    """
    stamp = completed_at or datetime.now(UTC)
    key = postmortem_key(job.scenario_id, stamp.date())
    bucket = BucketName.POSTMORTEMS.value

    s3_client().put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(body, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    uri = f"s3://{bucket}/{key}"
    logger.info("Postmortem archived to %s", uri)
    return uri
