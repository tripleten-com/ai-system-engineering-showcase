"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/remediation-worker/src/remediation_worker/handlers/queue.py
Component:          RB-312 Poison Message & Consumer Pool Remediation
Purpose:            Executes the approved DLQ quarantine and consumer pool restart.
Interacts With:     localstack (:4566)

Curriculum Project:  Project 3 — Asynchronous Queues & Cloud Operations
Skills:             Dead-Letter Queueing, Idempotent Processing, Runbook Automation
Tools:              Python 3.11
"""

from remediation_worker.handlers.types import HandlerResult, ToolContext
from tripleten_contracts import QueueName, ToolName

POISON_MESSAGE_ID = "msg-98234-corrupt"
MAX_RECEIVE_COUNT = 3
CONSUMER_POOL_SIZE = 4


def isolate_poison_message(ctx: ToolContext) -> HandlerResult:
    """Routes the malformed payload to the workload DLQ, leaving valid customer jobs untouched."""
    ctx.log(
        f"Executing RB-312 step 1: maxReceiveCount={MAX_RECEIVE_COUNT} redrive routes "
        f"{POISON_MESSAGE_ID} to {QueueName.CUSTOMER_DLQ.value}"
    )
    # RB-312's safety constraint, and the reason this is a quarantine and not a purge: the queue
    # also holds real customer work, which has to survive the remediation intact.
    ctx.log(
        f"Main queue {QueueName.CUSTOMER_JOBS.value} NOT purged; only the poison payload is quarantined"
    )
    return HandlerResult(
        tool=ToolName.ISOLATE_POISON_MESSAGE.value,
        operation="DLQ quarantine",
        detail={
            "message_id": POISON_MESSAGE_ID,
            "destination_queue": QueueName.CUSTOMER_DLQ.value,
            "max_receive_count": MAX_RECEIVE_COUNT,
        },
    )


def reboot_workers(ctx: ToolContext) -> HandlerResult:
    """Terminates the deadlocked consumers and brings a fresh pool up to drain the backlog."""
    ctx.log("Executing RB-312 step 2: terminating deadlocked consumer instances")
    ctx.log(
        f"Executing RB-312 step 3: {CONSUMER_POOL_SIZE} fresh consumers online, draining the backlog"
    )
    return HandlerResult(
        tool=ToolName.REBOOT_WORKERS.value,
        operation="consumer pool restart",
        detail={"pool_size": CONSUMER_POOL_SIZE},
    )
