/**
 * DO NOT EDIT — generated file.
 * Source:    packages/contracts/src/tripleten_contracts/
 * Generator: packages/contracts/scripts/export_typescript.py
 * Regenerate with `uv run poe contracts` (or `make contracts`).
 */

export const ScenarioId = {
  CACHE_THUNDERING_HERD: "cache_thundering_herd",
  DB_POOL_EXHAUSTION: "db_pool_exhaustion",
  PROMPT_INJECTION: "prompt_injection",
  WORKER_DEADLOCK: "worker_deadlock",
} as const;

export type ScenarioId = (typeof ScenarioId)[keyof typeof ScenarioId];

export const RunbookId = {
  RB_104: "RB-104",
  RB_208: "RB-208",
  RB_312: "RB-312",
  SEC_501: "SEC-501",
} as const;

export type RunbookId = (typeof RunbookId)[keyof typeof RunbookId];

export const QueueName = {
  CUSTOMER_DLQ: "customer-dlq",
  CUSTOMER_JOBS: "customer-jobs",
  REMEDIATION_DLQ: "remediation-dlq",
  REMEDIATION_JOBS: "remediation-jobs",
} as const;

export type QueueName = (typeof QueueName)[keyof typeof QueueName];

export const BucketName = {
  POSTMORTEMS: "tripleten-cloud-postmortems",
} as const;

export type BucketName = (typeof BucketName)[keyof typeof BucketName];

export const ToolName = {
  ARCHIVE_FORENSICS: "archive_forensics",
  BLOCK_IP: "block_ip",
  CHECK_HEALTH: "check_health",
  FLUSH_CONNECTION_POOL: "flush_connection_pool",
  ISOLATE_POISON_MESSAGE: "isolate_poison_message",
  READ_RUNBOOK: "read_runbook",
  REBOOT_WORKERS: "reboot_workers",
  REVOKE_SESSION: "revoke_session",
  WARM_CACHE: "warm_cache",
} as const;

export type ToolName = (typeof ToolName)[keyof typeof ToolName];

export const IncidentState = {
  AWAITING_APPROVAL: "AWAITING_APPROVAL",
  CRITICAL_OUTAGE: "CRITICAL_OUTAGE",
  EXECUTING: "EXECUTING",
  EXPLOIT_INTERCEPTED: "EXPLOIT_INTERCEPTED",
  FAILED: "FAILED",
  HEALTHY: "HEALTHY",
  RECOVERING: "RECOVERING",
  REJECTED: "REJECTED",
  SECURITY_CONTAINED: "SECURITY_CONTAINED",
} as const;

export type IncidentState = (typeof IncidentState)[keyof typeof IncidentState];

export const MetricName = {
  ACTIVE_WORKERS_COUNT: "active_workers_count",
  CACHE_HIT_RATIO_PCT: "cache_hit_ratio_pct",
  DB_POOL_UTILIZATION_PCT: "db_pool_utilization_pct",
  DLQ_MESSAGE_COUNT: "dlq_message_count",
  HTTP_5XX_ERRORS_TOTAL: "http_5xx_errors_total",
  HTTP_REQUESTS_TOTAL: "http_requests_total",
  HTTP_REQUEST_DURATION_MILLISECONDS: "http_request_duration_milliseconds",
  REDIS_MEMORY_UTILIZATION_PCT: "redis_memory_utilization_pct",
  SECURITY_VIOLATIONS_TOTAL: "security_violations_total",
  SQS_ACTIVE_QUEUE_DEPTH: "sqs_active_queue_depth",
  SYSTEM_HEALTH_STATUS: "system_health_status",
} as const;

export type MetricName = (typeof MetricName)[keyof typeof MetricName];

export const Quantile = {
  P50: "p50",
  P95: "p95",
  P99: "p99",
} as const;

export type Quantile = (typeof Quantile)[keyof typeof Quantile];

export const EventType = {
  AGENT_THOUGHT: "AGENT_THOUGHT",
  LOG_STREAM: "LOG_STREAM",
  METRICS_UPDATE: "METRICS_UPDATE",
  RAG_MATCH: "RAG_MATCH",
  WORKER_LOG: "WORKER_LOG",
} as const;

export type EventType = (typeof EventType)[keyof typeof EventType];

export const AgentPhase = {
  ANALYZING: "ANALYZING",
  AWAITING_APPROVAL: "AWAITING_APPROVAL",
  PLANNING: "PLANNING",
  RETRIEVING: "RETRIEVING",
  TOOL_SELECTION: "TOOL_SELECTION",
} as const;

export type AgentPhase = (typeof AgentPhase)[keyof typeof AgentPhase];

export const GuardrailVerdict = {
  BLOCKED: "BLOCKED",
  PASSED: "PASSED",
} as const;

export type GuardrailVerdict = (typeof GuardrailVerdict)[keyof typeof GuardrailVerdict];

export const WorkerLogSource = {
  LOCALSTACK_S3: "LocalStack S3",
  LOCALSTACK_SQS: "LocalStack SQS",
  WORKER: "Worker",
} as const;

export type WorkerLogSource = (typeof WorkerLogSource)[keyof typeof WorkerLogSource];

export const WorkerLogLevel = {
  ERROR: "ERROR",
  INFO: "INFO",
  WARN: "WARN",
} as const;

export type WorkerLogLevel = (typeof WorkerLogLevel)[keyof typeof WorkerLogLevel];

export const APPROVAL_PROMPT: Record<ScenarioId, string> = {
  "cache_thundering_herd": "Authorize Cache Warm-Up & Orphan Purge",
  "db_pool_exhaustion": "Authorize DB Pool Drain & Recycle",
  "prompt_injection": "Confirm Security Quarantine & Block IP",
  "worker_deadlock": "Authorize DLQ Quarantine & Worker Reboot",
};

export const BASELINE_BANDS: Readonly<Record<string, readonly [number, number]>> = {
  "active_workers_count": [4.0, 4.0],
  "cache_hit_ratio_pct": [98.5, 99.0],
  "db_pool_utilization_pct": [13.0, 17.0],
  "dlq_message_count": [0.0, 0.0],
  "http_5xx_error_rate_pct": [0.0, 0.0],
  "latency_p50_ms": [16.5, 20.5],
  "latency_p95_ms": [31.0, 37.0],
  "latency_p99_ms": [44.0, 52.0],
  "redis_memory_utilization_pct": [39.0, 41.0],
  "requests_per_sec": [127.0, 163.0],
  "sqs_active_queue_depth": [2.0, 6.0],
};

export interface AgentThoughtEvent {
  event_id: string;
  incident_id: string | null;
  timestamp: string;
  type: "AGENT_THOUGHT";
  data: AgentThoughtPayload;
}

export interface AgentThoughtPayload {
  step: number;
  phase: AgentPhase;
  text: string;
  tool_call: ToolCall | null;
  guardrail: GuardrailVerdict;
}

export interface GoldenSignals {
  requests_per_sec: number;
  http_5xx_error_rate_pct: number;
  latency_p50_ms: number;
  latency_p95_ms: number;
  latency_p99_ms: number;
}

export interface InfrastructureMetrics {
  system_health_status: number;
  db_pool_utilization_pct: number;
  redis_memory_utilization_pct: number;
  cache_hit_ratio_pct: number;
  sqs_active_queue_depth: number;
  dlq_message_count: number;
  active_workers_count: number;
  security_violations_total: number;
}

export interface LogStreamEvent {
  event_id: string;
  incident_id: string | null;
  timestamp: string;
  type: "LOG_STREAM";
  data: LogStreamPayload;
}

export interface LogStreamPayload {
  message: string;
  sanitized: boolean;
}

export interface MetricsSnapshot {
  status: IncidentState;
  golden_signals: GoldenSignals;
  infrastructure: InfrastructureMetrics;
}

export interface MetricsUpdateEvent {
  event_id: string;
  incident_id: string | null;
  timestamp: string;
  type: "METRICS_UPDATE";
  data: MetricsSnapshot;
}

export interface RagMatchEvent {
  event_id: string;
  incident_id: string | null;
  timestamp: string;
  type: "RAG_MATCH";
  data: RagMatchPayload;
}

export interface RagMatchPayload {
  runbook_id: RunbookId;
  title: string;
  cosine_similarity: number;
  rrf_rank: number;
  excerpt: string;
  source: string;
}

export interface TelemetrySnapshotResponse {
  incident_id: string | null;
  thread_id: string | null;
  scenario_id: ScenarioId | null;
  state: IncidentState;
  timestamp: string;
  golden_signals: GoldenSignals;
  infrastructure: InfrastructureMetrics;
}

export interface ToolCall {
  name: string;
  args: Record<string, unknown>;
  is_canonical: boolean;
}

export interface WorkerLogEvent {
  event_id: string;
  incident_id: string | null;
  timestamp: string;
  type: "WORKER_LOG";
  data: WorkerLogPayload;
}

export interface WorkerLogPayload {
  source: WorkerLogSource;
  level: WorkerLogLevel;
  message: string;
}

export type IncidentEvent = AgentThoughtEvent | LogStreamEvent | MetricsUpdateEvent | RagMatchEvent | WorkerLogEvent;
