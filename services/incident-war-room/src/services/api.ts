/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        services/api.ts
 * Purpose:          Typed client for the four incident endpoints and the polling snapshot.
 * Interacts With:   incident-agent-api (:8000)
 *
 * Curriculum Project: Project 5 — Autonomous Agent & Human-in-the-Loop
 * Skills:           API Client Design, Typed Contracts, Error Surfacing
 * Tools:            TypeScript, Fetch
 *
 * `fetch`, not axios: the calls are four POSTs and a GET, and a dependency for that is weight the
 * bundle does not need.
 *
 * Every function surfaces a failed response as a thrown `ApiError` carrying the status and the
 * server's message. The 409 bodies are load-bearing — a duplicate trigger returns the in-flight
 * `incident_id`, which is how a reloaded tab recovers the run it lost.
 */

import type { IncidentState, RagMatchPayload, ScenarioId, TelemetrySnapshotResponse } from '../types/contracts.gen'

/**
 * Empty by default, so the browser calls the same origin it was served from and the demo works
 * behind the nginx reverse proxy with no build-time configuration. `VITE_API_BASE_URL` overrides
 * it for `npm run dev`, where Vite serves on :5173 and the API is on :8000.
 *
 * `??` is correct here where the other three settings use `||`: empty *is* the intended value, so
 * an unset build arg arriving as `""` lands on exactly the right default rather than overriding it.
 */
export const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly detail?: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export interface TriggerResponse {
  incident_id: string
  thread_id: string
  scenario_id: ScenarioId
  state: IncidentState
}

export interface AuthorizeResponse {
  incident_id: string
  state: IncidentState
  job_id: string | null
  duplicate: boolean
}

export interface ResetResponse {
  incident_id: string | null
  state: IncidentState
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })

  if (!response.ok) {
    // The body is JSON on every documented error path, but a proxy 502 is HTML — so a parse
    // failure must not mask the status the caller needs to branch on.
    let detail: unknown
    let message = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      detail = body?.detail ?? body
      const inner = typeof detail === 'object' && detail !== null ? (detail as { message?: string }).message : undefined
      message = inner ?? (typeof detail === 'string' ? detail : message)
    } catch {
      // Keep the status-derived message.
    }
    throw new ApiError(response.status, message, detail)
  }

  return (await response.json()) as T
}

/** Starts a run. 409 when one is already in flight, carrying that run's id. */
export function triggerIncident(scenarioId: ScenarioId): Promise<TriggerResponse> {
  return request<TriggerResponse>('/api/incidents/trigger', {
    method: 'POST',
    body: JSON.stringify({ scenario_id: scenarioId }),
  })
}

/**
 * The one human decision in the pipeline. All three identifiers are sent because the API checks
 * all three — a `thread_id` from another run would resume the wrong graph.
 */
export function authorizeIncident(
  incidentId: string,
  threadId: string,
  scenarioId: ScenarioId,
  approved: boolean,
): Promise<AuthorizeResponse> {
  return request<AuthorizeResponse>('/api/incidents/authorize', {
    method: 'POST',
    body: JSON.stringify({
      incident_id: incidentId,
      thread_id: threadId,
      scenario_id: scenarioId,
      approved,
    }),
  })
}

/** Master Reset. Requires the retained `incident_id`; any other id is refused with 409. */
export function resetIncident(incidentId: string): Promise<ResetResponse> {
  return request<ResetResponse>('/api/incidents/reset', {
    method: 'POST',
    body: JSON.stringify({ incident_id: incidentId }),
  })
}

/**
 * The rehydration source. Returns the full metric object plus `incident_id`, `thread_id`,
 * `scenario_id`, and `state` — which is exactly what a client that lost the stream needs to
 * rebuild its UI, and why there is no `Last-Event-ID` replay buffer on the server.
 */
export function fetchSnapshot(): Promise<TelemetrySnapshotResponse> {
  return request<TelemetrySnapshotResponse>('/api/telemetry/current')
}

export interface SearchResponse {
  /** Echoed back, so a slow response cannot be mistaken for the answer to a newer query. */
  query: string
  results: RagMatchPayload[]
}

/**
 * The RAG Inspector's live probe. Read-only and stateless — it runs both retrieval legs against an
 * arbitrary query without touching the state machine, which is what makes it safe to expose as a
 * disclosure control rather than as part of the incident flow.
 */
export function searchRunbooks(query: string, limit = 3): Promise<SearchResponse> {
  return request<SearchResponse>('/api/retrieval/search', {
    method: 'POST',
    body: JSON.stringify({ query, limit }),
  })
}
