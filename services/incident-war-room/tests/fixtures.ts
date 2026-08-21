/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        fixtures.ts
 * Purpose:          Shared test payloads, built from the generated contracts rather than typed
 *                   out by hand.
 * Interacts With:   Every test file
 *
 * Curriculum Project: Cross-cutting — Contract Design
 * Skills:           Test Fixtures, Contract Alignment
 * Tools:            TypeScript, Vitest
 *
 * These use the generated types, so a contract change that the components have not caught up with
 * fails at compile time in the fixtures — which is a much clearer signal than a runtime assertion
 * on a shape that quietly changed.
 */

import type { PostmortemReport } from '../src/components/PostmortemDrawer'
import { redactionToken } from '../src/lib/redaction'
import type { LogEntry, RagEntry, ThoughtEntry, WorkerEntry } from '../src/hooks/useIncidentStream'
import {
  AgentPhase,
  BucketName,
  GuardrailVerdict,
  RunbookId,
  WorkerLogLevel,
  WorkerLogSource,
  type GoldenSignals,
  type InfrastructureMetrics,
  type RagMatchPayload,
} from '../src/types/contracts.gen'

/**
 * An envelope timestamp that renders as a known `[mm:ss]` in *any* timezone.
 *
 * Built from local-time components and then serialised to ISO, so parsing it back always yields
 * these minutes and seconds locally. A hardcoded `'2026-08-21T12:34:56Z'` would render as `[34:56]`
 * in most zones and something else in the half-hour-offset ones — a test that passes in London and
 * fails in Kolkata is worse than no test.
 */
export function eventTimestamp(minutes: number, seconds: number): string {
  return new Date(2026, 7, 21, 12, minutes, seconds).toISOString()
}

/** The default fixture instant. Renders as `[04:07]`. */
export const FIXTURE_TIMESTAMP = eventTimestamp(4, 7)

/** The documented baseline sample: 48ms p99, no errors. */
export const BASELINE_SIGNALS: GoldenSignals = {
  requests_per_sec: 118.4,
  http_5xx_error_rate_pct: 0,
  latency_p50_ms: 21.3,
  latency_p95_ms: 39.7,
  latency_p99_ms: 48,
}

/** The documented Scenario 1 peak. */
export const OUTAGE_SIGNALS: GoldenSignals = {
  requests_per_sec: 96.1,
  http_5xx_error_rate_pct: 36.4,
  latency_p50_ms: 812.5,
  latency_p95_ms: 2940.2,
  latency_p99_ms: 4820,
}

export const BASELINE_INFRA: InfrastructureMetrics = {
  system_health_status: 1,
  db_pool_utilization_pct: 14.8,
  redis_memory_utilization_pct: 31.2,
  cache_hit_ratio_pct: 96.4,
  sqs_active_queue_depth: 3,
  dlq_message_count: 0,
  active_workers_count: 4,
  security_violations_total: 0,
}

export function logEntry(id: string, message: string, timestamp = FIXTURE_TIMESTAMP): LogEntry {
  return { id, message, sanitized: true, timestamp }
}

/** A log line carrying all three Scenario 1 redactions. */
export const REDACTED_LOG = logEntry(
  'log-1',
  `Connection pool saturated. host=${redactionToken('ip')} db_pass=${redactionToken('password')} user_email=${redactionToken('email')}`,
)

export function thought(overrides: Partial<ThoughtEntry> & { id: string; step: number }): ThoughtEntry {
  return {
    phase: AgentPhase.ANALYZING,
    text: 'Analyzed alert and sanitized inbound logs.',
    tool_call: null,
    guardrail: GuardrailVerdict.PASSED,
    timestamp: FIXTURE_TIMESTAMP,
    ...overrides,
  }
}

/** The Scenario 4 signature: an injected tool call the firewall refused. */
export const BLOCKED_THOUGHT = thought({
  id: 'thought-blocked',
  step: 3,
  phase: AgentPhase.TOOL_SELECTION,
  text: 'Injected instruction requested a tool outside the allowlist.',
  tool_call: { name: 'delete_all_customer_records', args: { confirm: true }, is_canonical: false },
  guardrail: GuardrailVerdict.BLOCKED,
})

export const RB_104_MATCH: RagMatchPayload = {
  runbook_id: RunbookId.RB_104,
  title: 'PostgreSQL Connection Pool Drain & Recycle',
  cosine_similarity: 0.9412,
  rrf_rank: 1,
  excerpt: 'Terminate orphaned idle connections older than 60 seconds using pg_terminate_backend().',
  source: 'pgvector (cosine) + FTS, fused via RRF',
}

export function workerEntry(overrides: Partial<WorkerEntry> & { id: string }): WorkerEntry {
  return {
    source: WorkerLogSource.WORKER,
    level: WorkerLogLevel.INFO,
    message: 'Consumed job-99214 and executed pg_terminate_backend() on 84 pids.',
    timestamp: FIXTURE_TIMESTAMP,
    ...overrides,
  }
}

/** A live retrieval match, as the reducer produces it from a `RAG_MATCH` envelope. */
export function ragEntry(overrides: Partial<RagEntry> = {}): RagEntry {
  return { ...RB_104_MATCH, id: 'rag-1', timestamp: FIXTURE_TIMESTAMP, ...overrides }
}

export const POSTMORTEM_KEY = '2026-08-20-db-pool-exhaustion.json'

export const ARCHIVE_ENTRY = workerEntry({
  id: 'worker-archive',
  source: WorkerLogSource.LOCALSTACK_S3,
  message: `Archived report to s3://${BucketName.POSTMORTEMS}/${POSTMORTEM_KEY}`,
})

/**
 * A postmortem report as the worker archives it.
 *
 * Copied from an object the running stack actually wrote, not invented from the schema. The first
 * version of this fixture typed `operations[].detail` as a sentence; it is a `dict[str, Any]` on the
 * worker side, and the drawer handed that object to React as a child and blanked the whole page. A
 * fixture that agrees with the code but not with the artefact tests nothing.
 */
export const POSTMORTEM_REPORT: PostmortemReport = {
  schema_version: '1.0',
  incident_id: 'inc-6',
  thread_id: 'thread-inc-6',
  scenario_id: 'db_pool_exhaustion',
  runbook_id: 'RB-104',
  job_id: 'job-99214',
  idempotency_key: 'inc-6:job-99214',
  completed_at: '2026-08-20T12:00:09.412000+00:00',
  authorized_by_human: true,
  tools_executed: ['flush_connection_pool'],
  operations: [
    {
      tool: 'flush_connection_pool',
      operation: 'pg_terminate_backend',
      detail: { terminated_pids: 84, idle_threshold_seconds: 60 },
    },
  ],
  execution_log: [
    { source: 'LocalStack SQS', level: 'INFO', message: 'Consumed job-99214 from remediation-jobs.' },
    { source: 'Worker', level: 'INFO', message: 'Executed pg_terminate_backend() on 84 pids.' },
  ],
}
