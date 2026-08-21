/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        PlanApprovalModal.test.tsx
 * Purpose:          Tests the approval dialog — the plan it shows, the contract text it keeps, and
 *                   the guarantee that nothing but a click can resolve the gate.
 * Interacts With:   PlanApprovalModal
 *
 * Curriculum Project: Project 5 — Autonomous Agent & Human-in-the-Loop
 * Skills:           Modal Accessibility, HITL Gate, Focus Management
 * Tools:            Vitest, React Testing Library
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { PlanApprovalModal } from '../../src/components/PlanApprovalModal'
import { AgentPhase, APPROVAL_PROMPT, ScenarioId, ToolName } from '../../src/types/contracts.gen'
import { BLOCKED_THOUGHT, thought } from '../fixtures'

const PLAN = [
  thought({ id: 'a1', step: 1, phase: AgentPhase.ANALYZING }),
  thought({ id: 'r1', step: 2, phase: AgentPhase.RETRIEVING }),
  thought({
    id: 's1',
    step: 3,
    phase: AgentPhase.TOOL_SELECTION,
    tool_call: { name: ToolName.FLUSH_CONNECTION_POOL, args: { idle_seconds: 60 }, is_canonical: true },
  }),
  thought({ id: 'w1', step: 4, phase: AgentPhase.AWAITING_APPROVAL }),
]

function renderModal(overrides: Partial<Parameters<typeof PlanApprovalModal>[0]> = {}) {
  const onDecision = vi.fn()
  const onClose = vi.fn()
  render(
    <PlanApprovalModal
      scenarioId={ScenarioId.DB_POOL_EXHAUSTION}
      thoughts={PLAN}
      busy={false}
      error={null}
      onDecision={onDecision}
      onClose={onClose}
      {...overrides}
    />,
  )
  return { onDecision, onClose }
}

describe('PlanApprovalModal', () => {
  it('is a labelled modal dialog', () => {
    renderModal()

    const dialog = screen.getByTestId('plan-modal')
    expect(dialog).toHaveAttribute('role', 'dialog')
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(screen.getByRole('dialog', { name: /ai action plan/i })).toBeInTheDocument()
  })

  it('shows the narrated plan, one step per line, in order', () => {
    renderModal()

    const steps = screen.getAllByTestId('plan-modal-step')
    expect(steps.map((step) => step.dataset.step)).toEqual(['1', '2', '3', '4'])
    expect(steps[0]).toHaveTextContent('The AI compared the alert with live service signals')
    expect(steps[3]).toHaveTextContent('No approved action will run until a human approves it.')
  })

  it('says the plan is still being drafted rather than showing an empty list', () => {
    // A dialog opened a beat early must not read as "there is no plan".
    renderModal({ thoughts: [] })

    expect(screen.queryByTestId('plan-modal-step')).not.toBeInTheDocument()
    expect(screen.getByTestId('plan-modal-steps')).toHaveTextContent('The plan is still being drafted')
  })

  it.each(Object.values(ScenarioId))('keeps the contractual approval prompt on screen for %s', (scenarioId) => {
    // `APPROVAL_PROMPT` is a generated canonical identifier and four Playwright specs read it off the
    // DOM. It stopped being the button *label* when the button became "Approve" — so it has to keep
    // naming the action somewhere, or the scenario-specific language leaves the UI entirely.
    renderModal({ scenarioId })

    expect(screen.getByTestId('plan-modal-prompt')).toHaveTextContent(APPROVAL_PROMPT[scenarioId])
  })

  it('labels the decision plainly and reports the two outcomes distinctly', async () => {
    const { onDecision } = renderModal()

    const approve = screen.getByTestId('authorize-remediation')
    expect(approve).toHaveTextContent('Approve')
    await userEvent.click(approve)
    expect(onDecision).toHaveBeenCalledWith(true)

    await userEvent.click(screen.getByTestId('reject-remediation'))
    expect(onDecision).toHaveBeenCalledWith(false)
  })

  it('disables both decisions while one is in flight', () => {
    renderModal({ busy: true })

    expect(screen.getByTestId('authorize-remediation')).toBeDisabled()
    expect(screen.getByTestId('reject-remediation')).toBeDisabled()
  })

  it('focuses dismiss, never approve', async () => {
    // A modal that opens with its irreversible action focused is one stray Enter away from
    // authorizing a remediation nobody read.
    renderModal()

    await waitFor(() => expect(screen.getByTestId('plan-modal-close')).toHaveFocus())
    expect(screen.getByTestId('authorize-remediation')).not.toHaveFocus()
  })

  it('dismisses on the control, on Escape, and on the backdrop — and approves on none of them', async () => {
    const { onClose, onDecision } = renderModal()

    await userEvent.click(screen.getByTestId('plan-modal-close'))
    await userEvent.keyboard('{Escape}')
    await userEvent.click(screen.getByTestId('plan-modal-backdrop'))

    expect(onClose).toHaveBeenCalledTimes(3)
    // Dismissing returns the visitor to a run still sitting at the gate. Nothing here auto-advances.
    expect(onDecision).not.toHaveBeenCalled()
  })

  it('does not dismiss on a click inside the dialog', async () => {
    const { onClose } = renderModal()

    await userEvent.click(screen.getByTestId('plan-modal-prompt'))

    expect(onClose).not.toHaveBeenCalled()
  })

  it('surfaces an authorize failure without closing', () => {
    renderModal({ error: '409 — run is no longer awaiting approval' })

    expect(screen.getByTestId('hitl-error')).toHaveTextContent('409')
    expect(screen.getByTestId('plan-modal')).toBeInTheDocument()
  })

  it('keeps the raw plan out of the default view but inside Technical details', async () => {
    // This disclosure is the only place in the UI that shows what the model literally emitted, and it
    // belongs at the moment of the decision rather than in the console it opens from.
    renderModal()

    expect(screen.getByTestId('plan-modal-steps')).not.toHaveTextContent('flush_connection_pool')
    // Present in the DOM but inside a closed `<details>`, so not visible.
    expect(screen.getAllByTestId('plan-modal-step-detail')[0]).not.toBeVisible()

    await userEvent.click(screen.getByTestId('technical-details').querySelector('summary')!)

    const details = screen.getByTestId('technical-details')
    expect(details).toHaveTextContent('flush_connection_pool')
    expect(details).toHaveTextContent('idle_seconds')
    expect(details).toHaveTextContent('Step 1')
    expect(screen.getAllByTestId('plan-modal-step-detail')).toHaveLength(4)
  })

  it('strikes a blocked call through and labels it as refused', async () => {
    // Attacker-controlled text is rendered here and nowhere else, so it is rendered as refused
    // evidence rather than as a step the plan proposes.
    renderModal({ thoughts: [BLOCKED_THOUGHT] })

    await userEvent.click(screen.getByTestId('technical-details').querySelector('summary')!)

    const detail = screen.getByTestId('plan-modal-step-detail')
    expect(detail.querySelector('code')?.className).toContain('line-through')
    expect(detail).toHaveTextContent('Blocked by schema firewall')
    expect(detail).toHaveTextContent('delete_all_customer_records')
  })

  it('offers no disclosure at all before there is a plan to disclose', () => {
    renderModal({ thoughts: [] })

    expect(screen.queryByTestId('technical-details')).not.toBeInTheDocument()
  })

  it('honours reduced motion through the named enter animation', () => {
    renderModal()

    expect(screen.getByTestId('plan-modal').className).toContain('animate-panel-enter')
  })
})
