/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        lib/narration.ts
 * Purpose::         The visitor-facing plain-language copy: what the current state means, what the
 *                   incident is costing, and what each reasoning step actually did.
 * Interacts With:   App.tsx, components/AgentReasoningView.tsx, hooks/useIncidentStream.ts
 *
 * Curriculum Project: Cross-cutting — Marketing Delivery
 * Skills:           Copy as Data, Exhaustive Mapping, Progressive Disclosure
 * Tools:            TypeScript
 *
 * Copy lives here rather than inline in JSX for one reason that matters and one that is merely
 * convenient. The one that matters: this text is the *deliverable* — a prospective student reads it
 * to understand what an incident-response agent does, so every scenario needs every phase covered,
 * and a missing combination should be visible as a gap in a table rather than as a silently empty
 * paragraph three components away. The convenient one: it is testable without rendering anything.
 *
 * The backend's own reasoning text is never replaced by this — it moves to `Technical details`. The
 * default view answers "what is happening"; the disclosure answers "what did the model literally
 * emit". Both are true, and only one of them is readable at a glance.
 */

import type { ThoughtEntry } from '../hooks/useIncidentStream'
import {
  AgentPhase,
  GuardrailVerdict,
  ScenarioId,
  ToolName,
  type IncidentState,
} from '../types/contracts.gen'

/**
 * The phases a run's explanation is written for.
 *
 * Deliberately coarser than `IncidentState`: `CRITICAL_OUTAGE` and `EXPLOIT_INTERCEPTED` are both
 * "the problem has been detected and the AI is looking at it", and writing separate copy for each
 * would mean writing the same sentence twice for three of the four scenarios.
 */
type RunPhase = 'detection' | 'approval' | 'executing' | 'recovering' | 'completed'

/** Shown before any scenario has been chosen, and again after a Master Reset. */
export const NO_SCENARIO_EXPLANATION = 'Choose a scenario to watch the incident-response workflow.'

/**
 * Per-scenario, per-phase explanation of the current state.
 *
 * Scenario 4 has no `recovering` entry, and that absence is the point rather than an omission:
 * nothing degraded, so there is nothing to recover from and the state machine never enters
 * `RECOVERING` for it.
 */
const PHASE_EXPLANATION: Record<ScenarioId, Partial<Record<RunPhase, string>>> = {
  [ScenarioId.DB_POOL_EXHAUSTION]: {
    detection: 'Database connection capacity is exhausted and requests are failing while the AI investigates.',
    approval:
      'The AI found the database recovery guide and is waiting for human approval to recycle idle connections.',
    executing: 'The approved worker is recycling idle database connections.',
    recovering: 'Database capacity is returning to normal and failed requests are falling.',
    completed: 'Database capacity is restored and requests are succeeding normally.',
  },
  [ScenarioId.CACHE_THUNDERING_HERD]: {
    detection: 'Too many requests are missing the cache, increasing response time while the AI investigates.',
    approval:
      'The AI found the cache recovery guide and is waiting for human approval to warm key data and remove stale entries.',
    executing: 'The approved worker is warming the cache and clearing stale entries.',
    recovering: 'Cache hit rate is rising and response time is returning to normal.',
    completed: 'The cache is warm again and requests are responding normally.',
  },
  [ScenarioId.WORKER_DEADLOCK]: {
    detection: 'A bad message has stopped workers, so background jobs are building up while the AI investigates.',
    approval:
      'The AI found the queue recovery guide and is waiting for human approval to quarantine the bad message and restart workers.',
    executing: 'The approved worker is quarantining the bad message and restarting queue workers.',
    recovering: 'Workers are processing jobs again and the queue is shrinking.',
    completed: 'The bad message is isolated and the work queue is processing normally.',
  },
  [ScenarioId.PROMPT_INJECTION]: {
    detection: 'Malicious text requested an unsafe action. The safety guardrail blocked it before anything ran.',
    approval:
      'The unsafe action remains blocked. The AI is waiting for human approval to revoke the session, block its source, and archive evidence.',
    executing: 'The approved worker is revoking the session, blocking the source, and archiving evidence.',
    completed:
      'The unsafe action never ran. The session was revoked, the source was blocked, and evidence was archived.',
  },
}

/** The two endings that are not a success, and are the same sentence for every scenario. */
const TERMINAL_EXPLANATION: Partial<Record<IncidentState, string>> = {
  REJECTED: 'The proposed action was not approved, so no recovery tool ran.',
  FAILED: 'The approved action failed. The worker stopped and recorded the error for review.',
}

/** Maps a run state onto the phase its copy is written for. */
function runPhase(state: IncidentState): RunPhase | null {
  switch (state) {
    case 'CRITICAL_OUTAGE':
    case 'EXPLOIT_INTERCEPTED':
      return 'detection'
    case 'AWAITING_APPROVAL':
      return 'approval'
    case 'EXECUTING':
      return 'executing'
    case 'RECOVERING':
      return 'recovering'
    case 'HEALTHY':
    case 'SECURITY_CONTAINED':
      return 'completed'
    default:
      return null
  }
}

/**
 * The plain-language explanation shown under the current-state badge.
 *
 * `scenarioId` is the run's scenario *including a finished run's* — the caller passes the last
 * scenario seen, because the server stops reporting one the moment a run completes and "Database
 * capacity is restored" is exactly the sentence a visitor needs at that moment.
 *
 * Returns `null` when there is nothing honest to say, which the caller renders as an empty live
 * region rather than removing the region — an `aria-live` element has to exist before its content
 * changes to be announced at all.
 */
export function currentStateExplanation(state: IncidentState, scenarioId: ScenarioId | null): string | null {
  const terminal = TERMINAL_EXPLANATION[state]
  if (terminal) return terminal

  if (scenarioId === null) return state === 'HEALTHY' ? NO_SCENARIO_EXPLANATION : null

  const phase = runPhase(state)
  return phase === null ? null : (PHASE_EXPLANATION[scenarioId][phase] ?? null)
}

/** The cost of the incident while it is live, in the terms a non-engineer measures it in. */
export const SCENARIO_IMPACT: Record<ScenarioId, string> = {
  [ScenarioId.DB_POOL_EXHAUSTION]: 'Customer impact — database capacity is exhausted and requests are failing.',
  [ScenarioId.CACHE_THUNDERING_HERD]: 'Performance impact — cache misses are increasing response time.',
  [ScenarioId.WORKER_DEADLOCK]: 'Processing impact — background jobs are waiting behind a blocked worker.',
  // Scenario 4's strip is a claim rather than a cost, and it is the one the whole scenario exists
  // to make: the guardrail held, so there is nothing to report.
  [ScenarioId.PROMPT_INJECTION]: 'No customer impact — 0 unauthorized actions.',
}

/** What a successful run achieved. Never shown for `REJECTED` or `FAILED`. */
export const SCENARIO_OUTCOME: Record<ScenarioId, string> = {
  [ScenarioId.DB_POOL_EXHAUSTION]: 'Recovery complete — database capacity restored and requests are succeeding.',
  [ScenarioId.CACHE_THUNDERING_HERD]: 'Recovery complete — cache hit rate restored and response time normalized.',
  [ScenarioId.WORKER_DEADLOCK]: 'Recovery complete — bad message quarantined and queue processing restored.',
  [ScenarioId.PROMPT_INJECTION]:
    'Threat contained — session revoked, source blocked, and forensic evidence archived.',
}

/**
 * Which tool the worker was authorized to run, in words.
 *
 * One entry per tool rather than one per scenario: the same tool appears in more than one plan, and
 * a viewer watching two runs should read the same sentence for the same action. `check_health` and
 * `read_runbook` are absent because they are read-only investigation steps the planner never
 * proposes for approval.
 */
const TOOL_SELECTION: Partial<Record<string, string>> = {
  [ToolName.FLUSH_CONNECTION_POOL]: 'Recycling idle database connections was selected as the fix.',
  [ToolName.WARM_CACHE]: 'Warming the cache with key data was selected as the fix.',
  [ToolName.ISOLATE_POISON_MESSAGE]: 'Quarantining the blocking message was selected as the fix.',
  [ToolName.REBOOT_WORKERS]: 'Restarting the queue workers was selected as the fix.',
  [ToolName.REVOKE_SESSION]: 'Revoking the compromised session was selected as the containment step.',
  [ToolName.BLOCK_IP]: 'Blocking the request source was selected as the containment step.',
  [ToolName.ARCHIVE_FORENSICS]: 'Archiving forensic evidence was selected as the containment step.',
}

/** The two `ANALYZING` steps every run emits, in order. They do different things. */
const ANALYSIS_EXPLANATION = [
  'The AI compared the alert with live service signals to identify the failure.',
  'Sensitive values were removed before the incident evidence reached the AI.',
]

/** Blocked calls are numbered, because Scenario 4 blocks two and "an unsafe action" twice reads as one. */
const BLOCKED_EXPLANATION = [
  'The first unsafe action was blocked before it could run.',
  'The second unsafe action was blocked before it could run.',
]

const RETRIEVAL_EXPLANATION = 'The best matching recovery guide was found and checked.'
const PLANNING_EXPLANATION = 'The recovery guide was converted into a small, reversible plan.'
const APPROVAL_EXPLANATION = 'The plan is ready. No approved action will run until a human approves it.'

/** A reasoning step paired with the sentence the default console body shows for it. */
export interface PlanNarration {
  id: string
  thought: ThoughtEntry
  explanation: string
}

/**
 * Rewrites the agent's reasoning chain as distinct, plain explanations.
 *
 * Ordinal-aware, and that is the whole reason this is a fold over the array rather than a lookup
 * per entry: the two `ANALYZING` steps and Scenario 4's two blocked calls are indistinguishable
 * from their own fields, so what separates them is how many of their kind came before. Mapping each
 * entry independently is what produced the repetitive "The AI is reviewing incident evidence"
 * three times in a row that this replaces.
 *
 * The input array stays chronological and untouched; ordering for display is the console's job.
 */
export function narratePlan(thoughts: readonly ThoughtEntry[]): PlanNarration[] {
  let analyses = 0
  let blocks = 0

  return thoughts.map((thought) => {
    let explanation: string

    if (thought.guardrail === GuardrailVerdict.BLOCKED) {
      explanation = BLOCKED_EXPLANATION[blocks] ?? 'A further unsafe action was blocked before it could run.'
      blocks += 1
    } else if (thought.phase === AgentPhase.ANALYZING) {
      explanation = ANALYSIS_EXPLANATION[analyses] ?? 'The AI reviewed further incident evidence.'
      analyses += 1
    } else if (thought.phase === AgentPhase.RETRIEVING) {
      explanation = RETRIEVAL_EXPLANATION
    } else if (thought.phase === AgentPhase.PLANNING) {
      explanation = PLANNING_EXPLANATION
    } else if (thought.phase === AgentPhase.AWAITING_APPROVAL) {
      explanation = APPROVAL_EXPLANATION
    } else if (thought.tool_call) {
      explanation =
        TOOL_SELECTION[thought.tool_call.name] ?? 'A recovery step was selected from the recovery guide.'
    } else {
      explanation = 'The AI reviewed further incident evidence.'
    }

    return { id: thought.id, thought, explanation }
  })
}
