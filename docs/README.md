# Documentation

This handbook is the engineering deep dive behind the
[showcase overview](../README.md). It is written for readers who already ship software and want to
inspect how an AI-enabled system handles boundaries, state, failure, security, and operations.

Start with the path that matches the question you are trying to answer. The chapters preserve the
code-level detail so every architectural claim can be traced to an implementation or a test.

## Reading order

| Chapter | Read this when |
|---|---|
| [Architecture](./architecture.md) | Start here to see the service boundaries, data flow, and separation between planning and approved job processing |
| [Repository map](./repository-map.md) | Trace an architectural responsibility to its owning package, service, or contract |
| [API reference](./api-reference.md) | You want to call the API, read the streaming contract, or follow the state machine |
| [Incident behavior](./incident-behavior.md) | Examine how deterministic incidents create measurable, scenario-specific behavior |
| [Frontend](./frontend.md) | Study how the War Room presents evidence and protects a high-impact human decision |
| [Testing](./testing.md) | See which claims are covered by unit, integration, smoke, and browser tests |
| [Operations](./operations.md) | Run the stack and understand where its production-shaped design stops |

## Three reader journeys

**Ten-minute technical tour.** Read [architecture](./architecture.md), then
[incident behavior](./incident-behavior.md). Together they show the complete pipeline and the
boundary between deterministic simulation and real system behavior.

**System-design review.** Read [architecture](./architecture.md),
[API reference](./api-reference.md), [testing](./testing.md), and
[operations](./operations.md). This path exposes the state model, security boundary, reliability
mechanisms, verification strategy, and deployment tradeoffs.

**Code and verification deep dive.** Use the [repository map](./repository-map.md) to locate the
owner of a behavior, continue into the [frontend](./frontend.md) or
[API reference](./api-reference.md), and finish with [testing](./testing.md) to see how the contract
is defended.
