/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        PresentationLayer.test.tsx
 * Purpose:          Locks the approved showcase hierarchy and simplified visitor-facing copy.
 * Interacts With:   App, feature components, generated contracts
 *
 * Curriculum Project: Cross-cutting — Production Aesthetics
 * Skills:           React Component Testing, Marketing UI Verification
 * Tools:            Vitest, React Testing Library
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'

import { App } from '../../src/App'
import { AgentReasoningView } from '../../src/components/AgentReasoningView'
import { GoldenSignalsBar } from '../../src/components/GoldenSignalsBar'
import { ExecutionTerminal } from '../../src/components/ExecutionTerminal'
import { LogSanitizerView } from '../../src/components/LogSanitizerView'
import { RagInspectorView } from '../../src/components/RagInspectorView'
import { ScenarioControls } from '../../src/components/ScenarioControls'
import * as api from '../../src/services/api'
import * as sseClient from '../../src/services/sseClient'
import { ScenarioId } from '../../src/types/contracts.gen'
import { BASELINE_INFRA, BASELINE_SIGNALS, REDACTED_LOG, ragEntry, thought, workerEntry } from '../fixtures'

beforeEach(() => {
  vi.spyOn(api, 'fetchSnapshot').mockResolvedValue({
    incident_id: null,
    thread_id: null,
    scenario_id: null,
    state: 'HEALTHY',
    timestamp: '2026-08-21T12:00:00Z',
    golden_signals: BASELINE_SIGNALS,
    infrastructure: BASELINE_INFRA,
  })
  vi.spyOn(sseClient, 'connectIncidentStream').mockImplementation(({ onConnectionChange }) => {
    onConnectionChange('open', 0)
    return () => {}
  })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('approved showcase hierarchy', () => {
  it('introduces the demo with the approved hero copy', async () => {
    render(<App />)

    await waitFor(() => expect(screen.getByTestId('showcase-hero')).toBeInTheDocument())
    expect(screen.getByTestId('showcase-hero').className).toContain('lg:grid-cols-2')
    expect(screen.getByRole('heading', { name: 'See how AI systems respond to a production incident' })).toBeInTheDocument()
    expect(
      screen.getByText(
        'Trigger a realistic failure. Watch the system protect sensitive data, find the right recovery guide, propose a fix, wait for human approval, and recover.',
      ),
    ).toBeInTheDocument()
  })

  it('names the showcase in the hero eyebrow', async () => {
    render(<App />)

    await waitFor(() => expect(screen.getByTestId('showcase-hero')).toBeInTheDocument())
    expect(screen.getByTestId('showcase-hero')).toHaveTextContent(
      'TripleTen AI Systems Engineering - Showcase Project',
    )
  })

  it('drops the hero proof points that only restated the body copy', async () => {
    render(<App />)

    await waitFor(() => expect(screen.getByTestId('showcase-hero')).toBeInTheDocument())
    for (const proof of ['Real recovery-guide search', 'Human approval required', 'Local cloud services']) {
      expect(screen.queryByText(proof), proof).not.toBeInTheDocument()
    }
  })

  it('renders one current-state badge with one polite live-region ancestor', async () => {
    render(<App />)

    await waitFor(() => expect(screen.getAllByTestId('status-badge')).toHaveLength(1))
    expect(screen.getByTestId('status-badge').closest('[aria-live="polite"]')).not.toBeNull()
  })

  it('renders the scenario launcher as descriptive two-column cards from 640px', () => {
    render(
      <ScenarioControls
        state="HEALTHY"
        incidentId={null}
        busy={false}
        error={null}
        onTrigger={vi.fn()}
        onReset={vi.fn()}
      />,
    )

    const launcher = screen.getByTestId('scenario-launcher')
    expect(launcher.className).toContain('sm:grid-cols-2')
    expect(launcher.className).toContain('grid-cols-1')
    expect(screen.getByTestId('trigger-db_pool_exhaustion')).toHaveTextContent('Database overload')
    expect(screen.getByTestId('trigger-db_pool_exhaustion')).toHaveTextContent(
      'Connection capacity runs out and requests begin failing.',
    )
    expect(screen.getByTestId('trigger-prompt_injection')).toHaveTextContent('Prompt injection attempt')
    expect(screen.getByTestId('trigger-prompt_injection')).toHaveTextContent(
      'Malicious text asks the AI to perform an unsafe action.',
    )
    expect(screen.getByTestId('trigger-cache_thundering_herd')).toHaveTextContent('Cache traffic spike')
    expect(screen.getByTestId('trigger-cache_thundering_herd')).toHaveTextContent('Many requests miss the cache at the same time.')
    expect(screen.getByTestId('trigger-worker_deadlock')).toHaveTextContent('Queue processing stops')
    expect(screen.getByTestId('trigger-worker_deadlock')).toHaveTextContent('A bad message blocks the background workers.')
  })

  it('uses editorial metric labels without changing their data inputs', () => {
    render(
      <GoldenSignalsBar
        state="HEALTHY"
        scenarioId={null}
        goldenSignals={BASELINE_SIGNALS}
        infrastructure={BASELINE_INFRA}
        history={{}}
        stale={false}
        loading={false}
      />,
    )

    for (const label of ['API response time', 'Failed requests', 'Work queue', 'Database capacity']) {
      expect(screen.getByTestId(`metric-${label}`)).toBeInTheDocument()
    }
  })

  it('places each workflow output in a fixed-height console with collapsed technical detail', () => {
    const { rerender } = render(
      <LogSanitizerView logs={[REDACTED_LOG]} thoughts={[]} scenarioId={ScenarioId.DB_POOL_EXHAUSTION} />,
    )
    expect(screen.getByTestId('console-frame')).toHaveClass('h-console-mobile')
    expect(screen.getByText('Sensitive log protection')).toBeInTheDocument()
    expect(screen.getByTestId('technical-details')).not.toHaveAttribute('open')

    rerender(<RagInspectorView matches={[ragEntry()]} />)
    expect(screen.getByText('Recovery guide search')).toBeInTheDocument()
    expect(screen.getByTestId('technical-details')).not.toHaveAttribute('open')

    rerender(<ExecutionTerminal workerLogs={[workerEntry({ id: 'worker-1' })]} />)
    expect(screen.getByText('Approved action execution')).toBeInTheDocument()
    // Sized exactly like the other three now, and with no disclosure of its own — so it is asserted
    // here rather than inside the loop above.
    expect(screen.getByTestId('console-frame')).toHaveClass('h-console-mobile')
    expect(screen.queryByTestId('technical-details')).not.toBeInTheDocument()
  })

  it('gives the AI response plan a fixed-height console and no disclosure', () => {
    // The plan is the one output with no `Technical details`, so it is asserted apart from the three
    // that have one rather than being an exception inside that loop.
    render(
      <AgentReasoningView
        state="HEALTHY"
        scenarioId={null}
        thoughts={[thought({ id: 'thought-1', step: 1 })]}
        busy={false}
        error={null}
        onShowPlan={vi.fn()}
      />,
    )

    expect(screen.getByText('AI response plan')).toBeInTheDocument()
    expect(screen.getByTestId('console-frame')).toHaveClass('h-console-mobile')
    expect(screen.queryByTestId('technical-details')).not.toBeInTheDocument()
  })

  it('pairs the two remaining workflow outputs from 768px, with no CSS order juggling', async () => {
    // The plan moved above the charts, so the grid is two columns and the visible pipeline — logs
    // then retrieval — is also the DOM order.
    render(<App />)

    await waitFor(() => expect(screen.getByTestId('workflow-grid')).toBeInTheDocument())
    const grid = screen.getByTestId('workflow-grid')
    expect(grid).toHaveClass('md:grid-cols-2')
    expect(grid.className).not.toContain('lg:grid-cols-3')
    expect(grid.className).not.toMatch(/(?:^|\s)order-/)
    expect(screen.queryByTestId('workflow-agent')).not.toBeInTheDocument()
  })

  it('orders the run summary above the charts: state, then impact, then the decision', async () => {
    // The charts are the evidence for what these say, not the headline.
    vi.mocked(api.fetchSnapshot).mockResolvedValue({
      incident_id: 'inc-order',
      thread_id: 'thread-inc-order',
      scenario_id: ScenarioId.DB_POOL_EXHAUSTION,
      state: 'AWAITING_APPROVAL',
      timestamp: '2026-08-21T12:00:00Z',
      golden_signals: BASELINE_SIGNALS,
      infrastructure: BASELINE_INFRA,
    })
    render(<App />)

    await waitFor(() => expect(screen.getByTestId('decision-grid')).toBeInTheDocument())
    // The decision pair sits under the charts, still ahead of the two focusable output streams.
    const order = ['run-status', 'scenario-impact-strip', 'golden-signals', 'decision-grid', 'workflow-grid']
    const positions = order.map((id) => {
      const node = screen.getByTestId(id)
      return Array.from(document.querySelectorAll('[data-testid]')).indexOf(node)
    })
    expect(positions).toEqual([...positions].sort((a, b) => a - b))
  })

  it('shows the plan and the worker console together, and only once a run exists', async () => {
    render(<App />)

    await waitFor(() => expect(screen.getByTestId('golden-signals')).toBeInTheDocument())
    // Steady state: neither console is on the page, so a visitor is not reading standby copy twice.
    expect(screen.queryByTestId('decision-grid')).not.toBeInTheDocument()
    expect(screen.queryByTestId('agent-reasoning')).not.toBeInTheDocument()
    expect(screen.queryByTestId('execution-terminal')).not.toBeInTheDocument()
  })

  it('keeps scenario-card copy in ink on light tint surfaces', () => {
    render(
      <ScenarioControls
        state="HEALTHY"
        incidentId={null}
        busy={false}
        error={null}
        onTrigger={vi.fn()}
        onReset={vi.fn()}
      />,
    )

    for (const testId of ['trigger-db_pool_exhaustion', 'trigger-prompt_injection']) {
      const card = screen.getByTestId(testId)
      expect(card.className).toContain('text-ink')
      expect(card.className).not.toMatch(/text-(pending|guard)/)
    }
  })

  it('switches the single plan trigger between the desktop footer and mobile sticky bar', async () => {
    let mobile = false
    const listeners = new Set<(event: MediaQueryListEvent) => void>()
    vi.spyOn(window, 'matchMedia').mockImplementation(
      (query: string) =>
        ({
          matches: mobile && query === '(max-width: 767px)',
          media: query,
          onchange: null,
          addEventListener: (_event: string, listener: (event: MediaQueryListEvent) => void) => listeners.add(listener),
          removeEventListener: (_event: string, listener: (event: MediaQueryListEvent) => void) => listeners.delete(listener),
          addListener: () => {},
          removeListener: () => {},
          dispatchEvent: () => false,
        }) as unknown as MediaQueryList,
    )
    vi.mocked(api.fetchSnapshot).mockResolvedValue({
      incident_id: 'inc-approval',
      thread_id: 'thread-inc-approval',
      scenario_id: ScenarioId.DB_POOL_EXHAUSTION,
      state: 'AWAITING_APPROVAL',
      timestamp: '2026-08-21T12:00:00Z',
      golden_signals: BASELINE_SIGNALS,
      infrastructure: BASELINE_INFRA,
    })

    render(<App />)
    await waitFor(() => expect(screen.getAllByTestId('show-plan')).toHaveLength(1))
    expect(screen.queryByTestId('mobile-hitl-bar')).not.toBeInTheDocument()

    mobile = true
    await act(async () => {
      for (const listener of listeners) listener({ matches: true } as MediaQueryListEvent)
    })

    // Still exactly one way into the decision, now on the sticky bar instead of the plan footer.
    expect(screen.getAllByTestId('show-plan')).toHaveLength(1)
    expect(screen.getByTestId('mobile-hitl-bar')).toBeInTheDocument()
  })
})
