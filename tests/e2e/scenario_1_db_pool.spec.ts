/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        scenario_1_db_pool.spec.ts
 * Purpose:          Playwright E2E walkthrough for SEV-1 DB Connection Pool Exhaustion — the
 *                   canonical happy path through all five projects.
 * Interacts With:   Incident War Room UI (:3000) & API (:8000)
 *
 * Curriculum Project: Project 1 — Diagnostics & Telemetry
 * Skills:           E2E Incident Simulation, PII Masking Verification, HITL Approval
 * Tools:            Playwright, TypeScript
 *
 * One test, walked in order, because the assertions are a sequence rather than a set: the RAG match
 * only exists after the spike, the gate only after the match, the postmortem only after the click.
 * Splitting them into independent tests would mean triggering the scenario four times and asserting
 * each stage against a run whose earlier stages were never observed.
 */

import { expect, test } from '@playwright/test'

import { BASELINE, DECAY_MS, decideFromPlanModal, expectInBand, expectNoRawSecrets, openPlanModal, openTechnicalDetails, openWarRoom, readMetric, resetStack, snapshot, triggerAndAwaitGate, waitForState } from './helpers'

/** The raw values `scenarios.py` puts in the Scenario 1 log fixtures. */
const RAW_SECRETS = ['prod_k8s_secret_992', '10.0.1.42', 'jane@corp.com'] as const

test.describe('Scenario 1: DB Connection Pool Exhaustion', () => {
  test.beforeEach(async ({ request }) => {
    await resetStack(request)
  })

  test.afterEach(async ({ request }) => {
    await resetStack(request)
  })

  test('spikes, redacts, retrieves, gates, executes and decays', async ({ page, request }) => {
    await openWarRoom(page)

    // 1. Trigger.
    await page.getByTestId('trigger-db_pool_exhaustion').click()
    await waitForState(page, ['CRITICAL_OUTAGE', 'AWAITING_APPROVAL'])

    // 2. Telemetry spikes. All three thresholds are polled together rather than read once, because
    //    each metric reaches its peak at the end of its own smoothstep ramp — a single snapshot
    //    taken as p99 crosses its threshold will still catch the pool mid-climb.
    await expect
      .poll(
        async () => {
          const current = await snapshot(request)
          return (
            current.golden_signals.latency_p99_ms > 4_500 &&
            current.golden_signals.http_5xx_error_rate_pct > 30 &&
            current.infrastructure.db_pool_utilization_pct > 95
          )
        },
        { timeout: 15_000, intervals: [250] },
      )
      .toBe(true)

    // The enum is a function of the state machine: 0 = Down for Scenarios 1–3 in outage, and this
    // is what Grafana's status panel reads. It is the one metric with no jitter to hide behind.
    expect((await snapshot(request)).infrastructure.system_health_status, 'health enum must read Down').toBe(0)

    // The tile agrees with the snapshot, which is what proves the UI is rendering the real series
    // rather than a canned animation.
    await expect.poll(async () => readMetric(page, 'API response time'), { timeout: 10_000 }).toBeGreaterThan(1_000)

    // 3. Sanitization. Three tokens masked, and none of the raw values anywhere.
    await expect(page.getByTestId('masked-count')).toContainText('3 Sensitive Tokens Masked', { timeout: 15_000 })
    const logTail = page.getByTestId('log-tail')
    await expect(logTail).toContainText('Detected password redaction')
    const logDetails = await openTechnicalDetails(page, 'log-sanitizer')
    await expect(logDetails).toContainText('[REDACTED: password]')
    await expect(logDetails).toContainText('[REDACTED: ip]')
    await expect(logDetails).toContainText('[REDACTED: email]')
    await expectNoRawSecrets(page, RAW_SECRETS)

    // 4. Retrieval. RB-104 at rank 1, and the score rendered as a real number.
    //
    //    Rank, not an absolute similarity floor — `testing-strategy-and-specs.md` §5.2 says so
    //    explicitly, and it is right: the offline path derives vectors by signed feature hashing, so
    //    a short query against a long runbook lands nowhere near the `0.94` in the storyline. That
    //    figure is narrative. The margin over the runner-up is the property that actually holds, and
    //    it is asserted separately through the retrieval probe below.
    const match = page.getByTestId('rag-match').first()
    await expect(match).toBeVisible({ timeout: 15_000 })
    await expect(match).toHaveAttribute('data-runbook-id', 'RB-104')
    const ragDetails = await openTechnicalDetails(page, 'rag-inspector')
    expect(Number(await ragDetails.getByTestId('cosine-similarity').innerText())).toBeGreaterThan(0)
    await expect(ragDetails.getByTestId('rag-source')).toContainText('RRF')

    // The disclosure probe, against the same corpus, from the browser. This is the assertion that
    // proves retrieval is ranking rather than looking up: the right runbook wins by a wide margin,
    // and it wins for a query the demo never scripted.
    const probe = await request.post('/api/retrieval/search', {
      data: { query: 'postgres connection pool exhausted idle in transaction', limit: 3 },
    })
    const ranked = (await probe.json()).results as Array<{ runbook_id: string; cosine_similarity: number }>
    expect(ranked[0].runbook_id).toBe('RB-104')
    expect(ranked[0].cosine_similarity).toBeGreaterThan(ranked[1].cosine_similarity * 2)

    // 5. The gate. The contractual prompt now names the action inside the dialog rather than
    //    labelling the button, so it is read there.
    await waitForState(page, ['AWAITING_APPROVAL'])
    const modal = await openPlanModal(page)
    await expect(modal.getByTestId('plan-modal-prompt')).toContainText('Authorize DB Pool Drain & Recycle')
    await expect(modal.getByTestId('authorize-remediation')).toContainText('Approve')
    await page.getByTestId('plan-modal-close').click()

    // Nothing has been dispatched. The hard stop is the whole point of Project 5, so the observable
    // claim is asserted directly: no worker line, no postmortem, no DLQ traffic.
    await expect(page.getByTestId('worker-log')).toContainText('Awaiting approval')
    await expect(page.getByTestId('postmortem-modal')).toHaveCount(0)
    await expect(page.getByTestId('postmortem-link')).toHaveCount(0)
    expect((await snapshot(request)).infrastructure.dlq_message_count).toBe(0)

    // 6. Authorize, and watch the worker do real work.
    await decideFromPlanModal(page, true)
    await expect(page.getByTestId('worker-log')).toContainText('pg_terminate_backend()', { timeout: 25_000 })

    // 7. The postmortem drawer, opening itself and rendering the object the worker actually wrote.
    //    Fetched back over the same-origin path the drawer used, so this is the archive's own bytes
    //    rather than the page's account of them.
    const drawer = page.getByTestId('postmortem-modal')
    await expect(drawer).toBeVisible({ timeout: 25_000 })
    await expect(drawer.getByTestId('postmortem-scenario')).toHaveText('db_pool_exhaustion')
    await expect(drawer.getByTestId('postmortem-runbook')).toHaveText('RB-104')
    await expect(drawer.getByTestId('postmortem-tool')).toHaveText('flush_connection_pool')

    const link = page.getByTestId('postmortem-link')
    const href = await link.getAttribute('href')
    expect(href).toContain('tripleten-cloud-postmortems')
    const archived = await request.get(href!)
    expect(archived.ok(), 'the archived postmortem must be downloadable').toBeTruthy()
    expect((await archived.json()).scenario_id).toBe('db_pool_exhaustion')

    // Dismissing it leaves a way back in, and does not block the rest of the run.
    await page.getByTestId('postmortem-close').click()
    await expect(drawer).toHaveCount(0)
    await expect(page.getByTestId('postmortem-open')).toBeVisible()

    // 8. Decay, then settle.
    //
    //    Polled rather than read once, and the reason is arithmetic. The curve is
    //    `Baseline + (Peak − Baseline)·e^(−1.8t)`, so at t=4s the error rate is
    //    `36.4 × e^(−7.2) = 0.027%` — close to zero but not zero. It becomes exactly zero one tick
    //    later, when the state reaches HEALTHY and baseline generation resumes. A single snapshot
    //    taken the moment the badge flips can catch either, so this waits for the condition the
    //    requirement actually states ("returns to 0%") instead of racing the tick it happens on.
    await waitForState(page, ['RECOVERING', 'HEALTHY'])
    await page.waitForTimeout(DECAY_MS)
    await waitForState(page, ['HEALTHY'], 15_000)

    await expect
      .poll(async () => (await snapshot(request)).golden_signals.http_5xx_error_rate_pct, {
        timeout: 10_000,
        intervals: [500],
      })
      .toBe(0)

    const settled = await snapshot(request)
    expectInBand(settled.golden_signals.latency_p99_ms, BASELINE.p99Ms, 'settled p99')
    expectInBand(settled.infrastructure.db_pool_utilization_pct, BASELINE.dbPoolPct, 'settled DB pool')
  })

  test('refuses a second trigger while a run is in flight', async ({ page }) => {
    // The API answers 409. Disabling the controls rather than letting the click fail keeps the UI
    // honest about what is available.
    await openWarRoom(page)
    await triggerAndAwaitGate(page, 'db_pool_exhaustion')

    for (const scenario of ['db_pool_exhaustion', 'cache_thundering_herd', 'prompt_injection']) {
      await expect(page.getByTestId(`trigger-${scenario}`)).toBeDisabled()
    }
  })
})
