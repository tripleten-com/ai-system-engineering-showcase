# Testing

> **What to notice:** the test tiers are evidence for different claims. Unit tests defend local
> logic and contracts, integration tests cross real infrastructure boundaries, smoke tests prove
> provisioning, and browser tests exercise the complete human decision flow.

Four tiers, separated by what infrastructure they need. [The repository map](./repository-map.md)
covers where the files live; this chapter covers what each tier proves and how to run it.

## The four tiers

| Tier | Command | Needs the stack | Proves |
|---|---|---|---|
| Unit | `make test-unit` | No | Pure logic: telemetry math, rank fusion, sanitizer patterns, guardrail validation, idempotency, retry budgets, React components |
| Integration | `make test-integration` | Yes | The same logic against real Postgres, Redis, and LocalStack |
| Smoke | `make smoke` | Yes | The stack came up correctly and provisioned itself |
| End-to-end | `make test-e2e` | Yes | A browser driving all four scenarios through the real UI |

`make test` runs all four in gate order. `make lint` and `make typecheck` need nothing running.

### If `make` is not available

`make` is a forwarder. Every task is defined once under `[tool.poe.tasks]` in `pyproject.toml`, so
the runner works directly and identically on Windows, macOS, and Linux:

```bash
uv run poe test-unit
uv run poe smoke
```

That is not a fallback for exotic setups — `make` is absent from a standard Windows install, which
is why the task runner exists.

## Why a bare `pytest` runs only unit tests

`[tool.pytest.ini_options]` in `pyproject.toml` sets:

```
addopts = "-m 'not integration and not smoke' --import-mode=importlib"
```

So `uv run pytest` is the unit tier however many paths you name — the tiers that need a running
stack are deselected by default and have to opt their marker back in:

```bash
uv run pytest tests/smoke -m smoke -v
```

Which is what the `make` targets do. Prefer them over hand-rolling the marker expression.

`--import-mode=importlib` is there because both services use a `src` layout with no `__init__.py` in their test
packages; without it, the two services' `conftest.py` files would collide on module name.

Declared markers: `unit`, `integration`, `smoke`, `e2e`, and `slow`. The `slow` marker is worth a
note — it exists for tests whose duration *is* the assertion. The human-in-the-loop suite holds a
run at `AWAITING_APPROVAL` for 31 seconds to prove that no timeout auto-approves, so it is marked
rather than shortened.

## Where the tests live

| Location | Tier |
|---|---|
| `packages/contracts/tests/` | Contract and provisioning assertions |
| `services/incident-agent-api/tests/unit/` | API unit |
| `services/incident-agent-api/tests/integration/` | API integration |
| `services/remediation-worker/tests/unit/` | Worker unit |
| `services/remediation-worker/tests/integration/` | Worker integration |
| `services/incident-war-room/tests/unit/` | Vitest components |
| `services/incident-war-room/tests/responsive/` | Vitest breakpoints |
| `tests/smoke/` | Whole-stack readiness |
| `tests/e2e/` | Playwright browser scenarios |

Unit and integration tests sit beside the service they cover. Only the two whole-stack tiers live
at the root, because they do not belong to any single service.

## What the smoke tier asserts

Six files in `tests/smoke/`, run after startup:

| File | Asserts |
|---|---|
| `test_container_health.py` | Every container is up and reporting healthy |
| `test_baseline_telemetry.py` | The API is emitting baseline metrics in the expected bands |
| `test_seed_data_ready.py` | Runbooks are ingested, embedded, and indexed |
| `test_observability_stack.py` | Prometheus is scraping and Grafana's provisioned datasources and dashboards resolve by uid |
| `test_war_room_delivery.py` | The frontend is served and its proxies answer |
| `test_identifier_parity.py` | Canonical identifiers agree across every place they are spelled |

The last one defends against a specific failure. Some identifiers appear in files that cannot
import the Python enum — the Compose health check and both shell smoke validators spell out
`worker:heartbeat` as a literal, and the Grafana provisioning JSON hardcodes datasource and
dashboard uids in every panel. The test asserts all those sites still agree with the contract, so a
rename cannot half-land.

## What the end-to-end tier covers

Nine Playwright spec files plus one shared helper module in `tests/e2e/`, driven against a running
stack:

| Spec | Covers |
|---|---|
| `scenario_1_db_pool.spec.ts` | Database overload, trigger through recovery |
| `scenario_2_cache_stampede.spec.ts` | Cache traffic spike |
| `scenario_3_worker_deadlock.spec.ts` | Queue processing stops |
| `scenario_4_prompt_injection.spec.ts` | Prompt injection containment |
| `terminal_branches.spec.ts` | `REJECTED`, `FAILED`, and `SECURITY_CONTAINED` |
| `sse_reconnect.spec.ts` | Stream loss and recovery |
| `responsive_hitl.spec.ts` | The approval path on narrow viewports |
| `responsive_layouts.spec.ts` | Layout across breakpoints |
| `accessibility.spec.ts` | The accessibility contract |
| `helpers.ts` | Shared drivers, not a spec |

These specs select on `data-testid` values rather than on text or DOM structure — see
[the frontend chapter](./frontend.md#test-ids). Browser configuration is in
`playwright.config.ts`.

## Standalone validators

`scripts/smoke-test.sh` and `scripts/smoke-test.ps1` run the same readiness checks with no Python
tooling and no `make`:

```bash
./scripts/smoke-test.sh
```

```powershell
.\scripts\smoke-test.ps1
```

Useful straight after a deployment, where the test dependencies are not installed. CI runs the bash
one alongside the pytest smoke suite specifically to check the two stay in agreement.

## CI gates

`.github/workflows/ci.yml` defines four jobs with two independent entry gates and a stack-to-E2E
dependency chain:

| Job | Runs | Depends on |
|---|---|---|
| **Lint & unit tests** | Ruff, mypy, Python unit tests, war room unit tests, war room type-check and build | — |
| **Generated contracts are current** | Regenerates the TypeScript contracts and fails if the committed file is stale | — |
| **Stack, smoke & integration** | Brings up all nine containers, then the smoke suite, the bash validator, the integration tests, and the human-in-the-loop invariant | Lint & unit |
| **Playwright E2E** | The browser matrix | Stack, smoke & integration |

The third job also asserts two properties of the shipped images rather than of the code: that the
runtime images contain no test tooling, and that they run as non-root. Both jobs that start
containers dump container logs on failure and tear the stack down afterwards.

The contracts-drift job is the one that keeps `packages/contracts/` honest. A change to a Python
model without running `make contracts` fails CI rather than reaching the browser as a silent type
mismatch.

## Determinism

The suite needs no API key and no network access. The offline planner and the hash-based embeddings
in `services/incident-agent-api/src/incident_agent_api/retrieval/embeddings.py` mean the same input
produces the same output on every run — the same retrieval ranking, the same plan, the same
assertions.

`OPENAI_API_KEY` is reserved configuration in the current codebase; setting it does not select a
different planner. The application always uses the deterministic implementation in `mock_llm.py`.

The recovery decay carries no jitter for the same reason: a test can assert a value on the curve
because the curve is the same every time.
