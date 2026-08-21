# Incident behavior

> **What to notice:** deterministic simulation is used as a measurement tool, not as a substitute
> for infrastructure. Each scenario produces a specific signature, holds until approved work
> reports success, and follows a measurable recovery path.

What actually happens when a scenario runs: which numbers move, how far, on what curve, and which
parts of that are real. The [README](../README.md) describes the workflow; this chapter is the
mechanics underneath it.

Read [the simulation boundary](./architecture.md#the-simulation-boundary) first if you have not —
the metrics here are generated, and the distinction matters for everything below.

## The four scenarios

| Plain name (in the UI) | Scenario id | Runbook | Runbook title |
|---|---|---|---|
| Database overload | `db_pool_exhaustion` | `RB-104` | PostgreSQL Emergency Connection Drain & Pool Recycling |
| Cache traffic spike | `cache_thundering_herd` | `RB-208` | Redis Cache Stampede Mitigation & Hot-Key Repopulation |
| Queue processing stops | `worker_deadlock` | `RB-312` | SQS Poison Message Isolation & Consumer Pool Rebalance |
| Prompt injection attempt | `prompt_injection` | `SEC-501` | Adversarial Prompt Injection Containment & Audit Protocol |

The pairing is declared once, in `_SCENARIO_RUNBOOK` at
`packages/contracts/src/tripleten_contracts/identifiers.py:47`. Runbook content lives in
`services/incident-agent-api/src/incident_agent_api/seed/runbooks.py`.

Each scenario also has one fixed approval-button string, in `APPROVAL_PROMPT` in the same
contracts module — `Authorize DB Pool Drain & Recycle`,
`Authorize Cache Warm-Up & Orphan Purge`, `Authorize DLQ Quarantine & Worker Reboot`,
`Confirm Security Quarantine & Block IP`. The end-to-end tests read those strings off the DOM, so
the contracts module remains the source of truth rather than duplicating them in the test suite.

## The metric surface

Eleven Prometheus metric families, defined in `MetricName` at
`packages/contracts/src/tripleten_contracts/telemetry.py` and registered in
`services/incident-agent-api/src/incident_agent_api/telemetry/registry.py`.

| Metric | Type | Meaning |
|---|---|---|
| `http_requests_total` | counter | HTTP requests processed |
| `http_5xx_errors_total` | counter | 5xx responses |
| `security_violations_total` | counter | Security firewall intercepts |
| `http_request_duration_milliseconds` | gauge (labeled) | Simulated latency percentiles |
| `db_pool_utilization_pct` | gauge | PostgreSQL connection pool saturation |
| `redis_memory_utilization_pct` | gauge | Redis memory usage |
| `cache_hit_ratio_pct` | gauge | Cache hit ratio |
| `sqs_active_queue_depth` | gauge | Unhandled messages in the primary queue |
| `dlq_message_count` | gauge | Messages in the dead-letter queue |
| `active_workers_count` | gauge | Live consumer workers |
| `system_health_status` | gauge | `0` = Down, `1` = OK, `2` = Degraded |

`http_request_duration_milliseconds` is one labeled family carrying a `quantile` label with
`p50`, `p95`, and `p99`, so it produces three time series within that family.

## Baseline

Steady state is generated, not measured. `services/incident-agent-api/src/incident_agent_api/telemetry/baseline.py`
holds the nominal values and the jitter windows applied on top of them.

| Field | Nominal | Jitter |
|---|---|---|
| `requests_per_sec` | 145.0 | ±3.0, around a slow sine |
| `latency_p50_ms` | 18.5 | ±2.0 |
| `latency_p95_ms` | 34.0 | ±3.0 |
| `latency_p99_ms` | 48.0 | ±4.0 |
| `db_pool_utilization_pct` | 15.0 | ±2.0 |
| `redis_memory_utilization_pct` | 40.0 | ±1.0 |
| `cache_hit_ratio_pct` | 99.0 | −0.5 to 0.0 |
| `sqs_active_queue_depth` | 4.0 | sampled discretely, 2–6 |
| `http_5xx_error_rate_pct` | 0.0 | none |
| `dlq_message_count` | 0.0 | none |
| `active_workers_count` | 4.0 | none |

Throughput rides a sine centered on 145 req/s with amplitude 15 and a period of about 62.8 seconds,
so the chart moves at rest rather than sitting flat.

Two details in that table are deliberate rather than incidental. Cache hit ratio jitters only
downward, because a ratio above the 99% nominal would claim better-than-nominal performance the
demo has not earned. And queue depth, DLQ count, and worker count are integer fields — they are
counts of things and never surface as fractions.

These nominals do double duty: they are the origin a chaos ramp climbs from and the target the
recovery curve returns to, which is why they are declared once.

## Chaos

When a scenario is triggered, affected metrics leave baseline. Profiles are in
`services/incident-agent-api/src/incident_agent_api/telemetry/chaos.py`, and a metric moves in one
of three ways:

- **Ramped** — eased from nominal to a peak over its ramp window, then held with ±1% wander. The
  easing is a smoothstep, not a straight line. Default ramp is 2.0 s.
- **Stepped** — applied instantly at trigger and held exactly.
- **Overridden** — the baseline expression is replaced by a function of time, for a metric that
  thrashes rather than settling.

The hold jitter exists because `AWAITING_APPROVAL` has no time limit. A dead-flat line would sit
on screen for as long as the reader takes to decide; ±1% keeps the sparkline alive without leaving
the chaos band.

### Database overload — `db_pool_exhaustion`

A tail-latency signature. `p99` and `p95` blow out while `p50` stays healthy, because it is the
queued requests that time out, not the ones being served.

| Field | Peak | Mode |
|---|---|---|
| `db_pool_utilization_pct` | 98.5 | ramped |
| `http_5xx_error_rate_pct` | 36.4 | ramped |
| `latency_p99_ms` | 4820.0 | ramped |
| `latency_p95_ms` | 2960.0 | ramped |
| `requests_per_sec` | 42.0 | ramped |

Proposed tool: `flush_connection_pool`.

### Cache traffic spike — `cache_thundering_herd`

| Field | Peak | Mode |
|---|---|---|
| `redis_memory_utilization_pct` | 97.8 | ramped |
| `cache_hit_ratio_pct` | 14.1 | ramped |
| `latency_p95_ms` | 1840.0 | ramped |
| `latency_p99_ms` | 2598.0 | ramped |
| `requests_per_sec` | oscillates 60–180 | overridden |

Throughput is an override rather than a ramp because a stampede does not fall to a single floor —
it thrashes, on a sine at 2.1 rad/s clamped to the 60–180 req/s band. Recovery pulls it back from
the oscillation's center of 120.

Proposed tool: `warm_cache`.

### Queue processing stops — `worker_deadlock`

The golden signals stay green. HTTP is fine; it is the asynchronous workload that has stalled, and
that is the whole point of the scenario.

| Field | Value | Mode |
|---|---|---|
| `sqs_active_queue_depth` | 1540.0 | ramped over 8.0 s |
| `active_workers_count` | 0.0 | stepped |
| `dlq_message_count` | 1.0 | stepped |

Queue depth gets the long ramp on purpose: a 1,540-message backlog appearing in two seconds reads
as a data glitch, while eight seconds reads as a queue genuinely filling behind stalled consumers.

Separately from that displayed metric, `CustomerWorkload` really pauses its synthetic consumers,
continues publishing customer jobs to LocalStack SQS, and quarantines a poison message before
resuming. The real queue moves in the same direction as the generated gauge, but not to the
displayed magnitude.

Proposed tools, in order: `isolate_poison_message`, then `reboot_workers`. Quarantine precedes the
reboot so fresh consumers cannot pick the poison payload back up — the same order RB-312's
procedure gives.

### Prompt injection attempt — `prompt_injection`

The chaos profile is **empty**. See [its own section below](#prompt_injection-is-different).

Proposed tools, in order: `revoke_session`, `block_ip`, `archive_forensics`.

## Recovery

Recovery starts on the worker's authenticated success callback — not on a timer, and not when the
approval is clicked. Until the fix reports back, the chaos values hold.

`services/incident-agent-api/src/incident_agent_api/telemetry/decay.py` places every affected
metric on an exponential curve back to its nominal:

```
value(t) = nominal + (peak − nominal) · e^(−1.8·t)
```

with `k = 1.8` over a 4.0-second window. Some fields are stepped back to a held value instead of
decayed — after `worker_deadlock`, the worker pool returns to 4 immediately, because
`reboot_workers` has already landed by the time the callback arrives, while the poison message
stays in the dead-letter queue where it was quarantined.

The recovery curve carries no jitter, and that is deliberate: its intermediate values are the one
part of the simulation quoted in documentation, and a jittered curve could not land on them.
Jitter resumes when the run returns to `HEALTHY`.

## `prompt_injection` is different

In exactly two ways, and both are easy to get wrong.

**It never causes an outage.** The chaos profile is empty — no infrastructure metric moves, the
gauges hold baseline for the whole run, `system_health_status` sits at `2` (Degraded), and there is
no recovery decay because nothing degraded. A run that spikes the infrastructure metrics here has
misunderstood the scenario.

**The injected tool call is never executed.** The Pydantic guardrail in
`services/incident-agent-api/src/incident_agent_api/agent/guardrails.py` validates every proposed
call against its canonical schema and rejects the injected one automatically — before any human is
involved, and permanently. The rejection surfaces in the stream as an `AGENT_THOUGHT` frame with
`guardrail: BLOCKED`.

What does happen is containment, gated exactly like every other scenario. `SEC-501` is retrieved, a
plan is drafted, the run pauses at `AWAITING_APPROVAL`, and only after
`Confirm Security Quarantine & Block IP` does the worker process the `revoke_session`, `block_ip`,
and `archive_forensics` handlers from the `remediation-jobs` queue. The handlers record simulated
containment results, and the run ends in `SECURITY_CONTAINED`.

Both halves of that hold at once: the three containment handlers are authorized, and they are
processed only after the click. Nothing unauthorized executes at any point in the run.

## Retrieval

Runbooks live in one table, created by
`services/incident-agent-api/src/incident_agent_api/seed/ingest.py` at startup:

```sql
CREATE TABLE IF NOT EXISTS knowledge_runbooks (
    ...
    embedding vector(384),
    content_fts tsvector GENERATED ALWAYS AS (...) STORED
);
CREATE INDEX ... ON knowledge_runbooks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ... ON knowledge_runbooks USING gin (content_fts);
```

Two indexes because there are two searches. Vector similarity over the 384-dimension embedding
finds runbooks with similar technical vocabulary; PostgreSQL full-text search over the generated
`content_fts` column finds exact keywords and system terms. Reciprocal Rank Fusion combines the two
rankings into one — `services/incident-agent-api/src/incident_agent_api/retrieval/rank_fusion.py`,
driven by `services/incident-agent-api/src/incident_agent_api/retrieval/hybrid_search.py`.

Embeddings are deterministic and computed locally by
`services/incident-agent-api/src/incident_agent_api/retrieval/embeddings.py`. No embedding API is
called, which is why the same query returns the same ranking on every run and why the demo needs no
key.

The retrieved runbook grounds the plan in an approved procedure. It is not executed: the proposed
actions still pass the guardrail and still wait for authorization.

`POST /api/retrieval/search` exposes the same retrieval read-only, so the ranking can be probed
without starting an incident. See [the API reference](./api-reference.md).

## Sanitization

`services/incident-agent-api/src/incident_agent_api/security/sanitizer.py` is called by the
LangGraph `sanitize_logs` stage, not by the display layer or HTTP middleware. Secrets are masked
before retrieval and planning, and only the masked form is written into checkpointed graph state.

Six categories are recognized, from `packages/contracts/src/tripleten_contracts/security.py`:
`password`, `ip`, `hostname`, `jwt`, `email`, `aws_key`.

The masked tokens are rendered in the War Room as evidence the guardrail fired, with a count of
tokens masked. That visibility is the point: a redaction nobody can see is indistinguishable from a
redaction that did not happen.
