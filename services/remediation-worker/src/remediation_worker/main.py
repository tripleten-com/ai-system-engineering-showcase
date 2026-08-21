"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/remediation-worker/src/remediation_worker/main.py
Component:          Remediation Worker Daemon Entrypoint
Purpose:            Owns the daemon loop: refresh the heartbeat, poll the queue, repeat.
                    Wiring only — queue mechanics live in consumer.py.
Interacts With:     redis (:6379), localstack (:4566), incident-agent-api (:8000)

Curriculum Project:  Project 3 — Asynchronous Queues & Cloud Operations
Skills:             Daemon Design, Resilient Polling, Heartbeat Management
Tools:              LocalStack SQS, Redis, Boto3, Python 3.11
"""

import logging
import time

from remediation_worker.config import POLL_INTERVAL_SECONDS, get_settings
from remediation_worker.consumer import get_redis_client as get_job_redis
from remediation_worker.consumer import get_sqs_client, poll_once
from remediation_worker.heartbeat import get_redis_client, update_heartbeat

logger = logging.getLogger("remediation-worker")
logging.basicConfig(level=get_settings().log_level)


def main():
    """Main worker daemon loop: refreshes heartbeat and polls remediation-jobs queue."""
    logger.info("Starting remediation-worker daemon...")
    r = get_redis_client()
    sqs = get_sqs_client()
    # A second client rather than reusing the heartbeat's: the heartbeat must keep working even
    # if the job path is wedged, and sharing one connection would couple the container's
    # liveness signal to whatever the executor is doing.
    job_redis = get_job_redis()

    while True:
        try:
            # Heartbeat first: the container's health check depends on this key, so a
            # wedged queue must never make the worker look dead.
            update_heartbeat(r)

            try:
                poll_once(sqs, job_redis)
            except Exception as sqs_err:
                logger.warning(f"SQS polling retry: {sqs_err}")

        except Exception as e:
            logger.warning(f"Worker iteration exception: {e}")

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
