"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/tests/unit/test_chaos_math.py
Component:          Chaos & Decay Math Unit Tests
Purpose:            Unit test suite for baseline jitter, the smoothstep chaos ramp, per-scenario
                    peak profiles, and the exponential recovery decay curve.
Interacts With:     None (pure math)

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             Telemetry Math, Exponential Decay
Tools:              Pytest, Python 3.11
"""

import math
import random

import pytest

from incident_agent_api.telemetry import baseline, chaos, decay
from tripleten_contracts import BASELINE_BANDS, ScenarioId

SEED = 1337
SAMPLE_COUNT = 1000

# Peaks from the design spec §5.3, transcribed as literals so a change to the chaos tables
# has to be made here too rather than silently tracking the implementation.
SCENARIO_PEAKS: dict[ScenarioId, dict[str, float]] = {
    ScenarioId.DB_POOL_EXHAUSTION: {
        "db_pool_utilization_pct": 98.5,
        "http_5xx_error_rate_pct": 36.4,
        "latency_p99_ms": 4820.0,
        "latency_p95_ms": 2960.0,
        "requests_per_sec": 42.0,
    },
    ScenarioId.CACHE_THUNDERING_HERD: {
        "redis_memory_utilization_pct": 97.8,
        "cache_hit_ratio_pct": 14.1,
        "latency_p95_ms": 1840.0,
        "latency_p99_ms": 2598.0,
    },
    ScenarioId.WORKER_DEADLOCK: {
        "sqs_active_queue_depth": 1540.0,
    },
}


@pytest.fixture
def rng() -> random.Random:
    """A seeded generator, so every assertion below is bit-for-bit reproducible."""
    return random.Random(SEED)


# ---------------------------------------------------------------------------
# 1. Baseline steady state
# ---------------------------------------------------------------------------


def test_every_baseline_sample_lands_inside_its_documented_band(rng):
    for i in range(SAMPLE_COUNT):
        values = baseline.sample(t=i * 0.5, rng=rng)
        for field, (low, high) in BASELINE_BANDS.items():
            assert low <= values[field] <= high, f"{field}={values[field]} outside [{low}, {high}] at i={i}"


def test_baseline_sample_covers_exactly_the_banded_fields(rng):
    assert set(baseline.sample(t=0.0, rng=rng)) == set(BASELINE_BANDS)


def test_throughput_sine_term_stays_within_fifteen_of_its_centre():
    """145 + 15·sin(t/10) may never leave [130, 160]; jitter adds the remaining ±3."""
    for step in range(2000):
        nominal = baseline.throughput_nominal(t=step * 0.1)
        assert 130.0 <= nominal <= 160.0


def test_throughput_sine_completes_a_full_period_over_its_wavelength():
    """Guards against a t/10 typo: the wave must actually reach both extremes."""
    samples = [baseline.throughput_nominal(t=step * 0.5) for step in range(int(20 * math.pi * 2))]
    assert max(samples) == pytest.approx(160.0, abs=0.2)
    assert min(samples) == pytest.approx(130.0, abs=0.2)


def test_baseline_integer_fields_are_integers(rng):
    values = baseline.sample(t=0.0, rng=rng)
    for field in ("sqs_active_queue_depth", "dlq_message_count", "active_workers_count"):
        assert float(values[field]).is_integer()


def test_baseline_error_rate_is_exactly_zero(rng):
    for i in range(100):
        assert baseline.sample(t=float(i), rng=rng)["http_5xx_error_rate_pct"] == 0.0


# ---------------------------------------------------------------------------
# 2. The smoothstep ramp
# ---------------------------------------------------------------------------


def test_smoothstep_is_zero_at_the_start_of_the_ramp():
    assert chaos.smoothstep(0.0, chaos.RAMP_SECONDS) == 0.0


def test_smoothstep_is_exactly_half_way_at_the_ramp_midpoint():
    assert chaos.smoothstep(chaos.RAMP_SECONDS / 2, chaos.RAMP_SECONDS) == 0.5


def test_smoothstep_saturates_at_the_end_of_the_ramp():
    assert chaos.smoothstep(chaos.RAMP_SECONDS, chaos.RAMP_SECONDS) == 1.0


def test_smoothstep_stays_saturated_after_the_ramp():
    assert chaos.smoothstep(chaos.RAMP_SECONDS * 10, chaos.RAMP_SECONDS) == 1.0


def test_smoothstep_clamps_negative_time_to_zero():
    assert chaos.smoothstep(-5.0, chaos.RAMP_SECONDS) == 0.0


def test_smoothstep_never_decreases():
    previous = -1.0
    for step in range(200):
        current = chaos.smoothstep(step * 0.02, chaos.RAMP_SECONDS)
        assert current >= previous
        previous = current


# ---------------------------------------------------------------------------
# 3. Per-scenario chaos profiles
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", list(SCENARIO_PEAKS))
def test_chaos_starts_from_the_nominal_baseline(scenario, rng):
    """At t=0 the ramp has not moved: every ramped metric still reads its nominal."""
    values = chaos.apply(baseline.nominals(t=0.0), scenario, t_chaos=0.0, rng=rng)
    for field in SCENARIO_PEAKS[scenario]:
        assert values[field] == pytest.approx(baseline.NOMINALS[field], abs=1e-9)


@pytest.mark.parametrize("scenario", list(SCENARIO_PEAKS))
def test_chaos_reaches_its_documented_peak_exactly_at_the_end_of_the_ramp(scenario, rng):
    for field, peak in SCENARIO_PEAKS[scenario].items():
        ramp = chaos.ramp_seconds_for(field)
        values = chaos.apply(baseline.nominals(t=0.0), scenario, t_chaos=ramp, rng=rng)
        assert values[field] == pytest.approx(peak, abs=1e-9), f"{scenario}/{field}"


def test_worker_deadlock_steps_workers_and_dlq_immediately(rng):
    """A deadlock is instantaneous: workers drop and the poison message lands at t=0."""
    values = chaos.apply(baseline.nominals(t=0.0), ScenarioId.WORKER_DEADLOCK, t_chaos=0.0, rng=rng)
    assert values["active_workers_count"] == 0
    assert values["dlq_message_count"] == 1


def test_worker_deadlock_leaves_every_golden_signal_at_baseline(rng):
    """The teaching point: a queue backlog the golden-signals bar alone would not catch."""
    values = chaos.apply(baseline.sample(t=0.0, rng=rng), ScenarioId.WORKER_DEADLOCK, t_chaos=30.0, rng=rng)
    for field in ("requests_per_sec", "http_5xx_error_rate_pct", "latency_p50_ms", "latency_p95_ms", "latency_p99_ms"):
        low, high = BASELINE_BANDS[field]
        assert low <= values[field] <= high


def test_queue_depth_uses_the_longer_ramp():
    assert chaos.ramp_seconds_for("sqs_active_queue_depth") == chaos.QUEUE_RAMP_SECONDS
    assert chaos.ramp_seconds_for("db_pool_utilization_pct") == chaos.RAMP_SECONDS
    assert chaos.QUEUE_RAMP_SECONDS > chaos.RAMP_SECONDS


def test_queue_depth_is_still_climbing_when_the_short_ramp_has_finished(rng):
    """The 8s queue ramp must not silently collapse back to the 2s one."""
    mid = chaos.apply(baseline.nominals(t=0.0), ScenarioId.WORKER_DEADLOCK, t_chaos=2.0, rng=rng)
    later = chaos.apply(baseline.nominals(t=0.0), ScenarioId.WORKER_DEADLOCK, t_chaos=6.0, rng=rng)
    assert mid["sqs_active_queue_depth"] < later["sqs_active_queue_depth"] < 1540


def test_cache_stampede_throughput_oscillates_across_its_documented_range(rng):
    """Scenario 2 overrides throughput outright rather than ramping to a single peak."""
    observed = [
        chaos.apply(baseline.nominals(t=0.0), ScenarioId.CACHE_THUNDERING_HERD, t_chaos=step * 0.1, rng=rng)[
            "requests_per_sec"
        ]
        for step in range(200)
    ]
    assert min(observed) == pytest.approx(60.0, abs=1.0)
    assert max(observed) == pytest.approx(180.0, abs=1.0)
    for value in observed:
        assert 60.0 <= value <= 180.0


def test_prompt_injection_injects_no_chaos_at_all(rng):
    """Scenario 4 never causes an outage: every gauge holds its baseline sample."""
    for step in range(200):
        clean = baseline.sample(t=step * 0.5, rng=random.Random(SEED + step))
        chaotic = chaos.apply(dict(clean), ScenarioId.PROMPT_INJECTION, t_chaos=step * 0.5, rng=rng)
        assert chaotic == clean


def test_every_scenario_has_a_profile():
    assert set(chaos.PROFILES) == set(ScenarioId)


# ---------------------------------------------------------------------------
# 4. Hold-at-peak jitter
# ---------------------------------------------------------------------------


def test_hold_jitter_keeps_values_within_one_percent_of_peak(rng):
    peak = SCENARIO_PEAKS[ScenarioId.DB_POOL_EXHAUSTION]["latency_p99_ms"]
    for step in range(500):
        values = chaos.apply(
            baseline.nominals(t=0.0), ScenarioId.DB_POOL_EXHAUSTION, t_chaos=5.0 + step * 0.1, rng=rng
        )
        assert abs(values["latency_p99_ms"] - peak) <= peak * 0.01 + 1e-9


def test_hold_jitter_actually_moves_the_value(rng):
    """A dead-flat hold would sit on screen for the length of the approval pause."""
    observed = {
        chaos.apply(baseline.nominals(t=0.0), ScenarioId.DB_POOL_EXHAUSTION, t_chaos=5.0 + step * 0.1, rng=rng)[
            "latency_p99_ms"
        ]
        for step in range(50)
    }
    assert len(observed) > 1


def test_percentages_never_leave_zero_to_one_hundred_under_hold_jitter(rng):
    """db_pool peaks at 98.5 and redis at 97.8; +1% jitter must not push either past 100."""
    for scenario in (ScenarioId.DB_POOL_EXHAUSTION, ScenarioId.CACHE_THUNDERING_HERD):
        for step in range(500):
            values = chaos.apply(baseline.nominals(t=0.0), scenario, t_chaos=3.0 + step * 0.1, rng=rng)
            for field, value in values.items():
                if field.endswith("_pct"):
                    assert 0.0 <= value <= 100.0, f"{scenario}/{field}={value}"


def test_stepped_metrics_do_not_jitter(rng):
    for step in range(100):
        values = chaos.apply(baseline.nominals(t=0.0), ScenarioId.WORKER_DEADLOCK, t_chaos=20.0 + step, rng=rng)
        assert values["active_workers_count"] == 0
        assert values["dlq_message_count"] == 1


def test_integer_metrics_stay_integral_through_ramp_and_hold(rng):
    for t_chaos in (0.0, 1.0, 4.0, 8.0, 30.0):
        values = chaos.apply(baseline.nominals(t=0.0), ScenarioId.WORKER_DEADLOCK, t_chaos=t_chaos, rng=rng)
        for field in ("sqs_active_queue_depth", "dlq_message_count", "active_workers_count"):
            assert float(values[field]).is_integer(), f"{field} at t={t_chaos}"


# ---------------------------------------------------------------------------
# 5. Percentile ordering, in every scenario at every phase
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", list(ScenarioId))
@pytest.mark.parametrize("t_chaos", [0.0, 0.5, 1.0, 2.0, 4.0, 10.0])
def test_percentiles_stay_ordered_during_chaos(scenario, t_chaos, rng):
    values = chaos.apply(baseline.sample(t=t_chaos, rng=rng), scenario, t_chaos=t_chaos, rng=rng)
    assert values["latency_p50_ms"] <= values["latency_p95_ms"] <= values["latency_p99_ms"]


@pytest.mark.parametrize("scenario", [s for s in ScenarioId if s.causes_outage])
@pytest.mark.parametrize("t_decay", [0.0, 0.5, 1.0, 2.0, 3.0, 4.0])
def test_percentiles_stay_ordered_during_recovery(scenario, t_decay):
    values = decay.apply(baseline.nominals(t=0.0), scenario, t_decay=t_decay)
    assert values["latency_p50_ms"] <= values["latency_p95_ms"] <= values["latency_p99_ms"]


# ---------------------------------------------------------------------------
# 6. Exponential recovery decay
# ---------------------------------------------------------------------------


def test_decay_constant_is_the_documented_value():
    assert decay.DECAY_K == 1.8
    assert decay.DECAY_DURATION_SECONDS == 4.0


def test_decay_returns_the_peak_at_time_zero():
    assert decay.decayed(nominal=48.0, peak=4820.0, t=0.0) == 4820.0


@pytest.mark.parametrize(
    ("t", "expected"),
    [(0.0, 4820.00), (1.0, 836.81), (2.0, 178.39), (3.0, 69.55), (4.0, 51.56)],
)
def test_p99_decay_matches_the_documented_checkpoints(t, expected):
    assert decay.decayed(nominal=48.0, peak=4820.0, t=t) == pytest.approx(expected, abs=1.0)


def test_p99_lands_inside_the_baseline_band_when_the_decay_loop_ends():
    low, high = BASELINE_BANDS["latency_p99_ms"]
    settled = decay.decayed(nominal=48.0, peak=4820.0, t=decay.DECAY_DURATION_SECONDS)
    assert low <= settled <= high


def test_error_rate_decays_to_effectively_zero():
    assert decay.decayed(nominal=0.0, peak=36.4, t=2.0) == pytest.approx(0.99, abs=0.05)
    assert decay.decayed(nominal=0.0, peak=36.4, t=4.0) == pytest.approx(0.0, abs=0.05)


def test_db_pool_decays_back_into_its_baseline_band():
    low, high = BASELINE_BANDS["db_pool_utilization_pct"]
    assert low <= decay.decayed(nominal=15.0, peak=98.5, t=4.0) <= high


def test_decay_is_monotonic_towards_the_nominal():
    previous = math.inf
    for step in range(41):
        current = decay.decayed(nominal=48.0, peak=4820.0, t=step * 0.1)
        assert current < previous
        previous = current


def test_decay_works_for_metrics_whose_peak_is_below_baseline():
    """Throughput collapses to 42 and cache hits to 14.1; both recover upwards."""
    assert decay.decayed(nominal=145.0, peak=42.0, t=0.0) == 42.0
    assert decay.decayed(nominal=145.0, peak=42.0, t=4.0) == pytest.approx(145.0, abs=0.5)
    assert decay.decayed(nominal=99.0, peak=14.1, t=4.0) == pytest.approx(99.0, abs=0.5)


def test_decay_is_deterministic_and_takes_no_generator():
    """Recovery carries no jitter, which is what makes the checkpoints exact."""
    first = decay.apply(baseline.nominals(t=0.0), ScenarioId.DB_POOL_EXHAUSTION, t_decay=1.0)
    second = decay.apply(baseline.nominals(t=0.0), ScenarioId.DB_POOL_EXHAUSTION, t_decay=1.0)
    assert first == second


def test_recovery_restores_the_worker_pool_immediately():
    """reboot_workers has landed by the time the callback arrives."""
    values = decay.apply(baseline.nominals(t=0.0), ScenarioId.WORKER_DEADLOCK, t_decay=0.0)
    assert values["active_workers_count"] == 4


def test_recovery_holds_the_poison_message_in_the_dead_letter_queue():
    """The message really is in customer-dlq; it clears on return to HEALTHY, not during decay."""
    for t_decay in (0.0, 2.0, 4.0):
        values = decay.apply(baseline.nominals(t=0.0), ScenarioId.WORKER_DEADLOCK, t_decay=t_decay)
        assert values["dlq_message_count"] == 1


def test_recovery_drains_the_queue_backlog():
    values = decay.apply(baseline.nominals(t=0.0), ScenarioId.WORKER_DEADLOCK, t_decay=4.0)
    low, high = BASELINE_BANDS["sqs_active_queue_depth"]
    assert low <= values["sqs_active_queue_depth"] <= high


def test_prompt_injection_has_no_decay_phase():
    """Nothing left baseline, so there is nothing to recover from."""
    nominals = baseline.nominals(t=0.0)
    assert decay.apply(dict(nominals), ScenarioId.PROMPT_INJECTION, t_decay=0.0) == nominals


def test_decay_integer_metrics_stay_integral():
    for t_decay in (0.0, 1.5, 4.0):
        values = decay.apply(baseline.nominals(t=0.0), ScenarioId.WORKER_DEADLOCK, t_decay=t_decay)
        for field in ("sqs_active_queue_depth", "dlq_message_count", "active_workers_count"):
            assert float(values[field]).is_integer()
