/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        terminal_branches.spec.ts
 * Purpose:          The three non-happy-path endings — REJECTED, FAILED, and the Master Reset that
 *                   clears each of them.
 * Interacts With:   Incident War Room UI (:3000), API (:8000)
 *
 * Curriculum Project: Project 5 — Autonomous Agent & Human-in-the-Loop
 * Skills:           E2E Terminal Branches, Authenticated Callbacks, State Reset
 * Tools:            Playwright, TypeScript
 *
 * The failure branch is driven by the same authenticated callback the worker itself makes once its
 * retry budget is exhausted. That is deliberate: a test-only backdoor would prove the UI can render
 * a `FAILED` banner without proving the API can reach `FAILED`, which is the interesting half.
 */

import { expect, test, type Page } from '@playwright/test'

import { BASELINE, CALLBACK_SECRET, decideFromPlanModal, expectInBand, openPlanModal, openWarRoom, resetStack, setWorkerRunning, snapshot, triggerAndAwaitGate, waitForState, workerIsUp } from './helpers'

/**
 * Clicks Authorize and returns the `job_id` the API dispatched.
 *
 * Read off the response rather than guessed, because `WorkerCallback` requires the real job id —
 * and a callback for a job that was never dispatched is not the scenario under test.
 */
async function authorizeAndCaptureJob(page: Page): Promise<string> {
  await openPlanModal(page)
  const pending = page.waitForResponse(
    (response) => response.url().includes('/api/incidents/authorize') && response.request().method() === 'POST',
  )
  await page.getByTestId('authorize-remediation').click()
  const body = await (await pending).json()
  expect(body.job_id, 'authorize must report the dispatched job').toBeTruthy()
  return body.job_id as string
}

test.describe('Terminal branches', () => {
  test.beforeEach(async ({ request }) => {
    await resetStack(request)
  })

  test.afterEach(async ({ request }) => {
    await resetStack(request)
  })

  test('rejection ends the run without dispatching anything', async ({ page, request }) => {
    await openWarRoom(page)
    await triggerAndAwaitGate(page, 'db_pool_exhaustion')

    const gated = await snapshot(request)
    expect(gated.golden_signals.latency_p99_ms).toBeGreaterThan(1_000)

    await decideFromPlanModal(page, false)
    await waitForState(page, ['REJECTED'])

    const banner = page.getByTestId('terminal-state-banner')
    await expect(banner).toHaveAttribute('data-state', 'REJECTED')
    await expect(banner).toContainText('REMEDIATION REJECTED — INTERVENTION SKIPPED')

    // Nothing was dispatched. The terminal says so in the worker's own words, and there is no
    // postmortem because no job ever ran.
    await expect(page.getByTestId('worker-log')).toContainText(/nothing dispatched/i)
    await expect(page.getByTestId('postmortem-link')).toHaveCount(0)
    // No drawer, and no way to open one. A postmortem here would present a recovery that never
    // happened, and the success strip must stay away for the same reason.
    await expect(page.getByTestId('postmortem-modal')).toHaveCount(0)
    await expect(page.getByTestId('postmortem-open')).toHaveCount(0)
    await expect(page.getByTestId('scenario-outcome-strip')).toHaveCount(0)

    // Chaos holds rather than decaying — there was no remediation to recover from.
    await page.waitForTimeout(6_000)
    const held = await snapshot(request)
    expect(held.state).toBe('REJECTED')
    expect(held.golden_signals.latency_p99_ms, 'chaos must hold, not decay').toBeGreaterThan(1_000)

    // Master Reset from the banner returns everything to baseline.
    await page.getByTestId('banner-master-reset').click()
    await waitForState(page, ['HEALTHY'])
    const reset = await snapshot(request)
    expectInBand(reset.golden_signals.latency_p99_ms, BASELINE.p99Ms, 'p99 after reset')
    expect(reset.golden_signals.http_5xx_error_rate_pct).toBe(0)
  })

  test('a failed worker callback lands the run in FAILED with the error visible', async ({ page, request }) => {
    // The worker is stopped for the duration. Left running, its success callback lands within a
    // couple of seconds and races this test's failure callback — and a branch that is verified only
    // when it happens to win the race is not verified.
    setWorkerRunning(false)
    try {
      await openWarRoom(page)
      await triggerAndAwaitGate(page, 'db_pool_exhaustion')

      const incidentId = (await snapshot(request)).incident_id!
      expect(incidentId).toBeTruthy()

      const jobId = await authorizeAndCaptureJob(page)
      expect((await snapshot(request)).state, 'the run must be executing with no worker to finish it').toBe(
        'EXECUTING',
      )

      const error = 'pg_terminate_backend refused: role lacks privileges'
      const response = await request.post(`/api/incidents/${incidentId}/callback`, {
        headers: { Authorization: `Bearer ${CALLBACK_SECRET}` },
        data: {
          status: 'failed',
          job_id: jobId,
          idempotency_key: `e2e-failure-${jobId}`,
          error,
        },
      })
      expect(response.status(), `callback rejected: ${await response.text()}`).toBeLessThan(300)

      await waitForState(page, ['FAILED'])
      const banner = page.getByTestId('terminal-state-banner')
      await expect(banner).toHaveAttribute('data-state', 'FAILED')
      await expect(page.getByTestId('failure-reason')).toContainText('role lacks privileges')

      // No postmortem: the job never completed, so the worker never archived one.
      await expect(page.getByTestId('postmortem-modal')).toHaveCount(0)
      await expect(page.getByTestId('postmortem-open')).toHaveCount(0)
      await expect(page.getByTestId('scenario-outcome-strip')).toHaveCount(0)

      // Chaos holds here too, and only Master Reset clears it.
      const held = await snapshot(request)
      expect(held.golden_signals.latency_p99_ms, 'chaos must hold, not decay').toBeGreaterThan(1_000)

      // Reset *before* the worker comes back, deliberately. The job this test dispatched is still
      // sitting in `remediation-jobs`, and the restarted worker will consume it — landing on a run
      // that is already HEALTHY, which the callback route refuses with 409. A 409 is a permanent
      // refusal in the worker's delivery handling, so the message is deleted rather than redriven
      // into the DLQ. Restarting first would let that stale delivery race the next spec.
      await page.getByTestId('banner-master-reset').click()
      await waitForState(page, ['HEALTHY'])
    } finally {
      setWorkerRunning(true)
      // Bounded wait for the container to be accepting work again, so the suite never hands the
      // next spec — or a subsequent integration run against the same stack — a half-started worker.
      await expect.poll(() => workerIsUp(), { timeout: 30_000, intervals: [500] }).toBe(true)
    }
  })

  test('rejects an unauthenticated callback', async ({ page, request }) => {
    // The gate is only as strong as the callback's authentication: an unauthenticated caller that
    // could report success would be a way around the human decision. The body is deliberately
    // well-formed, so a 401 proves the token was checked rather than the payload rejected.
    await openWarRoom(page)
    await triggerAndAwaitGate(page, 'db_pool_exhaustion')

    const incidentId = (await snapshot(request)).incident_id!
    const response = await request.post(`/api/incidents/${incidentId}/callback`, {
      data: {
        status: 'succeeded',
        job_id: 'job-forged',
        idempotency_key: 'e2e-forged',
        postmortem_uri: 's3://tripleten-cloud-postmortems/forged.json',
      },
    })

    // 401, never 403 — a 403 would confirm the token was well-formed but wrong.
    expect(response.status()).toBe(401)
    expect((await snapshot(request)).state, 'an unauthenticated callback must change nothing').toBe(
      'AWAITING_APPROVAL',
    )
    // Still at the gate, so the way into the decision is still on the page.
    await expect(page.getByTestId('show-plan')).toBeVisible()
  })

  test('an authenticated callback cannot bypass the approval gate', async ({ page, request }) => {
    // Authentication is necessary and not sufficient. A valid token on a run still sitting at
    // AWAITING_APPROVAL is refused with 409 — otherwise the token would be the gate.
    await openWarRoom(page)
    await triggerAndAwaitGate(page, 'db_pool_exhaustion')

    const incidentId = (await snapshot(request)).incident_id!
    const response = await request.post(`/api/incidents/${incidentId}/callback`, {
      headers: { Authorization: `Bearer ${CALLBACK_SECRET}` },
      data: {
        status: 'succeeded',
        job_id: 'job-never-dispatched',
        idempotency_key: `e2e-bypass-${incidentId}`,
        postmortem_uri: 's3://tripleten-cloud-postmortems/bypass.json',
      },
    })

    expect(response.status()).toBe(409)
    expect((await snapshot(request)).state).toBe('AWAITING_APPROVAL')
    // Still at the gate, so the way into the decision is still on the page.
    await expect(page.getByTestId('show-plan')).toBeVisible()
  })

  test('Master Reset is reachable from the control bar as well as the banner', async ({ page, request }) => {
    await openWarRoom(page)
    await triggerAndAwaitGate(page, 'cache_thundering_herd')

    await page.getByTestId('master-reset').click()
    await waitForState(page, ['HEALTHY'])

    const reset = await snapshot(request)
    expect(reset.state).toBe('HEALTHY')
    expectInBand(reset.infrastructure.redis_memory_utilization_pct, BASELINE.redisMemoryPct, 'Redis after reset')

    // The panels clear on an explicit reset, and the controls are usable again — a reset that left
    // the buttons disabled would end the demo.
    await expect(page.getByTestId('log-tail')).toContainText('Awaiting incident')
    await expect(page.getByTestId('trigger-db_pool_exhaustion')).toBeEnabled()
  })
})
