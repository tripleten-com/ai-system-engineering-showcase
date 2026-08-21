/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        hooks/useTelemetryFallback.ts
 * Purpose:          Polls GET /api/telemetry/current while the SSE stream is down, so the metric
 *                   tiles keep moving instead of freezing at the last frame.
 * Interacts With:   services/api.ts, incident-agent-api (:8000)
 *
 * Curriculum Project: Project 1 — Diagnostics & Telemetry
 * Skills:           Polling Fallback, Resilient UI, Effect Lifecycle
 * Tools:            React 18, TypeScript
 *
 * The telemetry doc names `/api/telemetry/current` the polling fallback, and this is that
 * fallback. It matters more than it looks: a corporate proxy that buffers `text/event-stream` will
 * never deliver a frame, and without this the whole demo would be a still image behind a
 * reconnecting strip.
 *
 * **It polls only while disconnected.** Running it alongside a healthy stream would double the
 * request rate and interleave two sources into one history, which is how a sparkline ends up with
 * duplicated samples and a visibly wrong slope.
 *
 * The disconnected strip stays up the whole time this is running. Polling is a degraded mode and
 * the UI says so — replacing the warning with silently-polled data would be the same lie the
 * disconnected state exists to prevent.
 */

import { useEffect } from 'react'

/** 1 Hz, matching the SSE cadence, so the sparkline sample spacing does not change. */
export const FALLBACK_POLL_MS = 1_000

export interface TelemetryFallbackOptions {
  /** True while the stream is not delivering. Polling runs only then. */
  active: boolean
  /** Fetches and dispatches a snapshot — `rehydrate` from `useIncidentStream`. */
  poll: () => Promise<void>
  /** Overridable for tests; production always uses the 1 Hz default. */
  intervalMs?: number
}

/**
 * Polls `poll` on an interval while `active`.
 *
 * Serialised, not fire-and-forget on a timer: a slow response must not let a second request start
 * before the first finishes, or a backlog of in-flight snapshots resolves out of order and the
 * tiles jump backwards in time. So the next tick is scheduled after the previous one settles.
 */
export function useTelemetryFallback({ active, poll, intervalMs = FALLBACK_POLL_MS }: TelemetryFallbackOptions): void {
  useEffect(() => {
    if (!active) return

    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const tick = async () => {
      try {
        await poll()
      } catch {
        // `poll` already swallows its own failures; this is belt-and-braces so a rejection
        // cannot break the scheduling chain and silently stop the fallback.
      }
      if (!cancelled) timer = setTimeout(tick, intervalMs)
    }

    timer = setTimeout(tick, intervalMs)

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [active, poll, intervalMs])
}
