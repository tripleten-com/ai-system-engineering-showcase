/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        GoldenSignalsBar.test.tsx
 * Purpose:          Unit tests for metric formatting, status-colour semantics, the cold-start
 *                   skeletons, and the desaturated disconnected state.
 * Interacts With:   GoldenSignalsBar component
 *
 * Curriculum Project: Project 1 — Diagnostics & Telemetry
 * Skills:           React Component Testing, Metric Formatting
 * Tools:            Vitest, React Testing Library
 */

import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'

import { GoldenSignalsBar, signalsTone } from '../../src/components/GoldenSignalsBar'
import { ScenarioId, type IncidentState } from '../../src/types/contracts.gen'
import { BASELINE_INFRA, BASELINE_SIGNALS, OUTAGE_SIGNALS } from '../fixtures'

const HISTORY = { latency_p99_ms: [47, 48, 49], http_5xx_error_rate_pct: [0, 0, 0] }

describe('GoldenSignalsBar', () => {
  it('renders the four wireframe tiles', () => {
    render(
      <GoldenSignalsBar
        state="HEALTHY"
        scenarioId={ScenarioId.DB_POOL_EXHAUSTION}
        goldenSignals={BASELINE_SIGNALS}
        infrastructure={BASELINE_INFRA}
        history={HISTORY}
        stale={false}
        loading={false}
      />,
    )

    for (const label of ['API response time', 'Failed requests', 'Work queue', 'Database capacity']) {
      expect(screen.getByTestId(`metric-${label}`)).toBeInTheDocument()
    }
  })

  it('formats each metric to its documented precision', () => {
    render(
      <GoldenSignalsBar
        state="CRITICAL_OUTAGE"
        scenarioId={ScenarioId.DB_POOL_EXHAUSTION}
        goldenSignals={OUTAGE_SIGNALS}
        infrastructure={BASELINE_INFRA}
        history={HISTORY}
        stale={false}
        loading={false}
      />,
    )

    // p99 to one decimal with a thousands separator, error rate to two, queue depth as an integer.
    expect(screen.getByTestId('metric-API response time')).toHaveTextContent('4,820.0')
    expect(screen.getByTestId('metric-Failed requests')).toHaveTextContent('36.40')
    expect(screen.getByTestId('metric-Work queue')).toHaveTextContent('3')
  })

  it('uses tabular numerals on every streaming value', () => {
    // Without this the layout visibly jitters as the digits change ten times a second, and the
    // whole dashboard reads as unstable. It is a hard requirement, not a preference.
    render(
      <GoldenSignalsBar
        state="HEALTHY"
        scenarioId={ScenarioId.DB_POOL_EXHAUSTION}
        goldenSignals={BASELINE_SIGNALS}
        infrastructure={BASELINE_INFRA}
        history={HISTORY}
        stale={false}
        loading={false}
      />,
    )

    for (const value of screen.getAllByTestId('metric-value')) {
      expect(value.className).toContain('tabular-nums')
    }
  })

  it('takes tile colour from the run state, not from the value', () => {
    // The same numbers under two states must paint differently: a red tile means "the platform is
    // in an outage", never "this number crossed a threshold the frontend invented".
    const { unmount } = render(
      <GoldenSignalsBar
        state="HEALTHY"
        scenarioId={ScenarioId.DB_POOL_EXHAUSTION}
        goldenSignals={OUTAGE_SIGNALS}
        infrastructure={BASELINE_INFRA}
        history={HISTORY}
        stale={false}
        loading={false}
      />,
    )
    expect(screen.getAllByTestId('metric-value')[0]).toHaveAttribute('data-tone', 'healthy')
    unmount()

    render(
      <GoldenSignalsBar
        state="CRITICAL_OUTAGE"
        scenarioId={ScenarioId.DB_POOL_EXHAUSTION}
        goldenSignals={OUTAGE_SIGNALS}
        infrastructure={BASELINE_INFRA}
        history={HISTORY}
        stale={false}
        loading={false}
      />,
    )
    expect(screen.getAllByTestId('metric-value')[0]).toHaveAttribute('data-tone', 'alarm')
  })

  it.each<IncidentState>(['EXPLOIT_INTERCEPTED', 'AWAITING_APPROVAL', 'EXECUTING', 'SECURITY_CONTAINED'])(
    'keeps the security run green in %s',
    (state) => {
      // `ui-wireframe-and-ux.md` §3: the bar "stays green throughout" and this "must not be subtle:
      // three scenarios flash red, this one never does". Following the run state alone broke it —
      // the bar went amber at AWAITING_APPROVAL exactly like every outage, collapsing the visual
      // difference to a badge and a strip at the moment a viewer is reading the metrics.
      render(
        <GoldenSignalsBar
          state={state}
          scenarioId={ScenarioId.PROMPT_INJECTION}
          goldenSignals={BASELINE_SIGNALS}
          infrastructure={BASELINE_INFRA}
          history={HISTORY}
          stale={false}
          loading={false}
        />,
      )

      for (const value of screen.getAllByTestId('metric-value')) {
        expect(value).toHaveAttribute('data-tone', 'healthy')
      }
    },
  )

  it.each<IncidentState>(['CRITICAL_OUTAGE', 'AWAITING_APPROVAL', 'FAILED'])(
    'still tracks the run state for an outage scenario in %s',
    (state) => {
      // The exception is narrow: only a scenario that never touches the metrics gets a fixed tone.
      // Every other run must keep showing its state, or the bar stops meaning anything.
      render(
        <GoldenSignalsBar
          state={state}
          scenarioId={ScenarioId.DB_POOL_EXHAUSTION}
          goldenSignals={OUTAGE_SIGNALS}
          infrastructure={BASELINE_INFRA}
          history={HISTORY}
          stale={false}
          loading={false}
        />,
      )

      expect(screen.getAllByTestId('metric-value')[0]).not.toHaveAttribute('data-tone', 'healthy')
    },
  )

  it('resolves the tone from the scenario before the state', () => {
    expect(signalsTone('AWAITING_APPROVAL', ScenarioId.PROMPT_INJECTION)).toBe('healthy')
    expect(signalsTone('AWAITING_APPROVAL', ScenarioId.DB_POOL_EXHAUSTION)).toBe('pending')
    expect(signalsTone('CRITICAL_OUTAGE', ScenarioId.WORKER_DEADLOCK)).toBe('alarm')
    // No run in flight: the state is all there is to go on.
    expect(signalsTone('HEALTHY', null)).toBe('healthy')
  })

  it('renders an em dash rather than NaN before any data arrives', () => {
    render(
      <GoldenSignalsBar
        state="HEALTHY"
        scenarioId={ScenarioId.DB_POOL_EXHAUSTION}
        goldenSignals={null}
        infrastructure={null}
        history={{}}
        stale={false}
        loading={false}
      />,
    )

    for (const value of screen.getAllByTestId('metric-value')) {
      expect(value.textContent).toBe('—')
    }
  })

  it('shows shimmer skeletons during cold start', () => {
    render(
      <GoldenSignalsBar
        state="HEALTHY"
        scenarioId={ScenarioId.DB_POOL_EXHAUSTION}
        goldenSignals={null}
        infrastructure={null}
        history={{}}
        stale={false}
        loading
      />,
    )

    expect(screen.getByTestId('golden-signals-loading')).toBeInTheDocument()
    expect(screen.getAllByTestId('skeleton')).toHaveLength(4)
    expect(screen.queryByTestId('golden-signals')).not.toBeInTheDocument()
  })

  it('desaturates the sparklines and mutes the values while the stream is down', () => {
    // A stalled stream and a healthy system both draw a flat line at baseline. Removing that
    // ambiguity is the entire job of the disconnected state.
    render(
      <GoldenSignalsBar
        state="HEALTHY"
        scenarioId={ScenarioId.DB_POOL_EXHAUSTION}
        goldenSignals={BASELINE_SIGNALS}
        infrastructure={BASELINE_INFRA}
        history={HISTORY}
        stale
        loading={false}
      />,
    )

    for (const spark of screen.getAllByTestId('sparkline')) {
      expect(spark.dataset.stale).toBe('true')
    }
    for (const value of screen.getAllByTestId('metric-value')) {
      // The stale sparkline communicates a lost connection; values remain ink so rendered contrast
      // stays above the body-text floor rather than relying on a pale status colour.
      expect(value.className).toContain('text-ink')
    }
  })
})
