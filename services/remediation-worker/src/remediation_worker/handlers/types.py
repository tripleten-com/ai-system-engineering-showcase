"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/remediation-worker/src/remediation_worker/handlers/types.py
Component:          Handler Context & Result Types
Purpose:            The two types every tool handler shares: what it is given and what it
                    returns.
Interacts With:     localstack (:4566)

Curriculum Project:  Project 3 — Asynchronous Queues & Cloud Operations
Skills:             Handler Design, Structured Logging
Tools:              Python 3.11, Pydantic 2

**What is real here and what is simulated.** The remediation *plumbing* is entirely real: a job
really crosses SQS, this process really consumes it, the postmortem really lands in LocalStack
S3, and the completion callback is really authenticated with `Bearer $CALLBACK_SECRET`. What the
handlers themselves do is simulated, for the same reason the chaos engine is — nothing was
actually broken, so there is nothing to actually repair. `flush_connection_pool` did not
terminate live Postgres backends, and it must not: the connections it would kill belong to the
API serving the demo.

So each handler names the exact operation its runbook prescribes (`pg_terminate_backend()`,
`SCAN + UNLINK`, `maxReceiveCount=3` redrive) in a log line the War Room's execution terminal
renders and the E2E specs assert, and returns a structured result the postmortem records. That
keeps the demo honest: the line says what a real remediation would run, and the reader can check
it against the runbook.
"""

from dataclasses import dataclass, field
from typing import Any

from tripleten_contracts import RemediationJob, WorkerLogLevel, WorkerLogPayload, WorkerLogSource


@dataclass
class ToolContext:
    """Everything a handler is given: the approved job and a place to record what it did."""

    job: RemediationJob
    logs: list[WorkerLogPayload] = field(default_factory=list)

    def log(
        self,
        message: str,
        source: WorkerLogSource = WorkerLogSource.WORKER,
        level: WorkerLogLevel = WorkerLogLevel.INFO,
    ) -> None:
        """Appends one execution-terminal line.

        Collected rather than pushed: the lines ride back on the completion callback, which the
        API republishes onto the SSE stream. See the note on `WorkerCallback.logs` for why there
        is no separate worker→API log endpoint.
        """
        self.logs.append(WorkerLogPayload(source=source, level=level, message=message))


@dataclass(frozen=True)
class HandlerResult:
    """What a handler did, in a shape the S3 postmortem can record verbatim."""

    tool: str
    operation: str
    detail: dict[str, Any] = field(default_factory=dict)
