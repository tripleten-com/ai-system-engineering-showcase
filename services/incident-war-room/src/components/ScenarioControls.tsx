/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        ScenarioControls.tsx
 * Purpose:          The four one-click incident triggers and the Master Reset.
 * Interacts With:   incident-agent-api (POST /api/incidents/trigger, /reset)
 *
 * Curriculum Project: Project 1 — Diagnostics & Telemetry
 * Skills:           State-Driven UI, Accessibility, Error Surfacing
 * Tools:            React 18, Tailwind CSS, Lucide Icons
 *
 * These are the entry point, so they stay reachable at every width. A visitor who cannot find the
 * trigger has no demo at all — which is why the launcher is now an enclosed panel and the four
 * cards are unmistakably buttons rather than tinted information cards.
 *
 * The affordance is a hard ink offset shadow that lifts on hover and collapses on press. A blurred
 * drop shadow is nearly invisible on a white editorial page, and the previous status-tinted cards
 * read as *legend entries* — several reviewers looked at them and did not try clicking. The press
 * feedback is the part that cannot be skipped: the API takes a moment to answer, and a button that
 * does not visibly depress reads as a button that did not register.
 *
 * All four cards share one fill: the TripleTen page-background wash, peach into brand blue. Colour
 * is a run-state system in this UI (spa-design-guidelines §1), so tinting one card differently would
 * claim a *state* before the run exists. What distinguishes the scenarios is the glyph naming the
 * failing subsystem, which survives both greyscale and a colour-blind viewer.
 */

import { Database, Inbox, RotateCcw, ShieldAlert, Zap, type LucideIcon } from 'lucide-react'

import { cn } from '../lib/cn'
import { ScenarioId, type IncidentState } from '../types/contracts.gen'

interface ScenarioButton {
  id: ScenarioId
  label: string
  description: string
  /**
   * The glyph that names the failing subsystem.
   *
   * One per scenario rather than one per category: with three of the four sharing a lightning bolt,
   * the icon column carried no information and a reader scanning it learned nothing the label had
   * not already said. It is also the *only* thing distinguishing the scenarios visually, because
   * colour here is reserved for run state and these buttons exist before a run does.
   */
  Icon: LucideIcon
}

const SCENARIOS: ScenarioButton[] = [
  {
    id: ScenarioId.DB_POOL_EXHAUSTION,
    label: 'Database overload',
    description: 'Connection capacity runs out and requests begin failing.',
    Icon: Database,
  },
  {
    id: ScenarioId.CACHE_THUNDERING_HERD,
    label: 'Cache traffic spike',
    description: 'Many requests miss the cache at the same time.',
    Icon: Zap,
  },
  {
    id: ScenarioId.WORKER_DEADLOCK,
    label: 'Queue processing stops',
    description: 'A bad message blocks the background workers.',
    Icon: Inbox,
  },
  {
    id: ScenarioId.PROMPT_INJECTION,
    label: 'Prompt injection attempt',
    description: 'Malicious text asks the AI to perform an unsafe action.',
    Icon: ShieldAlert,
  },
]

/**
 * The shared button language: hard offset shadow, lift on hover, collapse on press.
 *
 * `disabled:` resets both the lift and the shadow so a refused control is visibly flat — an
 * unavailable trigger that still casts a raised shadow invites the click it is going to ignore.
 */
const PRESSABLE =
  'border-2 border-strong shadow-offset transition-all duration-status ' +
  'hover:-translate-y-0.5 hover:shadow-offset-lift ' +
  'active:translate-y-0 active:shadow-offset-press ' +
  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-guard ' +
  'disabled:cursor-not-allowed disabled:opacity-40 disabled:translate-y-0 disabled:shadow-offset ' +
  'disabled:hover:translate-y-0 disabled:hover:shadow-offset'

interface ScenarioControlsProps {
  state: IncidentState
  incidentId: string | null
  /**
   * True while a request is in flight *or* while the first snapshot is still loading. The cold-start
   * half matters: `state` defaults to `HEALTHY` before anything arrives, so without it a click in
   * the first second could fire at a stack that already has a run going and earn a 409.
   */
  busy: boolean
  error: string | null
  onTrigger: (scenario: ScenarioId) => void
  onReset: () => void
  /**
   * The plain-language explanation of the current state, rendered under this panel's title.
   *
   * It lives here rather than beside the hero badge because this is the panel a visitor is looking
   * at when they need it: the sentence tells them what to do before a run and what is happening
   * during one, and both are answers about *this* control. `App` still owns the copy — the panel
   * only decides where it sits.
   */
  explanation?: string | null
}

export function ScenarioControls({
  state,
  incidentId,
  busy,
  error,
  onTrigger,
  onReset,
  explanation = null,
}: ScenarioControlsProps) {
  // A second trigger while a run is in flight is refused with 409 by the API. Disabling rather
  // than letting it fail keeps the UI honest about what is available.
  const runInFlight = state !== 'HEALTHY'

  return (
    <section
      data-testid="scenario-panel"
      className="space-y-3 rounded-md border border-ink bg-secondary p-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-sans text-panel-title uppercase text-text-secondary">
          Simulate live incident
        </h2>
        <button
          type="button"
          onClick={onReset}
          disabled={busy || !incidentId}
          data-testid="master-reset"
          className={cn(
            'inline-flex min-h-[44px] items-center gap-2 rounded-sm bg-page px-4',
            'font-mono text-badge uppercase text-ink',
            PRESSABLE,
          )}
        >
          <RotateCcw aria-hidden className="h-3.5 w-3.5" />
          Master Reset
        </button>
      </div>

      {/* Polite, and always present: an `aria-live` element has to exist before its content changes
          to be announced at all. It is a sibling of the hero badge's own live region, never nested
          inside it — two nested polite regions announce the badge twice. */}
      <p
        data-testid="state-explanation"
        aria-live="polite"
        className="font-sans text-copy-secondary text-ink"
      >
        {explanation ?? ''}
      </p>

      {/* `auto-rows-fr` is what makes the four cards one height. Grid stretches items within a row
          by default but sizes each row to its own content, so a two-line description in row one made
          that row taller than row two — four buttons at two heights, which reads as a mistake. */}
      <div
        data-testid="scenario-launcher"
        className="grid grid-cols-1 gap-4 sm:auto-rows-fr sm:grid-cols-2"
      >
        {SCENARIOS.map(({ id, label, description, Icon }) => (
          <button
            key={id}
            type="button"
            onClick={() => onTrigger(id)}
            disabled={busy || runInFlight}
            data-testid={`trigger-${id}`}
            data-scenario={id}
            className={cn(
              'flex min-h-[44px] w-full items-start gap-2 rounded-md bg-scenario-trigger p-4 text-left text-ink',
              PRESSABLE,
            )}
          >
            <Icon aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-ink" />
            <span>
              <span className="block font-display text-body font-semibold">{label}</span>
              <span className="mt-1 block font-sans text-copy-secondary normal-case">{description}</span>
            </span>
          </button>
        ))}
      </div>

      {error && (
        <p role="alert" data-testid="control-error" className="font-mono text-copy-secondary text-alarm">
          {error}
        </p>
      )}
    </section>
  )
}
