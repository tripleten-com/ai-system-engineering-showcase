/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        LogSanitizerView.test.tsx
 * Purpose:          Unit tests for redaction-token pills, the masked count, the struck-through
 *                   blocked tool call, and the standby copy.
 * Interacts With:   LogSanitizerView component
 *
 * Curriculum Project: Project 4 — Security, PII Redaction & Guardrails
 * Skills:           React Component Testing, PII Redaction Highlights
 * Tools:            Vitest, React Testing Library
 */

import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { LogSanitizerView, countRedactions, segmentLogLine } from '../../src/components/LogSanitizerView'
import { redactionToken } from '../../src/lib/redaction'
import { ScenarioId } from '../../src/types/contracts.gen'
import { BLOCKED_THOUGHT, REDACTED_LOG, logEntry, thought } from '../fixtures'

describe('segmentLogLine', () => {
  it('splits a line into plain runs and redaction tokens', () => {
    const segments = segmentLogLine(`host=${redactionToken('ip')} ok`)

    expect(segments).toEqual([
      { text: 'host=', redacted: false },
      { text: '[REDACTED: ip]', redacted: true },
      { text: ' ok', redacted: false },
    ])
  })

  it('leaves a line with nothing masked as a single plain run', () => {
    expect(segmentLogLine('Connection pool healthy.')).toEqual([
      { text: 'Connection pool healthy.', redacted: false },
    ])
  })

  it('does not treat an unknown bracket tag as a redaction', () => {
    // The vocabulary is closed. A line mentioning `[REDACTED: everything]` is not evidence that
    // the guardrail fired, and counting it would inflate the number the panel reports.
    const segments = segmentLogLine('note=[REDACTED: everything]')

    expect(segments.some((segment) => segment.redacted)).toBe(false)
  })
})

describe('countRedactions', () => {
  it('counts every masked token across the visible tail', () => {
    expect(countRedactions([REDACTED_LOG])).toBe(3)
  })

  it('counts tokens of the same kind separately', () => {
    const line = logEntry('log-2', `a=${redactionToken('ip')} b=${redactionToken('ip')}`)
    expect(countRedactions([line])).toBe(2)
  })
})

describe('LogSanitizerView', () => {
  it('reports the detected redaction types and keeps complete lines in technical details', async () => {
    render(<LogSanitizerView logs={[REDACTED_LOG]} thoughts={[]} scenarioId={ScenarioId.DB_POOL_EXHAUSTION} />)

    expect(screen.getByTestId('log-sanitizer')).toHaveTextContent('3 Sensitive Tokens Masked')
    expect(screen.getByTestId('log-tail')).toHaveTextContent('Detected password redaction')
    expect(screen.getByTestId('log-tail')).toHaveTextContent('Detected email redaction')
    expect(screen.getByTestId('log-tail')).not.toHaveTextContent('[REDACTED: password]')

    await userEvent.click(screen.getByTestId('technical-details').querySelector('summary')!)
    expect(screen.getByTestId('technical-details')).toHaveTextContent('[REDACTED: password]')
    expect(screen.getByTestId('technical-details')).toHaveTextContent('[REDACTED: email]')
  })

  it('shows the standby copy before any incident', () => {
    render(<LogSanitizerView logs={[]} thoughts={[]} scenarioId={null} />)

    expect(screen.getByText(/Awaiting incident\. Redaction types will appear here\./)).toBeInTheDocument()
    expect(screen.getByTestId('log-sanitizer')).toHaveTextContent('0 Sensitive Tokens Masked')
  })

  it('keeps the blocked tool call visible as a guardrail signature', async () => {
    render(<LogSanitizerView logs={[]} thoughts={[BLOCKED_THOUGHT]} scenarioId={ScenarioId.PROMPT_INJECTION} />)

    const blocked = screen.getByTestId('blocked-tool-call')
    expect(blocked).toHaveTextContent(/Blocked by schema firewall/i)
    expect(blocked).toHaveTextContent('delete_all_customer_records')
    expect(blocked.querySelector('code')?.className).toContain('line-through')
  })

  it('reads the block off the guardrail verdict, not off the scenario', () => {
    // A thought carrying a tool call that PASSED is a plan step, not an interception. Keying the
    // strike-through on the scenario id instead would paint a legitimate Scenario 4 containment
    // call as refused.
    const passing = thought({
      id: 'thought-pass',
      step: 4,
      tool_call: { name: 'revoke_session', args: {}, is_canonical: true },
    })

    render(<LogSanitizerView logs={[]} thoughts={[passing]} scenarioId={ScenarioId.PROMPT_INJECTION} />)

    expect(screen.queryByTestId('blocked-tool-call')).not.toBeInTheDocument()
  })

  it('does not announce the log stream to screen readers', () => {
    // §11's explicit exception: a screen reader reciting ten log lines a second is unusable.
    render(<LogSanitizerView logs={[REDACTED_LOG]} thoughts={[]} scenarioId={ScenarioId.DB_POOL_EXHAUSTION} />)

    expect(screen.getByTestId('log-tail').closest('[aria-live]')).toBeNull()
  })
})
