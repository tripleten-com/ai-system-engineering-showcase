/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        accessibility.spec.ts
 * Purpose:          Runs axe-core against the states the demo actually spends time in, and pins the
 *                   §11 rules axe cannot check on its own.
 * Interacts With:   Incident War Room UI (:3000), API (:8000)
 *
 * Curriculum Project: Cross-cutting — Marketing Delivery
 * Skills:           Accessibility Auditing, Keyboard Navigation
 * Tools:            Playwright, axe-core
 *
 * Two halves, because neither is sufficient alone.
 *
 * axe catches the mechanical failures — an unlabelled control, a contrast ratio computed against the
 * *rendered* background rather than the token, a broken heading order. `tests/unit/contrast.test.ts`
 * checks the palette against solid surfaces; only a browser knows what `bg-pending/10` over
 * `surface-1` actually composites to.
 *
 * The rest of §11 is behavioural and has to be asserted by hand: that the HITL controls are reachable
 * by keyboard, that focus rings are visible on dark, and — the one that matters most — that the log
 * stream is *not* a live region, which axe would never flag because announcing everything is not a
 * violation, merely unusable.
 */

import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

import { decideFromPlanModal, openWarRoom, resetStack, triggerAndAwaitGate, waitForState } from './helpers'

/**
 * Rules deliberately not enforced, with the reason. An empty-by-default disable list is the point:
 * anything added here is a decision, not a default.
 */
const DISABLED_RULES: string[] = []

/**
 * Waits for every finite animation to finish.
 *
 * Without this, axe scans while the reasoning chain is still fading in — the steps enter on a 120ms
 * stagger — and reports `color-contrast` against a partially transparent element. That is a real
 * measurement of a transient frame, not a defect: WCAG does not require intermediate frames of an
 * enter animation to meet contrast, and the settled state does.
 *
 * The HITL glow is `infinite`, so waiting for *all* animations would hang. Only the finite ones are
 * waited on, which is exactly the set that has a settled state to wait for.
 */
async function settleAnimations(page: Page): Promise<void> {
  await page.waitForFunction(
    () =>
      document
        .getAnimations()
        .filter((animation) => animation.effect?.getTiming().iterations !== Infinity)
        .every((animation) => animation.playState === 'finished'),
    undefined,
    { timeout: 10_000 },
  )
}

async function scan(page: Page, context?: string) {
  await settleAnimations(page)

  const results = await new AxeBuilder({ page })
    // WCAG 2.1 A and AA, which is what §11's thresholds are drawn from.
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .disableRules(DISABLED_RULES)
    .analyze()

  const summary = results.violations.map((violation) => ({
    id: violation.id,
    impact: violation.impact,
    nodes: violation.nodes.map((node) => node.target.join(' ')),
  }))

  expect(summary, `axe violations${context ? ` (${context})` : ''}`).toEqual([])
}

test.describe('Accessibility', () => {
  test.beforeEach(async ({ request }) => {
    await resetStack(request)
  })

  test.afterEach(async ({ request }) => {
    await resetStack(request)
  })

  test('the steady state is clean', async ({ page }) => {
    await openWarRoom(page)
    await scan(page, 'HEALTHY')
  })

  test('the disclosure panel is clean', async ({ page }) => {
    // Expanded, because a collapsed panel is not in the DOM and would be scanned vacuously.
    await openWarRoom(page)
    await page.getByTestId('disclosure-toggle').click()
    await expect(page.getByTestId('disclosure-panel')).toBeVisible()
    await scan(page, 'disclosure expanded')
  })

  test('the outage and approval states are clean', async ({ page }) => {
    await openWarRoom(page)
    await triggerAndAwaitGate(page, 'db_pool_exhaustion')
    // The densest state in the demo: every panel populated, the gate open, the glow running.
    await scan(page, 'AWAITING_APPROVAL')
  })

  test('the security run is clean', async ({ page }) => {
    await openWarRoom(page)
    await triggerAndAwaitGate(page, 'prompt_injection')
    await expect(page.getByTestId('no-impact-strip')).toBeVisible()
    await scan(page, 'EXPLOIT_INTERCEPTED / AWAITING_APPROVAL')
  })

  test('a terminal banner is clean', async ({ page }) => {
    await openWarRoom(page)
    await triggerAndAwaitGate(page, 'db_pool_exhaustion')
    await decideFromPlanModal(page, false)
    await waitForState(page, ['REJECTED'])
    await scan(page, 'REJECTED')
  })

  test('the mobile layout is clean', async ({ page }) => {
    // A different layout, not the same one scaled — including the sticky bar, which overlays content
    // and is the most likely place for a focus-order or contrast problem.
    await page.setViewportSize({ width: 375, height: 812 })
    await openWarRoom(page)
    await triggerAndAwaitGate(page, 'db_pool_exhaustion')
    await expect(page.getByTestId('mobile-hitl-bar')).toBeVisible()
    await scan(page, 'mobile AWAITING_APPROVAL')
  })

  test('the HITL controls are reachable by keyboard before the log stream', async ({ page }) => {
    // §11, and the rule the whole demo depends on: if the one control that changes anything is not
    // reachable without a mouse, the pipeline stalls at the gate for a keyboard user.
    await openWarRoom(page)
    await triggerAndAwaitGate(page, 'db_pool_exhaustion')

    // The gate's entry point is what has to be reachable first; the decision itself is in a dialog
    // that traps nothing and is opened from here.
    const reached: string[] = []
    for (let step = 0; step < 40; step += 1) {
      await page.keyboard.press('Tab')
      const id = await page.evaluate(() => document.activeElement?.getAttribute('data-testid') ?? '')
      if (id) reached.push(id)
      if (reached.includes('show-plan')) break
    }

    expect(reached, 'the plan trigger must be tabbable').toContain('show-plan')
    const triggerAt = reached.indexOf('show-plan')
    const logAt = reached.indexOf('log-tail')
    if (logAt !== -1) {
      expect(triggerAt, 'the gate must come before the log stream in tab order').toBeLessThan(logAt)
    }
  })

  test('focus is visible on dark', async ({ page }) => {
    // §11 forbids removing outlines, and an invisible focus ring on `#0B0F19` is the same defect as
    // removing it. Asserted on the control that matters most.
    await openWarRoom(page)
    await triggerAndAwaitGate(page, 'db_pool_exhaustion')

    // Tabbed to, not `.focus()`ed. `:focus-visible` is what the ring is bound to, and a programmatic
    // focus does not reliably match it — the first version of this test measured the default
    // `outline: none` and read it as a missing ring.
    //
    // Measured on the plan trigger rather than inside the dialog. The dialog moves focus to its own
    // dismiss control when it opens, so a Tab dance in there races that effect — and the trigger is
    // the control a keyboard user actually arrives at while scanning the page.
    const trigger = page.getByTestId('show-plan')
    await page.keyboard.press('Tab')
    await trigger.evaluate((node) => (node as HTMLElement).focus())
    await page.keyboard.press('Shift+Tab')
    await page.keyboard.press('Tab')
    await expect(trigger).toBeFocused()

    const outline = await trigger.evaluate((node) => {
      const style = getComputedStyle(node)
      return { style: style.outlineStyle, width: style.outlineWidth, color: style.outlineColor }
    })

    expect(outline.style).not.toBe('none')
    expect(Number.parseFloat(outline.width)).toBeGreaterThanOrEqual(2)
  })

  test('the log stream is not announced', async ({ page }) => {
    // The §11 exception, and the reason it exists: a screen reader reciting ten log lines a second
    // is unusable. axe would never flag this — announcing everything is not a violation.
    await openWarRoom(page)
    await triggerAndAwaitGate(page, 'db_pool_exhaustion')

    const live = await page
      .getByTestId('log-tail')
      .evaluate((node) => ({ own: node.getAttribute('aria-live'), inherited: !!node.closest('[aria-live]') }))

    expect(live.own).toBeNull()
    expect(live.inherited).toBe(false)
  })

  test('the state badge and the terminal banner are announced', async ({ page }) => {
    // The inverse of the rule above. State changes are infrequent and consequential, which is
    // exactly what a polite live region is for.
    await openWarRoom(page)

    await expect(page.getByTestId('status-badge').locator('xpath=ancestor::*[@aria-live="polite"]')).toHaveCount(1)

    await triggerAndAwaitGate(page, 'db_pool_exhaustion')
    await decideFromPlanModal(page, false)
    await waitForState(page, ['REJECTED'])

    await expect(page.getByTestId('terminal-state-banner')).toHaveAttribute('aria-live', 'polite')
  })
})
