/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        sse_reconnect.spec.ts
 * Purpose:          Verifies the one ambiguity the disconnected state exists to remove — a stalled
 *                   stream and a healthy system both draw a flat line at baseline.
 * Interacts With:   Incident War Room UI (:3000), API (:8000)
 *
 * Curriculum Project: Project 1 — Diagnostics & Telemetry
 * Skills:           E2E SSE Reconnection, Resilient Event Streams, Degraded-Mode UX
 * Tools:            Playwright, TypeScript
 *
 * The stream is severed with Playwright route interception rather than by stopping a container: the
 * client behaviour under test is identical, and the stack stays up so the other specs in the run are
 * unaffected.
 */

import { expect, test } from '@playwright/test'

import { decideFromPlanModal, openWarRoom, recordSseFrames, resetStack, snapshot, waitForState } from './helpers'

test.describe('SSE disconnection and resilience', () => {
  test.beforeEach(async ({ request }) => {
    await resetStack(request)
  })

  test.afterEach(async ({ request }) => {
    await resetStack(request)
  })

  test('surfaces the disconnect, desaturates, then rehydrates on recovery', async ({ page, request }) => {
    // 1. Sever the stream *before* navigation. `page.route` only intercepts new requests, so
    //    installing it after the war room has an open EventSource leaves that connection alive and
    //    the UI correctly never reports a disconnect.
    let severed = true
    await page.route('**/api/stream*', async (route) => {
      if (severed) await route.abort('connectionrefused')
      else await route.fallback()
    })

    await openWarRoom(page)

    // 2. The persistent strip, naming the attempt count.
    const strip = page.getByTestId('stream-disconnected')
    await expect(strip).toBeVisible({ timeout: 20_000 })
    await expect(strip).toContainText(/TELEMETRY STREAM DISCONNECTED/i)
    await expect(strip).toContainText(/attempt \d+/i)

    // 3. Sparklines desaturate and the values mute — without the page crashing.
    for (const spark of await page.getByTestId('sparkline').all()) {
      await expect(spark).toHaveAttribute('data-stale', 'true')
    }
    await expect(page.getByTestId('golden-signals')).toBeVisible()

    // The polling fallback keeps the tiles alive while the stream is down. The strip stays up
    // throughout: polling is a degraded mode and the UI must say so.
    await expect
      .poll(async () => page.evaluate(() => performance.getEntriesByType('resource').filter((entry) => entry.name.includes('/api/telemetry/current')).length), { timeout: 15_000 })
      .toBeGreaterThan(1)
    await expect(strip).toBeVisible()

    // 4. Restore, and confirm the client rebuilds from the snapshot with its identifiers intact.
    await page.getByTestId('trigger-db_pool_exhaustion').click()
    await waitForState(page, ['CRITICAL_OUTAGE', 'AWAITING_APPROVAL'], 30_000)
    const duringOutage = await snapshot(request)
    expect(duringOutage.incident_id).toBeTruthy()

    severed = false
    await expect(strip).toHaveCount(0, { timeout: 30_000 })

    for (const spark of await page.getByTestId('sparkline').all()) {
      await expect(spark).toHaveAttribute('data-stale', 'false')
    }

    // `incident_id`, `thread_id`, `scenario_id` and `state` all survive, which is exactly what
    // `GET /api/telemetry/current` exists to provide — and why there is no server-side replay buffer.
    await waitForState(page, ['CRITICAL_OUTAGE', 'AWAITING_APPROVAL'])
    await expect(page.getByTestId('show-plan')).toBeVisible({ timeout: 30_000 })
  })

  test('renders baseline before anyone picks a scenario, and survives a reload mid-run', async ({ page, request }) => {
    // The War Room follows the platform rather than one incident: it opens on baseline and its
    // stream is not scoped to a run, so a reload recovers the run instead of losing it.
    await openWarRoom(page)
    await expect(page.getByTestId('status-badge')).toHaveAttribute('data-state', 'HEALTHY')

    await page.getByTestId('trigger-db_pool_exhaustion').click()
    await waitForState(page, ['AWAITING_APPROVAL'], 30_000)
    const before = await snapshot(request)

    await recordSseFrames(page)
    await page.reload()

    await expect(page.getByTestId('status-badge')).toHaveAttribute('data-state', 'AWAITING_APPROVAL', {
      timeout: 20_000,
    })
    await expect(page.getByTestId('show-plan')).toBeVisible()

    const after = await snapshot(request)
    expect(after.incident_id).toBe(before.incident_id)
    expect(after.thread_id).toBe(before.thread_id)

    // And the recovered page can still drive the gate, which is the only thing that matters. This is
    // the case the plan section's visibility rule is built around: a reload rebuilds from the
    // snapshot with an *empty* reasoning chain, so gating that section on `thoughts.length` would
    // hide the way into the decision at exactly this moment.
    await decideFromPlanModal(page, true)
    await waitForState(page, ['EXECUTING', 'RECOVERING', 'HEALTHY'], 30_000)
  })
})
