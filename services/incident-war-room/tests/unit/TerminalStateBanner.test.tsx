/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        TerminalStateBanner.test.tsx
 * Purpose:          Tests the three non-recovery endings — their copy, their colours, and the
 *                   Master Reset that ends the hold.
 * Interacts With:   TerminalStateBanner component
 *
 * Curriculum Project: Project 5 — Autonomous Agent & Human-in-the-Loop
 * Skills:           Terminal State Handling, Accessibility
 * Tools:            Vitest, React Testing Library
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import {
  TERMINAL_STATES,
  TerminalStateBanner,
  isTerminalState,
} from '../../src/components/TerminalStateBanner'
import { IncidentState } from '../../src/types/contracts.gen'

describe('isTerminalState', () => {
  it('recognises exactly the three non-recovery endings', () => {
    const terminal = Object.values(IncidentState).filter(isTerminalState)

    expect(terminal.sort()).toEqual([...TERMINAL_STATES].sort())
  })

  it('does not treat EXPLOIT_INTERCEPTED as terminal', () => {
    // It is a phase, not a state — the run sits there between the guardrail firing and the SRE
    // approving containment. Banner-ing it would end the demo three steps early.
    expect(isTerminalState('EXPLOIT_INTERCEPTED')).toBe(false)
  })
})

describe('TerminalStateBanner', () => {
  it('carries the documented rejection headline', () => {
    render(<TerminalStateBanner state="REJECTED" failureReason={null} busy={false} onReset={vi.fn()} />)

    expect(screen.getByTestId('terminal-state-banner')).toHaveTextContent(
      'REMEDIATION REJECTED — INTERVENTION SKIPPED',
    )
  })

  it('shows the worker error on a failed run', () => {
    render(
      <TerminalStateBanner
        state="FAILED"
        failureReason="pg_terminate_backend refused: role lacks privileges"
        busy={false}
        onReset={vi.fn()}
      />,
    )

    expect(screen.getByTestId('failure-reason')).toHaveTextContent('role lacks privileges')
  })

  it('shows no error line on the other two endings', () => {
    // A stray reason on REJECTED would suggest something went wrong, when nothing did — the human
    // simply declined.
    render(
      <TerminalStateBanner state="REJECTED" failureReason="should not appear" busy={false} onReset={vi.fn()} />,
    )

    expect(screen.queryByTestId('failure-reason')).not.toBeInTheDocument()
  })

  it('paints security containment cyan rather than crimson', () => {
    // A guardrail that held is a success. This is the assertion that stops a well-meaning refactor
    // from grouping all three "non-recovery" endings under one alarm colour.
    render(
      <TerminalStateBanner state="SECURITY_CONTAINED" failureReason={null} busy={false} onReset={vi.fn()} />,
    )

    const banner = screen.getByTestId('terminal-state-banner')
    expect(banner.className).toContain('border-guard')
    expect(banner.className).not.toContain('border-alarm')
    expect(banner).toHaveTextContent('SESSION REVOKED, IP BLOCKED, FORENSICS ARCHIVED')
  })

  it.each(TERMINAL_STATES)('names the state in text as well as in colour for %s', (state) => {
    // §1's never-colour-alone rule, and an accessibility requirement.
    render(<TerminalStateBanner state={state} failureReason={null} busy={false} onReset={vi.fn()} />)

    expect(screen.getByTestId('terminal-state-banner')).toHaveTextContent(/State:/)
  })

  it.each(TERMINAL_STATES)('offers Master Reset from %s', async (state) => {
    const onReset = vi.fn()
    render(<TerminalStateBanner state={state} failureReason={null} busy={false} onReset={onReset} />)

    await userEvent.click(screen.getByTestId('banner-master-reset'))
    expect(onReset).toHaveBeenCalledOnce()
  })

  it('announces itself politely', () => {
    render(<TerminalStateBanner state="FAILED" failureReason={null} busy={false} onReset={vi.fn()} />)

    expect(screen.getByTestId('terminal-state-banner')).toHaveAttribute('aria-live', 'polite')
  })

  it('disables reset while a request is in flight', () => {
    render(<TerminalStateBanner state="REJECTED" failureReason={null} busy onReset={vi.fn()} />)

    expect(screen.getByTestId('banner-master-reset')).toBeDisabled()
  })
})
