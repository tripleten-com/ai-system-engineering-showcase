/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        App.test.tsx
 * Purpose:          Tests the wiring the layout owns — which control an error belongs to, when the
 *                   triggers are usable, and which panels exist per state.
 * Interacts With:   App, services/api.ts, services/sseClient.ts
 *
 * Curriculum Project: Project 5 — Autonomous Agent & Human-in-the-Loop
 * Skills:           Integration-Style Component Testing, Error Attribution
 * Tools:            Vitest, React Testing Library
 *
 * Driven through fake `api` and `sseClient` modules rather than a fake `fetch`: the units under test
 * are the layout's decisions, and a network-level double would only add a serialisation step.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { App } from '../../src/App'
import * as api from '../../src/services/api'
import * as sseClient from '../../src/services/sseClient'
import {
  AgentPhase,
  EventType,
  GuardrailVerdict,
  ScenarioId,
  type IncidentEvent,
  type IncidentState,
} from '../../src/types/contracts.gen'
import { ARCHIVE_ENTRY, BASELINE_INFRA, BASELINE_SIGNALS, POSTMORTEM_KEY, POSTMORTEM_REPORT } from '../fixtures'

let emit: (event: IncidentEvent) => void

function snapshotFor(state: IncidentState, incidentId: string | null, scenarioId: ScenarioId | null) {
  return {
    incident_id: incidentId,
    thread_id: incidentId ? `thread-${incidentId}` : null,
    scenario_id: scenarioId,
    state,
    timestamp: '2026-08-20T12:00:00Z',
    golden_signals: BASELINE_SIGNALS,
    infrastructure: BASELINE_INFRA,
  }
}

function metricsFrame(state: IncidentState, incidentId: string | null): IncidentEvent {
  return {
    event_id: `m-${state}-${incidentId}`,
    incident_id: incidentId,
    timestamp: '2026-08-20T12:00:01Z',
    type: EventType.METRICS_UPDATE,
    data: { status: state, golden_signals: BASELINE_SIGNALS, infrastructure: BASELINE_INFRA },
  }
}

function thoughtFrame(incidentId: string, eventId: string, text: string): IncidentEvent {
  return {
    event_id: eventId,
    incident_id: incidentId,
    timestamp: '2026-08-20T12:00:03Z',
    type: EventType.AGENT_THOUGHT,
    data: { step: 1, phase: AgentPhase.ANALYZING, text, tool_call: null, guardrail: GuardrailVerdict.PASSED },
  }
}

function archiveFrame(incidentId: string, eventId: string): IncidentEvent {
  return {
    event_id: eventId,
    incident_id: incidentId,
    timestamp: '2026-08-20T12:00:05Z',
    type: EventType.WORKER_LOG,
    data: { source: ARCHIVE_ENTRY.source, level: ARCHIVE_ENTRY.level, message: ARCHIVE_ENTRY.message },
  }
}

beforeEach(() => {
  // The postmortem drawer reads the archived object over `fetch`. These tests are about *when* the
  // drawer appears, not what it renders, so the archive is stubbed with a valid report here and
  // exercised properly in PostmortemDrawer.test.tsx.
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify(POSTMORTEM_REPORT), { status: 200 })),
  )
  vi.spyOn(api, 'fetchSnapshot').mockResolvedValue(snapshotFor('HEALTHY', null, null))
  vi.spyOn(sseClient, 'connectIncidentStream').mockImplementation(({ onEvent, onConnectionChange }) => {
    emit = onEvent
    onConnectionChange('open', 0)
    return () => {}
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('App', () => {
  it('keeps the triggers unusable until the first snapshot lands', async () => {
    // `state` defaults to HEALTHY before anything arrives, so without the loading guard a click in
    // the first second could fire at a stack that already has a run going.
    let resolveSnapshot: (value: ReturnType<typeof snapshotFor>) => void = () => {}
    vi.mocked(api.fetchSnapshot).mockReturnValue(
      new Promise((resolve) => {
        resolveSnapshot = resolve
      }),
    )

    render(<App />)
    expect(screen.getByTestId('trigger-db_pool_exhaustion')).toBeDisabled()

    await waitFor(() => resolveSnapshot(snapshotFor('HEALTHY', null, null)))
    await waitFor(() => expect(screen.getByTestId('trigger-db_pool_exhaustion')).toBeEnabled())
  })

  it('shows a failed trigger under the scenario controls, not in the agent panel', async () => {
    vi.spyOn(api, 'triggerIncident').mockRejectedValue(new api.ApiError(409, 'a run is already in flight'))

    render(<App />)
    await waitFor(() => expect(screen.getByTestId('trigger-db_pool_exhaustion')).toBeEnabled())
    await userEvent.click(screen.getByTestId('trigger-db_pool_exhaustion'))

    await waitFor(() => expect(screen.getByTestId('control-error')).toHaveTextContent('409'))
    expect(screen.queryByTestId('hitl-error')).not.toBeInTheDocument()
  })

  it('shows a refused authorization in the agent panel, not under the triggers', async () => {
    // One untagged error string was rendered by both, so a refused authorize also printed an error
    // under the scenario buttons — pointing the viewer at the wrong control.
    vi.mocked(api.fetchSnapshot).mockResolvedValue(
      snapshotFor('AWAITING_APPROVAL', 'inc-1', ScenarioId.DB_POOL_EXHAUSTION),
    )
    vi.spyOn(api, 'authorizeIncident').mockRejectedValue(
      new api.ApiError(409, 'run is no longer awaiting approval'),
    )

    render(<App />)
    // The decision now lives in a modal, so the gate is two steps: open the plan, then approve.
    await waitFor(() => expect(screen.getByTestId('show-plan')).toBeInTheDocument())
    await userEvent.click(screen.getByTestId('show-plan'))
    await userEvent.click(screen.getByTestId('authorize-remediation'))

    await waitFor(() => expect(screen.getByTestId('hitl-error')).toHaveTextContent('409'))
    expect(screen.queryByTestId('control-error')).not.toBeInTheDocument()
  })

  it('sends all three identifiers with the decision', async () => {
    vi.mocked(api.fetchSnapshot).mockResolvedValue(
      snapshotFor('AWAITING_APPROVAL', 'inc-7', ScenarioId.WORKER_DEADLOCK),
    )
    const authorize = vi.spyOn(api, 'authorizeIncident').mockResolvedValue({
      incident_id: 'inc-7',
      state: 'EXECUTING',
      job_id: 'job-1',
      duplicate: false,
    })

    render(<App />)
    await waitFor(() => expect(screen.getByTestId('show-plan')).toBeInTheDocument())
    await userEvent.click(screen.getByTestId('show-plan'))
    await userEvent.click(screen.getByTestId('reject-remediation'))

    // A `thread_id` from another run would resume the wrong graph, which is why the API checks all
    // three and the client must send all three.
    await waitFor(() =>
      expect(authorize).toHaveBeenCalledWith('inc-7', 'thread-inc-7', ScenarioId.WORKER_DEADLOCK, false),
    )
  })

  it('shows the no-impact strip only for the security scenario', async () => {
    vi.mocked(api.fetchSnapshot).mockResolvedValue(
      snapshotFor('AWAITING_APPROVAL', 'inc-2', ScenarioId.DB_POOL_EXHAUSTION),
    )
    const { unmount } = render(<App />)
    await waitFor(() => expect(screen.getByTestId('show-plan')).toBeInTheDocument())
    expect(screen.queryByTestId('no-impact-strip')).not.toBeInTheDocument()
    unmount()

    vi.mocked(api.fetchSnapshot).mockResolvedValue(
      snapshotFor('EXPLOIT_INTERCEPTED', 'inc-3', ScenarioId.PROMPT_INJECTION),
    )
    render(<App />)
    await waitFor(() =>
      expect(screen.getByTestId('no-impact-strip')).toHaveTextContent(/no customer impact/i),
    )
  })

  it('banners the terminal states and not the security phase', async () => {
    vi.mocked(api.fetchSnapshot).mockResolvedValue(
      snapshotFor('EXPLOIT_INTERCEPTED', 'inc-4', ScenarioId.PROMPT_INJECTION),
    )
    const { unmount } = render(<App />)
    await waitFor(() => expect(screen.getByTestId('no-impact-strip')).toBeInTheDocument())
    // A phase, not a state — bannering it would end the demo three steps early.
    expect(screen.queryByTestId('terminal-state-banner')).not.toBeInTheDocument()
    unmount()

    vi.mocked(api.fetchSnapshot).mockResolvedValue(
      snapshotFor('SECURITY_CONTAINED', 'inc-5', ScenarioId.PROMPT_INJECTION),
    )
    render(<App />)
    await waitFor(() =>
      expect(screen.getByTestId('terminal-state-banner')).toHaveAttribute('data-state', 'SECURITY_CONTAINED'),
    )
  })

  it('opens the postmortem drawer once a run recovers, and only once', async () => {
    render(<App />)
    await waitFor(() => expect(screen.getByTestId('golden-signals')).toBeInTheDocument())

    // The archive line is what carries the object key — the worker's clock wrote it, not the browser.
    await waitFor(() => {
      emit(metricsFrame('EXECUTING', 'inc-6'))
      emit(archiveFrame('inc-6', 'w-archive'))
      emit(metricsFrame('HEALTHY', null))
    })

    expect(await screen.findByTestId('postmortem-modal')).toBeInTheDocument()
    expect(screen.getByTestId('postmortem-link')).toHaveTextContent(POSTMORTEM_KEY)
    expect(screen.getByTestId('postmortem-download')).toHaveTextContent('Download JSON')

    await userEvent.click(screen.getByTestId('postmortem-close'))
    expect(screen.queryByTestId('postmortem-modal')).not.toBeInTheDocument()

    // Dismissed for this run means dismissed — a further frame must not bring it back.
    await waitFor(() => emit(metricsFrame('HEALTHY', null)))
    expect(screen.queryByTestId('postmortem-modal')).not.toBeInTheDocument()
  })

  it.each([
    [ScenarioId.DB_POOL_EXHAUSTION, 'HEALTHY'],
    [ScenarioId.CACHE_THUNDERING_HERD, 'HEALTHY'],
    [ScenarioId.WORKER_DEADLOCK, 'HEALTHY'],
    [ScenarioId.PROMPT_INJECTION, 'SECURITY_CONTAINED'],
  ] as Array<[ScenarioId, IncidentState]>)('opens itself for a successful %s run', async (scenario, ending) => {
    vi.mocked(api.fetchSnapshot).mockResolvedValue(snapshotFor('EXECUTING', 'inc-auto', scenario))
    render(<App />)
    await waitFor(() => expect(screen.getByTestId('golden-signals')).toBeInTheDocument())

    await waitFor(() => {
      emit(archiveFrame('inc-auto', `w-auto-${scenario}`))
      emit(metricsFrame(ending, ending === 'HEALTHY' ? null : 'inc-auto'))
    })

    // No click required. The report is the payoff, and a link the visitor has to notice is a link
    // most visitors do not follow.
    expect(await screen.findByTestId('postmortem-modal')).toBeInTheDocument()
  })

  it('opens the drawer for a contained security run too', async () => {
    // `SECURITY_CONTAINED` archives forensics exactly like a recovery archives a postmortem, and
    // the report is the evidence that the three containment tools ran and the injected ones did not.
    render(<App />)
    await waitFor(() => expect(screen.getByTestId('golden-signals')).toBeInTheDocument())

    await waitFor(() => {
      emit(metricsFrame('EXECUTING', 'inc-sec'))
      emit(archiveFrame('inc-sec', 'w-sec'))
      emit(metricsFrame('SECURITY_CONTAINED', 'inc-sec'))
    })

    expect(await screen.findByTestId('postmortem-modal')).toBeInTheDocument()
  })

  it.each<IncidentState>(['REJECTED', 'FAILED'])('never opens the drawer for a %s run', async (state) => {
    // Neither ending produced a completed job, so there is no report — and a drawer here would
    // present a recovery that did not happen.
    render(<App />)
    await waitFor(() => expect(screen.getByTestId('golden-signals')).toBeInTheDocument())

    await waitFor(() => {
      emit(metricsFrame('EXECUTING', 'inc-t'))
      emit(archiveFrame('inc-t', 'w-t'))
      emit(metricsFrame(state, 'inc-t'))
    })

    expect(screen.queryByTestId('postmortem-modal')).not.toBeInTheDocument()
    expect(screen.queryByTestId('postmortem-open')).not.toBeInTheDocument()
  })

  it('leaves a persistent way back into a dismissed postmortem', async () => {
    render(<App />)
    await waitFor(() => expect(screen.getByTestId('golden-signals')).toBeInTheDocument())

    await waitFor(() => {
      emit(metricsFrame('EXECUTING', 'inc-9'))
      emit(archiveFrame('inc-9', 'w-9'))
      emit(metricsFrame('HEALTHY', null))
    })

    await screen.findByTestId('postmortem-modal')
    // While the drawer is open the footer control would be a second door into the same room.
    expect(screen.queryByTestId('postmortem-open')).not.toBeInTheDocument()

    await userEvent.click(screen.getByTestId('postmortem-close'))
    const reopen = await screen.findByTestId('postmortem-open')

    await userEvent.click(reopen)
    expect(await screen.findByTestId('postmortem-modal')).toBeInTheDocument()
  })

  it('keeps a finished run visible instead of blanking on completion', async () => {
    // The regression that motivated `lastRunId`: the worker log and the archive confirmation were
    // wiped the moment the run ended, because the server goes back to emitting `incident_id: null`.
    render(<App />)
    await waitFor(() => expect(screen.getByTestId('golden-signals')).toBeInTheDocument())

    await waitFor(() => {
      emit(metricsFrame('EXECUTING', 'inc-8'))
      emit(archiveFrame('inc-8', 'w-8'))
      emit(metricsFrame('HEALTHY', null))
    })

    expect(screen.getByTestId('worker-log')).toHaveTextContent('Postmortem archived')
  })
})

describe('the current-state explanation', () => {
  it('asks for a scenario before one is chosen', async () => {
    render(<App />)

    await waitFor(() =>
      expect(screen.getByTestId('state-explanation')).toHaveTextContent(
        'Choose a scenario to watch the incident-response workflow.',
      ),
    )
  })

  it('announces politely as a sibling of the badge, never as its ancestor', async () => {
    // Two nested polite regions make a screen reader announce the badge twice, and the E2E suite
    // asserts the badge has exactly one live ancestor.
    render(<App />)

    await waitFor(() => expect(screen.getByTestId('state-explanation')).toBeInTheDocument())
    const explanation = screen.getByTestId('state-explanation')
    expect(explanation).toHaveAttribute('aria-live', 'polite')
    expect(explanation.contains(screen.getByTestId('status-badge'))).toBe(false)
    expect(screen.getByTestId('status-badge').closest('[aria-live="polite"]')).not.toBe(explanation)
  })

  it.each([
    ['CRITICAL_OUTAGE', ScenarioId.DB_POOL_EXHAUSTION, 'Database connection capacity is exhausted'],
    ['AWAITING_APPROVAL', ScenarioId.CACHE_THUNDERING_HERD, 'waiting for human approval to warm key data'],
    ['EXECUTING', ScenarioId.WORKER_DEADLOCK, 'quarantining the bad message and restarting queue workers'],
    ['EXPLOIT_INTERCEPTED', ScenarioId.PROMPT_INJECTION, 'The safety guardrail blocked it before anything ran.'],
    ['REJECTED', ScenarioId.DB_POOL_EXHAUSTION, 'not approved, so no recovery tool ran'],
    ['FAILED', ScenarioId.DB_POOL_EXHAUSTION, 'The approved action failed.'],
  ] as Array<[IncidentState, ScenarioId, string]>)('explains %s for %s', async (state, scenario, copy) => {
    vi.mocked(api.fetchSnapshot).mockResolvedValue(snapshotFor(state, 'inc-x', scenario))
    render(<App />)

    await waitFor(() => expect(screen.getByTestId('state-explanation')).toHaveTextContent(copy))
  })

  it('keeps explaining the finished run after the server stops reporting its scenario', async () => {
    // The server emits `scenario_id: null` once a run ends, but "Database capacity is restored" is
    // exactly the sentence the visitor needs at that moment — so the copy reads the last scenario.
    vi.mocked(api.fetchSnapshot).mockResolvedValue(snapshotFor('RECOVERING', 'inc-done', ScenarioId.DB_POOL_EXHAUSTION))
    render(<App />)
    await waitFor(() => expect(screen.getByTestId('state-explanation')).toHaveTextContent(/returning to normal/))

    vi.mocked(api.fetchSnapshot).mockResolvedValue(snapshotFor('HEALTHY', null, null))
    await waitFor(() => emit(metricsFrame('HEALTHY', null)))
    // A reset is the one thing that clears it; a completed run does not.
    await waitFor(() =>
      expect(screen.getByTestId('state-explanation')).toHaveTextContent(
        'Database capacity is restored and requests are succeeding normally.',
      ),
    )
  })
})

describe('the impact and outcome strips', () => {
  it.each([
    [ScenarioId.DB_POOL_EXHAUSTION, 'scenario-impact-strip', /database capacity is exhausted/i],
    [ScenarioId.CACHE_THUNDERING_HERD, 'scenario-impact-strip', /cache misses are increasing response time/i],
    [ScenarioId.WORKER_DEADLOCK, 'scenario-impact-strip', /jobs are waiting behind a blocked worker/i],
    [ScenarioId.PROMPT_INJECTION, 'no-impact-strip', /no customer impact — 0 unauthorized actions/i],
  ] as Array<[ScenarioId, string, RegExp]>)('names the live cost of %s', async (scenario, testId, copy) => {
    vi.mocked(api.fetchSnapshot).mockResolvedValue(snapshotFor('AWAITING_APPROVAL', 'inc-i', scenario))
    render(<App />)

    await waitFor(() => expect(screen.getByTestId(testId)).toHaveTextContent(copy))
    expect(screen.getByTestId(testId)).toHaveAttribute('data-scenario', scenario)
  })

  it('keeps the security scenario on its established strip identity', async () => {
    // `no-impact-strip` is asserted by the Playwright suite. The generalisation added the other
    // three scenarios; it did not rename this one.
    vi.mocked(api.fetchSnapshot).mockResolvedValue(
      snapshotFor('SECURITY_CONTAINED', 'inc-sec2', ScenarioId.PROMPT_INJECTION),
    )
    render(<App />)

    await waitFor(() => expect(screen.getByTestId('no-impact-strip')).toBeInTheDocument())
    expect(screen.queryByTestId('scenario-impact-strip')).not.toBeInTheDocument()
  })

  it('stops claiming impact once an outage begins recovering', async () => {
    vi.mocked(api.fetchSnapshot).mockResolvedValue(snapshotFor('RECOVERING', 'inc-r', ScenarioId.DB_POOL_EXHAUSTION))
    render(<App />)

    await waitFor(() => expect(screen.getByTestId('golden-signals')).toBeInTheDocument())
    expect(screen.queryByTestId('scenario-impact-strip')).not.toBeInTheDocument()
    expect(screen.queryByTestId('scenario-outcome-strip')).not.toBeInTheDocument()
  })

  it.each([
    [ScenarioId.DB_POOL_EXHAUSTION, 'HEALTHY', /database capacity restored/i],
    [ScenarioId.CACHE_THUNDERING_HERD, 'HEALTHY', /cache hit rate restored/i],
    [ScenarioId.WORKER_DEADLOCK, 'HEALTHY', /bad message quarantined/i],
    [ScenarioId.PROMPT_INJECTION, 'SECURITY_CONTAINED', /threat contained/i],
  ] as Array<[ScenarioId, IncidentState, RegExp]>)('reports the outcome of %s', async (scenario, state, copy) => {
    vi.mocked(api.fetchSnapshot).mockResolvedValue(snapshotFor(state, 'inc-o', scenario))
    render(<App />)

    await waitFor(() => expect(screen.getByTestId('scenario-outcome-strip')).toHaveTextContent(copy))
  })

  it.each<IncidentState>(['REJECTED', 'FAILED'])('shows no success banner for a %s run', async (state) => {
    // These endings keep their terminal banner. A success message beside a failure banner would be
    // the page contradicting itself.
    vi.mocked(api.fetchSnapshot).mockResolvedValue(snapshotFor(state, 'inc-f', ScenarioId.DB_POOL_EXHAUSTION))
    render(<App />)

    await waitFor(() => expect(screen.getByTestId('terminal-state-banner')).toBeInTheDocument())
    expect(screen.queryByTestId('scenario-outcome-strip')).not.toBeInTheDocument()
    expect(screen.queryByTestId('scenario-impact-strip')).not.toBeInTheDocument()
  })

  it('shows neither strip before a scenario is chosen', async () => {
    render(<App />)

    await waitFor(() => expect(screen.getByTestId('golden-signals')).toBeInTheDocument())
    expect(screen.queryByTestId('scenario-impact-strip')).not.toBeInTheDocument()
    expect(screen.queryByTestId('no-impact-strip')).not.toBeInTheDocument()
    expect(screen.queryByTestId('scenario-outcome-strip')).not.toBeInTheDocument()
  })
})

describe('triggering a new incident', () => {
  it('clears the previous run from the plan and worker consoles on the click', async () => {
    // The panels used to wait for the server to *tell* them a new run had started: `CLEARED_RUN`
    // fires when a frame carrying the new incident id arrives, which is up to a second after the
    // click. In that window a finished plan sat on screen presented as the new run's.
    vi.mocked(api.fetchSnapshot).mockResolvedValue(snapshotFor('HEALTHY', null, null))
    vi.spyOn(api, 'triggerIncident').mockResolvedValue({
      incident_id: 'inc-new',
      thread_id: 'thread-new',
      scenario_id: ScenarioId.CACHE_THUNDERING_HERD,
      state: 'CRITICAL_OUTAGE',
    })

    render(<App />)
    await waitFor(() => expect(screen.getByTestId('golden-signals')).toBeInTheDocument())

    // A finished run, with reasoning and a worker line on screen.
    await waitFor(() => {
      emit(metricsFrame('AWAITING_APPROVAL', 'inc-old'))
      emit(thoughtFrame('inc-old', 't-old', 'Analyzed the previous incident.'))
      emit(archiveFrame('inc-old', 'w-old'))
      emit(metricsFrame('HEALTHY', null))
    })
    expect(screen.getAllByTestId('reasoning-step')).toHaveLength(1)
    expect(screen.getAllByTestId('worker-log-line')).toHaveLength(1)

    await userEvent.click(screen.getByTestId('trigger-cache_thundering_herd'))

    // Gone with the click, before any frame for the new run has arrived. The consoles fall back to
    // their standby copy rather than showing the old run's evidence.
    await waitFor(() => expect(screen.queryByTestId('reasoning-step')).not.toBeInTheDocument())
    expect(screen.queryByTestId('worker-log-line')).not.toBeInTheDocument()
    expect(screen.getByTestId('reasoning-chain')).toHaveTextContent('The AI investigates the incident.')
  })

  it('keeps the plan and worker consoles on the page across the reset', async () => {
    // Only the derived panels are cleared, not the identifiers — otherwise the pair would blink out
    // of existence between the click and the first frame of the new run.
    vi.spyOn(api, 'triggerIncident').mockResolvedValue({
      incident_id: 'inc-new',
      thread_id: 'thread-new',
      scenario_id: ScenarioId.CACHE_THUNDERING_HERD,
      state: 'CRITICAL_OUTAGE',
    })

    render(<App />)
    await waitFor(() => expect(screen.getByTestId('golden-signals')).toBeInTheDocument())
    await waitFor(() => emit(metricsFrame('CRITICAL_OUTAGE', 'inc-old')))
    expect(screen.getByTestId('decision-grid')).toBeInTheDocument()

    await userEvent.click(screen.getByTestId('trigger-worker_deadlock'))

    expect(screen.getByTestId('decision-grid')).toBeInTheDocument()
    expect(screen.getByTestId('agent-reasoning')).toBeInTheDocument()
    expect(screen.getByTestId('execution-terminal')).toBeInTheDocument()
  })
})
