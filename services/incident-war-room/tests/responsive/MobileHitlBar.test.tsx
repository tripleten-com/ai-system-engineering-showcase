/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        MobileHitlBar.test.tsx
 * Purpose:          Tests the most important responsive decision in the design system — the approval
 *                   entry point pinning to a sticky bottom bar below 768px, exactly once.
 * Interacts With:   MobileHitlBar, AgentReasoningView, App
 *
 * Curriculum Project: Project 5 — Autonomous Agent & Human-in-the-Loop
 * Skills:           Responsive Design, Mobile Viewport Testing
 * Tools:            Vitest, React Testing Library
 *
 * JSDOM has no viewport, so these assert the *mechanism* rather than the rendered breakpoint: the
 * bar carries `md:hidden`, the inline block is suppressed when the bar is present, and both render
 * the same control. A Playwright spec covers the real 375px render.
 *
 * What the bar carries is now the modal *trigger*, not the decision. The authorize and reject
 * buttons live in `PlanApprovalModal`, which `App` renders once for both placements — so the
 * "exactly one authorize control" rule is enforced by there being one dialog, and the rule this file
 * checks is that there is exactly one way to open it.
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { AgentReasoningView } from '../../src/components/AgentReasoningView'
import { MobileHitlBar } from '../../src/components/MobileHitlBar'
import { ScenarioId } from '../../src/types/contracts.gen'
import { thought } from '../fixtures'

describe('MobileHitlBar', () => {
  it('pins to the bottom and hides at md and above', () => {
    render(<MobileHitlBar busy={false} onShowPlan={vi.fn()} />)

    const bar = screen.getByTestId('mobile-hitl-bar')
    expect(bar.className).toContain('fixed')
    expect(bar.className).toContain('bottom-0')
    // Tailwind's `md` is 768px, which is exactly the breakpoint the rule names.
    expect(bar.className).toContain('md:hidden')
  })

  it('separates itself from the page with a top hairline over the raised console surface', () => {
    render(<MobileHitlBar busy={false} onShowPlan={vi.fn()} />)

    const bar = screen.getByTestId('mobile-hitl-bar')
    expect(bar.className).toContain('border-t')
    expect(bar.className).toContain('bg-raised')
  })

  it('carries the modal trigger, not the decision itself', () => {
    render(<MobileHitlBar busy={false} onShowPlan={vi.fn()} />)

    expect(screen.getByTestId('show-plan')).toHaveTextContent(/show ai action plan/i)
    // The decision lives in the dialog. A sticky bar that could approve outright would put an
    // irreversible action under a thumb resting at the bottom of a phone screen.
    expect(screen.queryByTestId('authorize-remediation')).not.toBeInTheDocument()
    expect(screen.queryByTestId('reject-remediation')).not.toBeInTheDocument()
  })

  it('meets the 44px touch-target minimum', () => {
    render(<MobileHitlBar busy={false} onShowPlan={vi.fn()} />)

    expect(screen.getByTestId('show-plan').className).toContain('min-h-[44px]')
  })

  it('opens the plan through the same callback the inline footer uses', async () => {
    const onShowPlan = vi.fn()
    render(<MobileHitlBar busy={false} onShowPlan={onShowPlan} />)

    await userEvent.click(screen.getByTestId('show-plan'))
    expect(onShowPlan).toHaveBeenCalledTimes(1)
  })

  it('is disabled while a request is in flight', () => {
    render(<MobileHitlBar busy onShowPlan={vi.fn()} />)

    expect(screen.getByTestId('show-plan')).toBeDisabled()
  })

  it('never coexists with an inline trigger', () => {
    // Two triggers would put two of them in the tab order, and a keyboard user would reach the
    // hidden one first. The App passes `hitlInline={false}` for exactly this reason.
    render(
      <>
        <AgentReasoningView
          state="AWAITING_APPROVAL"
          scenarioId={ScenarioId.DB_POOL_EXHAUSTION}
          thoughts={[thought({ id: 't1', step: 1 })]}
          busy={false}
          error={null}
          onShowPlan={vi.fn()}
          hitlInline={false}
        />
        <MobileHitlBar busy={false} onShowPlan={vi.fn()} />
      </>,
    )

    expect(screen.getAllByTestId('show-plan')).toHaveLength(1)
  })
})
