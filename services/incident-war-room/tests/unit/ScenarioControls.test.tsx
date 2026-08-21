/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        ScenarioControls.test.tsx
 * Purpose:          Holds the launcher to being an enclosed panel of unmistakable buttons, with the
 *                   scenario identifiers and disabled behaviour the API depends on.
 * Interacts With:   ScenarioControls component
 *
 * Curriculum Project: Project 1 — Diagnostics & Telemetry
 * Skills:           React Component Testing, Affordance Verification, Accessibility
 * Tools:            Vitest, React Testing Library
 *
 * The affordance assertions are class-level rather than computed-style, because jsdom applies no
 * Tailwind stylesheet — there is nothing to compute. What they protect is the intent: a refactor
 * that drops the offset shadow or the press state turns these cards back into the tinted
 * information panels that reviewers did not try clicking.
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { ScenarioControls } from '../../src/components/ScenarioControls'
import { ScenarioId, type IncidentState } from '../../src/types/contracts.gen'

const TRIGGER_IDS = Object.values(ScenarioId).map((id) => `trigger-${id}`)

function renderControls(overrides: Partial<Parameters<typeof ScenarioControls>[0]> = {}) {
  const onTrigger = vi.fn()
  const onReset = vi.fn()
  render(
    <ScenarioControls
      state="HEALTHY"
      incidentId={null}
      busy={false}
      error={null}
      onTrigger={onTrigger}
      onReset={onReset}
      {...overrides}
    />,
  )
  return { onTrigger, onReset }
}

describe('the launcher panel', () => {
  it('is enclosed by a border on a warm off-white surface', () => {
    renderControls()

    const panel = screen.getByTestId('scenario-panel')
    expect(panel.className).toContain('border-ink')
    expect(panel.className).toMatch(/(?:^|\s)border(?:\s|$)/)
    expect(panel.className).toContain('bg-secondary')
  })

  it('keeps its heading', () => {
    renderControls()

    expect(screen.getByRole('heading', { name: /simulate live incident/i })).toBeInTheDocument()
  })
})

describe('the four scenario controls', () => {
  it('preserves every scenario identifier and test id', () => {
    // These strings cross the API boundary and are read by the Playwright suite. They are a
    // contract, and a redesign is exactly the change most likely to rename them by accident.
    renderControls()

    for (const scenario of Object.values(ScenarioId)) {
      const button = screen.getByTestId(`trigger-${scenario}`)
      expect(button).toHaveAttribute('data-scenario', scenario)
      expect(button.tagName).toBe('BUTTON')
    }
  })

  it.each(TRIGGER_IDS)('%s reads as a brand-gradient button with a strong dark border', (testId) => {
    renderControls()

    const button = screen.getByTestId(testId)
    // The fill is the TripleTen page-background wash, reached through a token — no component writes
    // the gradient stops.
    expect(button.className).toContain('bg-scenario-trigger')
    expect(button.className).toContain('border-strong')
    expect(button.className).toContain('border-2')
  })

  it('gives all four controls one height', () => {
    // Grid stretches items within a row but sizes each row to its own content, so a two-line
    // description in row one made that row taller than row two — four buttons at two heights.
    renderControls()

    expect(screen.getByTestId('scenario-launcher').className).toContain('sm:auto-rows-fr')
  })

  it('names the failing subsystem with a distinct glyph per scenario', () => {
    renderControls()

    const glyphs = TRIGGER_IDS.map((testId) => screen.getByTestId(testId).querySelector('svg')?.innerHTML)
    expect(glyphs.every(Boolean)).toBe(true)
    // Four scenarios, four glyphs. Three of the four used to share a lightning bolt, which made the
    // icon column carry no information at all.
    expect(new Set(glyphs).size).toBe(TRIGGER_IDS.length)
  })

  it.each(TRIGGER_IDS)('%s carries a tokenized offset shadow with hover lift and press feedback', (testId) => {
    renderControls()

    const button = screen.getByTestId(testId)
    expect(button.className).toContain('shadow-offset')
    expect(button.className).toContain('hover:-translate-y-0.5')
    expect(button.className).toContain('hover:shadow-offset-lift')
    expect(button.className).toContain('active:shadow-offset-press')
  })

  it.each(TRIGGER_IDS)('%s shows a visible keyboard focus ring and meets the touch target', (testId) => {
    renderControls()

    const button = screen.getByTestId(testId)
    expect(button.className).toContain('focus-visible:outline-2')
    expect(button.className).toContain('focus-visible:outline-guard')
    expect(button.className).toContain('min-h-[44px]')
  })

  it('flattens a disabled control instead of leaving it looking raised', () => {
    // An unavailable trigger that still casts a raised shadow invites the click it will ignore.
    renderControls({ busy: true })

    const button = screen.getByTestId('trigger-db_pool_exhaustion')
    expect(button).toBeDisabled()
    expect(button.className).toContain('disabled:opacity-40')
    expect(button.className).toContain('disabled:hover:translate-y-0')
    expect(button.className).toContain('disabled:hover:shadow-offset')
  })

  it('tints no scenario with a status colour', () => {
    // Colour here is a run-state system, and these buttons exist before a run does.
    renderControls()

    for (const testId of TRIGGER_IDS) {
      expect(screen.getByTestId(testId).className, testId).not.toMatch(
        /bg-(?:healthy|alarm|pending|active|guard)/,
      )
    }
  })

  it('triggers the scenario it names', async () => {
    const { onTrigger } = renderControls()

    await userEvent.click(screen.getByTestId('trigger-worker_deadlock'))
    expect(onTrigger).toHaveBeenCalledWith(ScenarioId.WORKER_DEADLOCK)
  })

  it.each<IncidentState>(['CRITICAL_OUTAGE', 'AWAITING_APPROVAL', 'EXECUTING', 'RECOVERING'])(
    'refuses a second trigger during %s',
    (state) => {
      // The API answers 409. Disabling rather than letting the click fail keeps the UI honest.
      renderControls({ state })

      for (const testId of TRIGGER_IDS) {
        expect(screen.getByTestId(testId), testId).toBeDisabled()
      }
    },
  )
})

describe('Master Reset', () => {
  it('is unusable until there is a run to reset', () => {
    const { onReset } = renderControls()

    expect(screen.getByTestId('master-reset')).toBeDisabled()
    expect(onReset).not.toHaveBeenCalled()
  })

  it('shares the button language once a run exists', async () => {
    const { onReset } = renderControls({ incidentId: 'inc-1', state: 'REJECTED' })

    const reset = screen.getByTestId('master-reset')
    expect(reset.className).toContain('shadow-offset')
    expect(reset.className).toContain('border-strong')
    await userEvent.click(reset)
    expect(onReset).toHaveBeenCalledTimes(1)
  })
})

describe('the current-state explanation', () => {
  it('sits under the panel title, where the control it describes is', () => {
    // It used to sit beside the hero badge. This is the panel a visitor is looking at when they need
    // the sentence: it says what to do before a run and what is happening during one.
    renderControls({ explanation: 'Choose a scenario to watch the incident-response workflow.' })

    const explanation = screen.getByTestId('state-explanation')
    expect(explanation).toHaveTextContent('Choose a scenario to watch the incident-response workflow.')
    expect(screen.getByTestId('scenario-panel').contains(explanation)).toBe(true)
  })

  it('announces politely and stays in the DOM when there is nothing to say', () => {
    // An `aria-live` element has to exist before its content changes to be announced at all, so this
    // renders empty rather than unmounting.
    renderControls({ explanation: null })

    const explanation = screen.getByTestId('state-explanation')
    expect(explanation).toHaveAttribute('aria-live', 'polite')
    expect(explanation).toHaveTextContent('')
  })
})
