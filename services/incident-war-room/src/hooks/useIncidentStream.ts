/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        hooks/useIncidentStream.ts
 * Purpose:          Turns the multiplexed SSE channel into the single state object the whole UI
 *                   renders from, including the disconnected and cold-start cases.
 * Interacts With:   services/sseClient.ts, services/api.ts, incident-agent-api (:8000)
 *
 * Curriculum Project: Project 1 — Diagnostics & Telemetry
 * Skills:           Event Streaming, State Reduction, Resilient UI
 * Tools:            React 18, TypeScript
 *
 * One reducer over five channels, because the panels are five views of one run rather than five
 * independent widgets — the agent's reasoning and the metric spike have to agree about which
 * incident they belong to, and that is only guaranteed if one place decides.
 *
 * Two properties are worth reading the code for:
 *
 * **A run change clears the derived state.** Logs, RAG matches, reasoning steps, and terminal
 * lines are per-run. Without the reset, a Master Reset followed by a new scenario would render
 * the previous incident's reasoning chain beside the new one's telemetry.
 *
 * **"Run change" means a *different* run, not the absence of one.** When a run finishes, the server
 * goes back to emitting frames with `incident_id: null` — the platform has no incident again. That
 * is not a new run, and treating it as one wiped the worker log and the postmortem link at the exact
 * moment the demo pays off. So the reset fires only on a new non-null id, and an explicit `clear`
 * covers the one case where the viewer really did ask for a blank slate: Master Reset.
 *
 * **Metric history survives a run change.** The sparklines are a rolling 60-second window on the
 * platform, not on the incident. Clearing them at every trigger would erase the baseline the
 * spike is supposed to be read against.
 */

import { useCallback, useEffect, useMemo, useReducer, useRef } from 'react'

import { connectIncidentStream, type ConnectionState } from '../services/sseClient'
import { fetchSnapshot } from '../services/api'
import { SPARKLINE_WINDOW } from '../theme/tokens'
import {
  EventType,
  WorkerLogLevel,
  type AgentThoughtPayload,
  type GoldenSignals,
  type IncidentEvent,
  type IncidentState,
  type InfrastructureMetrics,
  type LogStreamPayload,
  type RagMatchPayload,
  type ScenarioId,
  type WorkerLogPayload,
} from '../types/contracts.gen'

/**
 * What every entry model adds to its wire payload.
 *
 * `timestamp` is the *envelope's* value, carried through rather than re-derived: the instant the API
 * recorded, not the instant this tab handled the frame. A `Date.now()` taken during reduction would
 * measure receipt, so a reconnect or a backgrounded tab would restamp history.
 *
 * The consoles no longer render it — the `[mm:ss]` prefix was removed as visual noise — but the
 * field stays, because it is the authoritative event time and the only correct source for one. It is
 * an internal frontend model either way: the generated contracts are untouched, and the value is
 * read off `IncidentEvent` rather than invented.
 */
interface StreamedEntry {
  id: string
  timestamp: string
}

export interface LogEntry extends LogStreamPayload, StreamedEntry {}

export interface ThoughtEntry extends AgentThoughtPayload, StreamedEntry {}

export interface WorkerEntry extends WorkerLogPayload, StreamedEntry {}

/**
 * A retrieval match with its envelope identity.
 *
 * The wire payload has no id of its own, and `runbook_id` is not one: the retrieval probe can
 * return the same runbook the live run matched, so keying on it would collide. The event id is
 * unique by construction.
 */
export interface RagEntry extends RagMatchPayload, StreamedEntry {}

/** Named metric series the sparklines read. Keyed by snapshot field name, as the contract is. */
export type MetricHistory = Record<string, number[]>

export interface IncidentUiState {
  connection: ConnectionState
  reconnectAttempt: number
  /** True until the first frame or snapshot lands — the cold-start window. */
  loading: boolean
  state: IncidentState
  incidentId: string | null
  /**
   * The last non-null incident id seen. Distinct from `incidentId`, which mirrors the server and
   * returns to null when a run ends — this is what "is this frame a *different* run?" is asked
   * against, and what keeps a finished run's evidence on screen.
   */
  lastRunId: string | null
  threadId: string | null
  scenarioId: ScenarioId | null
  /**
   * The last non-null scenario seen, on the same principle as `lastRunId`.
   *
   * The server stops reporting a scenario the moment a run completes, but "Database capacity is
   * restored" is exactly the sentence the state explanation needs at that moment — so the finished
   * run's identity has to outlive the run. Cleared only by Master Reset.
   */
  lastScenarioId: ScenarioId | null
  goldenSignals: GoldenSignals | null
  infrastructure: InfrastructureMetrics | null
  history: MetricHistory
  logs: LogEntry[]
  ragMatches: RagEntry[]
  thoughts: ThoughtEntry[]
  workerLogs: WorkerEntry[]
  /** The worker's error string on a FAILED run, surfaced in the banner. */
  failureReason: string | null
}

const INITIAL: IncidentUiState = {
  connection: 'connecting',
  reconnectAttempt: 0,
  loading: true,
  state: 'HEALTHY',
  incidentId: null,
  lastRunId: null,
  threadId: null,
  scenarioId: null,
  lastScenarioId: null,
  goldenSignals: null,
  infrastructure: null,
  history: {},
  logs: [],
  ragMatches: [],
  thoughts: [],
  workerLogs: [],
  failureReason: null,
}

// Bounded so a demo left running overnight cannot grow the DOM without limit. The panels show a
// scrolling tail, so the oldest entries are not reachable anyway.
const MAX_LOGS = 60
const MAX_THOUGHTS = 40
const MAX_WORKER_LOGS = 80

type Action =
  | { kind: 'connection'; state: ConnectionState; attempt: number }
  | { kind: 'event'; event: IncidentEvent }
  | { kind: 'snapshot'; snapshot: Awaited<ReturnType<typeof fetchSnapshot>> }
  | { kind: 'clear' }
  | { kind: 'clear-panels' }

/** The per-run fields. Cleared together, always — a partial reset is how panels end up disagreeing. */
const CLEARED_RUN: Pick<
  IncidentUiState,
  'logs' | 'ragMatches' | 'thoughts' | 'workerLogs' | 'failureReason'
> = {
  logs: [],
  ragMatches: [],
  thoughts: [],
  workerLogs: [],
  failureReason: null,
}

/** Appends to a bounded rolling series. */
function extend(series: number[] | undefined, value: number): number[] {
  const next = [...(series ?? []), value]
  return next.length > SPARKLINE_WINDOW ? next.slice(-SPARKLINE_WINDOW) : next
}

function recordMetrics(
  history: MetricHistory,
  golden: GoldenSignals,
  infrastructure: InfrastructureMetrics,
): MetricHistory {
  const next: MetricHistory = { ...history }
  for (const [field, value] of Object.entries({ ...golden, ...infrastructure })) {
    if (typeof value === 'number') next[field] = extend(history[field], value)
  }
  return next
}

function cap<T>(items: T[], limit: number): T[] {
  return items.length > limit ? items.slice(-limit) : items
}

function reduce(state: IncidentUiState, action: Action): IncidentUiState {
  switch (action.kind) {
    case 'connection':
      return { ...state, connection: action.state, reconnectAttempt: action.attempt }

    case 'clear-panels':
      // A trigger this tab just issued. Only the derived panels go — the identifiers stay, so the
      // plan and worker consoles do not blink out of existence between the click and the first frame
      // of the new run.
      //
      // Without this the panels waited for the server to *tell* them a new run had started, and the
      // previous run's plan stayed on screen until a frame carrying the new incident id arrived. On
      // a slow snapshot that is a second of a finished plan presented as the new one's.
      return { ...state, ...CLEARED_RUN }

    case 'clear':
      // Master Reset: the one place a viewer explicitly asks for a blank slate. Metric history
      // stays, because the sparklines are still showing the same platform.
      return {
        ...state,
        ...CLEARED_RUN,
        incidentId: null,
        lastRunId: null,
        threadId: null,
        scenarioId: null,
        lastScenarioId: null,
      }

    case 'snapshot': {
      // The rehydration path. Replaces identifiers and metrics but leaves the derived panels
      // alone: the snapshot has no logs or reasoning in it, and blanking them on every reconnect
      // would make a brief network blip look like the run restarting.
      const snapshot = action.snapshot
      const newRun = snapshot.incident_id !== null && snapshot.incident_id !== state.lastRunId
      return {
        ...state,
        ...(newRun ? CLEARED_RUN : {}),
        loading: false,
        state: snapshot.state,
        incidentId: snapshot.incident_id,
        lastRunId: snapshot.incident_id ?? state.lastRunId,
        threadId: snapshot.thread_id,
        scenarioId: snapshot.scenario_id,
        lastScenarioId: snapshot.scenario_id ?? state.lastScenarioId,
        goldenSignals: snapshot.golden_signals,
        infrastructure: snapshot.infrastructure,
        history: recordMetrics(state.history, snapshot.golden_signals, snapshot.infrastructure),
      }
    }

    case 'event': {
      const { event } = action
      const eventIncident = event.incident_id

      // A frame carrying a *different* non-null run id means a new run started. Derived panels
      // belong to a run and are dropped; metric history belongs to the platform and is kept.
      //
      // A null id is not a new run — it is the platform with no incident, which is what the server
      // emits again once a run finishes. Clearing there would wipe the worker log and the postmortem
      // link at the moment the demo pays off.
      const newRun = eventIncident !== null && eventIncident !== state.lastRunId
      const base: IncidentUiState = newRun
        ? { ...state, ...CLEARED_RUN, lastRunId: eventIncident }
        : state

      switch (event.type) {
        case EventType.METRICS_UPDATE: {
          const data = event.data
          return {
            ...base,
            loading: false,
            state: data.status,
            incidentId: eventIncident,
            goldenSignals: data.golden_signals,
            infrastructure: data.infrastructure,
            history: recordMetrics(state.history, data.golden_signals, data.infrastructure),
          }
        }

        case EventType.LOG_STREAM: {
          const entry: LogEntry = { ...event.data, id: event.event_id, timestamp: event.timestamp }
          return { ...base, logs: cap([...base.logs, entry], MAX_LOGS) }
        }

        case EventType.RAG_MATCH:
          // Replaced, not appended: the panel shows the match for the run in flight, and the
          // contract emits one per retrieval.
          return { ...base, ragMatches: [{ ...event.data, id: event.event_id, timestamp: event.timestamp }] }

        case EventType.AGENT_THOUGHT: {
          const entry: ThoughtEntry = { ...event.data, id: event.event_id, timestamp: event.timestamp }
          return { ...base, thoughts: cap([...base.thoughts, entry], MAX_THOUGHTS) }
        }

        case EventType.WORKER_LOG: {
          const entry: WorkerEntry = { ...event.data, id: event.event_id, timestamp: event.timestamp }
          return {
            ...base,
            workerLogs: cap([...base.workerLogs, entry], MAX_WORKER_LOGS),
            // An ERROR line is the worker's diagnosis, and it is what the `FAILED` banner shows.
            // There is no dedicated field for it on the wire — the API puts a failed callback's
            // `error` string onto this channel precisely so the terminal and the banner agree.
            failureReason: entry.level === WorkerLogLevel.ERROR ? entry.message : base.failureReason,
          }
        }

        default:
          return base
      }
    }

    default:
      return state
  }
}

export interface IncidentStream extends IncidentUiState {
  /** True when the stream is not delivering — drives the desaturated state. */
  stale: boolean
  /** Re-reads GET /api/telemetry/current. Called on reconnect and after a local action. */
  rehydrate: () => Promise<void>
  /** Drops the finished run's panels. Called after a successful Master Reset, and nowhere else. */
  clearRun: () => void
  /**
   * Drops the derived panels but keeps the run identifiers.
   *
   * Called the moment a trigger is accepted, so the previous run's evidence leaves the screen with
   * the click rather than whenever the server's next frame happens to arrive.
   */
  clearPanels: () => void
}

/**
 * Subscribes to the stream for the lifetime of the component and exposes the reduced state.
 *
 * Rehydration on reconnect is the documented recovery path: the server keeps no per-client replay
 * buffer, so a client that missed frames rebuilds from the snapshot rather than being served
 * stale ones.
 */
export function useIncidentStream(): IncidentStream {
  const [state, dispatch] = useReducer(reduce, INITIAL)
  // Tracks the previous connection state so a reconnect can be detected without putting it in
  // the reducer, where it would be state nothing renders.
  const wasDisconnected = useRef(false)

  const rehydrate = useCallback(async () => {
    try {
      dispatch({ kind: 'snapshot', snapshot: await fetchSnapshot() })
    } catch {
      // The stream is the primary source; a failed snapshot leaves the UI on whatever it last
      // had, which is better than blanking it.
    }
  }, [])

  useEffect(() => {
    // The cold-start fetch, so the panels have values before the first 1 Hz frame arrives.
    void rehydrate()

    return connectIncidentStream({
      onEvent: (event) => dispatch({ kind: 'event', event }),
      onConnectionChange: (connection, attempt) => {
        dispatch({ kind: 'connection', state: connection, attempt })
        if (connection === 'open' && wasDisconnected.current) void rehydrate()
        wasDisconnected.current = connection === 'reconnecting'
      },
    })
  }, [rehydrate])

  const clearRun = useCallback(() => dispatch({ kind: 'clear' }), [])
  const clearPanels = useCallback(() => dispatch({ kind: 'clear-panels' }), [])

  const stale = state.connection !== 'open'
  return useMemo(
    () => ({ ...state, stale, rehydrate, clearRun, clearPanels }),
    [state, stale, rehydrate, clearRun, clearPanels],
  )
}
