"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             tests/smoke/test_observability_stack.py
Component:          Observability Stack Smoke Test
Purpose:            Validates that Prometheus is really scraping and that every dashboard in
                    infra/grafana/provisioning/dashboards/ loaded with no manual setup.
Interacts With:     grafana (:3001), prometheus (:9090), incident-agent-api (:8000)

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             Observability, Grafana Provisioning, PromQL, Smoke Testing
Tools:              Pytest, HTTPX, Python 3.11

`test_container_health.py` proves Grafana answers /api/health. That is a liveness check and it
would pass just as happily with zero dashboards and a broken datasource — which is the entire
failure mode Stage 4 introduces. These assertions cover the gap: the scrape target is up, the
series are in the TSDB and inside their documented bands, and each dashboard resolves by uid
and is flagged as provisioned rather than hand-imported.
"""

import json
import time
from pathlib import Path

import httpx
import pytest

from tripleten_contracts import BASELINE_BANDS, MetricName

GRAFANA = "http://localhost:3001"
PROMETHEUS = "http://localhost:9090"
GRAFANA_AUTH = ("admin", "admin")

DASHBOARD_DIR = Path(__file__).resolve().parents[2] / "infra" / "grafana" / "provisioning" / "dashboards"
PROMETHEUS_DATASOURCE_UID = "tripleten-prometheus"
JAEGER_DATASOURCE_UID = "tripleten-jaeger"

# Prometheus series → the BASELINE_BANDS key holding its steady-state envelope. The latency
# family is one labelled series per quantile, so it maps three ways.
GAUGE_BANDS = {
    MetricName.DB_POOL_UTILIZATION_PCT.value: "db_pool_utilization_pct",
    MetricName.REDIS_MEMORY_UTILIZATION_PCT.value: "redis_memory_utilization_pct",
    MetricName.CACHE_HIT_RATIO_PCT.value: "cache_hit_ratio_pct",
    MetricName.SQS_ACTIVE_QUEUE_DEPTH.value: "sqs_active_queue_depth",
    MetricName.DLQ_MESSAGE_COUNT.value: "dlq_message_count",
    MetricName.ACTIVE_WORKERS_COUNT.value: "active_workers_count",
}
LATENCY_BANDS = {"p50": "latency_p50_ms", "p95": "latency_p95_ms", "p99": "latency_p99_ms"}


def iter_panels(dashboard: dict) -> list[dict]:
    """Flattens a dashboard's panels, descending into rows.

    Grafana nests a row's children under the row panel. Iterating `dashboard["panels"]` alone
    would let every query inside a row escape the check below while it still reported success.
    """
    flattened = []
    for panel in dashboard.get("panels", []):
        flattened.append(panel)
        flattened.extend(panel.get("panels", []))
    return flattened


def provisioned_dashboards() -> dict[str, str]:
    """Reads uid → title straight off disk so this suite cannot drift from the committed files."""
    dashboards = {}
    for path in sorted(DASHBOARD_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        dashboards[payload["uid"]] = payload["title"]
    return dashboards


def promql(client: httpx.Client, expr: str) -> list[dict]:
    """Runs an instant query and returns its result vector."""
    resp = client.get(f"{PROMETHEUS}/api/v1/query", params={"query": expr})
    assert resp.status_code == 200, f"query {expr!r} returned {resp.status_code}"
    body = resp.json()
    assert body["status"] == "success", f"query {expr!r} failed: {body}"
    return body["data"]["result"]


def scalar(client: httpx.Client, expr: str) -> float:
    """Runs an instant query expected to resolve to exactly one series.

    More than one series for a family the API exposes once means the target's label set
    changed and the TSDB is still serving the previous identity inside the lookback window.
    That is worth failing on rather than reducing away: while it lasts, every dashboard panel
    draws two lines.
    """
    result = promql(client, expr)
    assert len(result) == 1, (
        f"query {expr!r} returned {len(result)} series, expected 1 — label churn in "
        f"prometheus.yml? got {[series['metric'] for series in result]}"
    )
    return float(result[0]["value"][1])


@pytest.mark.smoke
def test_prometheus_target_is_up_and_scraping():
    """A configured target that is down produces empty dashboards, not an error anyone sees."""
    with httpx.Client(timeout=5.0) as client:
        resp = client.get(f"{PROMETHEUS}/api/v1/targets", params={"state": "active"})
        assert resp.status_code == 200
        targets = resp.json()["data"]["activeTargets"]

    assert len(targets) == 1, f"expected one scrape target, found {[t['scrapeUrl'] for t in targets]}"
    target = targets[0]
    assert target["health"] == "up", f"target unhealthy: {target.get('lastError')}"
    assert target["scrapeUrl"] == "http://incident-agent-api:8000/metrics"
    assert target["labels"]["job"] == "incident-agent-api"


@pytest.mark.smoke
def test_every_metric_family_is_present_in_the_tsdb():
    """The exposition roster is closed at eleven families; all eleven must reach Prometheus."""
    with httpx.Client(timeout=5.0) as client:
        for metric in MetricName:
            result = promql(client, metric.value)
            assert result, f"{metric.value} exposed by /metrics but absent from Prometheus"


@pytest.mark.smoke
def test_scraped_gauges_sit_inside_their_baseline_bands():
    """This is the Stage 4 verification criterion: the dashboards reflect real baseline data."""
    with httpx.Client(timeout=5.0) as client:
        for series, band_key in GAUGE_BANDS.items():
            low, high = BASELINE_BANDS[band_key]
            value = scalar(client, series)
            assert low <= value <= high, f"{series}={value} outside [{low}, {high}]"

        for quantile, band_key in LATENCY_BANDS.items():
            low, high = BASELINE_BANDS[band_key]
            expr = f'{MetricName.HTTP_REQUEST_DURATION_MILLISECONDS.value}{{quantile="{quantile}"}}'
            value = scalar(client, expr)
            assert low <= value <= high, f"{expr}={value} outside [{low}, {high}]"


@pytest.mark.smoke
def test_counters_advance_so_the_derived_rate_panels_have_data():
    """Throughput and error percentage are rate() ratios. A flat counter renders them as null.

    The rate() *value* is deliberately not asserted against the 127-163 req/s band here: right
    after `docker compose up` the 15s window the dashboards use is only partly filled, and
    Prometheus extrapolates over the gap. Monotonic growth plus a positive rate is the claim
    that holds at any uptime; the exact band is asserted on the instantaneous snapshot in
    test_baseline_telemetry.py.
    """
    requests_total = MetricName.HTTP_REQUESTS_TOTAL.value
    with httpx.Client(timeout=5.0) as client:
        before = scalar(client, requests_total)

        # Waited for, not slept through. A fixed 2s sleep flaked: at a 1s scrape interval two reads
        # can land inside the same scrape when the API is briefly busy — as it is immediately after
        # a container rebuild — and report a counter that is advancing as flat. The claim is that
        # the counter advances, not that it advances within any particular second.
        deadline = time.monotonic() + 20.0
        after = before
        while time.monotonic() < deadline:
            time.sleep(1.0)
            after = scalar(client, requests_total)
            if after > before:
                break

        assert after > before, f"{requests_total} never advanced ({before} -> {after})"

        # `rate()` needs two samples inside its window, and a container restarted seconds ago has
        # only one. Waited for rather than asserted outright: the claim is that the derived panels
        # get data, not that they have it within a second of boot.
        deadline = time.monotonic() + 20.0
        observed = 0.0
        while time.monotonic() < deadline:
            observed = scalar(client, f"rate({requests_total}[15s])")
            if observed > 0:
                break
            time.sleep(1.0)
        assert observed > 0, "rate() over the throughput counter never produced a value"


        # prometheus_client exports a counter at zero rather than omitting it, so the error
        # rate is a present series holding 0.0 — not an empty vector. That is what keeps the
        # 5xx panels reading "0.0%" at baseline instead of "No data".
        assert scalar(client, f"rate({MetricName.HTTP_5XX_ERRORS_TOTAL.value}[15s])") == 0.0


@pytest.mark.smoke
def test_grafana_datasources_are_provisioned_and_healthy():
    """Both datasources must resolve by the uid every dashboard panel hardcodes."""
    with httpx.Client(timeout=10.0, auth=GRAFANA_AUTH) as client:
        prometheus = client.get(f"{GRAFANA}/api/datasources/uid/{PROMETHEUS_DATASOURCE_UID}")
        assert prometheus.status_code == 200, f"Prometheus datasource missing: {prometheus.text}"
        assert prometheus.json()["type"] == "prometheus"

        jaeger = client.get(f"{GRAFANA}/api/datasources/uid/{JAEGER_DATASOURCE_UID}")
        assert jaeger.status_code == 200, f"Jaeger datasource missing: {jaeger.text}"
        assert jaeger.json()["type"] == "jaeger"

        health = client.get(f"{GRAFANA}/api/datasources/uid/{PROMETHEUS_DATASOURCE_UID}/health")
        assert health.status_code == 200, f"datasource health probe failed: {health.text}"
        assert health.json()["status"] == "OK", health.text


@pytest.mark.smoke
def test_every_dashboard_loaded_without_manual_setup():
    """Provisioned, not imported: `provisioned: true` is what makes the demo reproducible."""
    expected = provisioned_dashboards()
    assert expected, f"no dashboard JSON found under {DASHBOARD_DIR}"

    with httpx.Client(timeout=10.0, auth=GRAFANA_AUTH) as client:
        for uid, title in expected.items():
            resp = client.get(f"{GRAFANA}/api/dashboards/uid/{uid}")
            assert resp.status_code == 200, f"dashboard {uid} did not load: {resp.status_code} {resp.text}"
            body = resp.json()
            assert body["dashboard"]["title"] == title
            assert body["meta"]["provisioned"] is True, f"dashboard {uid} was not provisioned from disk"
            assert body["meta"]["folderTitle"] == "TripleTen Cloud Platform"


@pytest.mark.smoke
def test_the_home_dashboard_is_readable_without_signing_in():
    """A visitor clicking through from the War Room must land on a chart, not a login form."""
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(f"{GRAFANA}/api/dashboards/uid/tt-golden-signals")
        assert resp.status_code == 200, f"anonymous access is off: {resp.status_code} {resp.text}"
        assert resp.json()["dashboard"]["title"] == "TripleTen Cloud — Golden Signals"


@pytest.mark.smoke
def test_every_dashboard_panel_query_returns_data():
    """No panel may render "No data" on a healthy stack.

    Runs each committed panel expression through Grafana's own query API rather than straight
    at Prometheus, so the datasource wiring, the uid, and the plugin's step calculation are all
    on the path. A panel whose query is valid PromQL but resolves to an empty vector is the
    failure this catches, and it is invisible to every other check in the suite.
    """
    expressions: dict[str, str] = {}
    for path in sorted(DASHBOARD_DIR.glob("*.json")):
        dashboard = json.loads(path.read_text(encoding="utf-8"))
        for panel in iter_panels(dashboard):
            for target in panel.get("targets", []):
                expressions[f"{path.name}/{panel['title']}/{target['refId']}"] = target["expr"]

    assert expressions, f"no panel queries found under {DASHBOARD_DIR}"

    with httpx.Client(timeout=15.0, auth=GRAFANA_AUTH) as client:
        for label, expr in expressions.items():
            resp = client.post(
                f"{GRAFANA}/api/ds/query",
                json={
                    "from": "now-5m",
                    "to": "now",
                    "queries": [
                        {
                            "refId": "A",
                            "datasource": {"type": "prometheus", "uid": PROMETHEUS_DATASOURCE_UID},
                            "expr": expr,
                            "instant": True,
                        }
                    ],
                },
            )
            assert resp.status_code == 200, f"{label}: Grafana returned {resp.status_code} for {expr!r}"
            result = resp.json()["results"]["A"]
            assert result["status"] == 200, f"{label}: {result.get('error', result)}"
            assert result.get("frames"), f"{label}: {expr!r} returned no frames"
            # A frame with an empty time field is Grafana's "No data" state.
            rows = result["frames"][0]["data"]["values"][0]
            assert rows, f"{label}: {expr!r} resolved to an empty series — the panel renders No data"
