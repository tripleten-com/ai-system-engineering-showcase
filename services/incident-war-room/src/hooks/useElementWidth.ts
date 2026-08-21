/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        hooks/useElementWidth.ts
 * Purpose:          Measures an element's width and keeps it current, so a chart can be given
 *                   explicit pixel dimensions instead of percentages.
 * Interacts With:   components/ui/index.tsx (Sparkline)
 *
 * Curriculum Project: Project 1 — Diagnostics & Telemetry
 * Skills:           Layout Measurement, Resilient UI
 * Tools:            React 18, ResizeObserver
 *
 * This exists because of a bug worth recording. The sparklines were built on Recharts'
 * `ResponsiveContainer`, which sizes itself from its own `ResizeObserver` and renders `null` until
 * that observer reports a positive box. In this build it never did: the container div mounted with
 * a correct 247x40 layout box and stayed empty — no `<svg>`, no gradient, no line, and no console
 * error to say so. Every metric tile showed its number above a blank strip.
 *
 * Nothing caught it. The unit tests stub `ResizeObserver`, so they exercised a code path the browser
 * never took; the E2E specs assert on `data-testid="sparkline"`, which is the *wrapper* and was
 * present either way. A chart that silently renders nothing is exactly the failure a live dashboard
 * cannot afford, so the measurement is now ours: one observer, one number, and an `AreaChart` with
 * explicit `width` and `height`. The height is fixed by design, so width is all there is to track.
 */

import { useEffect, useRef, useState, type RefObject } from 'react'

/**
 * Returns a ref to attach and the element's current content width in pixels.
 *
 * Zero until the first measurement, which callers must treat as "not ready yet" rather than as a
 * real width — that ambiguity is precisely what made the original failure invisible.
 */
export function useElementWidth<T extends HTMLElement>(): [RefObject<T>, number] {
  const ref = useRef<T>(null)
  const [width, setWidth] = useState(0)

  useEffect(() => {
    const element = ref.current
    if (!element) return

    // Measured synchronously first. `ResizeObserver` does fire on observe, but reading the layout
    // box directly means the first paint already has a width rather than waiting a frame for one.
    setWidth(element.clientWidth)

    if (typeof ResizeObserver === 'undefined') return

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (!entry) return
      // `contentRect` where available, falling back to the live layout box — a stubbed observer in
      // a test environment may report neither, and a zero width must not blank a working chart.
      const next = entry.contentRect?.width || element.clientWidth
      if (next > 0) setWidth(next)
    })

    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  return [ref, width]
}
