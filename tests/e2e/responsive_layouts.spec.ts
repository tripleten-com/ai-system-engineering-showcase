/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        responsive_layouts.spec.ts
 * Purpose:          Verifies the four documented layouts at real viewports — the workflow grid, the
 *                   console geometry, the postmortem drawer, and the single authorize control.
 * Interacts With:   Incident War Room UI (:3000), API (:8000)
 *
 * Curriculum Project: Cross-cutting — Production Aesthetics
 * Skills:           Responsive Design Verification, Viewport Testing
 * Tools:            Playwright, TypeScript
 *
 * The unit suite asserts the *classes*; only a real browser resolves them. These four widths are the
 * ones `spa-design-guidelines.md` §5 writes the table for, and each is a different layout rather than
 * one layout scaled — which is exactly the kind of claim a class-name assertion cannot make.
 *
 * `responsive_hitl.spec.ts` owns the sticky-bar geometry at 375px. This spec owns the grid, the
 * console heights, and the drawer.
 */

import { expect, test, type Page } from '@playwright/test'

import { decideFromPlanModal, openPlanModal, openWarRoom, resetStack, triggerAndAwaitGate, waitForState } from './helpers'

const VIEWPORTS = {
  desktop: { width: 1440, height: 900 },
  laptop: { width: 1024, height: 768 },
  tablet: { width: 768, height: 1024 },
  phone: { width: 375, height: 812 },
} as const

/** Reads a box height, which is what the fixed-height console contract is actually about. */
async function heightOf(page: Page, testId: string): Promise<number> {
  const box = await page.getByTestId(testId).first().boundingBox()
  expect(box, `${testId} must be laid out`).not.toBeNull()
  return box!.height
}

/** Columns in the workflow grid, counted from resolved x-offsets rather than from class names. */
async function workflowColumns(page: Page): Promise<number> {
  return page.getByTestId('workflow-grid').evaluate((grid) => {
    const tops = new Set<number>()
    const lefts = new Set<number>()
    for (const child of Array.from(grid.children)) {
      const box = child.getBoundingClientRect()
      tops.add(Math.round(box.top))
      lefts.add(Math.round(box.left))
    }
    // Items sharing the topmost row, counted by distinct left edges.
    const firstRow = Math.min(...tops)
    return Array.from(grid.children).filter((child) => Math.round(child.getBoundingClientRect().top) === firstRow)
      .length && lefts.size >= 1
      ? Array.from(grid.children).filter(
          (child) => Math.round(child.getBoundingClientRect().top) === firstRow,
        ).length
      : 0
  })
}

test.describe('Documented layouts', () => {
  test.beforeEach(async ({ request }) => {
    await resetStack(request)
  })

  test.afterEach(async ({ request }) => {
    await resetStack(request)
  })

  test('1440×900 — split hero, two workflow columns, 480px consoles', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.desktop)
    await openWarRoom(page)

    // Two, not three: the AI plan moved out of this grid into the decision pair.
    expect(await workflowColumns(page)).toBe(2)
    // The 1280px band. Total frame height, chrome included, is the contract.
    expect(await heightOf(page, 'log-sanitizer')).toBe(480)
  })

  test('orders the page: state, impact, charts, decision, then the output streams', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.desktop)
    await openWarRoom(page)
    await triggerAndAwaitGate(page, 'db_pool_exhaustion')

    const tops = await page.evaluate(() =>
      ['run-status', 'scenario-impact-strip', 'golden-signals', 'decision-grid', 'workflow-grid'].map((id) => {
        const node = document.querySelector(`[data-testid="${id}"]`)
        return node ? Math.round(node.getBoundingClientRect().top) : Number.NaN
      }),
    )

    expect(tops.some(Number.isNaN)).toBe(false)
    expect(tops).toEqual([...tops].sort((a, b) => a - b))
  })

  test('the plan and worker consoles are a pair, and absent until a run exists', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.desktop)
    await openWarRoom(page)

    await expect(page.getByTestId('decision-grid')).toHaveCount(0)
    await expect(page.getByTestId('agent-reasoning')).toHaveCount(0)
    await expect(page.getByTestId('execution-terminal')).toHaveCount(0)

    await triggerAndAwaitGate(page, 'db_pool_exhaustion')

    // Both, side by side, on the same row.
    const plan = (await page.getByTestId('agent-reasoning').boundingBox())!
    const worker = (await page.getByTestId('execution-terminal').boundingBox())!
    expect(Math.round(plan.y)).toBe(Math.round(worker.y))
    expect(worker.x).toBeGreaterThan(plan.x)
    expect(Math.round(worker.height)).toBe(Math.round(plan.height))
  })

  test('gives all four consoles one frame, body, and footer height', async ({ page }) => {
    // The thing an eye actually compares in a 2×2 grid. Only a browser resolves it: the classes are
    // shared, but whether they *resolve* to one height depends on the cascade.
    await page.setViewportSize(VIEWPORTS.desktop)
    await openWarRoom(page)
    await triggerAndAwaitGate(page, 'db_pool_exhaustion')

    const boxes = await page.evaluate(() =>
      Array.from(document.querySelectorAll('[data-testid="console-frame"]')).map((frame) => ({
        frame: Math.round(frame.getBoundingClientRect().height),
        body: Math.round(frame.querySelector('[role="group"]')!.getBoundingClientRect().height),
        footer: Math.round(
          frame.querySelector('[data-testid="console-frame-footer"]')!.getBoundingClientRect().height,
        ),
      })),
    )

    expect(boxes).toHaveLength(4)
    expect(new Set(boxes.map((box) => box.frame)).size, 'frame heights').toBe(1)
    expect(new Set(boxes.map((box) => box.body)).size, 'body heights').toBe(1)
    expect(new Set(boxes.map((box) => box.footer)).size, 'footer heights').toBe(1)
    expect(boxes[0].frame).toBe(480)
    expect(boxes[0].footer).toBe(96)
  })

  test('an open disclosure grows a workflow console downward', async ({ page }) => {
    // Only a real browser can see this. The classes said "minimum 420px" while the cascade resolved
    // `min-height: 0` — twMerge cannot tell a custom `min-h-*` key from `min-h-0`, so both were
    // emitted and the stylesheet decided. The frame *shrank* when its disclosure opened, and every
    // class-level assertion still passed.
    await page.setViewportSize(VIEWPORTS.desktop)
    await openWarRoom(page)

    const frame = page.getByTestId('log-sanitizer').getByTestId('console-frame')
    const collapsed = (await frame.boundingBox())!.height
    expect(Math.round(collapsed)).toBe(480)

    await page.getByTestId('log-sanitizer').getByTestId('technical-details').locator('summary').click()
    await expect(frame).toHaveAttribute('data-console-expanded', 'true')

    const expanded = (await frame.boundingBox())!.height
    expect(expanded, 'the console must not shrink when its disclosure opens').toBeGreaterThanOrEqual(
      collapsed,
    )
    // And it grows in place rather than scrolling inside its own footer.
    const footerOverflow = await page
      .getByTestId('log-sanitizer')
      .getByTestId('console-frame-footer')
      .evaluate((node) => getComputedStyle(node).overflowY)
    expect(footerOverflow).toBe('visible')
  })

  test('1024×768 — two workflow columns, 420px consoles', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.laptop)
    await openWarRoom(page)

    expect(await workflowColumns(page)).toBe(2)
    expect(await heightOf(page, 'log-sanitizer')).toBe(420)
  })

  test('768×1024 — logs and retrieval share a row, 420px consoles', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.tablet)
    await openWarRoom(page)

    expect(await workflowColumns(page)).toBe(2)
    expect(await heightOf(page, 'log-sanitizer')).toBe(420)
  })

  test('375×812 — one visual column in pipeline order, 360px consoles', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.phone)
    await openWarRoom(page)

    expect(await workflowColumns(page)).toBe(1)
    expect(await heightOf(page, 'log-sanitizer')).toBe(360)

    // DOM order is now also the visual order — no CSS `order` needed, because the plan left this
    // grid and its approval controls sit above the charts, ahead of both focusable streams.
    // The grid's children *are* the console sections now — the plan's departure took the wrapper
    // divs that carried its CSS `order` with it — so the testid is read off the child itself.
    const order = await page.getByTestId('workflow-grid').evaluate((grid) =>
      Array.from(grid.children)
        .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top)
        .map((child) => child.getAttribute('data-testid')),
    )
    expect(order).toEqual(['log-sanitizer', 'rag-inspector'])
  })

  for (const [name, viewport] of Object.entries(VIEWPORTS)) {
    test(`exactly one authorize control at ${name}`, async ({ page }) => {
      // The rule the whole demo depends on. Two authorize buttons in the tab order is worse than one
      // in the wrong place, and a `getByTestId` that matches two is an ambiguous locator everywhere.
      await page.setViewportSize(viewport)
      await openWarRoom(page)
      await triggerAndAwaitGate(page, 'db_pool_exhaustion')

      // One way in, and one dialog behind it — so exactly one authorize control can ever exist.
      await expect(page.getByTestId('show-plan'), `${name} plan trigger count`).toHaveCount(1)
      await openPlanModal(page)
      await expect(page.getByTestId('authorize-remediation'), `${name} authorize count`).toHaveCount(1)
      await expect(page.getByTestId('reject-remediation')).toHaveCount(1)
      await page.getByTestId('plan-modal-close').click()
      // Below 768px it is the sticky bar; at and above it, the plan footer. Never both.
      const stickyBars = await page.getByTestId('mobile-hitl-bar').count()
      expect(stickyBars, `${name} sticky bar`).toBe(viewport.width < 768 ? 1 : 0)
    })
  }

  test('the postmortem drawer is a 480px panel on desktop and a full-screen sheet on a phone', async ({
    page,
  }) => {
    await page.setViewportSize(VIEWPORTS.desktop)
    await openWarRoom(page)
    await triggerAndAwaitGate(page, 'db_pool_exhaustion')
    await decideFromPlanModal(page, true)

    const drawer = page.getByTestId('postmortem-modal')
    await expect(drawer).toBeVisible({ timeout: 30_000 })
    const desktopBox = await drawer.boundingBox()
    expect(Math.round(desktopBox!.width)).toBe(480)
    // Anchored to the right edge and full height.
    expect(Math.round(desktopBox!.x + desktopBox!.width)).toBe(VIEWPORTS.desktop.width)

    await page.setViewportSize(VIEWPORTS.phone)
    const phoneBox = await drawer.boundingBox()
    expect(Math.round(phoneBox!.width), 'a 480px drawer on a 375px phone is a horizontal scrollbar').toBe(
      VIEWPORTS.phone.width,
    )

    await page.getByTestId('postmortem-close').click()
    await expect(drawer).toHaveCount(0)
    await waitForState(page, ['HEALTHY'], 15_000)
  })
})
