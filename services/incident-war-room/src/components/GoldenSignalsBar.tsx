/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        GoldenSignalsBar.tsx
 * Purpose:          The four headline metric tiles with their sparklines.
 * Interacts With:   hooks/useIncidentStream.ts, components/ui (MetricTile, Skeleton)
 *
 * Curriculum Project: Project 1 — Diagnostics & Telemetry
 * Skills:           Metric Formatting, Live Charts, Status Colour Semantics
 * Tools:            React 18, Tailwind CSS, Recharts
 *
 * The four tiles are the ones in the wireframe: p99 latency, HTTP 5xx rate, SQS queue depth, and
 * DB pool utilisation. Two come from `golden_signals` and two from `infrastructure`, which is why
 * this component reads both objects rather than one.
 *
 * **Tile tone follows the run state, not the value.** A red tile means "the platform is in an
 * outage", never "this number crossed a threshold I invented". Thresholds would have to be
 * per-metric guesses, and they would paint Scenario 4's tiles amber the moment queue depth
 * wobbled — which would destroy the one thing that scenario is built to show. The state is
 * already the authoritative answer to "is something wrong", so the tiles use it.
 *
 * **With one exception, and it is the whole point of Scenario 4.** `ui-wireframe-and-ux.md` §3 says
 * the golden-signals bar "stays green throughout" for the injection run and that this "must not be
 * subtle: three scenarios flash red, this one never does". Following the run state alone broke that
 * — the bar went amber at `AWAITING_APPROVAL` like every other scenario, so the visual difference
 * collapsed to a badge and a strip at the exact moment a viewer is looking at the metrics. Green is
 * also simply true here: no chaos math runs, and every gauge holds its baseline band start to
 * finish.
 */

import { CHART_DOMAINS, STATE_TONE, type StatusTone } from '../theme/tokens'
import { ScenarioId, type GoldenSignals, type IncidentState, type InfrastructureMetrics } from '../types/contracts.gen'
import { MetricTile, Skeleton } from './ui'
import type { MetricHistory } from '../hooks/useIncidentStream'

/**
 * Scenarios whose infrastructure metrics never leave baseline, and whose signals bar therefore
 * stays healthy for the whole run regardless of run state.
 *
 * A set rather than an `=== PROMPT_INJECTION` check, so the rule reads as a property of the scenario
 * — "this one never causes an outage" — rather than as a special case bolted onto one id.
 */
const NON_IMPACTING_SCENARIOS: ReadonlySet<ScenarioId> = new Set([ScenarioId.PROMPT_INJECTION])

/** The tone the whole bar takes: the run's state, unless the run never touches the metrics. */
export function signalsTone(state: IncidentState, scenarioId: ScenarioId | null): StatusTone {
  if (scenarioId !== null && NON_IMPACTING_SCENARIOS.has(scenarioId)) return 'healthy'
  return STATE_TONE[state]
}

interface GoldenSignalsBarProps {
  state: IncidentState
  /** Needed for the tone rule above — Scenario 4's bar stays green for the whole run. */
  scenarioId: ScenarioId | null
  goldenSignals: GoldenSignals | null
  infrastructure: InfrastructureMetrics | null
  history: MetricHistory
  stale: boolean
  loading: boolean
}

export function GoldenSignalsBar({
  state,
  scenarioId,
  goldenSignals,
  infrastructure,
  history,
  stale,
  loading,
}: GoldenSignalsBarProps) {
  const tone = signalsTone(state, scenarioId)

  if (loading) {
    return (
      <section data-testid="golden-signals-loading" className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        {[0, 1, 2, 3].map((index) => (
          <Skeleton key={index} className="h-metric-card" />
        ))}
      </section>
    )
  }

  return (
    <section data-testid="golden-signals" aria-label="Real-time golden signals" className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      <MetricTile
        label="API response time"
        value={goldenSignals?.latency_p99_ms ?? null}
        unit="ms"
        decimals={1}
        tone={tone}
        history={history.latency_p99_ms ?? []}
        domain={CHART_DOMAINS.latency_p99_ms}
        stale={stale}
      />
      <MetricTile
        label="Failed requests"
        value={goldenSignals?.http_5xx_error_rate_pct ?? null}
        unit="%"
        decimals={2}
        tone={tone}
        history={history.http_5xx_error_rate_pct ?? []}
        domain={CHART_DOMAINS.http_5xx_error_rate_pct}
        stale={stale}
      />
      <MetricTile
        label="Work queue"
        value={infrastructure?.sqs_active_queue_depth ?? null}
        unit="msgs"
        tone={tone}
        history={history.sqs_active_queue_depth ?? []}
        domain={CHART_DOMAINS.sqs_active_queue_depth}
        stale={stale}
      />
      <MetricTile
        label="Database capacity"
        value={infrastructure?.db_pool_utilization_pct ?? null}
        unit="%"
        decimals={1}
        tone={tone}
        history={history.db_pool_utilization_pct ?? []}
        domain={CHART_DOMAINS.db_pool_utilization_pct}
        stale={stale}
      />
    </section>
  )
}
