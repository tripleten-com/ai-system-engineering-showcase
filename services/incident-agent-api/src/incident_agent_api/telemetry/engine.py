"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/telemetry/engine.py
Component:          Telemetry Generator Engine
Purpose:            Owns the 1-second generator task: samples the active profile, publishes the
                    Prometheus families, and holds the snapshot served to the browser.
Interacts With:     prometheus (:9090), incident-war-room (:3000)

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             Async Background Tasks, Golden Signals, Observability
Tools:              FastAPI, prometheus-client, Python 3.11

State lives in memory, so the service runs a single uvicorn worker. Adding --workers would give
each process its own private chaos clock and counters, and Prometheus would scrape whichever
one it happened to reach.
"""

import asyncio
import contextlib
import logging
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel

from incident_agent_api.telemetry import baseline, chaos, decay, registry
from incident_agent_api.telemetry.baseline import MetricValues
from incident_agent_api.telemetry.state_machine import IncidentRun, TelemetryStateMachine
from tripleten_contracts import (
    GoldenSignals,
    IncidentState,
    InfrastructureMetrics,
    MetricsSnapshot,
    ScenarioId,
    TelemetrySnapshotResponse,
    health_status_for,
)

logger = logging.getLogger("incident-agent-api")

TICK_SECONDS = 1.0

# States in which an outage scenario holds its chaos values. The three terminal branches are
# included on purpose: a rejected or failed remediation leaves the incident running until reset.
_CHAOS_STATES = frozenset(
    {
        IncidentState.CRITICAL_OUTAGE,
        IncidentState.EXPLOIT_INTERCEPTED,
        IncidentState.AWAITING_APPROVAL,
        IncidentState.EXECUTING,
        IncidentState.REJECTED,
        IncidentState.FAILED,
        IncidentState.SECURITY_CONTAINED,
    }
)


class EventPublisher(Protocol):
    """The slice of the SSE bus this engine needs.

    Declared here, by the consumer, rather than imported from infra.eventbus: the engine is
    Project 1 and must not depend on the transport. Anything with this method satisfies it,
    which is what lets a unit test pass a list-appending fake and no bus at all.
    """

    def publish(self, payload: BaseModel, incident_id: str | None = ...) -> object:
        """Delivers one payload to every subscriber without blocking or raising."""
        ...


class NoRecoveryPhaseError(RuntimeError):
    """Raised when a recovery is requested for a scenario whose metrics never left baseline."""

    def __init__(self, scenario_id: ScenarioId) -> None:
        super().__init__(f"{scenario_id} causes no outage and therefore has no recovery phase")
        self.scenario_id = scenario_id


class TelemetryEngine:
    """Generates one telemetry sample per second and publishes it to Prometheus and the UI."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
        now_utc: Callable[[], datetime] | None = None,
        tick_seconds: float = TICK_SECONDS,
        publisher: EventPublisher | None = None,
    ) -> None:
        self._clock = clock
        self._rng = rng if rng is not None else random.Random()
        self._now_utc = now_utc if now_utc is not None else lambda: datetime.now(UTC)
        self._tick_seconds = tick_seconds
        # Optional, and a None publisher is a supported configuration rather than a degraded
        # one: it is what lets a unit test build a bare engine and assert on the Prometheus
        # path alone. In the running service create_app() constructs the bus first and injects
        # it here, so even the synchronous tick it performs before lifespan publishes a frame —
        # harmlessly, since no client has subscribed yet.
        self._publisher = publisher
        self._started_at = clock()
        self.machine = TelemetryStateMachine()
        self._values: MetricValues = baseline.nominals(t=0.0)
        # Per-run count, deliberately not the process-cumulative Prometheus counter: the War
        # Room claims "0 unauthorized actions" for the run in front of the viewer, so a second
        # demo run must not inherit the first run's tally.
        self._security_violations = 0

    # ------------------------------------------------------------------
    # Clocks
    # ------------------------------------------------------------------

    def _elapsed(self) -> float:
        """Returns seconds since the engine started, the argument to the baseline sine wave."""
        return self._clock() - self._started_at

    # ------------------------------------------------------------------
    # Run control
    # ------------------------------------------------------------------

    def trigger(self, scenario_id: ScenarioId) -> IncidentRun:
        """Opens an incident run and, on the security scenario, records the guardrail violation."""
        run = self.machine.trigger(scenario_id, now=self._elapsed())
        if scenario_id is ScenarioId.PROMPT_INJECTION:
            # Exactly once per run, at the moment the Pydantic firewall rejects the injected
            # call — not once per rejected tool in the payload.
            registry.SECURITY_VIOLATIONS_TOTAL.inc()
            self._security_violations = 1
        logger.info("Incident %s opened: scenario=%s state=%s", run.incident_id, scenario_id, run.state)
        return run

    def begin_recovery(self) -> IncidentRun:
        """Moves an executing run into RECOVERING and starts its decay clock.

        Refuses a scenario that never caused an outage. EXECUTING → RECOVERING is a legal
        transition for every scenario, so nothing in the contract table stops a caller from
        giving Scenario 4 a decay phase — but its metrics never left baseline, and a decay
        would land it in HEALTHY instead of terminal SECURITY_CONTAINED. The guard lives here
        because this is where the decay clock is stamped.
        """
        run = self.machine.run
        if run is not None and not run.scenario_id.causes_outage:
            raise NoRecoveryPhaseError(run.scenario_id)
        run = self.machine.transition(IncidentState.RECOVERING)
        run.decay_started_at = self._elapsed()
        return run

    def reset(self, incident_id: str | None) -> None:
        """Clears the run and returns the platform to baseline generation.

        incident_id is required rather than defaulting, because passing None deliberately
        force-clears whatever run is in flight without the ownership check. Nothing needs that
        today — the recovery-completion path in tick() passes the id of the run it just
        recovered — and the parameter is mandatory precisely so no caller reaches the
        unchecked behaviour by forgetting an argument.
        """
        self.machine.reset(incident_id)
        self._security_violations = 0

    # ------------------------------------------------------------------
    # Sampling
    # ------------------------------------------------------------------

    def _sample(self) -> MetricValues:
        """Returns this tick's values for whichever phase the run is in."""
        elapsed = self._elapsed()
        run = self.machine.run
        if run is None:
            return baseline.sample(t=elapsed, rng=self._rng)

        if run.state is IncidentState.RECOVERING:
            started = run.decay_started_at if run.decay_started_at is not None else elapsed
            t_decay = min(elapsed - started, decay.DECAY_DURATION_SECONDS)
            return decay.apply(baseline.nominals(t=elapsed), run.scenario_id, t_decay=t_decay)

        if run.state in _CHAOS_STATES:
            return chaos.apply(
                baseline.sample(t=elapsed, rng=self._rng),
                run.scenario_id,
                t_chaos=elapsed - run.chaos_started_at,
                rng=self._rng,
            )

        return baseline.sample(t=elapsed, rng=self._rng)

    def _recovery_is_complete(self) -> bool:
        """Returns True once a recovering run has run its full 4-second decay."""
        run = self.machine.run
        if run is None or run.state is not IncidentState.RECOVERING or run.decay_started_at is None:
            return False
        return self._elapsed() - run.decay_started_at >= decay.DECAY_DURATION_SECONDS

    def tick(self) -> None:
        """Advances the simulation by one sample and republishes every metric family."""
        self._values = self._sample()

        # Captured before a completed recovery clears the run, and that ordering is the whole
        # point. This tick's frame reports HEALTHY at fully-decayed values — it is the moment
        # the demo has been building to — and a War Room streaming with ?incident_id= is closed
        # the instant a frame arrives that does not belong to its run. Stamping this one with
        # None would end the scoped stream one frame *before* it could render the recovery,
        # so the operator would watch the dashboard drop out instead of go green. The run is
        # over either way; the client is closed on the next tick, having seen how it ended.
        run = self.machine.run
        attribution_id = run.incident_id if run is not None else None

        if self._recovery_is_complete():
            self.machine.transition(IncidentState.HEALTHY)
            if run is not None:
                logger.info("Incident %s recovered to baseline", run.incident_id)
                self.reset(run.incident_id)

        self._publish()
        self._stream(attribution_id)

    def _publish(self) -> None:
        """Writes the current sample into the Prometheus registry."""
        values = self._values

        requests = round(values["requests_per_sec"])
        registry.HTTP_REQUESTS_TOTAL.inc(requests)
        registry.HTTP_5XX_ERRORS_TOTAL.inc(round(requests * values["http_5xx_error_rate_pct"] / 100.0))

        for quantile, field_name in registry.QUANTILE_FIELDS.items():
            registry.REQUEST_DURATION_MS.labels(quantile=quantile.value).set(values[field_name])

        for field_name, gauge in registry.GAUGES_BY_FIELD.items():
            gauge.set(values[field_name])

        registry.SYSTEM_HEALTH_STATUS.set(health_status_for(self.machine.state, self.machine.scenario_id))

    # ------------------------------------------------------------------
    # Read models
    # ------------------------------------------------------------------

    def golden_signals(self) -> GoldenSignals:
        """Returns the traffic, error, and latency signals from the current sample."""
        values = self._values
        return GoldenSignals(
            requests_per_sec=values["requests_per_sec"],
            http_5xx_error_rate_pct=values["http_5xx_error_rate_pct"],
            latency_p50_ms=values["latency_p50_ms"],
            latency_p95_ms=values["latency_p95_ms"],
            latency_p99_ms=values["latency_p99_ms"],
        )

    def infrastructure(self) -> InfrastructureMetrics:
        """Returns the platform gauges from the current sample."""
        values = self._values
        return InfrastructureMetrics(
            system_health_status=health_status_for(self.machine.state, self.machine.scenario_id),
            db_pool_utilization_pct=values["db_pool_utilization_pct"],
            redis_memory_utilization_pct=values["redis_memory_utilization_pct"],
            cache_hit_ratio_pct=values["cache_hit_ratio_pct"],
            sqs_active_queue_depth=int(values["sqs_active_queue_depth"]),
            dlq_message_count=int(values["dlq_message_count"]),
            active_workers_count=int(values["active_workers_count"]),
            security_violations_total=self._security_violations,
        )

    def metrics_snapshot(self) -> MetricsSnapshot:
        """Returns the METRICS_UPDATE payload for the current sample.

        Built from the same golden_signals() and infrastructure() read models as the polling
        snapshot, which is what makes the two structurally identical and lets a reconnecting
        client rebuild the same UI from either.
        """
        return MetricsSnapshot(
            status=self.machine.state,
            golden_signals=self.golden_signals(),
            infrastructure=self.infrastructure(),
        )

    def _stream(self, incident_id: str | None) -> None:
        """Publishes this tick's sample to the SSE bus, if one is attached.

        Takes the incident id rather than reading it back off the state machine, because on the
        tick that completes a recovery the machine has already been cleared and the frame still
        belongs to the run that recovered. See tick().

        Swallowing the failure is deliberate. By the time this runs the sample is already in
        the Prometheus registry and any state transition has already happened, and tick() is
        called synchronously from /trigger and /reset — so letting a streaming fault escape
        would fail a request whose real work succeeded. A dropped frame costs the browser one
        second; a failed /reset strands the demo until the container restarts.
        """
        if self._publisher is None:
            return
        try:
            self._publisher.publish(self.metrics_snapshot(), incident_id)
        except Exception:
            logger.exception("Publishing the SSE metrics frame failed; telemetry continues")

    def snapshot(self) -> TelemetrySnapshotResponse:
        """Returns the polling snapshot. Read-only: it never advances the state machine."""
        run = self.machine.run
        return TelemetrySnapshotResponse(
            incident_id=run.incident_id if run else None,
            thread_id=run.thread_id if run else None,
            scenario_id=run.scenario_id if run else None,
            state=self.machine.state,
            timestamp=self._now_utc(),
            golden_signals=self.golden_signals(),
            infrastructure=self.infrastructure(),
        )

    # ------------------------------------------------------------------
    # Background task
    # ------------------------------------------------------------------

    async def run_forever(self) -> None:
        """Ticks once per second until cancelled; a bad tick is logged, never fatal."""
        logger.info("Telemetry generator started at %.1fs cadence", self._tick_seconds)
        try:
            while True:
                try:
                    self.tick()
                except Exception:
                    # A generator that dies takes the whole demo's telemetry with it, so a
                    # single bad sample is logged and the loop continues.
                    logger.exception("Telemetry tick failed; continuing")
                await asyncio.sleep(self._tick_seconds)
        except asyncio.CancelledError:
            logger.info("Telemetry generator stopped")
            raise


async def stop_task(task: asyncio.Task[None] | None) -> None:
    """Cancels the generator task and waits for it to unwind, re-raising an abnormal death.

    A task that already finished has its exception retrieved rather than discarded: without
    that, a generator killed by something `run_forever` does not catch surfaces only as a bare
    "Task exception was never retrieved" warning at garbage collection, with no traceback to
    explain why telemetry stopped.
    """
    if task is None:
        return
    if task.done():
        if not task.cancelled() and task.exception() is not None:
            raise task.exception()  # type: ignore[misc]
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
