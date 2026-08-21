/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        services/sseClient.ts
 * Purpose:          Wraps EventSource with typed demultiplexing and exponential-backoff
 *                   reconnection.
 * Interacts With:   incident-agent-api (:8000) via GET /api/stream
 *
 * Curriculum Project: Project 1 — Diagnostics & Telemetry
 * Skills:           Event Streaming, Reconnection Backoff, Typed Demultiplexing
 * Tools:            TypeScript, EventSource
 *
 * Every frame rides the default `message` event and is demultiplexed on the envelope's `type` —
 * that is what "multiplexed" means in this contract, and it is why one `onmessage` handler is
 * enough. Naming each SSE event after its channel would force five listeners and silently drop
 * any channel added later.
 *
 * **No `incident_id` on the connection.** Supplying it makes the server enforce ownership for the
 * stream's whole lifetime and *end the response* when a frame arrives that does not belong to the
 * named run — which is correct for the server and wrong for this client. The War Room follows the
 * platform: it renders baseline before anyone picks a scenario and must survive a Master Reset
 * without its stream dying. Scoping is for a client that wants to be told when its run is over.
 */

import { EventType, type IncidentEvent } from '../types/contracts.gen'
import { API_BASE } from './api'

/** `EventSource` sets a reconnect floor from the server's `retry:` directive; this is our ceiling. */
const BACKOFF_BASE_MS = 1_000
const BACKOFF_MAX_MS = 15_000

export type ConnectionState = 'connecting' | 'open' | 'reconnecting'

export interface StreamHandlers {
  onEvent: (event: IncidentEvent) => void
  onConnectionChange: (state: ConnectionState, attempt: number) => void
}

// Derived from the generated enum rather than listed: adding a sixth channel to the contract
// extends this automatically, where a hand-written array would silently reject the new type.
const KNOWN_EVENT_TYPES: readonly string[] = Object.values(EventType)

function isIncidentEvent(value: unknown): value is IncidentEvent {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as { type?: unknown; data?: unknown; event_id?: unknown }
  return (
    typeof candidate.event_id === 'string' &&
    typeof candidate.type === 'string' &&
    KNOWN_EVENT_TYPES.includes(candidate.type) &&
    typeof candidate.data === 'object' &&
    candidate.data !== null
  )
}

/**
 * Opens the stream and keeps it open. Returns a disposer.
 *
 * Reconnection is managed here rather than left to `EventSource`'s own retry because the UI has to
 * *know* it is disconnected — a stalled stream and a perfectly healthy system both render a flat
 * line at baseline, and that ambiguity is the one thing the disconnected state exists to remove.
 * So the native source is closed on error and reopened on our own schedule, reporting the attempt
 * count as it goes.
 */
export function connectIncidentStream({ onEvent, onConnectionChange }: StreamHandlers): () => void {
  let source: EventSource | null = null
  let timer: ReturnType<typeof setTimeout> | null = null
  let attempt = 0
  let disposed = false

  const open = () => {
    if (disposed) return
    onConnectionChange(attempt === 0 ? 'connecting' : 'reconnecting', attempt)

    source = new EventSource(`${API_BASE}/api/stream`)

    source.onopen = () => {
      attempt = 0
      onConnectionChange('open', 0)
    }

    source.onmessage = (message: MessageEvent<string>) => {
      let parsed: unknown
      try {
        parsed = JSON.parse(message.data)
      } catch {
        // A malformed frame is dropped rather than thrown: one bad payload must not take the
        // stream — or the demo — down.
        return
      }
      if (isIncidentEvent(parsed)) onEvent(parsed)
    }

    source.onerror = () => {
      source?.close()
      source = null
      if (disposed) return

      attempt += 1
      onConnectionChange('reconnecting', attempt)
      // Exponential with a ceiling. Uncapped growth would leave a tab that was backgrounded for
      // an hour waiting minutes to notice the stack came back.
      const delay = Math.min(BACKOFF_BASE_MS * 2 ** (attempt - 1), BACKOFF_MAX_MS)
      timer = setTimeout(open, delay)
    }
  }

  open()

  return () => {
    disposed = true
    if (timer) clearTimeout(timer)
    source?.close()
  }
}
