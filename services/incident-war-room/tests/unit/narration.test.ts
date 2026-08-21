/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        narration.test.ts
 * Purpose:          Holds the visitor-facing copy to its contract: every scenario covered at every
 *                   phase, human-centred approval wording, and no repeated AI narration.
 * Interacts With:   src/lib/narration.ts
 *
 * Curriculum Project: Cross-cutting — Marketing Delivery
 * Skills:           Copy as Data, Exhaustive Mapping
 * Tools:            Vitest
 *
 * This copy is the deliverable, so it is tested like one. The exhaustiveness tests matter most: a
 * scenario/phase combination with no sentence renders as an empty paragraph, which reads as a
 * rendering bug rather than as missing copy.
 */

import { describe, expect, it } from 'vitest'

import {
  NO_SCENARIO_EXPLANATION,
  SCENARIO_IMPACT,
  SCENARIO_OUTCOME,
  currentStateExplanation,
  narratePlan,
} from '../../src/lib/narration'
import {
  AgentPhase,
  GuardrailVerdict,
  IncidentState,
  ScenarioId,
  ToolName,
} from '../../src/types/contracts.gen'
import { thought } from '../fixtures'

/** The states a scenario walks through and must have copy for. */
const OUTAGE_STATES: IncidentState[] = ['CRITICAL_OUTAGE', 'AWAITING_APPROVAL', 'EXECUTING', 'RECOVERING', 'HEALTHY']
const SECURITY_STATES: IncidentState[] = ['EXPLOIT_INTERCEPTED', 'AWAITING_APPROVAL', 'EXECUTING', 'SECURITY_CONTAINED']

const OUTAGE_SCENARIOS = [
  ScenarioId.DB_POOL_EXHAUSTION,
  ScenarioId.CACHE_THUNDERING_HERD,
  ScenarioId.WORKER_DEADLOCK,
] as const

describe('currentStateExplanation — coverage', () => {
  it('explains the pre-scenario page', () => {
    expect(currentStateExplanation('HEALTHY', null)).toBe(NO_SCENARIO_EXPLANATION)
    expect(NO_SCENARIO_EXPLANATION).toBe('Choose a scenario to watch the incident-response workflow.')
  })

  it.each(OUTAGE_SCENARIOS.flatMap((scenario) => OUTAGE_STATES.map((state) => [scenario, state] as const)))(
    'covers %s at %s',
    (scenario, state) => {
      expect(currentStateExplanation(state, scenario)).toBeTruthy()
    },
  )

  it.each(SECURITY_STATES)('covers the security scenario at %s', (state) => {
    expect(currentStateExplanation(state, ScenarioId.PROMPT_INJECTION)).toBeTruthy()
  })

  it('has no recovery sentence for the security scenario, because nothing degraded', () => {
    // Not an omission. Scenario 4 never enters RECOVERING — there is no decay curve because there
    // was no spike — and inventing copy for it would describe a recovery that cannot happen.
    expect(currentStateExplanation('RECOVERING', ScenarioId.PROMPT_INJECTION)).toBeNull()
  })

  it('gives every explanation a distinct sentence per scenario', () => {
    // Four scenarios explained with the same words is four scenarios a visitor cannot tell apart.
    for (const state of OUTAGE_STATES) {
      const sentences = OUTAGE_SCENARIOS.map((scenario) => currentStateExplanation(state, scenario))
      expect(new Set(sentences).size, `${state} explanations`).toBe(sentences.length)
    }
  })
})

describe('currentStateExplanation — exact copy', () => {
  it.each([
    ['CRITICAL_OUTAGE', 'Database connection capacity is exhausted and requests are failing while the AI investigates.'],
    ['AWAITING_APPROVAL', 'The AI found the database recovery guide and is waiting for human approval to recycle idle connections.'],
    ['EXECUTING', 'The approved worker is recycling idle database connections.'],
    ['RECOVERING', 'Database capacity is returning to normal and failed requests are falling.'],
    ['HEALTHY', 'Database capacity is restored and requests are succeeding normally.'],
  ] as Array<[IncidentState, string]>)('database overload at %s', (state, copy) => {
    expect(currentStateExplanation(state, ScenarioId.DB_POOL_EXHAUSTION)).toBe(copy)
  })

  it.each([
    ['CRITICAL_OUTAGE', 'Too many requests are missing the cache, increasing response time while the AI investigates.'],
    ['AWAITING_APPROVAL', 'The AI found the cache recovery guide and is waiting for human approval to warm key data and remove stale entries.'],
    ['EXECUTING', 'The approved worker is warming the cache and clearing stale entries.'],
    ['RECOVERING', 'Cache hit rate is rising and response time is returning to normal.'],
    ['HEALTHY', 'The cache is warm again and requests are responding normally.'],
  ] as Array<[IncidentState, string]>)('cache traffic spike at %s', (state, copy) => {
    expect(currentStateExplanation(state, ScenarioId.CACHE_THUNDERING_HERD)).toBe(copy)
  })

  it.each([
    ['CRITICAL_OUTAGE', 'A bad message has stopped workers, so background jobs are building up while the AI investigates.'],
    ['AWAITING_APPROVAL', 'The AI found the queue recovery guide and is waiting for human approval to quarantine the bad message and restart workers.'],
    ['EXECUTING', 'The approved worker is quarantining the bad message and restarting queue workers.'],
    ['RECOVERING', 'Workers are processing jobs again and the queue is shrinking.'],
    ['HEALTHY', 'The bad message is isolated and the work queue is processing normally.'],
  ] as Array<[IncidentState, string]>)('queue processing stops at %s', (state, copy) => {
    expect(currentStateExplanation(state, ScenarioId.WORKER_DEADLOCK)).toBe(copy)
  })

  it.each([
    ['EXPLOIT_INTERCEPTED', 'Malicious text requested an unsafe action. The safety guardrail blocked it before anything ran.'],
    ['AWAITING_APPROVAL', 'The unsafe action remains blocked. The AI is waiting for human approval to revoke the session, block its source, and archive evidence.'],
    ['EXECUTING', 'The approved worker is revoking the session, blocking the source, and archiving evidence.'],
    ['SECURITY_CONTAINED', 'The unsafe action never ran. The session was revoked, the source was blocked, and evidence was archived.'],
  ] as Array<[IncidentState, string]>)('prompt injection at %s', (state, copy) => {
    expect(currentStateExplanation(state, ScenarioId.PROMPT_INJECTION)).toBe(copy)
  })

  it.each([
    ['REJECTED', 'The proposed action was not approved, so no recovery tool ran.'],
    ['FAILED', 'The approved action failed. The worker stopped and recorded the error for review.'],
  ] as Array<[IncidentState, string]>)('%s reads the same for every scenario', (state, copy) => {
    for (const scenario of Object.values(ScenarioId)) {
      expect(currentStateExplanation(state, scenario), scenario).toBe(copy)
    }
    // Terminal copy does not depend on knowing the scenario at all.
    expect(currentStateExplanation(state, null)).toBe(copy)
  })
})

describe('approval wording is human-centred', () => {
  it('never addresses the reader as the approver', () => {
    // A visitor watching a recorded demo is not the approver, and the claim the gate makes is that
    // a person is required at all — not that this particular reader is.
    const everySentence = [
      NO_SCENARIO_EXPLANATION,
      ...Object.values(SCENARIO_IMPACT),
      ...Object.values(SCENARIO_OUTCOME),
      ...Object.values(ScenarioId).flatMap((scenario) =>
        Object.values(IncidentState).map((state) => currentStateExplanation(state, scenario) ?? ''),
      ),
    ]

    for (const sentence of everySentence) {
      expect(sentence, sentence).not.toMatch(/your approval/i)
    }
  })

  it('says "human approval" at the gate', () => {
    for (const scenario of Object.values(ScenarioId)) {
      expect(currentStateExplanation('AWAITING_APPROVAL', scenario), scenario).toMatch(/human approval/)
    }
  })
})

describe('impact and outcome strips', () => {
  it('names a cost for every scenario while the incident is live', () => {
    expect(SCENARIO_IMPACT[ScenarioId.DB_POOL_EXHAUSTION]).toBe(
      'Customer impact — database capacity is exhausted and requests are failing.',
    )
    expect(SCENARIO_IMPACT[ScenarioId.CACHE_THUNDERING_HERD]).toBe(
      'Performance impact — cache misses are increasing response time.',
    )
    expect(SCENARIO_IMPACT[ScenarioId.WORKER_DEADLOCK]).toBe(
      'Processing impact — background jobs are waiting behind a blocked worker.',
    )
  })

  it('preserves the guardrail claim verbatim for the security scenario', () => {
    // This string is the assertion Scenario 4 exists to make, and the E2E suite reads it off the
    // DOM in upper case. It is a contract, not copy.
    expect(SCENARIO_IMPACT[ScenarioId.PROMPT_INJECTION]).toBe('No customer impact — 0 unauthorized actions.')
  })

  it('names what every successful run achieved', () => {
    expect(SCENARIO_OUTCOME[ScenarioId.DB_POOL_EXHAUSTION]).toBe(
      'Recovery complete — database capacity restored and requests are succeeding.',
    )
    expect(SCENARIO_OUTCOME[ScenarioId.CACHE_THUNDERING_HERD]).toBe(
      'Recovery complete — cache hit rate restored and response time normalized.',
    )
    expect(SCENARIO_OUTCOME[ScenarioId.WORKER_DEADLOCK]).toBe(
      'Recovery complete — bad message quarantined and queue processing restored.',
    )
    expect(SCENARIO_OUTCOME[ScenarioId.PROMPT_INJECTION]).toBe(
      'Threat contained — session revoked, source blocked, and forensic evidence archived.',
    )
  })

  it('covers every scenario in both tables', () => {
    for (const scenario of Object.values(ScenarioId)) {
      expect(SCENARIO_IMPACT[scenario], `impact ${scenario}`).toBeTruthy()
      expect(SCENARIO_OUTCOME[scenario], `outcome ${scenario}`).toBeTruthy()
    }
  })
})

describe('narratePlan', () => {
  /** The chain a healthy outage run emits, in the order graph.py emits it. */
  const OUTAGE_CHAIN = [
    thought({ id: 't1', step: 1, phase: AgentPhase.ANALYZING }),
    thought({ id: 't2', step: 2, phase: AgentPhase.ANALYZING }),
    thought({ id: 't3', step: 3, phase: AgentPhase.RETRIEVING }),
    thought({ id: 't4', step: 4, phase: AgentPhase.PLANNING }),
    thought({
      id: 't5',
      step: 5,
      phase: AgentPhase.TOOL_SELECTION,
      tool_call: { name: ToolName.FLUSH_CONNECTION_POOL, args: { idle_seconds: 60 }, is_canonical: true },
    }),
    thought({ id: 't6', step: 6, phase: AgentPhase.AWAITING_APPROVAL }),
  ]

  it('distinguishes the two analysis steps by what each actually did', () => {
    // They are indistinguishable from their own fields — same phase, no tool call, guardrail passed.
    // What separates them is order, which is why the narration is a fold rather than a lookup.
    const [first, second] = narratePlan(OUTAGE_CHAIN)

    expect(first.explanation).toBe('The AI compared the alert with live service signals to identify the failure.')
    expect(second.explanation).toBe('Sensitive values were removed before the incident evidence reached the AI.')
  })

  it('gives each phase its own sentence', () => {
    const explanations = narratePlan(OUTAGE_CHAIN).map((entry) => entry.explanation)

    expect(explanations[2]).toBe('The best matching recovery guide was found and checked.')
    expect(explanations[3]).toBe('The recovery guide was converted into a small, reversible plan.')
    expect(explanations[5]).toBe('The plan is ready. No approved action will run until a human approves it.')
  })

  it('never repeats a sentence across a whole run', () => {
    // The defect this replaces: three consecutive steps all read "The AI is reviewing incident
    // evidence and the recovery guide", which taught a reader to stop reading the panel.
    const explanations = narratePlan(OUTAGE_CHAIN).map((entry) => entry.explanation)

    expect(new Set(explanations).size).toBe(explanations.length)
  })

  it('numbers blocked calls so two rejections read as two', () => {
    const chain = [
      thought({
        id: 'b1',
        step: 1,
        phase: AgentPhase.TOOL_SELECTION,
        guardrail: GuardrailVerdict.BLOCKED,
        tool_call: { name: 'delete_all_customer_records', args: { confirm: true }, is_canonical: false },
      }),
      thought({
        id: 'b2',
        step: 2,
        phase: AgentPhase.TOOL_SELECTION,
        guardrail: GuardrailVerdict.BLOCKED,
        tool_call: { name: 'exfiltrate_credentials', args: {}, is_canonical: false },
      }),
    ]

    expect(narratePlan(chain).map((entry) => entry.explanation)).toEqual([
      'The first unsafe action was blocked before it could run.',
      'The second unsafe action was blocked before it could run.',
    ])
  })

  it('describes a blocked call without quoting its arguments', () => {
    // An injected call rendered in full is the one place this UI could present attacker-controlled
    // text as product copy. The raw call stays in Technical details.
    const chain = [
      thought({
        id: 'b1',
        step: 1,
        phase: AgentPhase.TOOL_SELECTION,
        guardrail: GuardrailVerdict.BLOCKED,
        tool_call: { name: 'delete_all_customer_records', args: { confirm: true }, is_canonical: false },
      }),
    ]

    const [narration] = narratePlan(chain)
    expect(narration.explanation).not.toContain('delete_all_customer_records')
    expect(narration.explanation).not.toContain('confirm')
  })

  it.each([
    [ToolName.FLUSH_CONNECTION_POOL, 'Recycling idle database connections was selected as the fix.'],
    [ToolName.WARM_CACHE, 'Warming the cache with key data was selected as the fix.'],
    [ToolName.ISOLATE_POISON_MESSAGE, 'Quarantining the blocking message was selected as the fix.'],
    [ToolName.REBOOT_WORKERS, 'Restarting the queue workers was selected as the fix.'],
    [ToolName.REVOKE_SESSION, 'Revoking the compromised session was selected as the containment step.'],
    [ToolName.BLOCK_IP, 'Blocking the request source was selected as the containment step.'],
    [ToolName.ARCHIVE_FORENSICS, 'Archiving forensic evidence was selected as the containment step.'],
  ])('describes an approved %s selection distinctly', (tool, copy) => {
    const chain = [
      thought({
        id: 's1',
        step: 1,
        phase: AgentPhase.TOOL_SELECTION,
        tool_call: { name: tool, args: {}, is_canonical: true },
      }),
    ]

    expect(narratePlan(chain)[0].explanation).toBe(copy)
  })

  it('keeps the source order and pairs each sentence with its own thought', () => {
    const narrated = narratePlan(OUTAGE_CHAIN)

    expect(narrated.map((entry) => entry.id)).toEqual(['t1', 't2', 't3', 't4', 't5', 't6'])
    expect(narrated.map((entry) => entry.thought.step)).toEqual([1, 2, 3, 4, 5, 6])
  })

  it('leaves the input array untouched', () => {
    const source = [...OUTAGE_CHAIN]
    narratePlan(source)

    expect(source.map((entry) => entry.id)).toEqual(OUTAGE_CHAIN.map((entry) => entry.id))
  })
})
