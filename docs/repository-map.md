# Repository map

> **What to notice:** ownership follows runtime and contract boundaries. Deployable services own
> their behavior, shared wire shapes live in one contracts package, and whole-stack tests live
> outside every individual service.

This chapter traces each responsibility to its implementation and identifies the extension points
that preserve those boundaries. [The architecture chapter](./architecture.md) explains what the
pieces do; this one shows where the evidence lives.

## Top-level tour

| Entry | Holds |
|---|---|
| `services/` | The three containers carrying project code: the API, the worker, and the React app |
| `packages/contracts/` | Pydantic models shared across services, plus the generator that exports them to TypeScript |
| `infra/` | Compose definition and provisioning assets: Grafana, Prometheus, LocalStack, Postgres |
| `tests/` | Whole-stack tiers only — `tests/smoke/` and `tests/e2e/` |
| `scripts/` | Standalone smoke validators for Linux/macOS and PowerShell |
| `compose.yaml` | Root Compose entrypoint; includes `infra/docker-compose.yml` |
| `Makefile` | Thin forwarder to the task runner |
| `pyproject.toml` | The `uv` workspace, dependency groups, and every task definition |
| `uv.lock` | One committed lockfile for the whole workspace |
| `playwright.config.ts` | Browser matrix for the end-to-end tier |
| `.env.example` | Every environment variable with a working default |

`compose.yaml` sits at the root so `docker-compose up -d` works from a fresh clone with no `-f`
flag and no directory change. It contains an `include` of `infra/docker-compose.yml`, which is
where the nine service definitions actually live — the root file stays short enough to read.

## The three services

### `services/incident-agent-api/`

A `src`-layout Python package at `src/incident_agent_api/`.

| Path | Owns |
|---|---|
| `api/routes/` | One module per route group: `health.py`, `metrics.py`, `telemetry.py`, `stream.py`, `incidents.py`, `retrieval.py` |
| `api/deps.py` | Shared FastAPI dependencies |
| `agent/` | The graph (`graph.py`), tool definitions and dispatch (`tools.py`), schema validation (`guardrails.py`), the offline planner (`mock_llm.py`), run coordination (`orchestrator.py`), and checkpoint persistence (`checkpointer.py`) |
| `retrieval/` | Deterministic embeddings (`embeddings.py`), the combined query (`hybrid_search.py`), and rank fusion (`rank_fusion.py`) |
| `telemetry/` | Steady state (`baseline.py`), per-scenario chaos (`chaos.py`), recovery (`decay.py`), the Prometheus registry (`registry.py`), and the engine and state machine (`engine.py`) |
| `security/` | `sanitizer.py`, used by the graph's `sanitize_logs` stage before retrieval and planning |
| `seed/` | Runbook content (`runbooks.py`) and startup ingestion (`ingest.py`) |
| `infra/` | Adapters to everything external: `db.py`, `redis.py`, `sqs.py`, `otel.py`, plus the in-process `eventbus.py`, `idempotency.py`, and `workload.py` |
| `config.py`, `constants.py`, `scenarios.py` | Settings, fixed values, and scenario definitions |
| `main.py` | Application factory and lifespan wiring — routers registered, startup and shutdown ordered. Wiring only |

### `services/remediation-worker/`

A `src`-layout Python package at `src/remediation_worker/`. Not a web service — a long-running
consumer.

| Path | Owns |
|---|---|
| `main.py` | Entrypoint and run loop |
| `consumer.py` | Polls the queue, claims the idempotency key, dispatches to a handler, reports |
| `handlers/` | One module per remediation family: `connection_pool.py`, `cache.py`, `queue.py`, `security.py`, with shared shapes in `types.py` |
| `idempotency.py` | Redis-backed claim and release, so a redelivered job does not execute twice |
| `retry.py` | The delivery budget and jittered exponential backoff |
| `callback.py` | The authenticated completion call back to the API |
| `postmortem.py` | Builds the report and uploads it to S3 |
| `heartbeat.py` | Publishes liveness to Redis |

### `services/incident-war-room/`

A Vite React application, served in production by nginx.

| Path | Owns |
|---|---|
| `src/App.tsx` | Page composition and the single approval dialog |
| `src/components/` | Feature components, with shared primitives in `components/ui/` |
| `src/hooks/` | Stream subscription, media queries, console scroll behavior |
| `src/lib/` | Helpers, including the class-merging utility |
| `src/services/` | HTTP and Server-Sent Events (SSE) clients |
| `src/theme/` | Design tokens |
| `src/types/` | Type declarations, including the generated contracts |
| `nginx.conf` | Static serving plus the API and postmortem proxies |

[The frontend chapter](./frontend.md) goes deeper.

## `packages/contracts/`

The one place a shape shared by two services is defined. Six modules under
`src/tripleten_contracts/`:

| Module | Defines |
|---|---|
| `identifiers.py` | Scenario IDs, runbook IDs, queue and bucket names, Redis keys, tool names, and the read-only/remediation split |
| `states.py` | The state machine's states |
| `telemetry.py` | Metric names, metric types, and latency quantiles |
| `events.py` | The SSE envelope and API request/response models |
| `jobs.py` | The remediation job and its result |
| `security.py` | Redaction categories and validated tool calls |

`packages/contracts/scripts/export_typescript.py` generates `services/incident-war-room/src/types/contracts.gen.ts`
from these models. Run it with `make contracts`. The generated file is not hand-edited — a change
starts in the Python model and is exported.

This package is why the API, the worker, and the browser cannot disagree about a string like
`remediation-jobs`.

## The `uv` workspace

`pyproject.toml` declares three workspace members: `packages/contracts`,
`services/incident-agent-api`, `services/remediation-worker`. The root is a meta-package that
depends on all three explicitly, so a bare `uv sync` installs everything and no `--all-packages`
flag is needed.

Both services use the `src` layout, so an import only resolves through the installed package —
there is no accidental "works because the current directory happens to be right".

Python is pinned to `>=3.11,<3.12`. One `uv.lock` covers the workspace and is committed.

`uv run` resolves the environment itself, so nothing needs activating:

```bash
uv run pytest -v
```

Every task is defined once, under `[tool.poe.tasks]` in `pyproject.toml`. The `Makefile` forwards
to it, which is why `make test-unit` and `uv run poe test-unit` do the same thing on every
platform.

## Test placement

Unit and integration tests live beside the service they cover; whole-stack tiers live at the
root.

| Location | Tier |
|---|---|
| `services/incident-agent-api/tests/unit/` | API unit tests |
| `services/incident-agent-api/tests/integration/` | API against real Postgres, Redis, LocalStack |
| `services/remediation-worker/tests/unit/` | Worker unit tests |
| `services/remediation-worker/tests/integration/` | Worker against real infrastructure |
| `services/incident-war-room/tests/unit/` | Vitest component tests |
| `services/incident-war-room/tests/responsive/` | Vitest breakpoint tests |
| `packages/contracts/tests/` | Contract and provisioning assertions |
| `tests/smoke/` | Post-startup checks against the running stack |
| `tests/e2e/` | Playwright browser scenarios |

[The testing chapter](./testing.md) covers what each tier asserts and how to run it.

## Extension points

These locations show how a change would enter the architecture without bypassing its ownership
rules. They are useful for reading the design even when no change is planned.

| Adding | Goes in | Also |
|---|---|---|
| An HTTP endpoint | `services/incident-agent-api/src/incident_agent_api/api/routes/` | Register the router in `main.py`; put request and response models in `packages/contracts/` |
| An agent tool | `services/incident-agent-api/src/incident_agent_api/agent/tools.py` | Add the name to `ToolName` in `packages/contracts/src/tripleten_contracts/identifiers.py`. A state-changing tool belongs in the remediation set, never in the API's dispatch table — see the import-time assertion described in [architecture](./architecture.md) |
| A worker remediation | `services/remediation-worker/src/remediation_worker/handlers/` | Wire it in `consumer.py` |
| A React component | `services/incident-war-room/src/components/` | Shared primitives go in `components/ui/` |
| A shape two services share | `packages/contracts/src/tripleten_contracts/` | Run `make contracts` to regenerate the TypeScript |
| A telemetry metric | `services/incident-agent-api/src/incident_agent_api/telemetry/registry.py` | Add the name to `MetricName` in `packages/contracts/src/tripleten_contracts/telemetry.py` |
| A provisioning asset | `infra/` | Grafana dashboards and datasources, the Prometheus config, and init scripts are all provisioned from here at startup |
