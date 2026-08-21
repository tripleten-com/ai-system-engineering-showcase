/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        scenario_2_cache_stampede.spec.ts
 * Purpose:          Playwright E2E walkthrough for SEV-2 Cache Thundering Herd.
 * Interacts With:   Incident War Room UI (:3000), API (:8000), redis (:6379)
 *
 * Curriculum Project: Project 2 — Hybrid RAG & Retrieval Architecture
 * Skills:           E2E Incident Simulation, Cache Metrics, HITL Approval
 * Tools:            Playwright, TypeScript
 */

import { expect, test } from '@playwright/test'

import { BASELINE, DECAY_MS, expectInBand, expectNoRawSecrets, openPlanModal, openTechnicalDetails, openWarRoom, resetStack, snapshot, waitForState } from './helpers'

/** The JWT fragment and the private IP from the Scenario 2 log fixtures. */
const RAW_SECRETS = ['eyJhbGciOi', '10.0.4.19'] as const

test.describe('Scenario 2: Cache Thundering Herd', () => {
  test.beforeEach(async ({ request }) => {
    await resetStack(request)
  })

  test.afterEach(async ({ request }) => {
    await resetStack(request)
  })

  test('collapses the hit ratio, matches RB-208, and warms the cache after approval', async ({ page, request }) => {
    await openWarRoom(page)

    await page.getByTestId('trigger-cache_thundering_herd').click()
    await waitForState(page, ['CRITICAL_OUTAGE', 'AWAITING_APPROVAL'])

    // Redis saturates and the hit ratio collapses — the two numbers that define this scenario.
    await expect
      .poll(async () => (await snapshot(request)).infrastructure.redis_memory_utilization_pct, { timeout: 15_000 })
      .toBeGreaterThanOrEqual(97)
    expect((await snapshot(request)).infrastructure.cache_hit_ratio_pct).toBeLessThan(20)

    await expect(page.getByTestId('masked-count')).toContainText('2 Sensitive Tokens Masked', { timeout: 15_000 })
    const logDetails = await openTechnicalDetails(page, 'log-sanitizer')
    await expect(logDetails).toContainText('[REDACTED: jwt]')
    await expect(logDetails).toContainText('[REDACTED: ip]')
    await expectNoRawSecrets(page, RAW_SECRETS)

    const match = page.getByTestId('rag-match').first()
    await expect(match).toBeVisible({ timeout: 15_000 })
    await expect(match).toHaveAttribute('data-runbook-id', 'RB-208')

    await waitForState(page, ['AWAITING_APPROVAL'])
    const modal = await openPlanModal(page)
    await expect(modal.getByTestId('plan-modal-prompt')).toContainText('Authorize Cache Warm-Up & Orphan Purge')
    await modal.getByTestId('authorize-remediation').click()
    await expect(page.getByTestId('plan-modal')).toHaveCount(0)

    // The worker warms keys in batches and jitters their TTLs, which is what RB-208 prescribes.
    await expect(page.getByTestId('worker-log')).toContainText(/warm|TTL|jitter/i, { timeout: 25_000 })

    // The postmortem drawer opens itself once the run settles. Dismissed straight away so the
    // overlay is not sitting over the telemetry the rest of this spec reads.
    const drawer = page.getByTestId('postmortem-modal')
    await expect(drawer).toBeVisible({ timeout: 25_000 })
    await expect(drawer.getByTestId('postmortem-runbook')).toHaveText('RB-208')
    await page.getByTestId('postmortem-close').click()
    await expect(drawer).toHaveCount(0)

    // Polled for the same reason as Scenario 1: the decay endpoint at t=4s is close to baseline but
    // not on it, and the exact values arrive one tick later when baseline generation resumes.
    await waitForState(page, ['RECOVERING', 'HEALTHY'])
    await page.waitForTimeout(DECAY_MS)
    await waitForState(page, ['HEALTHY'], 15_000)

    await expect
      .poll(
        async () => {
          const current = await snapshot(request)
          return (
            current.infrastructure.cache_hit_ratio_pct >= 98 &&
            current.infrastructure.redis_memory_utilization_pct <= BASELINE.redisMemoryPct[1]
          )
        },
        { timeout: 10_000, intervals: [500] },
      )
      .toBe(true)

    const settled = await snapshot(request)
    expectInBand(settled.infrastructure.redis_memory_utilization_pct, BASELINE.redisMemoryPct, 'settled Redis memory')
    expect(settled.infrastructure.cache_hit_ratio_pct).toBeGreaterThanOrEqual(98)
  })
})
