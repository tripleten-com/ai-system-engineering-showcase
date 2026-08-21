# Architecture

> **What to notice:** the most important boundary is not between “AI” and “non-AI” code. It is
> between a control plane that can analyze and propose, and a worker that alone can consume an
> approved job. Durable state, queueing, and authentication make that separation
> enforceable; the worker's handler effects remain simulated.

## What this is

One click drives a fixed pipeline. A scenario is triggered, telemetry moves, the raw evidence is
sanitized, a runbook is retrieved, a plan is drafted, the run stops for a human, and only after
authorization does a worker process the approved handlers and archive a report. The
[README](../README.md) walks that sequence in plain language; this chapter is about the machinery
underneath it.

`incident-agent-api` is the hub. It owns the telemetry generator, the state machine, the
sanitization stage, the retrieval layer, the agent graph, the OpenTelemetry exporter, and the
Prometheus endpoint. `remediation-worker` is the approved-job boundary — a daemon that
consumes approved jobs, records simulated handler results, and archives the postmortem.
`incident-war-room` carries the operator interface; everything else is a data store, an emulated
cloud service, or a viewer.

## The nine containers

| Container | Image | Host port | Responsibility |
|---|---|---|---|
| `incident-war-room` | built from `services/incident-war-room` | 3000 → 8080 | React interface, served by nginx, which also proxies the API and the archived postmortems |
| `incident-agent-api` | built from `services/incident-agent-api` | 8000 | FastAPI control plane: telemetry, sanitization, retrieval, planning, the approval gate |
| `remediation-worker` | built from `services/remediation-worker` | none published | Consumes approved jobs from SQS, records simulated handler results, archives the postmortem, calls back |
| `postgres-vector` | `pgvector/pgvector:0.8.0-pg16` | 5432 | Runbooks, their embeddings, and the agent's checkpoints |
| `redis` | `redis:7.4.2-alpine` | 6379 | Cache, fast state, idempotency claims, worker heartbeats |
| `localstack` | `localstack/localstack:4.0.3` | 4566 | Emulated SQS queues and S3 bucket |
| `prometheus` | `prom/prometheus:v3.12.0` | 9090 | Scrapes the API's metrics endpoint |
| `grafana` | `grafana/grafana:13.1.1` | 3001 → 3000 | Pre-provisioned dashboards, anonymous read-only access |
| `jaeger` | `jaegertracing/all-in-one:1.76.0` | 16686, 4317, 4318 | Trace collector and waterfall UI |

The war room binds 8080 inside the container because the image runs unprivileged and cannot take
`:80`. `remediation-worker` publishes nothing — it is reached only over the internal network and
through the queue.

Container definitions live in `infra/docker-compose.yml`, which the root `compose.yaml` includes.

## Where the code is

Three of the nine containers hold project source: `incident-agent-api`, `remediation-worker`, and
the React application in `incident-war-room`. The remaining six are stock images plus provisioning
assets under `infra/` — Grafana dashboards and
datasources, the Prometheus scrape config, the LocalStack init script, and the Postgres
extension bootstrap.

[The repository map](./repository-map.md) is the file-by-file tour.

## Request and data flow

```mermaid
flowchart TD
    UI[incident-war-room] -->|POST /api/incidents/trigger| API[incident-agent-api]
    API -->|Server-Sent Events (SSE) telemetry| UI
    API -->|1. generate metrics| TEL[telemetry engine]
    API -->|2. mask secrets| SAN[sanitizer]
    SAN -->|3. hybrid search| PG[(postgres-vector)]
    PG -->|matched runbook| AG[agent graph]
    AG -->|4. draft plan, then pause| CP[(checkpoint in postgres)]
    AG -->|5. after authorization| SQS[localstack SQS<br/>remediation-jobs]
    SQS --> W[remediation-worker]
    W -->|6. process approved handlers| SIM[structured simulated results]
    W -->|7. archive postmortem| S3[localstack S3<br/>tripleten-cloud-postmortems]
    W -->|8. authenticated callback| API
    API -->|9. recovery decay| TEL
```

Each hop has one owning module:

| Hop | Module |
|---|---|
| Telemetry generation and the state machine | `services/incident-agent-api/src/incident_agent_api/telemetry/engine.py` |
| Secret masking before the planner sees anything | `services/incident-agent-api/src/incident_agent_api/security/sanitizer.py` |
| Runbook retrieval | `services/incident-agent-api/src/incident_agent_api/retrieval/hybrid_search.py` |
| Planning, tool selection, and the pause | `services/incident-agent-api/src/incident_agent_api/agent/graph.py` |
| Job dispatch to the queue | `services/incident-agent-api/src/incident_agent_api/infra/sqs.py` |
| Processing approved handlers | `services/remediation-worker/src/remediation_worker/consumer.py` |
| Postmortem archival | `services/remediation-worker/src/remediation_worker/postmortem.py` |

The stream back to the browser is a single multiplexed Server-Sent Events channel; see
[the API reference](./api-reference.md).

## The simulation boundary

This is the part worth being precise about, because it is where a demo usually lies and this one
does not.

**Simulated.** The displayed incident metrics and handler effects. Metrics come from mathematical
profiles — jittered baselines, per-scenario chaos curves, and an exponential recovery decay — not
from real service degradation. No connection pool is genuinely exhausted, no cache is genuinely
stampeded, and no handler applies its described database, cache, process, or firewall changes. The
planner is a deterministic offline emulator standing at the exact integration point where a live
model would connect. That is what makes the demo repeatable and resettable rather than a coin flip
in front of an audience.

**Real.** Postgres, Redis, and LocalStack are actual services doing actual work. The runbooks are
really embedded and really retrieved by vector and full-text search. The sanitization graph stage
really masks the evidence before the planner receives it. The checkpoint is really persisted. The
job really travels through SQS, the worker really consumes it with idempotency and retries, the
callback is really authenticated, and the postmortem is really written to S3 and read back to
render in the browser. Traces really reach Jaeger. Scenario 3 also pauses and resumes the real
synthetic customer-workload consumer tasks and quarantines a poison message through LocalStack;
only the displayed 1,540-message queue-depth ramp and handler results are simulated.

**And the approval gate is structural, not procedural.** The API process cannot execute a
remediation tool, and that is enforced at import time rather than by convention. In
`services/incident-agent-api/src/incident_agent_api/agent/tools.py:136`:

```python
assert not (set(READ_ONLY_DISPATCH) & REMEDIATION_TOOLS), "a remediation tool is executable in the API process"
```

The API's dispatch table holds only `check_health` and `read_runbook`. If a state-changing tool
were ever added to it, the API would fail to start. So "the planner cannot apply a fix on its
own" is not a policy someone might forget to enforce — the capability is absent from the process,
and the process refuses to boot if that stops being true.

## Five engineering capabilities, and where each one lives

The showcase integrates five engineering capabilities. Each maps to specific code and specific
containers, so the system can be evaluated from implementation evidence rather than from a feature
list.

| Skill | Where it lives |
|---|---|
| Observability | `services/incident-agent-api/src/incident_agent_api/telemetry/` publishes the gauges and counters; `prometheus` scrapes them, `grafana` charts them, `jaeger` collects the traces exported by `services/incident-agent-api/src/incident_agent_api/infra/otel.py` |
| Retrieval-augmented generation (RAG) | `services/incident-agent-api/src/incident_agent_api/retrieval/` over the `knowledge_runbooks` table in `postgres-vector`, combining vector and full-text ranking |
| Cloud queues | `services/incident-agent-api/src/incident_agent_api/infra/sqs.py` publishes to LocalStack SQS; `services/remediation-worker/src/remediation_worker/consumer.py` consumes, with a dead-letter queue and a retry budget |
| Security guardrails | The `sanitize_logs` node in `services/incident-agent-api/src/incident_agent_api/agent/graph.py` uses `security/sanitizer.py` before planning; `agent/guardrails.py` validates every proposed tool call against its canonical schema |
| AI agents with human approval | `services/incident-agent-api/src/incident_agent_api/agent/graph.py` drafts the plan and halts; `services/incident-agent-api/src/incident_agent_api/agent/checkpointer.py` persists the paused run so authorization resumes it |

[What each scenario actually does](./incident-behavior.md) covers the behavior these subsystems
produce.
