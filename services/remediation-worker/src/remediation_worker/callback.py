"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/remediation-worker/src/remediation_worker/callback.py
Component:          Authenticated Completion Callback
Purpose:            Reports a job's outcome to POST /api/incidents/{id}/callback with a bearer
                    token, on every path including retry exhaustion.
Interacts With:     incident-agent-api (:8000)

Curriculum Project:  Project 3 — Asynchronous Queues & Cloud Operations
Skills:             Machine-to-Machine Auth, Resilient HTTP, Idempotent Processing
Tools:              HTTPX, Python 3.11

**Exhaustion is not silence.** The single most important property here: after the final failed
attempt the worker still POSTs `status: failed` with an `error`. A job that dies quietly strands
its run in `EXECUTING` forever, because `AWAITING_APPROVAL` and `EXECUTING` have no timeouts —
that is deliberate for the approval gate, and the cost is that the worker owes a report either
way. The UI would otherwise show a spinner with no end state and no explanation.
"""

import logging
from enum import Enum

import httpx

from remediation_worker.config import get_settings
from tripleten_contracts import CallbackStatus, RemediationJob, WorkerCallback, WorkerLogPayload

logger = logging.getLogger("remediation-worker")

REQUEST_TIMEOUT_SECONDS = 10.0

# The callback is the last step of an already-completed job, so a lost response must not lose the
# report. These retries are for the HTTP hop only and are unrelated to the SQS retry budget.
POST_ATTEMPTS = 3
POST_BACKOFF_SECONDS = 1.0


def callback_url(incident_id: str) -> str:
    """Returns the callback endpoint for one incident."""
    return f"{get_settings().agent_api_url.rstrip('/')}/api/incidents/{incident_id}/callback"


def build_success(job: RemediationJob, postmortem_uri: str, logs: list[WorkerLogPayload]) -> WorkerCallback:
    """Builds the success report for a completed job."""
    return WorkerCallback(
        status=CallbackStatus.SUCCEEDED,
        job_id=job.job_id,
        idempotency_key=job.idempotency_key,
        postmortem_uri=postmortem_uri,
        logs=logs,
    )


def build_failure(job: RemediationJob, error: str, logs: list[WorkerLogPayload]) -> WorkerCallback:
    """Builds the failure report sent once the retry budget is exhausted."""
    return WorkerCallback(
        status=CallbackStatus.FAILED,
        job_id=job.job_id,
        idempotency_key=job.idempotency_key,
        error=error,
        logs=logs,
    )


class Delivery(Enum):
    """The three outcomes of trying to report, which the consumer must tell apart.

    Collapsing these into a boolean was a real bug. A `False` covering both "the network
    blipped" and "the API refused this report" made the consumer redeliver a job the API would
    keep refusing, three times, until SQS redrove it to `remediation-dlq` — so a handful of
    reset runs filled the control-plane DLQ with reports that were merely moot, burying any
    genuine poison message among them.
    """

    ACCEPTED = "accepted"
    """2xx. The run advanced; acknowledge the message."""

    REFUSED = "refused"
    """401 or 409. Permanent: redelivery cannot change the answer, so acknowledge and log loudly."""

    UNDELIVERED = "undelivered"
    """Network failure or 5xx after retries. The report is still owed; let SQS redeliver."""


def send(job: RemediationJob, payload: WorkerCallback, client: httpx.Client | None = None) -> Delivery:
    """POSTs the report with `Bearer $CALLBACK_SECRET`, retrying only what retrying can fix.

    A 409 means the run is not in `EXECUTING` — it was reset, or this is a report for a run that
    moved on. A 401 means the shared secret is misconfigured. Neither changes on a retry, and
    neither is the queue's problem, so both come back as `REFUSED` for the consumer to
    acknowledge rather than redrive.
    """
    secret = get_settings().callback_secret.get_secret_value()
    headers = {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}
    url = callback_url(job.incident_id)
    owns_client = client is None
    http = client or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)

    try:
        for attempt in range(1, POST_ATTEMPTS + 1):
            try:
                response = http.post(url, content=payload.model_dump_json(), headers=headers)
            except httpx.HTTPError as err:
                logger.warning("Callback attempt %d for %s failed: %s", attempt, job.job_id, err)
            else:
                if response.is_success:
                    logger.info(
                        "Callback %s for %s accepted: %s",
                        payload.status.value,
                        job.job_id,
                        response.json().get("state", "?"),
                    )
                    return Delivery.ACCEPTED
                if response.status_code in (401, 409):
                    logger.error(
                        "Callback for %s permanently refused with %d; acknowledging the job "
                        "rather than redriving a report that cannot be accepted: %s",
                        job.job_id,
                        response.status_code,
                        response.text,
                    )
                    return Delivery.REFUSED
                logger.warning(
                    "Callback attempt %d for %s returned %d", attempt, job.job_id, response.status_code
                )

            if attempt < POST_ATTEMPTS:
                _sleep(POST_BACKOFF_SECONDS * attempt)
        logger.error("Callback for %s exhausted %d attempts; the run may be stranded", job.job_id, POST_ATTEMPTS)
        return Delivery.UNDELIVERED
    finally:
        if owns_client:
            http.close()


def _sleep(seconds: float) -> None:
    """Blocking sleep, wrapped so the retry tests can patch it without patching `time`."""
    import time

    time.sleep(seconds)
