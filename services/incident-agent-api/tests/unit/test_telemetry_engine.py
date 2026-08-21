"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/tests/unit/test_telemetry_engine.py
Component:          Telemetry Engine, Registry & State Machine Unit Tests
Purpose:            Unit tests for the Prometheus exposition roster, counter monotonicity, the
                    run state machine's transition guard, and the 1-second engine tick.
Interacts With:     None (in-process engine, no containers)

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             Prometheus Exposition, State Machine Design, Deterministic Simulation
Tools:              Pytest, prometheus-client, Python 3.11
"""

import asyncio
import contextlib
import random

import pytest

from incident_agent_api.telemetry import registry as registry_module
from incident_agent_api.telemetry.engine import NoRecoveryPhaseError, TelemetryEngine, stop_task
from incident_agent_api.telemetry.state_machine import (
    IllegalTransitionError,
    IncidentAlreadyActiveError,
    TelemetryStateMachine,
    UnknownIncidentError,
)
from tripleten_contracts import (
    BASELINE_BANDS,
    METRIC_KINDS,
    SCENARIO_SLUG,
    IncidentState,
    MetricKind,
    MetricName,
    Quantile,
    ScenarioId,
)

SEED = 1337


class FakeClock:
    """A manually advanced monotonic clock, so tick timing is exact instead of wall-dependent."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def engine(clock: FakeClock) -> TelemetryEngine:
    return TelemetryEngine(clock=clock, rng=random.Random(SEED))


def counter_value(name: str) -> float:
    """Reads a counter's current total straight out of the exposition registry."""
    return registry_module.REGISTRY.get_sample_value(name) or 0.0


def rendered_families() -> dict[str, str]:
    """Parses the exposition's TYPE lines into {family name: prometheus type}.

    Read off the raw text rather than via text_string_to_metric_families, because that parser
    normalises a counter's `_total` suffix away (`http_requests_total` becomes `http_requests`)
    while the exposition Prometheus actually scrapes keeps it. The suffix is the contract.
    """
    body, _ = registry_module.render()
    families: dict[str, str] = {}
    for line in body.decode().splitlines():
        if line.startswith("# TYPE "):
            _, _, name, metric_type = line.split(" ", 3)
            families[name] = metric_type
    return families


# ---------------------------------------------------------------------------
# Prometheus exposition roster
# ---------------------------------------------------------------------------


def test_exposition_contains_exactly_the_canonical_metric_roster(engine):
    engine.tick()
    assert set(rendered_families()) == {m.value for m in MetricName}


def test_exposition_carries_no_created_series(engine):
    """prometheus_client emits a companion _created gauge per counter unless it is disabled."""
    engine.tick()
    body, _ = registry_module.render()
    assert "_created" not in body.decode()


def test_exposition_carries_no_default_process_collectors(engine):
    engine.tick()
    text = registry_module.render()[0].decode()
    for unwanted in ("process_", "python_gc_", "python_info"):
        assert unwanted not in text


def test_metric_types_match_the_declared_kinds(engine):
    engine.tick()
    families = rendered_families()
    for name, kind in METRIC_KINDS.items():
        expected = "counter" if kind is MetricKind.COUNTER else "gauge"
        assert families[name.value] == expected, name


def test_latency_gauge_exposes_all_three_quantile_labels(engine):
    engine.tick()
    for quantile in Quantile:
        value = registry_module.REGISTRY.get_sample_value(
            MetricName.HTTP_REQUEST_DURATION_MILLISECONDS.value, {"quantile": quantile.value}
        )
        assert value is not None, quantile


def test_exposition_exports_no_precomputed_rate_or_percentage_from_a_counter(engine):
    """Throughput and error percentage exist only as PromQL rate() ratios in Grafana."""
    engine.tick()
    for name in rendered_families():
        assert not name.endswith("_per_second")
        assert not (name.endswith("_rate_pct") or name.endswith("_per_sec"))


def test_content_type_is_the_prometheus_text_format(engine):
    _, content_type = registry_module.render()
    assert "text/plain" in content_type


# ---------------------------------------------------------------------------
# Counter semantics
# ---------------------------------------------------------------------------


def test_request_counter_never_decreases_across_an_incident_and_a_reset(engine, clock):
    previous = counter_value(MetricName.HTTP_REQUESTS_TOTAL.value)
    for i in range(100):
        if i == 30:
            engine.trigger(ScenarioId.DB_POOL_EXHAUSTION)
        if i == 70:
            engine.reset(engine.machine.run.incident_id if engine.machine.run else None)
        clock.advance(1.0)
        engine.tick()
        current = counter_value(MetricName.HTTP_REQUESTS_TOTAL.value)
        assert current >= previous, f"counter went backwards at tick {i}"
        previous = current


def test_reset_clears_gauges_without_clearing_counters(engine, clock):
    engine.trigger(ScenarioId.DB_POOL_EXHAUSTION)
    clock.advance(3.0)
    engine.tick()
    before_reset = counter_value(MetricName.HTTP_REQUESTS_TOTAL.value)

    engine.reset(engine.machine.run.incident_id)
    engine.tick()

    assert counter_value(MetricName.HTTP_REQUESTS_TOTAL.value) >= before_reset
    pool = registry_module.REGISTRY.get_sample_value(MetricName.DB_POOL_UTILIZATION_PCT.value)
    low, high = BASELINE_BANDS["db_pool_utilization_pct"]
    assert low <= pool <= high


def test_error_counter_stays_flat_at_baseline(engine, clock):
    before = counter_value(MetricName.HTTP_5XX_ERRORS_TOTAL.value)
    for _ in range(20):
        clock.advance(1.0)
        engine.tick()
    assert counter_value(MetricName.HTTP_5XX_ERRORS_TOTAL.value) == before


def test_error_counter_climbs_during_an_error_flood(engine, clock):
    before = counter_value(MetricName.HTTP_5XX_ERRORS_TOTAL.value)
    engine.trigger(ScenarioId.DB_POOL_EXHAUSTION)
    for _ in range(5):
        clock.advance(1.0)
        engine.tick()
    assert counter_value(MetricName.HTTP_5XX_ERRORS_TOTAL.value) > before


def test_security_counter_increments_exactly_once_per_injection_run(engine, clock):
    before = counter_value(MetricName.SECURITY_VIOLATIONS_TOTAL.value)
    engine.trigger(ScenarioId.PROMPT_INJECTION)
    for _ in range(10):
        clock.advance(1.0)
        engine.tick()
    assert counter_value(MetricName.SECURITY_VIOLATIONS_TOTAL.value) == before + 1


def test_security_counter_ignores_the_outage_scenarios(engine, clock):
    for scenario in (ScenarioId.DB_POOL_EXHAUSTION, ScenarioId.CACHE_THUNDERING_HERD, ScenarioId.WORKER_DEADLOCK):
        before = counter_value(MetricName.SECURITY_VIOLATIONS_TOTAL.value)
        run = engine.trigger(scenario)
        for _ in range(5):
            clock.advance(1.0)
            engine.tick()
        engine.reset(run.incident_id)
        assert counter_value(MetricName.SECURITY_VIOLATIONS_TOTAL.value) == before


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


def test_a_fresh_machine_is_healthy_with_no_run():
    machine = TelemetryStateMachine()
    assert machine.state is IncidentState.HEALTHY
    assert machine.run is None
    assert machine.scenario_id is None


def test_outage_scenarios_open_in_critical_outage():
    machine = TelemetryStateMachine()
    run = machine.trigger(ScenarioId.DB_POOL_EXHAUSTION, now=0.0)
    assert run.state is IncidentState.CRITICAL_OUTAGE


def test_injection_scenario_opens_in_exploit_intercepted():
    """Scenario 4 never enters CRITICAL_OUTAGE — the attack is not an outage."""
    machine = TelemetryStateMachine()
    run = machine.trigger(ScenarioId.PROMPT_INJECTION, now=0.0)
    assert run.state is IncidentState.EXPLOIT_INTERCEPTED


def test_triggering_while_a_run_is_active_is_refused():
    machine = TelemetryStateMachine()
    machine.trigger(ScenarioId.DB_POOL_EXHAUSTION, now=0.0)
    with pytest.raises(IncidentAlreadyActiveError):
        machine.trigger(ScenarioId.WORKER_DEADLOCK, now=1.0)


def test_generated_identifiers_carry_the_scenario_slug():
    machine = TelemetryStateMachine()
    run = machine.trigger(ScenarioId.CACHE_THUNDERING_HERD, now=0.0)
    assert run.incident_id.startswith("inc-")
    assert run.incident_id.endswith("-cache")
    assert run.thread_id.startswith("thread-")


def test_identifiers_are_unique_across_runs():
    machine = TelemetryStateMachine()
    seen = set()
    for _ in range(50):
        run = machine.trigger(ScenarioId.DB_POOL_EXHAUSTION, now=0.0)
        seen.add(run.incident_id)
        machine.reset(run.incident_id)
    assert len(seen) == 50


def test_the_incident_id_carries_enough_entropy_for_that_uniqueness_claim():
    """Pins the width structurally, because the test above can only sample it.

    A probabilistic assertion cannot distinguish "unique" from "narrow and lucky". At the
    original 2 bytes the 50-draw check above collided on 1.85% of runs -- it had been failing
    roughly one CI run in 54 since Stage 1, which reads as infrastructure flakiness rather
    than as the defect it was. This asserts the property that makes the sampling meaningful,
    so narrowing the id fails here immediately and deterministically instead of surfacing as
    an occasional red build weeks later.
    """
    machine = TelemetryStateMachine()
    run = machine.trigger(ScenarioId.DB_POOL_EXHAUSTION, now=0.0)

    prefix, entropy, slug = run.incident_id.split("-")
    assert prefix == "inc"
    assert slug == SCENARIO_SLUG[ScenarioId.DB_POOL_EXHAUSTION]
    assert len(entropy) == 8, f"expected 32 bits of hex entropy, got {entropy!r}"
    assert int(entropy, 16) >= 0, f"{entropy!r} is not hex"


def test_illegal_transitions_are_refused_and_change_nothing():
    """The transition guard is the machine-checkable form of the HITL guarantee."""
    machine = TelemetryStateMachine()
    machine.trigger(ScenarioId.DB_POOL_EXHAUSTION, now=0.0)
    with pytest.raises(IllegalTransitionError):
        machine.transition(IncidentState.EXECUTING)
    assert machine.state is IncidentState.CRITICAL_OUTAGE


def test_executing_is_reachable_only_through_the_approval_gate():
    machine = TelemetryStateMachine()
    machine.trigger(ScenarioId.DB_POOL_EXHAUSTION, now=0.0)
    machine.transition(IncidentState.AWAITING_APPROVAL)
    machine.transition(IncidentState.EXECUTING)
    assert machine.state is IncidentState.EXECUTING


def test_transitioning_without_an_active_run_is_refused():
    machine = TelemetryStateMachine()
    with pytest.raises(IllegalTransitionError):
        machine.transition(IncidentState.CRITICAL_OUTAGE)


def test_reset_without_an_active_run_is_an_idempotent_no_op():
    """A double-click on Master Reset must not raise."""
    machine = TelemetryStateMachine()
    machine.reset(None)
    machine.reset("inc-dead-db")
    assert machine.state is IncidentState.HEALTHY


def test_reset_refuses_an_identifier_that_does_not_match_the_active_run():
    machine = TelemetryStateMachine()
    machine.trigger(ScenarioId.DB_POOL_EXHAUSTION, now=0.0)
    with pytest.raises(UnknownIncidentError):
        machine.reset("inc-9999-db")
    assert machine.state is IncidentState.CRITICAL_OUTAGE


def test_reset_returns_the_machine_to_healthy():
    machine = TelemetryStateMachine()
    run = machine.trigger(ScenarioId.DB_POOL_EXHAUSTION, now=0.0)
    machine.reset(run.incident_id)
    assert machine.state is IncidentState.HEALTHY
    assert machine.run is None


def test_chaos_clock_is_stamped_at_trigger_time():
    machine = TelemetryStateMachine()
    run = machine.trigger(ScenarioId.DB_POOL_EXHAUSTION, now=17.5)
    assert run.chaos_started_at == 17.5
    assert run.decay_started_at is None


# ---------------------------------------------------------------------------
# Engine tick and snapshot
# ---------------------------------------------------------------------------


def test_snapshot_is_healthy_baseline_before_any_incident(engine):
    engine.tick()
    snapshot = engine.snapshot()
    assert snapshot.state is IncidentState.HEALTHY
    assert snapshot.incident_id is None
    assert snapshot.thread_id is None
    assert snapshot.scenario_id is None
    assert snapshot.infrastructure.system_health_status == 1


def test_snapshot_metrics_sit_inside_the_baseline_bands(engine, clock):
    for _ in range(50):
        clock.advance(1.0)
        engine.tick()
        snapshot = engine.snapshot()
        for field, (low, high) in BASELINE_BANDS.items():
            value = getattr(snapshot.golden_signals, field, None)
            if value is None:
                value = getattr(snapshot.infrastructure, field)
            assert low <= value <= high, field


def test_snapshot_carries_the_run_identifiers_once_triggered(engine, clock):
    run = engine.trigger(ScenarioId.DB_POOL_EXHAUSTION)
    clock.advance(1.0)
    engine.tick()
    snapshot = engine.snapshot()
    assert snapshot.incident_id == run.incident_id
    assert snapshot.thread_id == run.thread_id
    assert snapshot.scenario_id is ScenarioId.DB_POOL_EXHAUSTION
    assert snapshot.state is IncidentState.CRITICAL_OUTAGE


def test_chaos_reaches_its_peak_two_seconds_after_the_trigger(engine, clock):
    engine.trigger(ScenarioId.DB_POOL_EXHAUSTION)
    clock.advance(2.0)
    engine.tick()
    signals = engine.snapshot()
    assert signals.golden_signals.latency_p99_ms == pytest.approx(4820.0, abs=1.0)
    assert signals.infrastructure.db_pool_utilization_pct == pytest.approx(98.5, abs=0.1)
    assert signals.golden_signals.http_5xx_error_rate_pct == pytest.approx(36.4, abs=0.1)


def test_health_gauge_reports_down_during_an_outage(engine, clock):
    engine.trigger(ScenarioId.DB_POOL_EXHAUSTION)
    clock.advance(1.0)
    engine.tick()
    assert registry_module.REGISTRY.get_sample_value(MetricName.SYSTEM_HEALTH_STATUS.value) == 0


def test_health_gauge_reports_degraded_for_the_whole_injection_run(engine, clock):
    engine.trigger(ScenarioId.PROMPT_INJECTION)
    for state in (IncidentState.AWAITING_APPROVAL, IncidentState.EXECUTING, IncidentState.SECURITY_CONTAINED):
        engine.machine.transition(state)
        clock.advance(1.0)
        engine.tick()
        assert registry_module.REGISTRY.get_sample_value(MetricName.SYSTEM_HEALTH_STATUS.value) == 2


def test_injection_run_holds_every_infrastructure_gauge_at_baseline(engine, clock):
    """The persistent NO CUSTOMER IMPACT claim has to be true in the numbers, not just the copy."""
    engine.trigger(ScenarioId.PROMPT_INJECTION)
    for _ in range(20):
        clock.advance(1.0)
        engine.tick()
        snapshot = engine.snapshot()
        for field in ("db_pool_utilization_pct", "redis_memory_utilization_pct", "cache_hit_ratio_pct"):
            low, high = BASELINE_BANDS[field]
            assert low <= getattr(snapshot.infrastructure, field) <= high
        low, high = BASELINE_BANDS["latency_p99_ms"]
        assert low <= snapshot.golden_signals.latency_p99_ms <= high


def test_terminal_rejected_state_holds_chaos_at_peak(engine, clock):
    """Rejecting the remediation leaves the outage running — that is the point of the branch."""
    engine.trigger(ScenarioId.DB_POOL_EXHAUSTION)
    engine.machine.transition(IncidentState.AWAITING_APPROVAL)
    engine.machine.transition(IncidentState.REJECTED)
    # Past the 2s ramp, so the assertion is about chaos persisting rather than about ramp timing.
    clock.advance(3.0)
    for _ in range(10):
        clock.advance(1.0)
        engine.tick()
        assert engine.snapshot().golden_signals.latency_p99_ms > 4000.0


def test_recovering_state_follows_the_decay_curve(engine, clock):
    engine.trigger(ScenarioId.DB_POOL_EXHAUSTION)
    engine.machine.transition(IncidentState.AWAITING_APPROVAL)
    engine.machine.transition(IncidentState.EXECUTING)
    clock.advance(5.0)
    engine.begin_recovery()

    clock.advance(2.0)
    engine.tick()
    assert engine.snapshot().golden_signals.latency_p99_ms == pytest.approx(178.4, abs=2.0)

    clock.advance(2.0)
    engine.tick()
    low, high = BASELINE_BANDS["latency_p99_ms"]
    assert low <= engine.snapshot().golden_signals.latency_p99_ms <= high


def test_recovery_completes_by_returning_the_run_to_healthy(engine, clock):
    engine.trigger(ScenarioId.DB_POOL_EXHAUSTION)
    engine.machine.transition(IncidentState.AWAITING_APPROVAL)
    engine.machine.transition(IncidentState.EXECUTING)
    engine.begin_recovery()
    clock.advance(4.5)
    engine.tick()
    assert engine.machine.state is IncidentState.HEALTHY
    assert engine.machine.run is None


class RecordingPublisher:
    """Captures (incident_id, status) per published frame, standing in for the SSE bus."""

    def __init__(self) -> None:
        self.frames: list[tuple[str | None, IncidentState]] = []

    def publish(self, payload, incident_id: str | None = None) -> object:
        self.frames.append((incident_id, payload.status))
        return payload


def test_the_frame_reporting_recovery_still_belongs_to_the_run_that_recovered(clock):
    """Regression: the recovery frame was stamped None, closing every scoped stream too early.

    A War Room streaming with ?incident_id= is closed as soon as a frame arrives that is not
    its run's. When the tick that completes a recovery cleared the run *before* publishing, the
    HEALTHY frame carried incident_id=None — so the scoped client was disconnected one frame
    before the recovery it had been waiting for, and the dashboard dropped out instead of going
    green. The run does end, and the client is closed on the following tick; it just gets to see
    how the story finished first.
    """
    publisher = RecordingPublisher()
    engine = TelemetryEngine(clock=clock, rng=random.Random(SEED), publisher=publisher)
    run = engine.trigger(ScenarioId.DB_POOL_EXHAUSTION)
    incident_id = run.incident_id
    engine.machine.transition(IncidentState.AWAITING_APPROVAL)
    engine.machine.transition(IncidentState.EXECUTING)
    engine.begin_recovery()

    publisher.frames.clear()
    clock.advance(4.5)
    engine.tick()

    assert publisher.frames == [(incident_id, IncidentState.HEALTHY)]
    assert engine.machine.run is None

    # And the run really is over: the next frame is platform baseline, which is what ends the
    # scoped stream and sends that client back through reconnect-and-rehydrate.
    clock.advance(1.0)
    engine.tick()
    assert publisher.frames[-1] == (None, IncidentState.HEALTHY)


def test_a_master_reset_frame_is_not_attributed_to_the_cleared_run(clock):
    """POST /api/incidents/reset must close scoped streams — that is what the guard is for."""
    publisher = RecordingPublisher()
    engine = TelemetryEngine(clock=clock, rng=random.Random(SEED), publisher=publisher)
    run = engine.trigger(ScenarioId.DB_POOL_EXHAUSTION)

    engine.reset(run.incident_id)
    publisher.frames.clear()
    engine.tick()

    assert publisher.frames == [(None, IncidentState.HEALTHY)]


def test_reset_from_a_terminal_state_returns_metrics_to_baseline(engine, clock):
    engine.trigger(ScenarioId.DB_POOL_EXHAUSTION)
    engine.machine.transition(IncidentState.AWAITING_APPROVAL)
    engine.machine.transition(IncidentState.REJECTED)
    clock.advance(3.0)
    engine.tick()

    engine.reset(engine.machine.run.incident_id)
    clock.advance(1.0)
    engine.tick()

    snapshot = engine.snapshot()
    assert snapshot.state is IncidentState.HEALTHY
    assert snapshot.infrastructure.system_health_status == 1
    low, high = BASELINE_BANDS["latency_p99_ms"]
    assert low <= snapshot.golden_signals.latency_p99_ms <= high


def test_worker_deadlock_backs_up_the_queue_without_touching_the_golden_signals(engine, clock):
    engine.trigger(ScenarioId.WORKER_DEADLOCK)
    clock.advance(8.0)
    engine.tick()
    snapshot = engine.snapshot()
    assert snapshot.infrastructure.sqs_active_queue_depth == 1540
    assert snapshot.infrastructure.active_workers_count == 0
    assert snapshot.infrastructure.dlq_message_count == 1
    low, high = BASELINE_BANDS["latency_p99_ms"]
    assert low <= snapshot.golden_signals.latency_p99_ms <= high


def test_snapshot_never_advances_the_state_machine(engine, clock):
    """GET /api/telemetry/current is a read: ten polls may not move the run."""
    engine.trigger(ScenarioId.DB_POOL_EXHAUSTION)
    clock.advance(1.0)
    engine.tick()
    before = engine.machine.state
    for _ in range(10):
        engine.snapshot()
    assert engine.machine.state is before


def test_snapshot_timestamp_is_timezone_aware(engine):
    engine.tick()
    assert engine.snapshot().timestamp.tzinfo is not None


# ---------------------------------------------------------------------------
# Review hardening — guards at the Stage 5 seam
# ---------------------------------------------------------------------------


def test_begin_recovery_refuses_a_scenario_that_never_caused_an_outage(engine):
    """Scenario 4 has no decay phase: its metrics never left baseline, so there is nothing
    to recover from, and a decay would end it in HEALTHY instead of SECURITY_CONTAINED."""
    engine.trigger(ScenarioId.PROMPT_INJECTION)
    engine.machine.transition(IncidentState.AWAITING_APPROVAL)
    engine.machine.transition(IncidentState.EXECUTING)
    with pytest.raises(NoRecoveryPhaseError):
        engine.begin_recovery()
    assert engine.machine.state is IncidentState.EXECUTING


def test_begin_recovery_still_accepts_the_outage_scenarios(engine):
    for scenario in (ScenarioId.DB_POOL_EXHAUSTION, ScenarioId.CACHE_THUNDERING_HERD, ScenarioId.WORKER_DEADLOCK):
        engine.trigger(scenario)
        engine.machine.transition(IncidentState.AWAITING_APPROVAL)
        engine.machine.transition(IncidentState.EXECUTING)
        run = engine.begin_recovery()
        assert run.state is IncidentState.RECOVERING
        assert run.decay_started_at is not None
        engine.reset(run.incident_id)


async def test_stop_task_surfaces_the_reason_a_dead_generator_died():
    """A telemetry loop that died must not be swallowed as an unretrieved task exception."""

    async def explode() -> None:
        raise RuntimeError("generator died")

    task = asyncio.ensure_future(explode())
    with contextlib.suppress(RuntimeError):
        await task
    task = asyncio.ensure_future(explode())
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError, match="generator died"):
        await stop_task(task)


async def test_stop_task_is_quiet_for_a_cleanly_cancelled_generator(engine):
    task = asyncio.ensure_future(engine.run_forever())
    await asyncio.sleep(0)
    await stop_task(task)
    assert task.cancelled()


async def test_stop_task_tolerates_a_task_that_finished_normally():
    async def done_quickly() -> None:
        return None

    task = asyncio.ensure_future(done_quickly())
    await asyncio.sleep(0)
    await stop_task(task)
