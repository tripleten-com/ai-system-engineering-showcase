/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        TerminalStateBanner.tsx
 * Purpose:          The REJECTED / FAILED / SECURITY_CONTAINED banner, carrying the state name,
 *                   its explanation, and the Master Reset that ends the hold.
 * Interacts With:   services/api.ts (POST /api/incidents/reset)
 *
 * Curriculum Project: Project 5 — Autonomous Agent & Human-in-the-Loop
 * Skills:           Terminal State Handling, Accessibility, Status Colour Semantics
 * Tools:            React 18, Tailwind CSS, Lucide Icons
 *
 * All three of these states hold their metric values until someone presses reset — nothing decays,
 * nothing animates anywhere. That is why the banner has to be explicit about what happened: a
 * frozen crimson chart with no explanation reads as a UI that stopped updating.
 *
 * `SECURITY_CONTAINED` is cyan rather than red, and that is the point of Scenario 4. A guardrail
 * that held is a success. The colour comes from `STATE_TONE`, which is the only place that mapping
 * is written down.
 */

import { RotateCcw } from 'lucide-react'

import { cn } from '../lib/cn'
import { STATE_LABEL, STATE_TONE, type StatusTone } from '../theme/tokens'
import type { IncidentState } from '../types/contracts.gen'

/** The three states that end a run without recovery. */
export const TERMINAL_STATES = ['REJECTED', 'FAILED', 'SECURITY_CONTAINED'] as const

export type TerminalState = (typeof TERMINAL_STATES)[number]

export function isTerminalState(state: IncidentState): state is TerminalState {
  return (TERMINAL_STATES as readonly string[]).includes(state)
}

/**
 * The banner headline per state, verbatim from `ui-wireframe-and-ux.md` §3.
 *
 * `FAILED` appends the worker's error string, which is the only one of the three that carries
 * information the UI does not already know.
 */
const HEADLINE: Record<TerminalState, string> = {
  REJECTED: 'REMEDIATION REJECTED — INTERVENTION SKIPPED',
  FAILED: 'REMEDIATION FAILED',
  SECURITY_CONTAINED: 'SECURITY_CONTAINED — SESSION REVOKED, IP BLOCKED, FORENSICS ARCHIVED',
}

const EXPLANATION: Record<TerminalState, string> = {
  REJECTED:
    'No tool ran. The graph was discarded at the approval gate and the chaos jitter continues until reset.',
  FAILED:
    'The worker exhausted its retry budget and reported failure. Metrics hold at their current values.',
  SECURITY_CONTAINED:
    'The injected tool call was never executed. Containment ran only after authorization, and no customer request was affected.',
}

const TONE_SURFACE: Record<StatusTone, string> = {
  healthy: 'border-healthy/40 bg-healthy/10 text-ink',
  alarm: 'border-alarm/40 bg-alarm/10 text-ink',
  pending: 'border-pending/40 bg-pending/10 text-ink',
  active: 'border-active/40 bg-active/10 text-ink',
  guard: 'border-guard/40 bg-guard/10 text-ink',
}

interface TerminalStateBannerProps {
  state: TerminalState
  /** The worker's error string. Rendered on `FAILED` only, where it is the whole diagnosis. */
  failureReason: string | null
  busy: boolean
  onReset: () => void
}

export function TerminalStateBanner({ state, failureReason, busy, onReset }: TerminalStateBannerProps) {
  const tone = STATE_TONE[state]

  return (
    // Announced, unlike the log stream: a terminal state is exactly the kind of infrequent,
    // consequential change `aria-live="polite"` exists for.
    <section
      aria-live="polite"
      data-testid="terminal-state-banner"
      data-state={state}
      className={cn(
        'flex flex-col gap-3 rounded-lg border p-4 lg:flex-row lg:items-center lg:justify-between',
        'animate-panel-enter transition-colors duration-banner-enter',
        TONE_SURFACE[tone],
      )}
    >
      <div className="space-y-1">
        <p className="font-mono text-badge uppercase">{HEADLINE[state]}</p>
        <p className="font-sans text-body text-ink">{EXPLANATION[state]}</p>
        {state === 'FAILED' && failureReason && (
          <p data-testid="failure-reason" className="font-mono text-log text-ink">
            {failureReason}
          </p>
        )}
        {/* The state name in text as well as in colour — the §1 never-colour-alone rule. */}
        <p className="font-mono text-copy-secondary text-ink">State: {STATE_LABEL[state]}</p>
      </div>

      <button
        type="button"
        onClick={onReset}
        disabled={busy}
        data-testid="banner-master-reset"
        className={cn(
          'inline-flex min-h-[44px] shrink-0 items-center justify-center gap-2 rounded-full',
          'border border-current bg-secondary px-4 font-mono text-badge uppercase text-ink',
          'transition-colors duration-status hover:bg-page',
          'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-guard',
          'disabled:cursor-not-allowed disabled:opacity-40',
        )}
      >
        <RotateCcw aria-hidden className="h-4 w-4" />
        Master Reset
      </button>
    </section>
  )
}
