/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        helpers.ts
 * Purpose:          Shared E2E vocabulary — the documented baseline bands, the SSE frame recorder,
 *                   and the trigger → gate → authorize → settle steps every spec walks.
 * Interacts With:   Incident War Room UI (:3000), incident-agent-api (:8000)
 *
 * Curriculum Project: Cross-cutting — E2E Verification
 * Skills:           Playwright Fixtures, Assertion Helpers, Deterministic Waiting
 * Tools:            Playwright, TypeScript
 *
 * Every wait here is on an *observable condition* rather than on a duration. The one exception is
 * the recovery decay, where the four seconds are the thing being measured — and even there the
 * assertion is on the value at the end of the window, not on the sleep.
 */

import { execFileSync } from 'node:child_process'

import { expect, type Page, type APIRequestContext, type Locator } from '@playwright/test'

import { BASELINE_BANDS } from '../../services/incident-war-room/src/types/contracts.gen'

/**
 * Documented baseline bands, read from the generated contract rather than retyped.
 *
 * They had been transcribed by hand here, which is exactly the copy-paste this project treats as a
 * defect: `BASELINE_BANDS` in `packages/contracts` is the source, the telemetry generator clamps to
 * it, and a band widened in one place and not the other would make these assertions agree with
 * nothing. The values happened to match; the coupling is what matters.
 */
export const BASELINE = {
  p99Ms: BASELINE_BANDS.latency_p99_ms,
  dbPoolPct: BASELINE_BANDS.db_pool_utilization_pct,
  redisMemoryPct: BASELINE_BANDS.redis_memory_utilization_pct,
} as const

/** The recovery window: `Baseline + (Peak − Baseline) · e^(−1.8t)` over four seconds. */
export const DECAY_MS = 4_000

/** Generous but bounded. The agent graph runs retrieval and planning before it pauses. */
export const GATE_TIMEOUT_MS = 25_000

/**
 * The bearer token the callback endpoint expects.
 *
 * Asked of the running container rather than guessed, and this is the third time this project has
 * had to learn why: no cheap source is right everywhere. `process.env` is empty for a developer who
 * never exported it, and reading `.env` loses whenever Compose interpolated a shell value over it —
 * which is exactly how CI's job-level secret wins. The API process itself is the only authority on
 * what it will accept, so ask it.
 *
 * An explicit `CALLBACK_SECRET` in the environment still wins, because that is how CI passes it
 * without paying for a `docker compose exec` per run.
 */
export const CALLBACK_SECRET: string = (() => {
  if (process.env.CALLBACK_SECRET) return process.env.CALLBACK_SECRET
  try {
    return execFileSync('docker', ['compose', 'exec', '-T', 'incident-agent-api', 'printenv', 'CALLBACK_SECRET'], {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim()
  } catch {
    throw new Error(
      'CALLBACK_SECRET is not set and could not be read from the incident-agent-api container; ' +
        'start the stack with `make up` or export CALLBACK_SECRET before running the E2E suite',
    )
  }
})()

/**
 * Stops or starts the `remediation-worker` container.
 *
 * Used by exactly one spec, to make the `FAILED` branch deterministic. With the worker running, its
 * success callback lands within a couple of seconds and races the test's failure callback — whichever
 * arrives first wins, so the branch was being skipped rather than verified. Taking the worker out
 * models the real condition anyway: `FAILED` is what the API reports when no worker ever succeeds.
 *
 * The queued job survives the outage and is consumed on restart. It is refused with 409 because the
 * run has moved on, and a 409 is a *permanent* refusal in the worker's delivery handling — so the
 * message is deleted rather than retried into the DLQ.
 */
export function setWorkerRunning(running: boolean): void {
  execFileSync('docker', ['compose', running ? 'start' : 'stop', 'remediation-worker'], {
    stdio: 'ignore',
  })
}

/**
 * Whether the worker container is up.
 *
 * Used to wait out a restart rather than assuming `docker compose start` is synchronous. Without it,
 * the spec that stops the worker can hand the next one — or a subsequent integration run against the
 * same stack — a container that is not yet consuming, which surfaced once as an unrelated
 * `test_customer_workload` failure that passed on its own.
 */
export function workerIsUp(): boolean {
  try {
    const state = execFileSync(
      'docker',
      ['compose', 'ps', '--format', '{{.State}}', 'remediation-worker'],
      { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] },
    ).trim()
    return state.startsWith('running')
  } catch {
    return false
  }
}

export type ScenarioId =
  | 'db_pool_exhaustion'
  | 'cache_thundering_herd'
  | 'worker_deadlock'
  | 'prompt_injection'

/**
 * Installs an `EventSource` wrapper that records every frame the browser receives.
 *
 * This is what makes the negative assertions real. Checking the rendered DOM proves the UI does not
 * *display* a secret; checking the frames proves the secret never crossed the network in the first
 * place, which is the actual claim inbound sanitization makes. Must run before navigation.
 */
export async function recordSseFrames(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const frames: string[] = []
    ;(window as unknown as { __sseFrames: string[] }).__sseFrames = frames

    const Native = window.EventSource
    class Recording extends Native {
      constructor(url: string | URL, config?: EventSourceInit) {
        super(url, config)
        this.addEventListener('message', (event) => {
          frames.push((event as MessageEvent<string>).data)
        })
      }
    }
    window.EventSource = Recording as unknown as typeof EventSource
  })
}

/** Returns everything the browser has received on the stream, concatenated. */
export async function sseText(page: Page): Promise<string> {
  const frames = await page.evaluate(() => (window as unknown as { __sseFrames: string[] }).__sseFrames ?? [])
  return frames.join('\n')
}

/**
 * Asserts a set of raw secrets appears neither in the DOM nor in any frame the browser received.
 *
 * The demo's redaction happens inbound, server-side, before the model seam — so the correct
 * assertion is absence at the boundary, not absence from the screen.
 */
export async function expectNoRawSecrets(page: Page, secrets: readonly string[]): Promise<void> {
  const dom = await page.content()
  const stream = await sseText(page)

  for (const secret of secrets) {
    expect(dom, `raw secret ${secret} must never reach the DOM`).not.toContain(secret)
    expect(stream, `raw secret ${secret} must never cross the stream`).not.toContain(secret)
  }
}

/** Reads a rendered metric tile as a number, stripping the thousands separators. */
export async function readMetric(page: Page, label: string): Promise<number> {
  const text = await page.getByTestId(`metric-${label}`).getByTestId('metric-value').innerText()
  return Number.parseFloat(text.replace(/,/g, ''))
}

/** The authoritative snapshot, for the metrics that have no tile of their own. */
export async function snapshot(request: APIRequestContext): Promise<{
  state: string
  incident_id: string | null
  thread_id: string | null
  scenario_id: string | null
  golden_signals: Record<string, number>
  infrastructure: Record<string, number>
}> {
  const response = await request.get('/api/telemetry/current')
  expect(response.ok(), 'telemetry snapshot must be available').toBeTruthy()
  return response.json()
}

/** Waits until the badge reports one of the given states, then returns the one it reached. */
export async function waitForState(page: Page, states: string[], timeout = GATE_TIMEOUT_MS): Promise<string> {
  const badge = page.getByTestId('status-badge')
  await expect
    .poll(async () => badge.getAttribute('data-state'), { timeout, intervals: [250] })
    .toMatch(new RegExp(`^(${states.join('|')})$`))
  return (await badge.getAttribute('data-state')) ?? ''
}

/** Opens the war room with frame recording on and waits for the first telemetry to land. */
export async function openWarRoom(page: Page): Promise<void> {
  await recordSseFrames(page)
  await page.goto('/')
  await expect(page.getByTestId('golden-signals')).toBeVisible({ timeout: 20_000 })
}

/** Opens a console's scoped technical disclosure before asserting its progressive details. */
export async function openTechnicalDetails(page: Page, consoleTestId: string): Promise<Locator> {
  const details = page.getByTestId(consoleTestId).getByTestId('technical-details')
  await expect(details).toBeVisible()
  if ((await details.getAttribute('open')) === null) await details.locator('summary').click()
  return details
}

/**
 * Returns the stack to baseline if a previous spec left a run in flight.
 *
 * Reset is idempotent from the API's point of view but requires the retained `incident_id`, so this
 * reads it from the snapshot rather than from the page — a spec that failed mid-run may have left
 * the browser on a state the DOM no longer reflects.
 */
export async function resetStack(request: APIRequestContext): Promise<void> {
  const current = await snapshot(request)
  if (current.state === 'HEALTHY' || current.incident_id === null) return
  await request.post('/api/incidents/reset', { data: { incident_id: current.incident_id } })
  await expect.poll(async () => (await snapshot(request)).state, { timeout: 10_000 }).toBe('HEALTHY')
}

/**
 * Clicks a scenario trigger and waits for the run to reach its human gate.
 *
 * The gate is two steps now: the console footer (or the sticky bar) carries a control that opens the
 * plan, and the decision itself is taken in the dialog. This waits for the *trigger*, so a spec that
 * only needs the run parked at the gate does not have to open the dialog it may not care about.
 */
export async function triggerAndAwaitGate(page: Page, scenario: ScenarioId): Promise<void> {
  await page.getByTestId(`trigger-${scenario}`).click()
  await waitForState(page, ['AWAITING_APPROVAL'])
  await expect(page.getByTestId('show-plan')).toBeVisible()
}

/** Opens the approval dialog and returns it, ready to assert against or decide from. */
export async function openPlanModal(page: Page): Promise<Locator> {
  await page.getByTestId('show-plan').click()
  const modal = page.getByTestId('plan-modal')
  await expect(modal).toBeVisible()
  return modal
}

/**
 * Resolves the gate through the dialog.
 *
 * Opening and deciding are one helper because every spec that decides also has to open, and a spec
 * that forgot the first half would fail on a missing locator rather than on the behaviour it meant
 * to check.
 */
export async function decideFromPlanModal(page: Page, approve: boolean): Promise<void> {
  await openPlanModal(page)
  await page.getByTestId(approve ? 'authorize-remediation' : 'reject-remediation').click()
  await expect(page.getByTestId('plan-modal')).toHaveCount(0)
}

/** Asserts a value sits inside an inclusive band, with the band in the failure message. */
export function expectInBand(value: number, band: readonly [number, number], what: string): void {
  expect(value, `${what} must be inside ${band[0]}–${band[1]}`).toBeGreaterThanOrEqual(band[0])
  expect(value, `${what} must be inside ${band[0]}–${band[1]}`).toBeLessThanOrEqual(band[1])
}
