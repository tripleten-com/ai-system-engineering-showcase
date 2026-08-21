"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/tests/unit/test_event_bus.py
Component:          SSE Event Bus Fan-Out & Backpressure Tests
Purpose:            Unit tests for the in-process broadcaster: fan-out to many War Rooms, a
                    publisher that cannot block or be broken by a stalled client, and the
                    single teardown path every subscriber leaves through.
Interacts With:     None (pure asyncio)

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             Async Fan-Out, Backpressure, Resource Lifecycle
Tools:              Pytest, Pytest-Asyncio, Python 3.11
"""

import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from incident_agent_api.infra.eventbus import EventBus, Subscription
from tripleten_contracts import (
    EventType,
    LogStreamPayload,
    UnknownPayloadError,
    WorkerLogLevel,
    WorkerLogPayload,
    WorkerLogSource,
)

FIXED_NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
READ_TIMEOUT = 1.0


@pytest.fixture
def bus() -> EventBus:
    return EventBus(now_utc=lambda: FIXED_NOW)


def log(message: str = "pool saturated") -> LogStreamPayload:
    return LogStreamPayload(message=message, sanitized=False)


def worker_log() -> WorkerLogPayload:
    return WorkerLogPayload(source=WorkerLogSource.WORKER, level=WorkerLogLevel.INFO, message="done")


async def take(subscription: Subscription, count: int) -> list:
    """Reads exactly `count` frames, failing the test rather than hanging if they never arrive."""
    iterator = subscription.__aiter__()
    return [await asyncio.wait_for(iterator.__anext__(), READ_TIMEOUT) for _ in range(count)]


async def assert_stream_ended(subscription: Subscription) -> None:
    """Asserts the subscription's iterator terminates rather than blocking forever."""
    iterator = subscription.__aiter__()
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(iterator.__anext__(), READ_TIMEOUT)


# ----------------------------------------------------------------------------------
# Fan-out
# ----------------------------------------------------------------------------------


async def test_every_subscriber_receives_the_same_frame(bus):
    """One incident, many browsers: the whole point of a bus rather than a per-client generator."""
    async with bus.subscribe() as first, bus.subscribe() as second:
        published = bus.publish(log(), incident_id="inc-9938-db")

        assert await take(first, 1) == [published]
        assert await take(second, 1) == [published]


async def test_publishing_with_no_subscribers_is_harmless(bus):
    """The engine ticks once at app construction, before any browser has connected."""
    event = bus.publish(log())

    assert event.incident_id is None
    assert bus.subscriber_count == 0


async def test_event_ids_are_monotonic_across_channels(bus):
    """`event_id` orders the whole stream, not one channel within it."""
    async with bus.subscribe() as subscription:
        bus.publish(log())
        bus.publish(worker_log())
        bus.publish(log())

        ids = [event.event_id for event in await take(subscription, 3)]

    assert ids == ["evt-1", "evt-2", "evt-3"]


async def test_channel_is_derived_from_the_payload_type(bus):
    """Producers publish a payload and never name a channel, so the two cannot disagree."""
    async with bus.subscribe() as subscription:
        bus.publish(log())
        bus.publish(worker_log())

        types = [event.type for event in await take(subscription, 2)]

    assert types == [EventType.LOG_STREAM, EventType.WORKER_LOG]


async def test_incident_id_and_timestamp_are_stamped_by_the_bus(bus):
    async with bus.subscribe() as subscription:
        bus.publish(log(), incident_id="inc-1234-sec")
        (event,) = await take(subscription, 1)

    assert event.incident_id == "inc-1234-sec"
    assert event.timestamp == FIXED_NOW


async def test_unregistered_payload_raises(bus):
    """A payload with no channel is a bug in the caller, and must not fail silently."""

    class RoguePayload(BaseModel):
        value: int

    with pytest.raises(UnknownPayloadError):
        bus.publish(RoguePayload(value=1))


# ----------------------------------------------------------------------------------
# Backpressure
# ----------------------------------------------------------------------------------


async def test_a_full_subscriber_never_breaks_the_publisher():
    """publish() runs inside TelemetryEngine.tick(); an exception here stops the demo's metrics."""
    bus = EventBus(queue_maxsize=2)

    async with bus.subscribe():
        for _ in range(10):
            # No assertion needed beyond "this returns" — the guarantee under test is that a
            # stalled browser cannot propagate anything into the telemetry loop.
            bus.publish(log())


async def test_overflow_closes_only_the_stalled_subscriber():
    """A dead client is dropped; the healthy one beside it keeps streaming."""
    bus = EventBus(queue_maxsize=2)

    async with bus.subscribe() as stalled, bus.subscribe() as healthy:
        # The healthy client drains as it goes; the stalled one never reads.
        healthy_iter = healthy.__aiter__()
        for _ in range(3):
            bus.publish(log())
            await asyncio.wait_for(healthy_iter.__anext__(), READ_TIMEOUT)

        assert stalled.closed is True
        assert healthy.closed is False
        assert bus.subscriber_count == 1

        # And the survivor still receives everything published after the drop.
        bus.publish(log("after the drop"))
        event = await asyncio.wait_for(healthy_iter.__anext__(), READ_TIMEOUT)
        assert event.data.message == "after the drop"


async def test_a_dropped_subscriber_stream_terminates_rather_than_hanging():
    """The route's `async for` must end, so the HTTP response closes and the client reconnects."""
    bus = EventBus(queue_maxsize=1)

    async with bus.subscribe() as subscription:
        bus.publish(log())
        bus.publish(log())  # overflows, dropping the subscriber

        assert subscription.closed is True
        await assert_stream_ended(subscription)


async def test_buffered_frames_are_discarded_on_drop():
    """A dropped client rehydrates from the snapshot; replaying stale telemetry first would
    animate a four-minute-old incident across the War Room before the fresh state landed."""
    bus = EventBus(queue_maxsize=2)

    async with bus.subscribe() as subscription:
        for _ in range(5):
            bus.publish(log())

        await assert_stream_ended(subscription)


# ----------------------------------------------------------------------------------
# Lifecycle
# ----------------------------------------------------------------------------------


async def test_subscriber_is_detached_on_scope_exit(bus):
    async with bus.subscribe() as subscription:
        assert bus.subscriber_count == 1

    assert bus.subscriber_count == 0
    assert subscription.closed is True


async def test_subscriber_is_detached_when_the_scope_raises(bus):
    """Client disconnect surfaces as an exception through the generator; it must still detach."""
    with pytest.raises(RuntimeError):
        async with bus.subscribe():
            assert bus.subscriber_count == 1
            raise RuntimeError("client went away")

    assert bus.subscriber_count == 0


async def test_a_detached_subscriber_receives_nothing_further(bus):
    async with bus.subscribe() as subscription:
        pass

    bus.publish(log())
    await assert_stream_ended(subscription)


async def test_close_all_ends_every_stream(bus):
    """Shutdown: an idle stream is blocked on an empty queue and only the bus can wake it."""
    async with bus.subscribe() as first, bus.subscribe() as second:
        bus.close_all()

        assert bus.subscriber_count == 0
        await assert_stream_ended(first)
        await assert_stream_ended(second)


async def test_close_all_is_idempotent(bus):
    async with bus.subscribe() as subscription:
        bus.close_all()
        bus.close_all()

        await assert_stream_ended(subscription)
