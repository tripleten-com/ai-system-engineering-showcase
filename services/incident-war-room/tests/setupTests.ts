/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        setupTests.ts
 * Purpose:          Vitest setup — jest-dom matchers and the JSDOM gaps Recharts and the SSE
 *                   client need.
 * Interacts With:   JSDOM, every test file
 *
 * Curriculum Project: Project 1 — Diagnostics & Telemetry
 * Skills:           Test Environment Configuration, JSDOM
 * Tools:            Vitest, Testing Library
 */

// The `/vitest` entry point, not the bare package: it registers the matchers *and* augments
// Vitest's `Assertion` interface, so `toBeInTheDocument()` typechecks instead of only running.
import '@testing-library/jest-dom/vitest'

import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

// JSDOM has no layout engine, so every element measures 0x0. The charts are sized by
// `useElementWidth`, which reads `clientWidth` and an observer entry's `contentRect` — so both are
// stubbed here, and a sparkline that renders nothing in JSDOM would also render nothing in a
// browser. That equivalence is the point: the previous stub made the tests pass through a code path
// the browser never took, and hid a chart that drew nothing in production for a whole stage.
const STUB_WIDTH = 240
const STUB_HEIGHT = 40

class ResizeObserverStub {
  constructor(private readonly callback: ResizeObserverCallback) {}

  observe(target: Element): void {
    // Reports a size immediately. Recharts reads `contentRect` off the entry, and an observer that
    // never fires leaves the container at 0×0 no matter what the element claims to measure.
    this.callback(
      [{ target, contentRect: { width: STUB_WIDTH, height: STUB_HEIGHT } } as unknown as ResizeObserverEntry],
      this as unknown as ResizeObserver,
    )
  }

  unobserve(): void {}
  disconnect(): void {}
}

globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver

/**
 * JSDOM implements no media queries at all, so `useMediaQuery` would throw on `matchMedia`. The
 * stub reports *no* match by default, which puts every test on the desktop layout — the same
 * fallback the hook itself chooses when `matchMedia` is missing. A test that wants the mobile
 * layout overrides `window.matchMedia` for its own duration.
 */
window.matchMedia ??= ((query: string) => ({
  matches: false,
  media: query,
  onchange: null,
  addEventListener: () => {},
  removeEventListener: () => {},
  addListener: () => {},
  removeListener: () => {},
  dispatchEvent: () => false,
})) as unknown as typeof window.matchMedia

// The observer covers ResponsiveContainer; these cover everything else that measures itself.
Object.defineProperties(HTMLElement.prototype, {
  clientWidth: { configurable: true, get: () => STUB_WIDTH },
  clientHeight: { configurable: true, get: () => STUB_HEIGHT },
  offsetWidth: { configurable: true, get: () => STUB_WIDTH },
  offsetHeight: { configurable: true, get: () => STUB_HEIGHT },
})

afterEach(() => {
  cleanup()
})
