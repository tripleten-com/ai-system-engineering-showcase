/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        ui.test.tsx
 * Purpose:          Tests the primitives' own behaviour — the Terminal's auto-scroll and
 *                   pause-on-hover, and the Sparkline's per-instance gradient id.
 * Interacts With:   components/ui/index.tsx
 *
 * Curriculum Project: Cross-cutting — Production Aesthetics
 * Skills:           Design Systems, Component Testing, Accessibility
 * Tools:            Vitest, React Testing Library
 *
 * These are behaviours the feature components rely on and cannot see. When the log tail and the
 * execution terminal each rendered their own `overflow-y-auto` div, both silently lost the pause —
 * which is a defect you only notice by trying to read a streaming log.
 */

import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { Panel, Sparkline, Terminal } from '../../src/components/ui'
import { CHART_DOMAINS } from '../../src/theme/tokens'

describe('Terminal', () => {
  it('shows the empty copy instead of its children when there is nothing to show', () => {
    render(
      <Terminal label="log" testId="term" emptyCopy="nothing yet" isEmpty scrollKey={0}>
        <p>a line</p>
      </Terminal>,
    )

    expect(screen.getByTestId('term')).toHaveTextContent('nothing yet')
    expect(screen.getByTestId('term')).not.toHaveTextContent('a line')
  })

  it('scrolls to the newest line when the line count changes', () => {
    const { rerender } = render(
      <Terminal label="log" testId="term" emptyCopy="" isEmpty={false} scrollKey={1}>
        <p>one</p>
      </Terminal>,
    )

    const region = screen.getByTestId('term')
    // JSDOM has no layout, so scrollHeight is 0 and the assertion is about the *assignment*
    // happening rather than about a pixel value. `scrollTop` is writable, so a scroll that never
    // ran would leave it untouched.
    Object.defineProperty(region, 'scrollHeight', { configurable: true, value: 500 })

    rerender(
      <Terminal label="log" testId="term" emptyCopy="" isEmpty={false} scrollKey={2}>
        <p>one</p>
        <p>two</p>
      </Terminal>,
    )

    expect(region.scrollTop).toBe(500)
  })

  it('pauses auto-scroll while hovered', async () => {
    // The behaviour that matters. Worker output arrives as a burst and the log tail streams at
    // 1 Hz, so a viewer who moves the cursor to read a line would have it yanked away.
    const { rerender } = render(
      <Terminal label="log" testId="term" emptyCopy="" isEmpty={false} scrollKey={1}>
        <p>one</p>
      </Terminal>,
    )

    const region = screen.getByTestId('term')
    await userEvent.hover(region)
    expect(region.dataset.paused).toBe('true')

    Object.defineProperty(region, 'scrollHeight', { configurable: true, value: 500 })
    rerender(
      <Terminal label="log" testId="term" emptyCopy="" isEmpty={false} scrollKey={2}>
        <p>one</p>
        <p>two</p>
      </Terminal>,
    )
    expect(region.scrollTop).toBe(0)

    await userEvent.unhover(region)
    expect(region.dataset.paused).toBe('false')
  })

  it('is a labelled group rather than a live region', () => {
    // `role="log"` carries an implicit `aria-live="polite"`, and §11's one explicit exception to
    // the live-region rule is exactly this content.
    render(
      <Terminal label="Streaming incident logs" testId="term" emptyCopy="" isEmpty={false} scrollKey={0}>
        <p>one</p>
      </Terminal>,
    )

    const region = screen.getByTestId('term')
    expect(region).toHaveAttribute('role', 'group')
    expect(region).toHaveAttribute('aria-label', 'Streaming incident logs')
    expect(region).not.toHaveAttribute('aria-live')
  })

  it('adds no tab stop', () => {
    // It was `tabIndex={0}` at first, which is a reasonable affordance for a scroll region in
    // general — but here it put the log tail ahead of the authorize button in the desktop tab order,
    // and §11 requires the HITL controls to come first. Nothing inside is interactive, so the trade
    // is two lost scroll stops against burying the one control that changes anything.
    render(
      <Terminal label="log" testId="term" emptyCopy="" isEmpty={false} scrollKey={0}>
        <p>one</p>
      </Terminal>,
    )

    expect(screen.getByTestId('term')).not.toHaveAttribute('tabindex')
  })
})

describe('Sparkline', () => {
  it('actually draws a chart', () => {
    // The assertion that was missing. `ResponsiveContainer` rendered `null` indefinitely in the
    // production build — a correct 247x40 wrapper with no `<svg>` inside it, no console error, and
    // a blank strip under every metric number for a whole stage. The old tests asserted on the
    // wrapper, which was present either way.
    render(<Sparkline values={[40, 45, 4820, 51]} tone="alarm" domain={CHART_DOMAINS.latency_p99_ms} />)

    const spark = screen.getByTestId('sparkline')
    expect(spark.dataset.rendered).toBe('true')
    expect(spark.querySelector('svg')).not.toBeNull()
    // The line itself, not just the surface.
    expect(spark.querySelector('.recharts-area-curve')).not.toBeNull()
    expect(spark.querySelector('linearGradient')).not.toBeNull()
  })

  it('reports that it has not drawn when it has no width to draw in', () => {
    // Zero width is "not measured yet", and it must be distinguishable from a flat line — that
    // ambiguity is what made the original failure invisible.
    const original = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth')
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => 0 })
    // The setup file's observer reports a size on `observe`, which is the whole point of it — so a
    // genuinely unmeasured element needs a silent observer too.
    const observer = globalThis.ResizeObserver
    globalThis.ResizeObserver = class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    } as unknown as typeof ResizeObserver

    try {
      render(<Sparkline values={[1, 2]} tone="healthy" domain={CHART_DOMAINS.latency_p99_ms} />)

      const spark = screen.getByTestId('sparkline')
      expect(spark.dataset.rendered).toBe('false')
      expect(spark.querySelector('svg')).toBeNull()
    } finally {
      if (original) Object.defineProperty(HTMLElement.prototype, 'clientWidth', original)
      globalThis.ResizeObserver = observer
    }
  })

  it('holds the y-domain fixed so a spike cannot be rescaled away', () => {
    // The single most common way a live dashboard lies. A 4,820ms spike and a 48ms baseline must not
    // look alike, which they would if the axis rescaled to fit whatever is on screen.
    const [, max] = CHART_DOMAINS.latency_p99_ms
    render(<Sparkline values={[48, 48, 48]} tone="healthy" domain={CHART_DOMAINS.latency_p99_ms} />)

    // A baseline-only series must not fill the frame: with a 0–5000 domain, 48ms sits at the bottom.
    const curve = screen.getByTestId('sparkline').querySelector('.recharts-area-curve')
    const points = (curve?.getAttribute('d') ?? '').match(/-?\d+(\.\d+)?/g)?.map(Number) ?? []
    const ys = points.filter((_, index) => index % 2 === 1)
    expect(ys.length).toBeGreaterThan(0)
    expect(Math.min(...ys)).toBeGreaterThan(0.5 * 40 * (1 - 48 / max))
  })

  it('gives every instance its own gradient id', () => {
    // All four golden-signal tiles share the run's tone. A tone-derived id put four elements with
    // the same `id` in the document, and every `url(#...)` would resolve to the first one parsed.
    render(
      <>
        <Sparkline values={[1, 2, 3]} tone="healthy" domain={CHART_DOMAINS.latency_p99_ms} />
        <Sparkline values={[4, 5, 6]} tone="healthy" domain={CHART_DOMAINS.latency_p99_ms} />
      </>,
    )

    const ids = [...document.querySelectorAll('linearGradient')].map((node) => node.id)
    expect(ids).toHaveLength(2)
    expect(new Set(ids).size).toBe(2)

    // And each id is a valid CSS identifier. `useId` emits `:r0:`, which resolves inside a
    // presentation attribute but throws from `querySelector` — React's docs say so explicitly.
    for (const id of ids) {
      expect(id).toMatch(/^spark-[A-Za-z0-9_-]+$/)
      expect(() => document.querySelector(`#${id}`)).not.toThrow()
    }
  })

  it('reports staleness so a stalled stream cannot look like calm', () => {
    render(<Sparkline values={[1]} tone="healthy" domain={CHART_DOMAINS.latency_p99_ms} stale />)

    const spark = screen.getByTestId('sparkline')
    expect(spark.dataset.stale).toBe('true')
    expect(spark.className).toContain('opacity-40')
  })
})

describe('Panel', () => {
  it('forwards its test id to the DOM', () => {
    // The reason `testId` is a named prop: TypeScript exempts hyphenated JSX attributes from
    // excess-property checking, so a stray `data-testid` compiles and then never renders.
    render(
      <Panel testId="a-panel" title="Title">
        <p>body</p>
      </Panel>,
    )

    expect(screen.getByTestId('a-panel')).toHaveTextContent('body')
  })
})
