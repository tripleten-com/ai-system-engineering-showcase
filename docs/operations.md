# Operations

> **What to notice:** the stack is production-shaped, not production-ready. It demonstrates
> health ordering, restart behavior, same-origin delivery, telemetry, and authenticated callbacks
> while remaining intentionally limited to one host and emulated cloud services.

This chapter covers configuring, running, and deploying the stack. The [README](../README.md) has
the quickstart; this chapter explains what to change and what to expect.

## Running it locally

```bash
cp .env.example .env
docker-compose up -d
```

In PowerShell, use `Copy-Item .env.example .env` for the first command.

Nine containers come up self-provisioning: database schema initialization, `CREATE EXTENSION
vector`, LangGraph checkpointer setup, runbook embedding and ingestion, and LocalStack queue and
bucket creation all happen during startup. There are no manual steps.

Every container declares a health check on a 3-second interval and `restart: unless-stopped`.
Redis and Prometheus use a 3-second startup grace period; the other seven use 5 seconds.
Dependencies are ordered on health rather than on start, so the war room does not come up in front
of an API that is not answering yet.

Service URLs are in the README's infrastructure list. Container definitions are in
`infra/docker-compose.yml`; [the architecture chapter](./architecture.md#the-nine-containers) has
the port table.

## Configuration

Everything is in `.env.example`, which ships working values for the whole file. Copy it to `.env`
and edit only what you need.

### Required

| Variable | Default | Controls |
|---|---|---|
| `CALLBACK_SECRET` | a demo value | Signs and verifies the worker completion callback |

Both the API and the worker refuse to start without it. There is deliberately no source-code
fallback: one that silently worked would let the callback authentication gate pass on a stack nobody
configured. A real deployment replaces the demo value.

### Reserved model setting

| Variable | Default | Controls |
|---|---|---|
| `OPENAI_API_KEY` | blank | Reserved for a future model adapter; currently unused |

The current application always runs the deterministic offline planner. Supplying a value does not
activate a live model. See [determinism](./testing.md#determinism).

### Infrastructure

| Variable | Default |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@postgres-vector:5432/tripleten_db` |
| `REDIS_URL` | `redis://redis:6379/0` |
| `LOCALSTACK_ENDPOINT` | `http://localstack:4566` |
| `AWS_REGION` / `AWS_DEFAULT_REGION` | `us-east-1` |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | `test` |

The AWS credentials are LocalStack placeholders. Nothing here reaches real AWS.

### Queues and bucket

| Variable | Default |
|---|---|
| `SQS_CUSTOMER_JOBS_QUEUE` | `customer-jobs` |
| `SQS_CUSTOMER_DLQ_QUEUE` | `customer-dlq` |
| `SQS_REMEDIATION_JOBS_QUEUE` | `remediation-jobs` |
| `SQS_REMEDIATION_DLQ_QUEUE` | `remediation-dlq` |
| `S3_POSTMORTEMS_BUCKET` | `tripleten-cloud-postmortems` |

These names are also asserted by the identifier-parity test. Renaming one is a contract change
across several files, not a local edit.

### Tracing and service config

| Variable | Default |
|---|---|
| `OTEL_SERVICE_NAME` | `incident-agent-api` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://jaeger:4317` |
| `OTEL_EXPORTER_OTLP_INSECURE` | `true` |
| `AGENT_API_URL` | `http://incident-agent-api:8000` |
| `API_PORT` | `8000` |
| `HOST` | `0.0.0.0` |
| `LOG_LEVEL` | `INFO` |

### War room build arguments

| Variable | Default when blank |
|---|---|
| `VITE_CURRICULUM_URL` | The AI Systems Engineering program page |
| `VITE_REPOSITORY_URL` | This repository |
| `VITE_API_BASE_URL` | Same origin |
| `VITE_LOCALSTACK_URL` | `/s3` |

**These four are build arguments, not runtime environment.** Vite bakes `import.meta.env.VITE_*`
into the bundle, so a value set on a running container arrives far too late to matter — changing one
requires a rebuild, not a restart. Compose forwards them from `.env` into the image build.

All four are optional, and blank is correct for every Compose deployment: nginx proxies `/api/` and
`/s3/` internally, so the same-origin defaults work. Override `VITE_API_BASE_URL` only if the API is
genuinely on another origin, and `VITE_LOCALSTACK_URL` only if you publish LocalStack directly
instead of proxying it.

## Single-VM deployment

The stack is designed to run on one host. Clone, provide `.env`, `docker-compose up -d`, and put a
reverse proxy in front of port 3000.

The supplied Compose file publishes the API, databases, LocalStack, and observability ports on all
host interfaces. A remotely reachable deployment must firewall or bind those ports to loopback or
a private network. Normally only the reverse proxy should be public. If Grafana or Jaeger is
intentionally exposed, publish it through an explicitly secured route rather than its raw host
port. In particular, direct access to LocalStack on `:4566` bypasses the nginx `/s3/` read-only
method restriction.

Everything the browser needs is same-origin, because the war room's nginx already proxies the API
and the archived postmortems. That is what makes a single-VM deployment work with no build-time
configuration: the postmortem link is relative, so it is correct on localhost, on a VM, and behind a
reverse proxy alike. Pointing it directly at LocalStack would produce
`http://localhost:4566/...`, which resolves to the *viewer's* laptop and is a dead link for
everyone but the person running the stack.

## The Server-Sent Events (SSE) caveat

A reverse proxy in front of this stack needs three settings, or the live stream breaks in ways that
look like application bugs:

| Setting | Why |
|---|---|
| Response buffering **off** | A buffering proxy holds frames until a block fills, which destroys the only property a stream has |
| Read timeout **long** | A default 60-second read timeout cuts a stream that goes quiet for a minute |
| Response compression **off** on the stream | Compression buffers by nature |

`services/incident-war-room/nginx.conf` already does the first two on `/api/` —
`proxy_buffering off` and `proxy_read_timeout 86400s`. An outer proxy needs the equivalent, because
the innermost correct setting does not save you from an outer one that buffers.

## Operating it

| Task | Command |
|---|---|
| Follow logs | `make logs` |
| Container status | `make ps` |
| Stop | `make down` |
| Verify a deployment | `./scripts/smoke-test.sh` or `.\scripts\smoke-test.ps1` |

The standalone validators need no Python tooling, which is what makes them useful on a host that
only has Docker. See [testing](./testing.md#standalone-validators).

To return the platform to baseline without restarting anything, `POST /api/incidents/reset`. This
is also the only way out of the three terminal states — see
[the state machine](./api-reference.md#the-state-machine).

Grafana opens read-only with anonymous access enabled, defaulting to the golden-signals dashboard.
`admin` / `admin` gets an editing session. Prometheus scrapes every second, and Grafana's minimum
refresh interval is set to match — the default 5 seconds would discard four fifths of the data.

## What this deployment is not

Worth being explicit, because the stack looks more production-shaped than it is:

- **One host.** No horizontal scaling, no load balancing, no failover. Every container is a single
  replica.
- **No high availability.** Postgres and Redis are single instances with no replication.
- **No managed backups.** Data lives in Docker volumes on that one host.
- **Not AWS.** SQS and S3 are LocalStack. Nothing crosses a real cloud boundary.
- **Host ports are open by default.** Compose publishes the backing services on all interfaces;
  firewalling or private bindings are deployment responsibilities.
- **A demo secret in version control.** `.env.example` contains a working `CALLBACK_SECRET` so the
  stack starts on a fresh clone. Any deployment anyone else can reach wants a new one.
- **An unauthenticated window on the postmortem bucket.** The `/s3/` proxy is read-only by
  construction — only `GET` and `HEAD` reach it, so it cannot write or delete — but it is
  unauthenticated, onto a bucket holding nothing but generated demo postmortems.
- **Incidents are simulated.** Nothing here exercises real failure recovery; see
  [the simulation boundary](./architecture.md#the-simulation-boundary).
