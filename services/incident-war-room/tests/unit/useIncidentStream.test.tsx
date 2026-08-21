/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        useIncidentStream.test.tsx
 * Purpose:          Tests the reducer that every panel renders from — channel demultiplexing, the
 *                   run-change reset, and the metric history that survives it.
 * Interacts With:   hooks/useIncidentStream.ts, services/sseClient.ts, services/api.ts
 *
 * Curriculum Project: Project 1 — Diagnostics & Telemetry
 * Skills:           State Reduction, Event Streaming, Test Doubles
 * Tools:            Vitest, React Testing Library
 *
 * Driven through a fake `connectIncidentStream` rather than a fake `EventSource`, because the unit
 * under test is the reduction, not the transport. The transport has its own coverage in the
 * `sse_reconnect` Playwright spec, where a real network can actually be severed.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'

import * as api from '../../src/services/api'
import * as sseClient from '../../src/services/sseClient'
import { useIncidentStream } from '../../src/hooks/useIncidentStream'
import {
  EventType,
  ScenarioId,
  type IncidentEvent,
  type IncidentState,
} from '../../src/types/contracts.gen'
import { BASELINE_INFRA, BASELINE_SIGNALS, OUTAGE_SIGNALS, RB_104_MATCH, thought, workerEntry } from '../fixtures'

let emit: (event: IncidentEvent) => void
let setConnection: (state: sseClient.ConnectionState, attempt: number) => void

function metricsFrame(incidentId: string | null, status: IncidentState, p99: number): IncidentEvent {
  return {
    event_id: `metrics-${incidentId}-${p99}`,
    incident_id: incidentId,
    timestamp: '2026-08-20T11:00:00Z',
    type: EventType.METRICS_UPDATE,
    data: {
      status,
      golden_signals: { ...BASELINE_SIGNALS, latency_p99_ms: p99 },
      infrastructure: BASELINE_INFRA,
    },
  }
}

beforeEach(() => {
  vi.spyOn(api, 'fetchSnapshot').mockResolvedValue({
    incident_id: null,
    thread_id: null,
    scenario_id: null,
    state: 'HEALTHY',
    timestamp: '2026-08-20T11:00:00Z',
    golden_signals: BASELINE_SIGNALS,
    infrastructure: BASELINE_INFRA,
  })

  vi.spyOn(sseClient, 'connectIncidentStream').mockImplementation(({ onEvent, onConnectionChange }) => {
    emit = onEvent
    setConnection = onConnectionChange
    onConnectionChange('open', 0)
    return () => {}
  })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useIncidentStream', () => {
  it('leaves the cold-start window once the snapshot lands', async () => {
    const { result } = renderHook(() => useIncidentStream())

    expect(result.current.loading).toBe(true)
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.goldenSignals?.latency_p99_ms).toBe(48)
  })

  it('demultiplexes all five channels off the single message event', async () => {
    const { result } = renderHook(() => useIncidentStream())
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      emit(metricsFrame('inc-1', 'CRITICAL_OUTAGE', 4820))
      emit({
        event_id: 'log-1',
        incident_id: 'inc-1',
        timestamp: '2026-08-20T11:00:01Z',
        type: EventType.LOG_STREAM,
        data: { message: 'Connection pool saturated.', sanitized: true },
      })
      emit({
        event_id: 'rag-1',
        incident_id: 'inc-1',
        timestamp: '2026-08-20T11:00:02Z',
        type: EventType.RAG_MATCH,
        data: RB_104_MATCH,
      })
      emit({
        event_id: 'thought-1',
        incident_id: 'inc-1',
        timestamp: '2026-08-20T11:00:03Z',
        type: EventType.AGENT_THOUGHT,
        data: thought({ id: 'ignored', step: 1 }),
      })
      emit({
        event_id: 'worker-1',
        incident_id: 'inc-1',
        timestamp: '2026-08-20T11:00:04Z',
        type: EventType.WORKER_LOG,
        data: workerEntry({ id: 'ignored' }),
      })
    })

    expect(result.current.state).toBe('CRITICAL_OUTAGE')
    expect(result.current.incidentId).toBe('inc-1')
    expect(result.current.logs).toHaveLength(1)
    expect(result.current.ragMatches).toHaveLength(1)
    expect(result.current.thoughts).toHaveLength(1)
    expect(result.current.workerLogs).toHaveLength(1)
  })

  it('replaces the RAG match rather than appending to it', async () => {
    // The contract emits one match per retrieval, and the panel shows the match for the run in
    // flight. Appending would stack a re-retrieval on top of the first one.
    const { result } = renderHook(() => useIncidentStream())
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      emit({
        event_id: 'rag-1',
        incident_id: 'inc-1',
        timestamp: '2026-08-20T11:00:02Z',
        type: EventType.RAG_MATCH,
        data: RB_104_MATCH,
      })
      emit({
        event_id: 'rag-2',
        incident_id: 'inc-1',
        timestamp: '2026-08-20T11:00:03Z',
        type: EventType.RAG_MATCH,
        data: { ...RB_104_MATCH, cosine_similarity: 0.8 },
      })
    })

    expect(result.current.ragMatches).toHaveLength(1)
    expect(result.current.ragMatches[0].cosine_similarity).toBe(0.8)
  })

  it('clears the derived panels when the run changes', async () => {
    // Without this, a Master Reset followed by a new scenario would render the previous incident's
    // reasoning chain beside the new one's telemetry.
    const { result } = renderHook(() => useIncidentStream())
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      emit(metricsFrame('inc-1', 'CRITICAL_OUTAGE', 4820))
      emit({
        event_id: 'log-1',
        incident_id: 'inc-1',
        timestamp: '2026-08-20T11:00:01Z',
        type: EventType.LOG_STREAM,
        data: { message: 'first run', sanitized: true },
      })
    })
    expect(result.current.logs).toHaveLength(1)

    act(() => emit(metricsFrame('inc-2', 'CRITICAL_OUTAGE', 4820)))

    expect(result.current.incidentId).toBe('inc-2')
    expect(result.current.logs).toHaveLength(0)
    expect(result.current.ragMatches).toHaveLength(0)
    expect(result.current.thoughts).toHaveLength(0)
    expect(result.current.workerLogs).toHaveLength(0)
  })

  it('keeps a finished run on screen when the incident id returns to null', async () => {
    // The regression this exists for: once a run completes, the server emits frames with
    // `incident_id: null` again. Treating that as a run change wiped the worker log and the
    // postmortem link at the exact moment the demo pays off.
    const { result } = renderHook(() => useIncidentStream())
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      emit(metricsFrame('inc-1', 'EXECUTING', 4820))
      emit({
        event_id: 'worker-1',
        incident_id: 'inc-1',
        timestamp: '2026-08-20T11:00:04Z',
        type: EventType.WORKER_LOG,
        data: workerEntry({ id: 'ignored' }),
      })
    })
    expect(result.current.workerLogs).toHaveLength(1)

    act(() => emit(metricsFrame(null, 'HEALTHY', 48)))

    expect(result.current.state).toBe('HEALTHY')
    expect(result.current.incidentId).toBeNull()
    expect(result.current.workerLogs, 'the finished run keeps its evidence').toHaveLength(1)
    expect(result.current.lastRunId).toBe('inc-1')
  })

  it('blanks the panels only when Master Reset asks it to', async () => {
    const { result } = renderHook(() => useIncidentStream())
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      emit(metricsFrame('inc-1', 'REJECTED', 4820))
      emit({
        event_id: 'log-1',
        incident_id: 'inc-1',
        timestamp: '2026-08-20T11:00:01Z',
        type: EventType.LOG_STREAM,
        data: { message: 'held', sanitized: true },
      })
    })
    expect(result.current.logs).toHaveLength(1)

    act(() => result.current.clearRun())

    expect(result.current.logs).toHaveLength(0)
    expect(result.current.incidentId).toBeNull()
    expect(result.current.lastRunId).toBeNull()
    // The sparklines are still showing the same platform, so their history survives.
    expect(result.current.history.latency_p99_ms.length).toBeGreaterThan(0)
  })

  it('keeps the metric history across a run change', async () => {
    // The sparklines are a rolling window on the platform, not on the incident. Clearing them at
    // every trigger would erase the baseline the spike is meant to be read against.
    const { result } = renderHook(() => useIncidentStream())
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      emit(metricsFrame(null, 'HEALTHY', 47))
      emit(metricsFrame(null, 'HEALTHY', 48))
    })
    const beforeTrigger = result.current.history.latency_p99_ms.length

    act(() => emit(metricsFrame('inc-1', 'CRITICAL_OUTAGE', 4820)))

    expect(result.current.history.latency_p99_ms).toHaveLength(beforeTrigger + 1)
    expect(result.current.history.latency_p99_ms.at(-1)).toBe(4820)
  })

  it('windows the history to the sparkline length', async () => {
    const { result } = renderHook(() => useIncidentStream())
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      for (let index = 0; index < 80; index += 1) emit(metricsFrame(null, 'HEALTHY', 40 + index))
    })

    expect(result.current.history.latency_p99_ms).toHaveLength(60)
    expect(result.current.history.latency_p99_ms[0]).toBe(60)
  })

  it('reports staleness and rehydrates when the stream comes back', async () => {
    const { result } = renderHook(() => useIncidentStream())
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.stale).toBe(false)

    act(() => setConnection('reconnecting', 2))
    expect(result.current.stale).toBe(true)
    expect(result.current.reconnectAttempt).toBe(2)

    vi.mocked(api.fetchSnapshot).mockResolvedValue({
      incident_id: 'inc-9',
      thread_id: 'thread-9',
      scenario_id: ScenarioId.WORKER_DEADLOCK,
      state: 'AWAITING_APPROVAL',
      timestamp: '2026-08-20T11:05:00Z',
      golden_signals: OUTAGE_SIGNALS,
      infrastructure: BASELINE_INFRA,
    })

    act(() => setConnection('open', 0))

    // The server keeps no replay buffer, so a client that missed frames rebuilds from the snapshot
    // — including the three identifiers the authorize call needs.
    await waitFor(() => expect(result.current.threadId).toBe('thread-9'))
    expect(result.current.state).toBe('AWAITING_APPROVAL')
    expect(result.current.scenarioId).toBe(ScenarioId.WORKER_DEADLOCK)
    expect(result.current.stale).toBe(false)
  })

  it('holds the last known values when a snapshot fetch fails', async () => {
    const { result } = renderHook(() => useIncidentStream())
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => emit(metricsFrame('inc-1', 'CRITICAL_OUTAGE', 4820)))
    vi.mocked(api.fetchSnapshot).mockRejectedValue(new Error('network down'))

    await act(async () => {
      await result.current.rehydrate()
    })

    // Blanking the panels would be worse than showing the last frame with the disconnected strip up.
    expect(result.current.goldenSignals?.latency_p99_ms).toBe(4820)
    expect(result.current.state).toBe('CRITICAL_OUTAGE')
  })
})

describe('envelope timestamps', () => {
  /** One instant per channel, so a field read from the wrong envelope is visible. */
  const AT = {
    log: '2026-08-20T11:03:07.100000+00:00',
    rag: '2026-08-20T11:03:11.200000+00:00',
    thought: '2026-08-20T11:03:14.300000+00:00',
    worker: '2026-08-20T11:03:22.400000+00:00',
  }

  it('carries the server timestamp onto every entry model', async () => {
    // The consoles stamp entries `[mm:ss]`, and the only defensible source is the instant the API
    // recorded. A client-side `Date.now()` would measure receipt, so a reconnect or a backgrounded
    // tab would restamp history.
    const { result } = renderHook(() => useIncidentStream())
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      emit({
        event_id: 'log-ts',
        incident_id: 'inc-ts',
        timestamp: AT.log,
        type: EventType.LOG_STREAM,
        data: { message: 'Connection pool saturated.', sanitized: true },
      })
      emit({
        event_id: 'rag-ts',
        incident_id: 'inc-ts',
        timestamp: AT.rag,
        type: EventType.RAG_MATCH,
        data: RB_104_MATCH,
      })
      emit({
        event_id: 'thought-ts',
        incident_id: 'inc-ts',
        timestamp: AT.thought,
        type: EventType.AGENT_THOUGHT,
        data: thought({ id: 'ignored', step: 1 }),
      })
      emit({
        event_id: 'worker-ts',
        incident_id: 'inc-ts',
        timestamp: AT.worker,
        type: EventType.WORKER_LOG,
        data: workerEntry({ id: 'ignored' }),
      })
    })

    expect(result.current.logs[0].timestamp).toBe(AT.log)
    expect(result.current.ragMatches[0].timestamp).toBe(AT.rag)
    expect(result.current.thoughts[0].timestamp).toBe(AT.thought)
    expect(result.current.workerLogs[0].timestamp).toBe(AT.worker)
  })

  it('gives the retrieval match the envelope id rather than reusing the runbook id', async () => {
    // The probe can return the same runbook the live run matched, so keying on `runbook_id` would
    // collide. The event id is unique by construction.
    const { result } = renderHook(() => useIncidentStream())
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() =>
      emit({
        event_id: 'rag-unique',
        incident_id: 'inc-ts',
        timestamp: AT.rag,
        type: EventType.RAG_MATCH,
        data: RB_104_MATCH,
      }),
    )

    expect(result.current.ragMatches[0].id).toBe('rag-unique')
    expect(result.current.ragMatches[0].runbook_id).toBe(RB_104_MATCH.runbook_id)
  })
})

describe('the finished run’s scenario', () => {
  it('remembers the last scenario after the server stops reporting one', async () => {
    // The completed-state copy needs it: "Database capacity is restored" is the sentence the visitor
    // needs exactly when `scenario_id` has gone back to null.
    const { result } = renderHook(() => useIncidentStream())
    await waitFor(() => expect(result.current.loading).toBe(false))

    vi.mocked(api.fetchSnapshot).mockResolvedValue({
      incident_id: 'inc-run',
      thread_id: 'thread-inc-run',
      scenario_id: ScenarioId.DB_POOL_EXHAUSTION,
      state: 'RECOVERING',
      timestamp: '2026-08-20T11:04:00Z',
      golden_signals: BASELINE_SIGNALS,
      infrastructure: BASELINE_INFRA,
    })
    await act(async () => {
      await result.current.rehydrate()
    })
    expect(result.current.scenarioId).toBe(ScenarioId.DB_POOL_EXHAUSTION)

    vi.mocked(api.fetchSnapshot).mockResolvedValue({
      incident_id: null,
      thread_id: null,
      scenario_id: null,
      state: 'HEALTHY',
      timestamp: '2026-08-20T11:04:10Z',
      golden_signals: BASELINE_SIGNALS,
      infrastructure: BASELINE_INFRA,
    })
    await act(async () => {
      await result.current.rehydrate()
    })

    expect(result.current.scenarioId).toBeNull()
    expect(result.current.lastScenarioId).toBe(ScenarioId.DB_POOL_EXHAUSTION)
  })

  it('forgets it on an explicit Master Reset', async () => {
    // The one case where the viewer really did ask for a blank slate.
    vi.mocked(api.fetchSnapshot).mockResolvedValue({
      incident_id: 'inc-reset',
      thread_id: 'thread-inc-reset',
      scenario_id: ScenarioId.WORKER_DEADLOCK,
      state: 'REJECTED',
      timestamp: '2026-08-20T11:05:00Z',
      golden_signals: BASELINE_SIGNALS,
      infrastructure: BASELINE_INFRA,
    })
    const { result } = renderHook(() => useIncidentStream())
    await waitFor(() => expect(result.current.lastScenarioId).toBe(ScenarioId.WORKER_DEADLOCK))

    act(() => result.current.clearRun())

    expect(result.current.lastScenarioId).toBeNull()
    expect(result.current.scenarioId).toBeNull()
  })
})
