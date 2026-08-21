/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        AgentReasoningView.test.tsx
 * Purpose:          Unit tests for the reasoning chain and, more importantly, for the HITL gate:
 *                   the controls exist only in AWAITING_APPROVAL and carry the contractual text.
 * Interacts With:   AgentReasoningView component
 *
 * Curriculum Project: Project 5 — Autonomous Agent & Human-in-the-Loop
 * Skills:           React Component Testing, HITL Gate, Accessibility
 * Tools:            Vitest, React Testing Library
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { AgentReasoningView } from '../../src/components/AgentReasoningView'
import {
  AgentPhase,
  GuardrailVerdict,
  ScenarioId,
  ToolName,
  type IncidentState,
} from '../../src/types/contracts.gen'
import { BLOCKED_THOUGHT, thought } from '../fixtures'

const CHAIN = [
  thought({ id: 't1', step: 1 }),
  thought({ id: 't2', step: 2, text: 'RAG retrieved RB-104.' }),
  thought({
    id: 't3',
    step: 3,
    text: 'Formulated remediation payload.',
    tool_call: { name: 'flush_connection_pool', args: { idle_seconds: 60 }, is_canonical: true },
  }),
]

function renderView(state: IncidentState, overrides: Partial<Parameters<typeof AgentReasoningView>[0]> = {}) {
  const onShowPlan = vi.fn()
  render(
    <AgentReasoningView
      state={state}
      scenarioId={ScenarioId.DB_POOL_EXHAUSTION}
      thoughts={CHAIN}
      busy={false}
      error={null}
      onShowPlan={onShowPlan}
      {...overrides}
    />,
  )
  return { onShowPlan }
}

describe('AgentReasoningView', () => {
  it('renders every reasoning step in the order the agent emitted it', () => {
    // A plan is a sequence: step 2 follows step 1 because it depended on it, and reading that
    // bottom-to-top makes the reader rebuild the order themselves.
    renderView('CRITICAL_OUTAGE')

    const steps = screen.getAllByTestId('reasoning-step')
    expect(steps.map((step) => step.dataset.step)).toEqual(['1', '2', '3'])
  })

  it('shows the standby copy before the agent runs', () => {
    render(
      <AgentReasoningView
        state="HEALTHY"
        scenarioId={null}
        thoughts={[]}
        busy={false}
        error={null}
        onShowPlan={vi.fn()}
      />,
    )

    expect(screen.getByText(/The AI investigates the incident\./)).toBeInTheDocument()
  })

  it('has no technical-details disclosure, and renders no raw tool call anywhere', () => {
    // Removed by request. The consequence is worth stating: the model's own reasoning text and its
    // tool arguments are no longer rendered in this console at all. The upside is that the one place
    // this UI could have shown attacker-controlled text as product copy is now closed by
    // construction — `LogSanitizerView` still carries the blocked-call signature as evidence.
    render(
      <AgentReasoningView
        state="EXPLOIT_INTERCEPTED"
        scenarioId={ScenarioId.PROMPT_INJECTION}
        thoughts={[CHAIN[2], BLOCKED_THOUGHT]}
        busy={false}
        error={null}
        onShowPlan={vi.fn()}
      />,
    )

    expect(screen.queryByTestId('technical-details')).not.toBeInTheDocument()
    expect(screen.queryByTestId('reasoning-step-detail')).not.toBeInTheDocument()
    const panel = screen.getByTestId('agent-reasoning')
    expect(panel).not.toHaveTextContent('flush_connection_pool')
    expect(panel).not.toHaveTextContent('delete_all_customer_records')
  })

  it.each<IncidentState>(['HEALTHY', 'CRITICAL_OUTAGE', 'EXECUTING', 'RECOVERING', 'REJECTED', 'FAILED'])(
    'renders no way into the decision in %s',
    (state) => {
      // Absent, not disabled. A greyed-out control invites a viewer to wonder whether it would have
      // worked; the claim is that nothing can run without the click.
      renderView(state)

      expect(screen.queryByTestId('show-plan')).not.toBeInTheDocument()
      // The decision itself never lives in this console — it is in the modal.
      expect(screen.queryByTestId('authorize-remediation')).not.toBeInTheDocument()
      expect(screen.queryByTestId('reject-remediation')).not.toBeInTheDocument()
    },
  )

  it('renders the gate only in AWAITING_APPROVAL', () => {
    renderView('AWAITING_APPROVAL')

    expect(screen.getByTestId('hitl-block')).toBeInTheDocument()
    expect(screen.getByTestId('show-plan')).toBeInTheDocument()
  })

  it('keeps a fixed-height footer so it lines up with the other three consoles', () => {
    renderView('CRITICAL_OUTAGE')

    const footer = screen.getByTestId('console-frame-footer')
    expect(footer).toHaveClass('md:h-console-footer')
    expect(footer.className).not.toContain('md:min-h-console-footer')
  })

  it('opens the plan rather than deciding anything', async () => {
    // The footer control commits to nothing, which is what lets it sit in a console footer where an
    // irreversible pair could not.
    const { onShowPlan } = renderView('AWAITING_APPROVAL')

    await userEvent.click(screen.getByTestId('show-plan'))
    expect(onShowPlan).toHaveBeenCalledTimes(1)
    expect(screen.queryByTestId('authorize-remediation')).not.toBeInTheDocument()
  })

  it('disables the trigger while a request is in flight', () => {
    renderView('AWAITING_APPROVAL', { busy: true })

    expect(screen.getByTestId('show-plan')).toBeDisabled()
  })

  it('suppresses the inline trigger when it has moved to the sticky bar', () => {
    // Two triggers in the tab order is worse than one in the wrong place.
    renderView('AWAITING_APPROVAL', { hitlInline: false })

    expect(screen.queryByTestId('hitl-block')).not.toBeInTheDocument()
    expect(screen.queryByTestId('show-plan')).not.toBeInTheDocument()
  })

  it('announces the gate politely', () => {
    renderView('AWAITING_APPROVAL')

    expect(screen.getByTestId('hitl-block').closest('[aria-live="polite"]')).not.toBeNull()
  })

  it('keeps an authorize failure visible after the gate has closed behind it', () => {
    // A refused decision that leaves no trace reads as a click that did nothing.
    renderView('EXECUTING', { error: '409 — run is no longer awaiting approval' })

    expect(screen.queryByTestId('hitl-block')).not.toBeInTheDocument()
    expect(screen.getByTestId('hitl-error')).toHaveTextContent('409')
  })

  it('always renders a footer, so the paired consoles cannot drift out of line', () => {
    renderView('CRITICAL_OUTAGE')

    expect(screen.getByTestId('console-frame-footer')).toHaveTextContent(
      'No approved action will run until a human approves it.',
    )
  })
})

describe('plan narration', () => {
  const CHAIN_WITH_PHASES = [
    thought({ id: 'a1', step: 1, phase: AgentPhase.ANALYZING }),
    thought({ id: 'a2', step: 2, phase: AgentPhase.ANALYZING }),
    thought({ id: 'r1', step: 3, phase: AgentPhase.RETRIEVING }),
    thought({ id: 'p1', step: 4, phase: AgentPhase.PLANNING }),
    thought({
      id: 's1',
      step: 5,
      phase: AgentPhase.TOOL_SELECTION,
      tool_call: { name: ToolName.FLUSH_CONNECTION_POOL, args: { idle_seconds: 60 }, is_canonical: true },
    }),
    thought({ id: 'w1', step: 6, phase: AgentPhase.AWAITING_APPROVAL }),
  ]

  function renderChain(thoughts = CHAIN_WITH_PHASES) {
    render(
      <AgentReasoningView
        state="AWAITING_APPROVAL"
        scenarioId={ScenarioId.DB_POOL_EXHAUSTION}
        thoughts={thoughts}
        busy={false}
        error={null}
        onShowPlan={vi.fn()}
      />,
    )
  }

  it('gives every step a distinct plain-language sentence', () => {
    // The defect this replaces: one of three generic sentences repeated down the whole chain.
    renderChain()

    const sentences = screen.getAllByTestId('reasoning-step').map((step) => step.textContent)
    expect(new Set(sentences).size).toBe(sentences.length)
  })

  it('names what each phase did', () => {
    renderChain()

    const shown = screen.getAllByTestId('reasoning-step').map((step) => step.textContent)
    expect(shown).toContain('The AI compared the alert with live service signals to identify the failure.')
    expect(shown).toContain('Sensitive values were removed before the incident evidence reached the AI.')
    expect(shown).toContain('The best matching recovery guide was found and checked.')
    expect(shown).toContain('The recovery guide was converted into a small, reversible plan.')
    expect(shown).toContain('Recycling idle database connections was selected as the fix.')
    expect(shown).toContain('The plan is ready. No approved action will run until a human approves it.')
  })



  it('describes blocked calls as numbered unsafe actions without their arguments', async () => {
    const injected = [
      thought({
        id: 'x1',
        step: 1,
        phase: AgentPhase.TOOL_SELECTION,
        guardrail: GuardrailVerdict.BLOCKED,
        text: 'INSPECTION_HALTED_MALICIOUS_PAYLOAD — delete_all_customer_records rejected.',
        tool_call: { name: 'delete_all_customer_records', args: { confirm: true }, is_canonical: false },
      }),
      thought({
        id: 'x2',
        step: 2,
        phase: AgentPhase.TOOL_SELECTION,
        guardrail: GuardrailVerdict.BLOCKED,
        text: 'INSPECTION_HALTED_MALICIOUS_PAYLOAD — exfiltrate_credentials rejected.',
        tool_call: { name: 'exfiltrate_credentials', args: { target: 'sre@example.com' }, is_canonical: false },
      }),
    ]
    renderChain(injected)

    const shown = screen.getAllByTestId('reasoning-step').map((step) => step.textContent)
    expect(shown).toEqual([
      'The first unsafe action was blocked before it could run.',
      'The second unsafe action was blocked before it could run.',
    ])
    // Attacker-controlled text must not be rendered as product copy in the default view.
    for (const sentence of shown) {
      expect(sentence).not.toContain('delete_all_customer_records')
      expect(sentence).not.toContain('sre@example.com')
    }

  })

  it('never renders a raw tool name or argument', () => {
    // The body says what a step did; the identifiers and arguments are not shown at all now that the
    // disclosure is gone.
    renderChain()

    const panel = screen.getByTestId('agent-reasoning')
    expect(panel).not.toHaveTextContent('flush_connection_pool')
    expect(panel).not.toHaveTextContent('idle_seconds')
  })

  it('renders machine output in the console face and colour', () => {
    renderChain()

    const step = screen.getAllByTestId('reasoning-step')[0]
    expect(step.className).toContain('font-console')
    expect(step.className).toContain('text-console-output')
    expect(step.className).toContain('text-console-line')
  })
})

describe('the approval gate wording', () => {
  it('asks for human approval rather than addressing the reader', () => {
    renderView('AWAITING_APPROVAL')

    const block = screen.getByTestId('hitl-block')
    expect(block).toHaveTextContent('Human approval is required before the proposed action can run.')
    expect(block).not.toHaveTextContent(/your approval/i)
  })

  it('says the same in the standby copy', () => {
    render(
      <AgentReasoningView
        state="HEALTHY"
        scenarioId={null}
        thoughts={[]}
        busy={false}
        error={null}
        onShowPlan={vi.fn()}
      />,
    )

    expect(
      screen.getByText('It stops and waits for human approval before anything changes.'),
    ).toBeInTheDocument()
    expect(screen.getByTestId('agent-reasoning')).not.toHaveTextContent(/your approval/i)
  })
})
