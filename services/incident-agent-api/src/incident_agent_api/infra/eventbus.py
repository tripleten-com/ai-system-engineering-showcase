"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/infra/eventbus.py
Component:          In-Process SSE Event Bus
Purpose:            Fans one published event out to every connected War Room, with bounded
                    per-subscriber buffers and a publisher that can never block or raise.
Interacts With:     incident-war-room (:3000) via GET /api/stream

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             Async Fan-Out, Backpressure, Event Streaming
Tools:              asyncio, Pydantic 2, Python 3.11

State lives in memory, matching the single-uvicorn-worker constraint the telemetry engine
already documents. Two worker processes would each hold their own subscriber roster, and a
browser would see only the events published by whichever process it happened to connect to.
"""

import asyncio
import itertools
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import cast

from pydantic import BaseModel

from tripleten_contracts import BaseIncidentEvent, build_event

logger = logging.getLogger("incident-agent-api")

# About four minutes of frames at the 1s metric cadence. A client further behind than this is
# not lagging, it is gone.
DEFAULT_QUEUE_MAXSIZE = 250

# Put on a subscriber's queue to end its stream. Never rendered to the wire.
_CLOSE = object()


class Subscription:
    """One connected client's view of the bus.

    Iterating it yields envelopes until the bus closes it — because the client went away, the
    buffer overflowed, or the application is shutting down.
    """

    def __init__(self, subscription_id: int, maxsize: int) -> None:
        self.id = subscription_id
        self._queue: asyncio.Queue[BaseIncidentEvent | object] = asyncio.Queue(maxsize=maxsize)
        self._closed = False

    @property
    def closed(self) -> bool:
        """True once the bus has stopped delivering to this subscriber."""
        return self._closed

    def _offer(self, event: BaseIncidentEvent) -> bool:
        """Tries to enqueue one frame. Returns False when the buffer is full."""
        if self._closed:
            return False
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            return False
        return True

    def _close(self) -> None:
        """Ends the stream promptly, discarding anything still buffered.

        The buffered frames are dropped rather than delivered because this subscriber is about
        to reconnect and rehydrate from GET /api/telemetry/current. Flushing a four-minute
        backlog first would animate stale telemetry across the War Room before the fresh state
        landed, which reads as a broken dashboard rather than a recovered one.
        """
        if self._closed:
            return
        self._closed = True
        while not self._queue.empty():
            self._queue.get_nowait()
        # Guaranteed to fit: the queue was just emptied, and no publisher can reach a closed
        # subscription to refill it.
        self._queue.put_nowait(_CLOSE)

    async def __aiter__(self) -> AsyncIterator[BaseIncidentEvent]:
        """Yields frames until the bus closes this subscription."""
        while True:
            item = await self._queue.get()
            if item is _CLOSE:
                return
            yield cast(BaseIncidentEvent, item)


class EventBus:
    """Fans published payloads out to every live subscriber.

    The bus stamps `event_id` and `timestamp` and resolves the channel from the payload's type,
    so a producer publishes a payload and never names a channel — which is what makes a
    type/data mismatch unreachable rather than merely tested for.
    """

    def __init__(
        self,
        queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
        now_utc: Callable[[], datetime] | None = None,
    ) -> None:
        self._queue_maxsize = queue_maxsize
        self._now_utc = now_utc if now_utc is not None else lambda: datetime.now(UTC)
        self._subscribers: dict[int, Subscription] = {}
        self._event_ids = itertools.count(1)
        self._subscription_ids = itertools.count(1)

    @property
    def subscriber_count(self) -> int:
        """Number of clients currently attached."""
        return len(self._subscribers)

    def publish(self, payload: BaseModel, incident_id: str | None = None) -> BaseIncidentEvent:
        """Delivers one payload to every subscriber. Synchronous and non-blocking.

        No subscriber can make this raise, and that guarantee is load-bearing rather than
        defensive habit: this is called from TelemetryEngine.tick(), which runs on the
        1-second generator task and again synchronously from /trigger and /reset. An
        exception escaping here because one browser stalled would stop the telemetry loop and
        take the whole demo's metrics down with it. A slow subscriber is therefore dropped,
        never propagated.

        An unregistered payload type is the one thing that does raise (UnknownPayloadError).
        That is a contract gap in the caller's own code, not a runtime condition, and it
        should be loud the first time a developer hits it.

        Returns the envelope it built, so callers and tests can assert on it without
        subscribing.
        """
        event = build_event(
            payload=payload,
            event_id=f"evt-{next(self._event_ids)}",
            incident_id=incident_id,
            timestamp=self._now_utc(),
        )

        # Snapshot the roster: _close() mutates it, and a subscriber overflowing mid-iteration
        # would otherwise resize the dict being walked.
        for subscription in list(self._subscribers.values()):
            if subscription._offer(event):
                continue
            logger.warning(
                "SSE subscriber %d overflowed its %d-frame buffer; closing it to force a "
                "reconnect and snapshot rehydrate",
                subscription.id,
                self._queue_maxsize,
            )
            self._drop(subscription)

        return event

    def _drop(self, subscription: Subscription) -> None:
        """Removes a subscriber from the roster and ends its stream."""
        self._subscribers.pop(subscription.id, None)
        subscription._close()

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[Subscription]:
        """Attaches a client for the duration of the block, detaching it on any exit.

        Client disconnect, buffer overflow, and application shutdown all leave through this
        one teardown, so there is a single place that takes a subscriber off the roster.
        """
        subscription = Subscription(next(self._subscription_ids), self._queue_maxsize)
        self._subscribers[subscription.id] = subscription
        logger.info("SSE subscriber %d attached (%d total)", subscription.id, self.subscriber_count)
        try:
            yield subscription
        finally:
            self._drop(subscription)
            logger.info("SSE subscriber %d detached (%d total)", subscription.id, self.subscriber_count)

    def close_all(self) -> None:
        """Ends every stream. Called on application shutdown.

        Without this an idle stream would sit blocked on an empty queue forever and hold
        shutdown open, since nothing else would ever wake it.
        """
        for subscription in list(self._subscribers.values()):
            self._drop(subscription)
