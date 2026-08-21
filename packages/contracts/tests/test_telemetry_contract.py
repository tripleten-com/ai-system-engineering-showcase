"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             packages/contracts/tests/test_telemetry_contract.py
Component:          Telemetry Contract Conformance Tests
Purpose:            Asserts the Prometheus metric roster, baseline bands, scenario slugs, and the
                    system_health_status mapping match the telemetry specification exactly.
Interacts With:     None (pure contract assertions)

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             Contract Testing, Golden Signals, Drift Prevention
Tools:              Pytest, Pydantic 2, Python 3.11
"""

import pytest
from pydantic import ValidationError

from tripleten_contracts import (
    BASELINE_BANDS,
    METRIC_KINDS,
    SCENARIO_SLUG,
    GoldenSignals,
    IncidentState,
    InfrastructureMetrics,
    MetricKind,
    MetricName,
    Quantile,
    ScenarioId,
    health_status_for,
)

# The mapping table in telemetry-and-chaos-engine.md §3, transcribed as (state, scenario) -> value.
# Scenarios 1-3 share a column; prompt_injection is the divergent one.
OUTAGE_SCENARIOS = [s for s in ScenarioId if s is not ScenarioId.PROMPT_INJECTION]


def test_metric_roster_is_exactly_the_eleven_canonical_families():
    """§3 fixes the exposition roster; /metrics may expose no more and no fewer."""
    assert {m.value for m in MetricName} == {
        "http_requests_total",
        "http_5xx_errors_total",
        "security_violations_total",
        "http_request_duration_milliseconds",
        "db_pool_utilization_pct",
        "redis_memory_utilization_pct",
        "cache_hit_ratio_pct",
        "sqs_active_queue_depth",
        "dlq_message_count",
        "active_workers_count",
        "system_health_status",
    }


def test_only_the_three_total_families_are_counters():
    counters = {name for name, kind in METRIC_KINDS.items() if kind is MetricKind.COUNTER}
    assert counters == {
        MetricName.HTTP_REQUESTS_TOTAL,
        MetricName.HTTP_5XX_ERRORS_TOTAL,
        MetricName.SECURITY_VIOLATIONS_TOTAL,
    }


def test_every_metric_has_a_declared_kind():
    assert set(METRIC_KINDS) == set(MetricName)


def test_quantiles_are_the_three_canonical_labels():
    assert [q.value for q in Quantile] == ["p50", "p95", "p99"]


def test_baseline_bands_match_the_steady_state_table():
    """The §3 generator table, band by band. These bands gate the unit and smoke suites."""
    assert BASELINE_BANDS["requests_per_sec"] == (127.0, 163.0)
    assert BASELINE_BANDS["http_5xx_error_rate_pct"] == (0.0, 0.0)
    assert BASELINE_BANDS["latency_p50_ms"] == (16.5, 20.5)
    assert BASELINE_BANDS["latency_p95_ms"] == (31.0, 37.0)
    assert BASELINE_BANDS["latency_p99_ms"] == (44.0, 52.0)
    assert BASELINE_BANDS["db_pool_utilization_pct"] == (13.0, 17.0)
    assert BASELINE_BANDS["redis_memory_utilization_pct"] == (39.0, 41.0)
    assert BASELINE_BANDS["cache_hit_ratio_pct"] == (98.5, 99.0)
    assert BASELINE_BANDS["sqs_active_queue_depth"] == (2.0, 6.0)
    assert BASELINE_BANDS["dlq_message_count"] == (0.0, 0.0)
    assert BASELINE_BANDS["active_workers_count"] == (4.0, 4.0)


def test_baseline_bands_cover_every_simulated_snapshot_field():
    """A banded field is one the generator samples; the other two are state- and event-driven."""
    simulated = set(GoldenSignals.model_fields) | set(InfrastructureMetrics.model_fields)
    simulated -= {"system_health_status", "security_violations_total"}
    assert set(BASELINE_BANDS) == simulated


def test_every_band_is_ordered_low_to_high():
    for field, (low, high) in BASELINE_BANDS.items():
        assert low <= high, f"{field} band is inverted"


def test_scenario_slugs_are_distinct_and_canonical():
    assert SCENARIO_SLUG == {
        ScenarioId.DB_POOL_EXHAUSTION: "db",
        ScenarioId.CACHE_THUNDERING_HERD: "cache",
        ScenarioId.WORKER_DEADLOCK: "worker",
        ScenarioId.PROMPT_INJECTION: "sec",
    }
    assert len(set(SCENARIO_SLUG.values())) == len(ScenarioId)


def test_healthy_is_ok_regardless_of_scenario():
    assert health_status_for(IncidentState.HEALTHY, None) == 1
    for scenario in ScenarioId:
        assert health_status_for(IncidentState.HEALTHY, scenario) == 1


@pytest.mark.parametrize(
    "state",
    [IncidentState.CRITICAL_OUTAGE, IncidentState.AWAITING_APPROVAL, IncidentState.EXECUTING],
)
def test_outage_scenarios_report_down_through_the_incident(state):
    for scenario in OUTAGE_SCENARIOS:
        assert health_status_for(state, scenario) == 0


def test_recovering_reports_degraded():
    for scenario in OUTAGE_SCENARIOS:
        assert health_status_for(IncidentState.RECOVERING, scenario) == 2


@pytest.mark.parametrize(
    "state",
    [
        IncidentState.EXPLOIT_INTERCEPTED,
        IncidentState.AWAITING_APPROVAL,
        IncidentState.EXECUTING,
        IncidentState.SECURITY_CONTAINED,
        # The terminal branches belong here too. A declined containment plan, or a containment
        # worker that exhausted its retries, leaves the platform exactly as it found it: no
        # chaos math ever ran, so there is no outage to report and nothing to recover from.
        IncidentState.REJECTED,
        IncidentState.FAILED,
    ],
)
def test_scenario_four_reports_degraded_for_its_entire_run(state):
    """Scenario 4 never causes an outage: it sits at Degraded end to end, never Down."""
    assert health_status_for(state, ScenarioId.PROMPT_INJECTION) == 2


@pytest.mark.parametrize("state", [IncidentState.REJECTED, IncidentState.FAILED])
def test_unremediated_terminal_states_report_down_on_the_outage_path(state):
    """Chaos persists after a rejection or a failure, so Scenarios 1-3 stay Down until reset."""
    for scenario in OUTAGE_SCENARIOS:
        assert health_status_for(state, scenario) == 0


@pytest.mark.parametrize("state", [IncidentState.REJECTED, IncidentState.FAILED])
def test_a_declined_or_failed_containment_never_reports_an_outage(state):
    """Regression: REJECTED and FAILED were once Down for every scenario, Scenario 4 included.

    That put `system_health_status` at 0 while every infrastructure gauge in the same frame
    read baseline, and it broke the NO CUSTOMER IMPACT claim the War Room holds up for the
    whole security run. The gauge and the gauges have to tell the same story.
    """
    assert health_status_for(state, ScenarioId.PROMPT_INJECTION) == 2


@pytest.mark.parametrize(
    "state",
    [
        IncidentState.AWAITING_APPROVAL,
        IncidentState.EXECUTING,
        IncidentState.REJECTED,
        IncidentState.FAILED,
    ],
)
def test_post_interception_states_diverge_on_the_security_scenario(state):
    """Every state reachable from both paths resolves by scenario, not by state alone."""
    assert health_status_for(state, ScenarioId.PROMPT_INJECTION) == 2
    assert health_status_for(state, ScenarioId.DB_POOL_EXHAUSTION) == 0


def test_health_status_is_total_over_every_state():
    """No state may raise: the gauge is written on every tick, in every state."""
    for state in IncidentState:
        for scenario in (None, *ScenarioId):
            assert health_status_for(state, scenario) in {0, 1, 2}


def test_percentage_fields_reject_values_outside_zero_to_one_hundred():
    with pytest.raises(ValidationError):
        InfrastructureMetrics(
            system_health_status=1,
            db_pool_utilization_pct=101.0,
            redis_memory_utilization_pct=40.0,
            cache_hit_ratio_pct=99.0,
            sqs_active_queue_depth=3,
            dlq_message_count=0,
            active_workers_count=4,
            security_violations_total=0,
        )


def test_queue_counts_reject_negative_values():
    with pytest.raises(ValidationError):
        InfrastructureMetrics(
            system_health_status=1,
            db_pool_utilization_pct=15.0,
            redis_memory_utilization_pct=40.0,
            cache_hit_ratio_pct=99.0,
            sqs_active_queue_depth=-1,
            dlq_message_count=0,
            active_workers_count=4,
            security_violations_total=0,
        )


def test_health_status_field_rejects_values_outside_the_enum_range():
    with pytest.raises(ValidationError):
        InfrastructureMetrics(
            system_health_status=3,
            db_pool_utilization_pct=15.0,
            redis_memory_utilization_pct=40.0,
            cache_hit_ratio_pct=99.0,
            sqs_active_queue_depth=3,
            dlq_message_count=0,
            active_workers_count=4,
            security_violations_total=0,
        )


def test_golden_signals_reject_negative_latency():
    with pytest.raises(ValidationError):
        GoldenSignals(
            requests_per_sec=145.0,
            http_5xx_error_rate_pct=0.0,
            latency_p50_ms=-1.0,
            latency_p95_ms=34.0,
            latency_p99_ms=48.0,
        )
