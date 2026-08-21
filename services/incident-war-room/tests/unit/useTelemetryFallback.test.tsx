/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        useTelemetryFallback.test.tsx
 * Purpose:          Tests the polling fallback — that it runs only while disconnected, serialises
 *                   its requests, and survives a rejection.
 * Interacts With:   hooks/useTelemetryFallback.ts
 *
 * Curriculum Project: Project 1 — Diagnostics & Telemetry
 * Skills:           Polling Fallback, Fake Timers, Effect Lifecycle
 * Tools:            Vitest, React Testing Library
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'

import { FALLBACK_POLL_MS, useTelemetryFallback } from '../../src/hooks/useTelemetryFallback'

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

/** Advances the fake clock and lets the awaited poll settle inside `act`. */
async function tick(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

describe('useTelemetryFallback', () => {
  it('does not poll while the stream is healthy', async () => {
    // Running alongside a live stream would double the request rate and interleave two sources into
    // one history — which is how a sparkline ends up with duplicated samples and a wrong slope.
    const poll = vi.fn().mockResolvedValue(undefined)
    renderHook(() => useTelemetryFallback({ active: false, poll }))

    await tick(FALLBACK_POLL_MS * 5)

    expect(poll).not.toHaveBeenCalled()
  })

  it('polls at the SSE cadence while disconnected', async () => {
    const poll = vi.fn().mockResolvedValue(undefined)
    renderHook(() => useTelemetryFallback({ active: true, poll }))

    await tick(FALLBACK_POLL_MS)
    expect(poll).toHaveBeenCalledTimes(1)

    await tick(FALLBACK_POLL_MS)
    expect(poll).toHaveBeenCalledTimes(2)
  })

  it('waits for the previous poll before starting the next', async () => {
    // A backlog of in-flight snapshots can resolve out of order, which would make the tiles jump
    // backwards in time.
    let release: () => void = () => {}
    const poll = vi.fn(() => new Promise<void>((resolve) => {
      release = resolve
    }))

    renderHook(() => useTelemetryFallback({ active: true, poll }))

    await tick(FALLBACK_POLL_MS)
    expect(poll).toHaveBeenCalledTimes(1)

    await tick(FALLBACK_POLL_MS * 3)
    expect(poll).toHaveBeenCalledTimes(1)

    await act(async () => {
      release()
    })
    await tick(FALLBACK_POLL_MS)
    expect(poll).toHaveBeenCalledTimes(2)
  })

  it('keeps polling after a rejection', async () => {
    // A rejection that broke the scheduling chain would silently stop the fallback, leaving the UI
    // frozen behind a reconnecting strip that never resolves.
    const poll = vi.fn().mockRejectedValue(new Error('offline'))
    renderHook(() => useTelemetryFallback({ active: true, poll }))

    await tick(FALLBACK_POLL_MS * 3)

    expect(poll.mock.calls.length).toBeGreaterThanOrEqual(3)
  })

  it('stops polling when the stream recovers', async () => {
    const poll = vi.fn().mockResolvedValue(undefined)
    const { rerender } = renderHook((props: { active: boolean }) => useTelemetryFallback({ ...props, poll }), {
      initialProps: { active: true },
    })

    await tick(FALLBACK_POLL_MS)
    const whileDown = poll.mock.calls.length

    rerender({ active: false })
    await tick(FALLBACK_POLL_MS * 5)

    expect(poll.mock.calls.length).toBe(whileDown)
  })

  it('stops polling on unmount', async () => {
    const poll = vi.fn().mockResolvedValue(undefined)
    const { unmount } = renderHook(() => useTelemetryFallback({ active: true, poll }))

    await tick(FALLBACK_POLL_MS)
    const beforeUnmount = poll.mock.calls.length

    unmount()
    await tick(FALLBACK_POLL_MS * 5)

    expect(poll.mock.calls.length).toBe(beforeUnmount)
  })
})
