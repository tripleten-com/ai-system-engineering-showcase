/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        AgentReasoningView.tsx
 * Purpose:          Presents the AI plan and the structural human approval checkpoint.
 * Interacts With:   hooks/useIncidentStream.ts, lib/narration.ts, components/MobileHitlBar.tsx
 *
 * Curriculum Project: Project 5 — Autonomous Agent & Human-in-the-Loop
 * Skills:           Progressive Disclosure, HITL Gate, Accessibility
 * Tools:            React 18, Tailwind CSS, Lucide Icons
 *
 * The body says what each step *did*, one distinct sentence per phase, because the previous copy
 * repeated one of three generic sentences — "The AI is reviewing incident evidence and the recovery
 * guide" appeared three times in a row on every run — which taught a reader to stop reading it.
 * `lib/narration.ts` owns that copy, including the ordinal logic that separates the two analysis
 * steps and Scenario 4's two blocked calls.
 *
 * There is no `Technical details` disclosure here any more, so the model's own reasoning text and
 * raw tool arguments are not rendered anywhere in this console. That also means the one place this
 * UI could have shown attacker-controlled text as product copy is now closed by construction: a
 * blocked call is described by ordinal, never quoted. `LogSanitizerView` still shows the blocked
 * call signature, which is where the guardrail evidence lives.
 */

import { ClipboardList } from 'lucide-react'

import type { ThoughtEntry } from '../hooks/useIncidentStream'
import { cn } from '../lib/cn'
import { narratePlan, type PlanNarration } from '../lib/narration'
import type { IncidentState, ScenarioId } from '../types/contracts.gen'
import { ConsoleFrame } from './ui/ConsoleFrame'

interface AgentReasoningViewProps {
  state: IncidentState
  scenarioId: ScenarioId | null
  thoughts: ThoughtEntry[]
  busy: boolean
  error: string | null
  /** Opens the approval modal. The decision itself is taken there, never here. */
  onShowPlan: () => void
  hitlInline?: boolean
  incidentId?: string | null
  resetKey?: string | number
}

/** What the console shows before a run exists. */
const DEFAULT_STEPS = [
  { id: 'investigate', text: 'The AI investigates the incident.' },
  { id: 'guide', text: 'It uses the recovery guide to propose a safe action.' },
  { id: 'approval', text: 'It stops and waits for human approval before anything changes.' },
]

type PlanConsoleEntry =
  | { kind: 'standby'; id: string; text: string }
  | { kind: 'step'; id: string; narration: PlanNarration }

function ReasoningStep({ narration }: { narration: PlanNarration }) {
  const { thought, explanation } = narration
  return (
    <span
      data-testid="reasoning-step"
      data-step={thought.step}
      data-guardrail={thought.guardrail}
      className="font-console text-console-line text-console-output"
    >
      {explanation}
    </span>
  )
}

/**
 * The control that opens the approval modal.
 *
 * Shared by the inline footer and the sticky mobile bar so there is one implementation and one
 * label. It is not the approval — pressing it commits to nothing, which is why it can sit in a
 * console footer where the two-button pair could not.
 */
export function ShowPlanButton({
  busy,
  onShowPlan,
  className,
}: {
  busy: boolean
  onShowPlan: () => void
  className?: string
}) {
  return (
    <button
      type="button"
      onClick={onShowPlan}
      disabled={busy}
      data-testid="show-plan"
      className={cn(
        'inline-flex min-h-[44px] w-full items-center justify-center gap-2 rounded-sm border-2 border-strong bg-accent px-4',
        'font-mono text-badge uppercase text-ink',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-guard',
        'disabled:cursor-not-allowed disabled:opacity-40',
        className,
      )}
    >
      <ClipboardList aria-hidden className="h-4 w-4 shrink-0" />
      Show AI action plan
    </button>
  )
}

export function AgentReasoningView({
  state,
  scenarioId,
  thoughts,
  busy,
  error,
  onShowPlan,
  hitlInline = true,
  incidentId,
  resetKey,
}: AgentReasoningViewProps) {
  const awaitingApproval = state === 'AWAITING_APPROVAL' && scenarioId !== null
  const entries: PlanConsoleEntry[] =
    thoughts.length === 0
      ? DEFAULT_STEPS.map((step) => ({ kind: 'standby' as const, id: step.id, text: step.text }))
      : narratePlan(thoughts).map((narration) => ({ kind: 'step' as const, id: narration.id, narration }))

  return (
    <section data-testid="agent-reasoning">
      <ConsoleFrame
        title="AI response plan"
        description="Agent investigates, proposes safe action, stops for approval."
        entries={entries}
        entryKey={(entry) => entry.id}
        incidentId={incidentId}
        resetKey={resetKey}
        scrollTestId="reasoning-chain"
        renderEntry={(entry) =>
          entry.kind === 'standby' ? (
            <span className="font-console text-console-line text-console-output">{entry.text}</span>
          ) : (
            <ReasoningStep narration={entry.narration} />
          )
        }
        // Always rendered, so this console's footer cannot vanish and break the grid's alignment. It
        // holds the modal trigger and nothing else — the raw plan moved into the modal's own
        // `Technical details`, which is where a visitor deciding whether to approve wants it.
        footer={
          <div className="space-y-2">
            {awaitingApproval && hitlInline ? (
              <div aria-live="polite" data-testid="hitl-block" className="space-y-2">
                <p className="font-sans text-copy-secondary text-ink">
                  Human approval is required before the proposed action can run.
                </p>
                <ShowPlanButton busy={busy} onShowPlan={onShowPlan} />
              </div>
            ) : (
              <p className="font-sans text-copy-secondary text-ink">
                {awaitingApproval
                  ? 'Human approval is required before the proposed action can run.'
                  : 'No approved action will run until a human approves it.'}
              </p>
            )}
            {/* An authorize failure stays visible after the gate has closed behind it — a refused
                decision that leaves no trace reads as a click that did nothing. */}
            {error && !awaitingApproval && (
              <p role="alert" data-testid="hitl-error" className="font-mono text-copy-secondary text-alarm">
                {error}
              </p>
            )}
          </div>
        }
      />
    </section>
  )
}
