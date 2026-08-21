"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             packages/contracts/src/tripleten_contracts/jobs.py
Component:          Remediation Job & Worker Callback Contract
Purpose:            The two messages that cross the control plane: the job incident-agent-api
                    publishes to remediation-jobs, and the authenticated completion callback
                    remediation-worker posts back.
Interacts With:     incident-agent-api (:8000), remediation-worker (internal), localstack (:4566)

Curriculum Project:  Project 3 — Asynchronous Queues & Cloud Operations
Skills:             Contract-First Design, Idempotent Processing, Message Schema Design
Tools:              Pydantic 2, Python 3.11

Both models live here rather than in either service because both services need them, and
because a mismatch between them is the failure that strands a run in EXECUTING forever: the
UI has no approval timeout, by design, so a callback the API cannot parse is indistinguishable
from a worker that never finished.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from tripleten_contracts.events import WorkerLogPayload
from tripleten_contracts.identifiers import RunbookId, ScenarioId, ToolName


class CallbackStatus(StrEnum):
    """Terminal outcome the worker reports for a dispatched job."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RemediationJob(BaseModel):
    """The SQS message body on `remediation-jobs`.

    Frozen: the job is a record of what an SRE approved. A consumer that could edit it in
    flight would make the authorization audit trail meaningless.
    """

    model_config = {"frozen": True}

    incident_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    scenario_id: ScenarioId
    job_id: str = Field(min_length=1)
    # Derived as "<incident_id>:<job_id>" by the dispatcher. The worker holds it in Redis before
    # doing state-changing work, so an at-least-once redelivery re-reports rather than re-runs.
    idempotency_key: str = Field(min_length=1)
    runbook_id: RunbookId
    tools: Annotated[list[ToolName], Field(min_length=1)]

    @model_validator(mode="after")
    def _tools_are_all_state_changing(self) -> "RemediationJob":
        """Refuses a job carrying a read-only tool.

        `check_health` and `read_runbook` are planning-phase diagnostics; dispatching one as
        remediation work would put a tool the HITL gate deliberately allows *before* approval
        into the queue that only runs *after* it, blurring the one distinction Project 5 exists
        to demonstrate.
        """
        from tripleten_contracts.identifiers import READ_ONLY_TOOLS

        offenders = sorted(tool.value for tool in self.tools if tool in READ_ONLY_TOOLS)
        if offenders:
            raise ValueError(f"read-only tools may not be dispatched as remediation work: {offenders}")
        return self


class WorkerCallback(BaseModel):
    """The body of POST /api/incidents/{incident_id}/callback.

    One model for both outcomes, with the success and failure fields made mutually exclusive by
    a validator rather than by two models. The worker builds exactly one of these on every
    path including exhaustion, and a single shape is what lets the route reject a half-filled
    body instead of inferring which outcome was meant.
    """

    status: CallbackStatus
    job_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    postmortem_uri: str | None = Field(default=None, description="s3:// URI, required on success")
    error: str | None = Field(default=None, description="Failure reason, required on failure")
    # The execution terminal's content, carried on the callback rather than pushed to a new
    # endpoint. The API surface in telemetry-and-chaos-engine.md §6 admits no worker->API log
    # channel, and adding one would put a write path on the terminal. Riding the callback keeps
    # the contract intact; the trade-off is that these lines land as a burst when the job
    # finishes rather than streaming during it, which for a few-second execution reads fine.
    logs: list[WorkerLogPayload] = Field(default_factory=list, description="Execution terminal lines")

    @model_validator(mode="after")
    def _outcome_carries_its_evidence(self) -> "WorkerCallback":
        """Requires postmortem_uri on success and error on failure, and forbids the other."""
        if self.status is CallbackStatus.SUCCEEDED:
            if not self.postmortem_uri:
                raise ValueError("a succeeded callback must carry postmortem_uri")
            if self.error:
                raise ValueError("a succeeded callback must not carry error")
        else:
            if not self.error:
                raise ValueError("a failed callback must carry error")
            if self.postmortem_uri:
                raise ValueError("a failed callback must not carry postmortem_uri")
        return self
