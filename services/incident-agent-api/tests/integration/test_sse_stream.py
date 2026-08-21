"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/tests/integration/test_sse_stream.py
Component:          SSE Transport Integration Tests
Purpose:            Integration tests for GET /api/stream against a live stack: real HTTP
                    framing, broadcast to multiple clients, the optional incident_id contract,
                    and the headers that keep the stream unbuffered end to end.
Interacts With:     incident-agent-api (:8000)

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             Event Streaming, API Contract Design, Broadcast Fan-Out
Tools:              Pytest, HTTPX, Python 3.11
"""

import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from tripleten_contracts import (
    INCIDENT_EVENT_ADAPTER,
    SSE_RETRY_MS,
    EventType,
    IncidentState,
    MetricsUpdateEvent,
    ScenarioId,
)

pytestmark = pytest.mark.integration

API = "http://localhost:8000"

# Thread-join bound for the concurrent reads below. The reader has its own HTTP timeout; this
# only stops a wedged worker from hanging the suite.
JOIN_TIMEOUT = 30.0


@pytest.fixture
def client():
    with httpx.Client(base_url=API, timeout=10.0) as http_client:
        yield http_client


@pytest.fixture(autouse=True)
def baseline(client):
    """Clears any run left behind by an earlier test, before and after each test."""
    _ensure_baseline(client)
    yield
    _ensure_baseline(client)


def _ensure_baseline(client: httpx.Client) -> None:
    snapshot = client.get("/api/telemetry/current").json()
    if snapshot["incident_id"] is not None:
        client.post("/api/incidents/reset", json={"incident_id": snapshot["incident_id"]})


# ----------------------------------------------------------------------------------
# Transport
# ----------------------------------------------------------------------------------



def metrics_frames(frames: list[dict]) -> list[dict]:
    """Keeps only METRICS_UPDATE envelopes.

    Necessary from Stage 5 onward and a correctness improvement regardless: the channel is
    multiplexed, and a test that read `frames[0]["data"]["status"]` was silently assuming the
    other four producers did not exist yet. Filtering on the envelope's `type` is what the
    browser does, and what the contract says to do.
    """
    return [frame for frame in frames if frame["type"] == EventType.METRICS_UPDATE.value]

def test_stream_declares_an_unbuffered_event_stream(read_stream):
    """A proxy that buffers this response destroys the only property it has."""
    headers, _preamble, _frames = read_stream(1)

    assert headers["content-type"].startswith("text/event-stream")
    assert headers["cache-control"] == "no-cache"
    assert headers["x-accel-buffering"] == "no"


def test_stream_opens_with_the_retry_directive(read_stream):
    """The server states EventSource's reconnect floor before sending any event."""
    _headers, preamble, _frames = read_stream(1)

    assert f"retry: {SSE_RETRY_MS}" in preamble


def test_frames_validate_against_the_published_envelope(read_stream):
    """What the browser receives is exactly what packages/contracts declares."""
    _headers, _preamble, frames = read_stream(2)

    for frame in frames:
        event = INCIDENT_EVENT_ADAPTER.validate_python(frame)
        assert set(frame) == {"event_id", "incident_id", "timestamp", "type", "data"}
        assert isinstance(event, MetricsUpdateEvent)


def test_metrics_arrive_at_the_documented_cadence(read_stream):
    """Telemetry is emitted every tick in every state, so a healthy platform still streams.

    Read at baseline, where the metrics generator is the only producer — the agent channels only
    open once a run is triggered. Event ids are asserted monotonic across *all* frames, since a
    single counter is what orders the multiplexed stream.
    """
    _headers, _preamble, frames = read_stream(3)

    assert metrics_frames(frames), "no telemetry frame arrived at baseline"
    ids = [int(frame["event_id"].removeprefix("evt-")) for frame in frames]
    assert ids == sorted(ids), f"event ids must be monotonic, got {ids}"


# ----------------------------------------------------------------------------------
# Broadcast
# ----------------------------------------------------------------------------------


def test_two_clients_receive_the_same_frames(read_stream):
    """Testing strategy §3: the stream broadcasts, it is not a per-client private generator."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(read_stream, 3)
        second = pool.submit(read_stream, 3)
        _h1, _p1, frames_a = first.result(timeout=JOIN_TIMEOUT)
        _h2, _p2, frames_b = second.result(timeout=JOIN_TIMEOUT)

    ids_a = [frame["event_id"] for frame in frames_a]
    ids_b = [frame["event_id"] for frame in frames_b]

    # The two clients attach a fraction of a second apart, so one may start a frame ahead.
    # What must hold is that they see the same events in the same order where they overlap.
    overlap = set(ids_a) & set(ids_b)
    assert overlap, f"no shared frames between concurrent clients: {ids_a} vs {ids_b}"
    assert [i for i in ids_a if i in overlap] == [i for i in ids_b if i in overlap]


# ----------------------------------------------------------------------------------
# The incident_id contract
# ----------------------------------------------------------------------------------


def test_baseline_stream_needs_no_incident_id(client, read_stream):
    """The War Room renders live charts before anyone picks a scenario."""
    assert client.get("/api/telemetry/current").json()["incident_id"] is None

    _headers, _preamble, frames = read_stream(1)

    assert frames[0]["incident_id"] is None
    assert frames[0]["data"]["status"] == IncidentState.HEALTHY.value


def test_stream_scoped_to_the_active_run_is_accepted(client, read_stream):
    """A client that supplies the in-flight id gets the stream, and the frames name that run."""
    incident_id = client.post(
        "/api/incidents/trigger", json={"scenario_id": ScenarioId.DB_POOL_EXHAUSTION.value}
    ).json()["incident_id"]

    # Enough frames that a telemetry one is certain: the agent's reasoning chain is also
    # publishing on this stream now, so asking for two could return two AGENT_THOUGHTs.
    _headers, _preamble, frames = read_stream(6, incident_id=incident_id)

    assert {frame["incident_id"] for frame in frames} == {incident_id}
    telemetry = metrics_frames(frames)
    assert telemetry, f"no telemetry frame among {[f['type'] for f in frames]}"
    assert telemetry[0]["data"]["status"] == IncidentState.CRITICAL_OUTAGE.value


def test_scoped_stream_ends_rather_than_following_the_next_run(client, read_stream):
    """Scope must hold for the stream's lifetime, not just its first instant.

    Checking ownership only at connect time leaves the real failure wide open: a tab already
    streaming when the operator hits Master Reset would carry straight on into the next
    incident, rendering its telemetry under the previous run's identity. The stream has to end
    instead, sending that client down the reconnect-and-rehydrate path.
    """
    first = client.post(
        "/api/incidents/trigger", json={"scenario_id": ScenarioId.DB_POOL_EXHAUSTION.value}
    ).json()["incident_id"]

    with ThreadPoolExecutor(max_workers=1) as pool:
        # More frames than the run will produce: the read is expected to end early because the
        # server closed the stream, not because it was satisfied.
        reader = pool.submit(read_stream, 50, first)
        time.sleep(2.0)
        client.post("/api/incidents/reset", json={"incident_id": first})
        second = client.post(
            "/api/incidents/trigger", json={"scenario_id": ScenarioId.CACHE_THUNDERING_HERD.value}
        ).json()["incident_id"]

        with pytest.raises(AssertionError, match="stream closed after"):
            reader.result(timeout=JOIN_TIMEOUT)

    # And the run that replaced it is reachable only by asking for it explicitly.
    _headers, _preamble, frames = read_stream(1, incident_id=second)
    assert frames[0]["incident_id"] == second


def test_stale_incident_id_is_refused_while_a_run_is_active(client):
    """A tab left open across a Master Reset must not silently render the next incident's data."""
    active = client.post(
        "/api/incidents/trigger", json={"scenario_id": ScenarioId.WORKER_DEADLOCK.value}
    ).json()["incident_id"]

    response = client.get("/api/stream", params={"incident_id": "inc-0000-db"})

    assert response.status_code == 409
    assert response.json()["detail"]["incident_id"] == active


def test_incident_id_is_refused_when_no_run_is_active(client):
    """Nothing to scope to: the client is holding an id the platform has already cleared."""
    response = client.get("/api/stream", params={"incident_id": "inc-0000-db"})

    assert response.status_code == 409
    assert response.json()["detail"]["incident_id"] is None


def test_state_change_reaches_the_stream(client, read_stream):
    """One click has to be visible on every connected browser, not just the one that clicked."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        reader = pool.submit(read_stream, 8)
        client.post("/api/incidents/trigger", json={"scenario_id": ScenarioId.PROMPT_INJECTION.value})
        _headers, _preamble, frames = reader.result(timeout=JOIN_TIMEOUT)

    statuses = [frame["data"]["status"] for frame in metrics_frames(frames)]
    assert IncidentState.EXPLOIT_INTERCEPTED.value in statuses, statuses
