"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/tests/integration/test_otel_tracing.py
Component:          OpenTelemetry Trace Export Integration Tests
Purpose:            Verifies spans reach Jaeger over OTLP, that requests are wrapped in a server
                    span the manual spans hang off, that a trigger produces the chaos injection
                    span tree, and that the scrape, probe, and stream endpoints stay excluded.
Interacts With:     incident-agent-api (:8000), jaeger (:16686)

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             Distributed Tracing, OTLP Export, Observability
Tools:              Pytest, HTTPX, Jaeger, Python 3.11
"""

import time
from itertools import islice

import httpx
import pytest

from tripleten_contracts import ScenarioId

pytestmark = pytest.mark.integration

API = "http://localhost:8000"
JAEGER = "http://localhost:16686"
SERVICE = "incident-agent-api"

# BatchSpanProcessor exports on a schedule rather than per span, so every assertion here polls.
EXPORT_TIMEOUT_SECONDS = 45.0
POLL_INTERVAL_SECONDS = 2.0


@pytest.fixture
def client():
    with httpx.Client(timeout=10.0) as http_client:
        yield http_client


def _await_condition(check, description: str):
    """Polls until check() returns a truthy value, or fails with what it was waiting for."""
    deadline = time.monotonic() + EXPORT_TIMEOUT_SECONDS
    last = None
    while time.monotonic() < deadline:
        last = check()
        if last:
            return last
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError(f"timed out after {EXPORT_TIMEOUT_SECONDS:.0f}s waiting for {description}; last={last!r}")


def _services(client: httpx.Client) -> list[str]:
    data = client.get(f"{JAEGER}/api/services").json().get("data")
    return data or []


def _operations(client: httpx.Client) -> list[str]:
    response = client.get(f"{JAEGER}/api/operations", params={"service": SERVICE})
    if response.status_code != 200:
        return []
    data = response.json().get("data")
    return [entry["name"] if isinstance(entry, dict) else entry for entry in (data or [])]


def test_the_api_registers_itself_as_a_jaeger_service(client):
    """Generate some traffic first: a service with no exported spans is unknown to Jaeger."""
    client.get(f"{API}/api/telemetry/current")
    _await_condition(lambda: SERVICE in _services(client), f"{SERVICE} to appear in Jaeger's service list")


def test_triggering_an_incident_produces_the_chaos_injection_span(client):
    snapshot = client.get(f"{API}/api/telemetry/current").json()
    if snapshot["incident_id"]:
        client.post(f"{API}/api/incidents/reset", json={"incident_id": snapshot["incident_id"]})

    response = client.post(
        f"{API}/api/incidents/trigger", json={"scenario_id": ScenarioId.DB_POOL_EXHAUSTION.value}
    )
    assert response.status_code == 202
    incident_id = response.json()["incident_id"]

    try:
        traces = _await_condition(
            lambda: client.get(
                f"{JAEGER}/api/traces", params={"service": SERVICE, "operation": "inject_chaos", "limit": 20}
            ).json().get("data"),
            "an inject_chaos trace to reach Jaeger",
        )
        span_names = {span["operationName"] for trace in traces for span in trace["spans"]}
        assert "inject_chaos" in span_names
        # One child per perturbed metric family, so the trace shows what the scenario touched.
        assert any(name.startswith("perturb:") for name in span_names)
    finally:
        client.post(f"{API}/api/incidents/reset", json={"incident_id": incident_id})


def test_the_trigger_request_is_the_root_of_the_chaos_waterfall(client):
    """Regression: inject_chaos used to be its own root, with no request span above it at all.

    FastAPIInstrumentor installs middleware, and Starlette freezes its middleware stack before
    lifespan startup runs — so instrumenting from inside lifespan was silently discarded and the
    service exported no HTTP spans whatsoever. Nothing failed and nothing logged; Jaeger simply
    had no request to hang the waterfall from. Asserting on the parent link is what makes that
    regression visible, because asserting on inject_chaos alone passed throughout.
    """
    snapshot = client.get(f"{API}/api/telemetry/current").json()
    if snapshot["incident_id"]:
        client.post(f"{API}/api/incidents/reset", json={"incident_id": snapshot["incident_id"]})

    response = client.post(
        f"{API}/api/incidents/trigger", json={"scenario_id": ScenarioId.DB_POOL_EXHAUSTION.value}
    )
    assert response.status_code == 202
    incident_id = response.json()["incident_id"]

    def rooted_trace():
        traces = client.get(
            f"{JAEGER}/api/traces", params={"service": SERVICE, "operation": "inject_chaos", "limit": 20}
        ).json().get("data") or []
        for trace in traces:
            by_id = {span["spanID"]: span for span in trace["spans"]}
            for span in trace["spans"]:
                if span["operationName"] != "inject_chaos":
                    continue
                parents = [ref["spanID"] for ref in span.get("references", []) if ref["refType"] == "CHILD_OF"]
                if parents and (parent := by_id.get(parents[0])) is not None:
                    return parent["operationName"]
        return None

    try:
        parent_name = _await_condition(rooted_trace, "inject_chaos to arrive under a server span")
        assert "/api/incidents/trigger" in parent_name, parent_name
    finally:
        client.post(f"{API}/api/incidents/reset", json={"incident_id": incident_id})


def test_the_scrape_probe_and_stream_endpoints_are_excluded_from_tracing(client):
    """A 1s scrape and a 3s health probe would bury the waterfall; the stream would dwarf it.

    An SSE span stays open for the life of the connection, so tracing /api/stream would export
    one multi-minute span whenever the War Room tab finally closes.

    The positive assertion below is load-bearing. While no endpoint was traced at all, this test
    passed by vacuity — "no excluded operation appears" is trivially true of an empty set.
    """
    for _ in range(5):
        client.get(f"{API}/metrics")
        client.get(f"{API}/healthz")
    with client.stream("GET", f"{API}/api/stream") as stream:
        # Open it, read a little, close it. If the stream were traced, its span would only be
        # exported here on disconnect — which is precisely the shape being excluded.
        for _ in islice(stream.iter_lines(), 3):
            pass
    client.get(f"{API}/api/telemetry/current")

    _await_condition(
        lambda: [name for name in _operations(client) if "telemetry/current" in name],
        "the traced endpoints to be traced, so exclusion means something",
    )
    traced = _operations(client)
    excluded = [name for name in traced if any(part in name for part in ("metrics", "healthz", "stream"))]
    assert not excluded, traced
