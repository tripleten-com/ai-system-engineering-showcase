"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/infra/workload.py
Component:          Customer Workload Producer / Consumer Pair
Purpose:            Runs the synthetic business workload over `customer-jobs` — the queue that
                    genuinely backs up when Scenario 3 stalls its consumers.
Interacts With:     localstack (:4566)

Curriculum Project:  Project 3 — Asynchronous Queues & Cloud Operations
Skills:             SQS Producer/Consumer, Backpressure, Dead-Letter Queueing
Tools:              LocalStack SQS, Boto3, asyncio, Python 3.11

**This queue is real; the gauge is simulated.** Two things that look like one thing, and keeping
them straight is the difference between an honest demo and a misleading one:

* Real: this task pair publishes and drains actual SQS messages on `customer-jobs`, a real
  poison payload is really routed to `customer-dlq` by a real redrive policy, and you can watch
  all of it with `awslocal sqs`. When Scenario 3 stops the consumers, the queue genuinely backs
  up.
* Simulated: `sqs_active_queue_depth` on `/metrics` follows the chaos profile in
  telemetry-and-chaos-engine.md §4 — an 8-second ramp toward 1,540. The demo does not publish
  1,540 real messages to make a number move, and it never claimed to.

The two agree in *direction* at every moment, which is what makes the story true; they do not
agree in magnitude, which is what makes it a simulation. The integration suite asserts the real
queue's behaviour through boto3 and the gauge's values through `/metrics`, separately and on
purpose.

The other thing to keep straight: the "workers" that deadlock in Scenario 3 are the consumer
tasks *in this module*, not the `remediation-worker` container. That container only ever consumes
`remediation-jobs`, and the control plane stays isolated from the workload — which is the
separation `active_workers_count` is reporting on.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field

from incident_agent_api.infra import sqs
from tripleten_contracts import QueueName

logger = logging.getLogger("incident-agent-api")

# Steady-state target. The producer holds the queue below the ceiling and the consumer keeps it
# above the floor, so the pair is a controller rather than two open loops that happen to
# average out. Matches the 2-6 band in BASELINE_BANDS.
DEPTH_FLOOR = 2
DEPTH_CEILING = 6
NOMINAL_CONSUMERS = 4

PRODUCE_INTERVAL_SECONDS = 0.5
CONSUME_INTERVAL_SECONDS = 0.5
RECEIVE_BATCH = 5

# The malformed payload Scenario 3 introduces. Real: it is published to `customer-jobs`, fails
# consumer validation, and is moved to `customer-dlq` when `isolate_poison_message` is approved.
POISON_MESSAGE_ID = "msg-98234-corrupt"
POISON_BODY = '{"job_id": "msg-98234-corrupt", "payload": <<<TRUNCATED'


@dataclass
class WorkloadState:
    """What the pair is doing right now, read by the telemetry engine and the tests."""

    draining: bool = True
    active_consumers: int = NOMINAL_CONSUMERS
    poison_in_flight: bool = False
    poison_quarantined: bool = False
    produced: int = field(default=0)
    consumed: int = field(default=0)


class CustomerWorkload:
    """The producer/consumer task pair over `customer-jobs`.

    Every SQS call goes through `asyncio.to_thread`: boto3 blocks, and this shares an event loop
    with the SSE stream and the 1-second telemetry tick. One synchronous `receive_message` with a
    long poll would freeze the entire demo's telemetry for the duration.
    """

    def __init__(
        self,
        produce_interval: float = PRODUCE_INTERVAL_SECONDS,
        consume_interval: float = CONSUME_INTERVAL_SECONDS,
    ) -> None:
        self.state = WorkloadState()
        self._produce_interval = produce_interval
        self._consume_interval = consume_interval
        self._tasks: list[asyncio.Task[None]] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Starts the producer and consumer tasks."""
        if self._tasks:
            return
        self._tasks = [
            asyncio.create_task(self._produce_forever(), name="customer-jobs-producer"),
            asyncio.create_task(self._consume_forever(), name="customer-jobs-consumer"),
        ]
        logger.info("Customer workload started: %d consumers", self.state.active_consumers)

    async def stop(self) -> None:
        """Cancels both tasks and waits for them to unwind."""
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Workload task %s died during shutdown", task.get_name())
        self._tasks = []

    # ------------------------------------------------------------------
    # Scenario 3 control surface
    # ------------------------------------------------------------------

    async def simulate_deadlock(self) -> None:
        """Stops the consumers and injects the poison payload. The producer keeps publishing.

        This is the whole of Scenario 3's "failure": no consumer is drained, so the backlog is a
        genuine consequence rather than a number written into a gauge.
        """
        self.state.draining = False
        self.state.active_consumers = 0
        self.state.poison_quarantined = False
        try:
            await asyncio.to_thread(self._publish_poison_sync)
            self.state.poison_in_flight = True
        except Exception:
            logger.exception("Publishing the poison payload failed; deadlock still simulated")

    async def recover(self) -> None:
        """Quarantines the poison payload and restarts the consumer pool.

        Quarantine precedes restart, matching RB-312 and the approval prompt: fresh consumers
        must not immediately pick the poison payload back up and crash-loop again.
        """
        if self.state.poison_in_flight:
            try:
                await asyncio.to_thread(self._quarantine_poison_sync)
                self.state.poison_quarantined = True
                self.state.poison_in_flight = False
            except Exception:
                logger.exception("Quarantining the poison payload failed")
        self.state.draining = True
        self.state.active_consumers = NOMINAL_CONSUMERS

    async def reset(self) -> None:
        """Returns the pair to steady state and clears the quarantine flag."""
        self.state.draining = True
        self.state.active_consumers = NOMINAL_CONSUMERS
        self.state.poison_in_flight = False
        self.state.poison_quarantined = False

    # ------------------------------------------------------------------
    # Task bodies
    # ------------------------------------------------------------------

    async def _produce_forever(self) -> None:
        """Publishes business jobs, holding the queue under its ceiling while draining."""
        while True:
            try:
                depth = await sqs.queue_depth(QueueName.CUSTOMER_JOBS.value)
                # While the consumers are stalled the producer deliberately ignores the ceiling:
                # a producer that backed off would hide the backpressure Scenario 3 is about.
                if not self.state.draining or depth < DEPTH_CEILING:
                    await asyncio.to_thread(self._publish_job_sync, self.state.produced)
                    self.state.produced += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("Workload producer iteration failed", exc_info=True)
            await asyncio.sleep(self._produce_interval)

    async def _consume_forever(self) -> None:
        """Drains business jobs, holding the queue above its floor.

        The floor is what keeps the steady-state band honest. Draining to zero would make
        `customer-jobs` look idle rather than busy, and the demo's claim is a platform under
        continuous load.
        """
        while True:
            try:
                if self.state.draining:
                    depth = await sqs.queue_depth(QueueName.CUSTOMER_JOBS.value)
                    if depth > DEPTH_FLOOR:
                        drained = await asyncio.to_thread(self._drain_sync, min(RECEIVE_BATCH, depth - DEPTH_FLOOR))
                        self.state.consumed += drained
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("Workload consumer iteration failed", exc_info=True)
            await asyncio.sleep(self._consume_interval)

    # ------------------------------------------------------------------
    # Synchronous boto3 bodies, all called via asyncio.to_thread
    # ------------------------------------------------------------------

    def _publish_job_sync(self, sequence: int) -> None:
        """Publishes one synthetic business job."""
        client = sqs.client("sqs")
        client.send_message(
            QueueUrl=sqs.queue_url(client, QueueName.CUSTOMER_JOBS.value),
            MessageBody=json.dumps({"job_id": f"cust-{sequence:06d}", "kind": "invoice_render"}),
        )

    def _publish_poison_sync(self) -> None:
        """Publishes the malformed payload that stalls the consumers."""
        client = sqs.client("sqs")
        client.send_message(
            QueueUrl=sqs.queue_url(client, QueueName.CUSTOMER_JOBS.value),
            MessageBody=POISON_BODY,
            MessageAttributes={"poison": {"DataType": "String", "StringValue": POISON_MESSAGE_ID}},
        )

    def _drain_sync(self, max_messages: int) -> int:
        """Receives and deletes up to `max_messages`, leaving anything malformed in place.

        A message that fails validation is *not* deleted, so its receive count climbs and the
        `maxReceiveCount=3` redrive policy eventually routes it to `customer-dlq` on its own.
        That is the real mechanism RB-312 describes, and letting SQS do it is more honest than
        moving the message by hand.
        """
        client = sqs.client("sqs")
        queue_url = sqs.queue_url(client, QueueName.CUSTOMER_JOBS.value)
        response = client.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=max(1, min(10, max_messages)),
            WaitTimeSeconds=0,
        )
        deleted = 0
        for message in response.get("Messages", []):
            if not self._is_valid_job(message.get("Body", "")):
                logger.debug("Leaving malformed workload message in place for DLQ redrive")
                continue
            client.delete_message(QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"])
            deleted += 1
        return deleted

    def _quarantine_poison_sync(self) -> None:
        """Moves the poison payload to `customer-dlq` — the workload DLQ, never the control plane."""
        client = sqs.client("sqs")
        source_url = sqs.queue_url(client, QueueName.CUSTOMER_JOBS.value)
        dlq_url = sqs.queue_url(client, QueueName.CUSTOMER_DLQ.value)

        # Bounded scan: the poison payload is one known message, so this looks for it rather than
        # draining the queue. An unbounded loop here could spin on a busy queue.
        for _ in range(10):
            response = client.receive_message(QueueUrl=source_url, MaxNumberOfMessages=10, WaitTimeSeconds=0)
            messages = response.get("Messages", [])
            if not messages:
                return

            valid_receipts: list[str] = []
            for message in messages:
                if self._is_valid_job(message.get("Body", "")):
                    valid_receipts.append(message["ReceiptHandle"])
                    continue
                client.send_message(QueueUrl=dlq_url, MessageBody=message["Body"])
                client.delete_message(QueueUrl=source_url, ReceiptHandle=message["ReceiptHandle"])
                logger.info("Poison payload %s quarantined to %s", POISON_MESSAGE_ID, QueueName.CUSTOMER_DLQ.value)
                self._release(client, source_url, valid_receipts)
                return

            # Every message this scan looked at and did not want has to be handed straight back.
            # Receiving a message hides it for the queue's 30-second visibility timeout, so a
            # scan that simply moved on would leave up to ten real customer jobs invisible for
            # half a minute — and the consumer, which had just been restarted to drain exactly
            # those jobs, would find nothing to drain. Observed as a backlog that grew instead of
            # shrinking after remediation.
            self._release(client, source_url, valid_receipts)

    @staticmethod
    def _release(client, queue_url: str, receipts: list[str]) -> None:
        """Returns received-but-unwanted messages to the queue immediately."""
        for receipt in receipts:
            try:
                client.change_message_visibility(
                    QueueUrl=queue_url, ReceiptHandle=receipt, VisibilityTimeout=0
                )
            except Exception:
                logger.debug("Releasing a scanned workload message failed", exc_info=True)

    @staticmethod
    def _is_valid_job(body: str) -> bool:
        """True when a message body parses as a job envelope — the consumer's schema check."""
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            return False
        return isinstance(payload, dict) and "job_id" in payload
