"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/tests/integration/test_prometheus_metrics.py
Component:          Prometheus Metrics Exposition Integration Tests
Purpose:            Integration tests verifying /metrics roster, types, labels, counter
                    monotonicity, and the system_health_status enum mapping against a live stack.
Interacts With:     incident-agent-api (:8000)

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             Prometheus Scraping, Golden Signals Exposition
Tools:              Pytest, HTTPX, Python 3.11
"""

import time

import httpx
import pytest

from tripleten_contracts import (
    METRIC_KINDS,
    IncidentState,
    MetricKind,
    MetricName,
    Quantile,
    ScenarioId,
)

pytestmark = pytest.mark.integration

API = "http://localhost:8000"

# Long enough that the newest sample in the exposition is guaranteed to be past the chaos ramp.
# The exposition only advances once per background tick, so the value a scrape returns can be up
# to one tick old: the wait has to cover the 2.0s ramp plus a full 1.0s tick interval plus
# margin. Waiting only the ramp duration makes these assertions pass or fail on tick phase.
RAMP_SETTLE_SECONDS = 4.0


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


def _families(client: httpx.Client) -> dict[str, str]:
    """Returns {family name: prometheus type} parsed from the exposition's TYPE lines."""
    body = client.get("/metrics").text
    families: dict[str, str] = {}
    for line in body.splitlines():
        if line.startswith("# TYPE "):
            _, _, name, metric_type = line.split(" ", 3)
            families[name] = metric_type
    return families


def _sample(client: httpx.Client, name: str, labels: str = "") -> float:
    """Returns one sample value from the exposition, by exact series name."""
    needle = f"{name}{labels} "
    for line in client.get("/metrics").text.splitlines():
        if line.startswith(needle):
            return float(line.rsplit(" ", 1)[1])
    raise AssertionError(f"series {name}{labels} missing from the exposition")


def _trigger(client: httpx.Client, scenario: ScenarioId) -> dict:
    response = client.post("/api/incidents/trigger", json={"scenario_id": scenario.value})
    assert response.status_code == 202, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Case 1 — metric roster
# ---------------------------------------------------------------------------


def test_metrics_exposes_exactly_the_canonical_roster(client):
    assert set(_families(client)) == {m.value for m in MetricName}


def test_metrics_exposes_no_client_library_extras(client):
    body = client.get("/metrics").text
    for unwanted in ("_created", "process_", "python_gc_", "python_info"):
        assert unwanted not in body


# ---------------------------------------------------------------------------
# Case 2 — types and labels
# ---------------------------------------------------------------------------


def test_metric_types_are_correct(client):
    families = _families(client)
    for name, kind in METRIC_KINDS.items():
        expected = "counter" if kind is MetricKind.COUNTER else "gauge"
        assert families[name.value] == expected, name


def test_latency_family_carries_all_three_quantile_labels(client):
    for quantile in Quantile:
        value = _sample(client, MetricName.HTTP_REQUEST_DURATION_MILLISECONDS.value, f'{{quantile="{quantile.value}"}}')
        assert value > 0.0


# ---------------------------------------------------------------------------
# Case 3 — counters never decrease
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "counter",
    [
        MetricName.HTTP_REQUESTS_TOTAL,
        MetricName.HTTP_5XX_ERRORS_TOTAL,
        MetricName.SECURITY_VIOLATIONS_TOTAL,
    ],
)
def test_counters_never_decrease_across_an_incident_and_a_reset(client, counter):
    readings = [_sample(client, counter.value)]

    run = _trigger(client, ScenarioId.DB_POOL_EXHAUSTION)
    time.sleep(RAMP_SETTLE_SECONDS)
    readings.append(_sample(client, counter.value))

    client.post("/api/incidents/reset", json={"incident_id": run["incident_id"]})
    time.sleep(1.5)
    readings.append(_sample(client, counter.value))

    assert readings == sorted(readings), f"{counter.value} went backwards: {readings}"


def test_reset_restores_gauges_while_the_request_counter_keeps_climbing(client):
    run = _trigger(client, ScenarioId.DB_POOL_EXHAUSTION)
    time.sleep(RAMP_SETTLE_SECONDS)
    assert _sample(client, MetricName.DB_POOL_UTILIZATION_PCT.value) > 95.0
    requests_at_peak = _sample(client, MetricName.HTTP_REQUESTS_TOTAL.value)

    client.post("/api/incidents/reset", json={"incident_id": run["incident_id"]})
    time.sleep(1.5)

    assert _sample(client, MetricName.DB_POOL_UTILIZATION_PCT.value) <= 17.0
    assert _sample(client, MetricName.HTTP_REQUESTS_TOTAL.value) >= requests_at_peak


# ---------------------------------------------------------------------------
# Case 4 — no pre-computed rates
# ---------------------------------------------------------------------------


def test_no_series_is_a_precomputed_rate_or_percentage_of_a_counter(client):
    """Throughput and error percentage exist only as PromQL rate() ratios in Grafana."""
    for name in _families(client):
        assert not name.endswith("_per_second")
        assert not name.endswith("_per_sec")
        assert not name.endswith("_rate_pct")


# ---------------------------------------------------------------------------
# Case 5 — health status enum mapping
# ---------------------------------------------------------------------------


def test_health_status_is_ok_at_baseline(client):
    assert _sample(client, MetricName.SYSTEM_HEALTH_STATUS.value) == 1


@pytest.mark.parametrize("scenario", [s for s in ScenarioId if s.causes_outage])
def test_health_status_is_down_during_an_outage(client, scenario):
    _trigger(client, scenario)
    assert _sample(client, MetricName.SYSTEM_HEALTH_STATUS.value) == 0


def test_health_status_is_degraded_for_the_whole_injection_run(client):
    """Scenario 4 sits at Degraded, never Down: the attack never becomes an outage."""
    _trigger(client, ScenarioId.PROMPT_INJECTION)
    for _ in range(3):
        assert _sample(client, MetricName.SYSTEM_HEALTH_STATUS.value) == 2
        time.sleep(1.0)


def test_health_status_returns_to_ok_after_a_reset(client):
    run = _trigger(client, ScenarioId.DB_POOL_EXHAUSTION)
    client.post("/api/incidents/reset", json={"incident_id": run["incident_id"]})
    assert _sample(client, MetricName.SYSTEM_HEALTH_STATUS.value) == 1


def _wait_for_state(client, target: IncidentState, timeout: float = 25.0) -> None:
    """Polls the snapshot until the run reports `target`, or fails with what it saw instead."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = client.get("/api/telemetry/current").json()["state"]
        if last == target.value:
            return
        time.sleep(0.5)
    raise AssertionError(f"never reached {target.value}; last state was {last!r}")


def _authorize(client, run: dict, approved: bool) -> dict:
    response = client.post(
        "/api/incidents/authorize",
        json={
            "incident_id": run["incident_id"],
            "thread_id": run["thread_id"],
            "scenario_id": run["scenario_id"],
            "approved": approved,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_health_status_is_down_while_awaiting_approval_on_an_outage(client):
    """The enum is read straight off /metrics, not inferred from health_status_for().

    Until Stage 5 these states were unreachable without the approval routes, so the mapping was
    asserted only at the unit tier against the pure function. That leaves the wiring between the
    function and the exported gauge unasserted — and the gauge is the number Grafana's status
    panel actually reads.
    """
    run = _trigger(client, ScenarioId.DB_POOL_EXHAUSTION)
    _wait_for_state(client, IncidentState.AWAITING_APPROVAL)

    assert _sample(client, MetricName.SYSTEM_HEALTH_STATUS.value) == 0
    client.post("/api/incidents/reset", json={"incident_id": run["incident_id"]})


def test_health_status_is_degraded_while_awaiting_approval_on_the_security_path(client):
    """The one state pair whose value depends on the scenario rather than the state alone."""
    run = _trigger(client, ScenarioId.PROMPT_INJECTION)
    _wait_for_state(client, IncidentState.AWAITING_APPROVAL)

    assert _sample(client, MetricName.SYSTEM_HEALTH_STATUS.value) == 2
    client.post("/api/incidents/reset", json={"incident_id": run["incident_id"]})


def test_health_status_is_down_through_executing_and_degraded_through_recovering(client):
    """Scenarios 1-3 touch Degraded only while recovering, and the worker drives that."""
    run = _trigger(client, ScenarioId.DB_POOL_EXHAUSTION)
    _wait_for_state(client, IncidentState.AWAITING_APPROVAL)

    outcome = _authorize(client, run, approved=True)
    assert outcome["state"] == IncidentState.EXECUTING.value

    # RECOVERING and HEALTHY are both acceptable: the decay is four seconds and the worker may
    # already have completed it. What must not appear is Down after a successful remediation.
    deadline = time.monotonic() + 40.0
    seen = set()
    while time.monotonic() < deadline:
        state = client.get("/api/telemetry/current").json()["state"]
        seen.add((state, _sample(client, MetricName.SYSTEM_HEALTH_STATUS.value)))
        if state == IncidentState.HEALTHY.value:
            break
        time.sleep(0.5)

    assert (IncidentState.HEALTHY.value, 1) in seen, f"never settled at OK; saw {sorted(seen)}"
    for state, value in seen:
        if state == IncidentState.RECOVERING.value:
            assert value == 2, "RECOVERING must report Degraded, not Down"
        if state == IncidentState.EXECUTING.value:
            assert value == 0, "EXECUTING on an outage scenario must report Down"


def test_health_status_is_down_in_rejected_on_an_outage(client):
    """A declined remediation leaves the chaos in place, so Down persists until reset."""
    run = _trigger(client, ScenarioId.DB_POOL_EXHAUSTION)
    _wait_for_state(client, IncidentState.AWAITING_APPROVAL)

    outcome = _authorize(client, run, approved=False)
    assert outcome["state"] == IncidentState.REJECTED.value
    assert _sample(client, MetricName.SYSTEM_HEALTH_STATUS.value) == 0

    client.post("/api/incidents/reset", json={"incident_id": run["incident_id"]})
    assert _sample(client, MetricName.SYSTEM_HEALTH_STATUS.value) == 1


def test_health_status_is_degraded_in_rejected_on_the_security_path(client):
    """The easiest value in the table to get wrong, and the reason it is asserted separately.

    "The remediation did not happen" reads like an outage. For Scenarios 1-3 it is one. For
    Scenario 4 there is nothing to persist: no chaos math ever ran, so reporting Down would put
    this gauge at 0 while every infrastructure gauge in the same frame reads healthy — and would
    contradict the NO CUSTOMER IMPACT claim the War Room holds up for the whole run.
    """
    run = _trigger(client, ScenarioId.PROMPT_INJECTION)
    _wait_for_state(client, IncidentState.AWAITING_APPROVAL)

    _authorize(client, run, approved=False)

    assert _sample(client, MetricName.SYSTEM_HEALTH_STATUS.value) == 2
    client.post("/api/incidents/reset", json={"incident_id": run["incident_id"]})


def test_health_status_is_degraded_in_security_contained(client):
    """Scenario 4's terminal, and the only run that sits at Degraded end to end."""
    run = _trigger(client, ScenarioId.PROMPT_INJECTION)
    _wait_for_state(client, IncidentState.AWAITING_APPROVAL)
    _authorize(client, run, approved=True)
    _wait_for_state(client, IncidentState.SECURITY_CONTAINED, timeout=40.0)

    assert _sample(client, MetricName.SYSTEM_HEALTH_STATUS.value) == 2
    client.post("/api/incidents/reset", json={"incident_id": run["incident_id"]})
    assert _sample(client, MetricName.SYSTEM_HEALTH_STATUS.value) == 1


# ---------------------------------------------------------------------------
# Case 6 — the security counter fires exactly once
# ---------------------------------------------------------------------------


def test_security_counter_increments_once_across_an_injection_run(client):
    before = _sample(client, MetricName.SECURITY_VIOLATIONS_TOTAL.value)
    run = _trigger(client, ScenarioId.PROMPT_INJECTION)
    time.sleep(RAMP_SETTLE_SECONDS)
    client.post("/api/incidents/reset", json={"incident_id": run["incident_id"]})
    assert _sample(client, MetricName.SECURITY_VIOLATIONS_TOTAL.value) == before + 1


@pytest.mark.parametrize("scenario", [s for s in ScenarioId if s.causes_outage])
def test_security_counter_ignores_the_outage_scenarios(client, scenario):
    before = _sample(client, MetricName.SECURITY_VIOLATIONS_TOTAL.value)
    run = _trigger(client, scenario)
    time.sleep(RAMP_SETTLE_SECONDS)
    client.post("/api/incidents/reset", json={"incident_id": run["incident_id"]})
    assert _sample(client, MetricName.SECURITY_VIOLATIONS_TOTAL.value) == before


# ---------------------------------------------------------------------------
# Chaos math reaching the exposition
# ---------------------------------------------------------------------------


def test_db_pool_exhaustion_reaches_its_documented_peaks(client):
    _trigger(client, ScenarioId.DB_POOL_EXHAUSTION)
    time.sleep(RAMP_SETTLE_SECONDS)
    assert _sample(client, MetricName.DB_POOL_UTILIZATION_PCT.value) == pytest.approx(98.5, abs=1.0)
    p99 = _sample(client, MetricName.HTTP_REQUEST_DURATION_MILLISECONDS.value, '{quantile="p99"}')
    assert p99 == pytest.approx(4820.0, abs=50.0)


def test_worker_deadlock_drains_the_worker_pool_and_backs_up_the_queue(client):
    _trigger(client, ScenarioId.WORKER_DEADLOCK)
    time.sleep(1.5)
    assert _sample(client, MetricName.ACTIVE_WORKERS_COUNT.value) == 0
    assert _sample(client, MetricName.DLQ_MESSAGE_COUNT.value) == 1
    assert _sample(client, MetricName.SQS_ACTIVE_QUEUE_DEPTH.value) > 6


def test_injection_run_holds_the_infrastructure_gauges_at_baseline(client):
    """The NO CUSTOMER IMPACT claim has to hold in the exposition, not just in the UI copy."""
    _trigger(client, ScenarioId.PROMPT_INJECTION)
    time.sleep(RAMP_SETTLE_SECONDS)
    assert 13.0 <= _sample(client, MetricName.DB_POOL_UTILIZATION_PCT.value) <= 17.0
    assert 39.0 <= _sample(client, MetricName.REDIS_MEMORY_UTILIZATION_PCT.value) <= 41.0
    p99 = _sample(client, MetricName.HTTP_REQUEST_DURATION_MILLISECONDS.value, '{quantile="p99"}')
    assert 44.0 <= p99 <= 52.0


def test_the_generator_is_live_rather_than_frozen(client):
    """Two scrapes a second apart must differ, or the 1s task is not running."""
    first = _sample(client, MetricName.HTTP_REQUESTS_TOTAL.value)
    time.sleep(1.5)
    assert _sample(client, MetricName.HTTP_REQUESTS_TOTAL.value) > first
