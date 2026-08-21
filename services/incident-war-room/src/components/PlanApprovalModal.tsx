/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        PlanApprovalModal.tsx
 * Purpose:          The human approval decision — the proposed plan, the action being authorized,
 *                   and the two buttons that resolve it.
 * Interacts With:   App.tsx, lib/narration.ts, incident-agent-api (POST /api/incidents/authorize)
 *
 * Curriculum Project: Project 5 — Autonomous Agent & Human-in-the-Loop
 * Skills:           Modal Accessibility, Focus Management, HITL Gate
 * Tools:            React 18, Tailwind CSS, Lucide Icons
 *
 * The gate used to be two buttons in a console footer. Moving it into a modal makes the decision a
 * deliberate stop rather than something a visitor can click past while scrolling: the plan is on
 * screen, alone, at the moment they approve it — which is the claim Project 5 exists to make.
 *
 * **`Approve` is the button; `APPROVAL_PROMPT` is the heading.** The generated per-scenario prompt —
 * `Authorize DB Pool Drain & Recycle` and its three siblings — is a canonical contract identifier,
 * so it stays on screen, naming the action being authorized. What it no longer does is double as the
 * button label. That keeps the scenario-specific language visible while the control itself says
 * plainly what pressing it does.
 *
 * **Escape and the backdrop dismiss; neither approves.** Dismissing returns the visitor to a run
 * still sitting at `AWAITING_APPROVAL`, exactly as before they opened it. Nothing here can
 * auto-advance the gate — no timeout, no default action, no focus-triggered submit — because the
 * hard stop is the whole point.
 *
 * **`Technical details` is the evidence for the decision.** It holds what the model literally emitted
 * — each step's own text, and the tool call with its arguments — and it is the only place in the UI
 * that does. It belongs here rather than in the console it opens from: this is the moment a visitor
 * is deciding whether to authorize, so it is the moment the raw plan is worth reading. It stays
 * collapsed, because the narrated summary above it is what most readers need.
 *
 * Blocked calls are struck through and labelled. That is the one place attacker-controlled text is
 * rendered at all, so it is rendered as *refused evidence*, never as a step the plan proposes.
 */

import { useEffect, useRef } from 'react'
import { Check, ShieldAlert, X } from 'lucide-react'

import type { ThoughtEntry } from '../hooks/useIncidentStream'
import { cn } from '../lib/cn'
import { narratePlan } from '../lib/narration'
import { APPROVAL_PROMPT, GuardrailVerdict, type ScenarioId } from '../types/contracts.gen'
import { TechnicalDetails } from './ui/ConsoleFrame'

interface PlanApprovalModalProps {
  scenarioId: ScenarioId
  thoughts: ThoughtEntry[]
  busy: boolean
  error: string | null
  onDecision: (approved: boolean) => void
  onClose: () => void
}

export function PlanApprovalModal({
  scenarioId,
  thoughts,
  busy,
  error,
  onDecision,
  onClose,
}: PlanApprovalModalProps) {
  const closeRef = useRef<HTMLButtonElement>(null)
  const plan = narratePlan(thoughts)

  useEffect(() => {
    // Focus lands on dismiss, never on approve. A modal that opens with its irreversible action
    // focused is one stray Enter away from authorizing a remediation nobody read.
    closeRef.current?.focus()

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  return (
    <div
      data-testid="plan-modal-backdrop"
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-end justify-center bg-ink/40 p-4 sm:items-center"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="plan-modal-title"
        data-testid="plan-modal"
        // Stops a click inside the card from reaching the backdrop's dismiss handler.
        onClick={(event) => event.stopPropagation()}
        className="flex max-h-full w-full max-w-2xl animate-panel-enter flex-col rounded-lg border border-ink bg-page"
      >
        <header className="flex shrink-0 items-start justify-between gap-3 border-b border-ink bg-secondary px-5 py-4">
          <div>
            <h2 id="plan-modal-title" className="font-sans text-panel-title uppercase text-ink">
              AI action plan
            </h2>
            <p className="mt-1 font-sans text-copy-secondary text-ink">
              Review the proposed plan. Nothing runs until you approve it.
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close the action plan"
            data-testid="plan-modal-close"
            className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-full border border-ink text-ink transition-colors duration-status hover:bg-secondary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-guard"
          >
            <X aria-hidden className="h-4 w-4" />
          </button>
        </header>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
          <ol data-testid="plan-modal-steps" className="space-y-2">
            {plan.length === 0 ? (
              <li className="font-sans text-body text-ink">
                The plan is still being drafted. Nothing has run.
              </li>
            ) : (
              plan.map(({ id, thought, explanation }) => (
                <li
                  key={id}
                  data-testid="plan-modal-step"
                  data-step={thought.step}
                  data-guardrail={thought.guardrail}
                  className="font-sans text-body text-ink"
                >
                  {explanation}
                </li>
              ))
            )}
          </ol>

          {plan.length > 0 && (
            <TechnicalDetails>
              <ol className="space-y-2">
                {plan.map(({ id, thought }) => (
                  <li key={`detail-${id}`} data-testid="plan-modal-step-detail" data-step={thought.step}>
                    <span className="block">
                      Step {thought.step} · {thought.phase}: {thought.text}
                    </span>
                    {thought.tool_call && (
                      <code
                        className={cn(
                          'mt-1 block break-all font-mono text-log text-ink',
                          thought.guardrail === GuardrailVerdict.BLOCKED && 'line-through',
                        )}
                      >
                        {thought.tool_call.name}({JSON.stringify(thought.tool_call.args)})
                      </code>
                    )}
                    {thought.guardrail === GuardrailVerdict.BLOCKED && (
                      <span className="font-mono text-badge uppercase text-ink">
                        Blocked by schema firewall
                      </span>
                    )}
                  </li>
                ))}
              </ol>
            </TechnicalDetails>
          )}

          {/* The contractual prompt, naming the action rather than labelling the button. */}
          <p
            data-testid="plan-modal-prompt"
            className="flex items-start gap-2 rounded-sm border border-pending bg-pending/10 p-3 font-mono text-badge uppercase text-ink"
          >
            <ShieldAlert aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-pending" />
            {APPROVAL_PROMPT[scenarioId]}
          </p>

          {error && (
            <p role="alert" data-testid="hitl-error" className="font-mono text-copy-secondary text-alarm">
              {error}
            </p>
          )}
        </div>

        <footer className="flex shrink-0 flex-col gap-2 border-t border-ink bg-secondary px-5 py-4 sm:flex-row">
          <button
            type="button"
            onClick={() => onDecision(true)}
            disabled={busy}
            data-testid="authorize-remediation"
            className="inline-flex min-h-[44px] flex-1 items-center justify-center gap-2 rounded-sm border-2 border-strong bg-accent px-4 font-mono text-badge uppercase text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-guard disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Check aria-hidden className="h-4 w-4 shrink-0" />
            Approve
          </button>
          <button
            type="button"
            onClick={() => onDecision(false)}
            disabled={busy}
            data-testid="reject-remediation"
            className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-sm border-2 border-strong bg-page px-4 font-mono text-badge uppercase text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-guard disabled:cursor-not-allowed disabled:opacity-40"
          >
            <X aria-hidden className="h-4 w-4 shrink-0" />
            Reject
          </button>
        </footer>
      </div>
    </div>
  )
}
