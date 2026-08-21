/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        responsive_hitl.spec.ts
 * Purpose:          Verifies the mobile HITL rule at a real 375px viewport — the approval controls
 *                   pin to a sticky bottom bar and stay reachable without scrolling.
 * Interacts With:   Incident War Room UI (:3000), API (:8000)
 *
 * Curriculum Project: Project 5 — Autonomous Agent & Human-in-the-Loop
 * Skills:           Responsive Design Verification, Viewport Testing
 * Tools:            Playwright, TypeScript
 *
 * `spa-design-guidelines.md` §7 calls this "the most important responsive decision in the document",
 * and the failure mode it guards against is specific: if the gate sits below the fold behind three
 * panels of scrolling, the pipeline stalls at `AWAITING_APPROVAL` and the visitor leaves believing
 * the demo is broken. The unit suite covers the mechanism; only a real viewport covers the geometry.
 */

import { expect, test } from '@playwright/test'

import { decideFromPlanModal, openWarRoom, resetStack, triggerAndAwaitGate, waitForState } from './helpers'

/** iPhone-class width, comfortably inside the `<768px` band. */
const MOBILE = { width: 375, height: 812 }
const DESKTOP = { width: 1440, height: 900 }

test.describe('Mobile HITL bar', () => {
  test.beforeEach(async ({ request }) => {
    await resetStack(request)
  })

  test.afterEach(async ({ request }) => {
    await resetStack(request)
  })

  test('pins the approval controls above the fold at 375px', async ({ page }) => {
    await page.setViewportSize(MOBILE)
    await openWarRoom(page)

    // The entry point stays reachable at every width.
    await expect(page.getByTestId('trigger-db_pool_exhaustion')).toBeVisible()
    await triggerAndAwaitGate(page, 'db_pool_exhaustion')

    const bar = page.getByTestId('mobile-hitl-bar')
    await expect(bar).toBeVisible()

    // Exactly one way into the decision, so the tab order is unambiguous.
    await expect(page.getByTestId('show-plan')).toHaveCount(1)
    await expect(page.getByTestId('hitl-block')).toHaveCount(0)

    // Pinned to the bottom of the viewport, not merely present somewhere down the page.
    const box = (await bar.boundingBox())!
    expect(box.y + box.height).toBeLessThanOrEqual(MOBILE.height + 2)
    expect(box.y).toBeLessThan(MOBILE.height)

    // Reachable without scrolling, which is the whole claim.
    await expect(page.getByTestId('show-plan')).toBeInViewport()
    expect(await page.evaluate(() => window.scrollY)).toBe(0)

    // The touch target clears 44px.
    const target = (await page.getByTestId('show-plan').boundingBox())!
    expect(target.height).toBeGreaterThanOrEqual(44)

    await decideFromPlanModal(page, true)
    await waitForState(page, ['EXECUTING', 'RECOVERING', 'HEALTHY'], 30_000)
    await expect(bar).toHaveCount(0)
  })

  test('keeps the gate inside the agent panel on desktop', async ({ page }) => {
    await page.setViewportSize(DESKTOP)
    await openWarRoom(page)
    await triggerAndAwaitGate(page, 'db_pool_exhaustion')

    await expect(page.getByTestId('hitl-block')).toBeVisible()
    await expect(page.getByTestId('mobile-hitl-bar')).toHaveCount(0)
    await expect(page.getByTestId('show-plan')).toHaveCount(1)
  })

  test('moves the controls when the viewport crosses the breakpoint mid-run', async ({ page }) => {
    // A visitor rotating a tablet at the gate must not lose the button.
    await page.setViewportSize(DESKTOP)
    await openWarRoom(page)
    await triggerAndAwaitGate(page, 'db_pool_exhaustion')
    await expect(page.getByTestId('hitl-block')).toBeVisible()

    await page.setViewportSize(MOBILE)
    await expect(page.getByTestId('mobile-hitl-bar')).toBeVisible()
    await expect(page.getByTestId('hitl-block')).toHaveCount(0)
    await expect(page.getByTestId('show-plan')).toHaveCount(1)

    await page.setViewportSize(DESKTOP)
    await expect(page.getByTestId('hitl-block')).toBeVisible()
    await expect(page.getByTestId('mobile-hitl-bar')).toHaveCount(0)
  })
})
