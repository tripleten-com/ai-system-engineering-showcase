"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/remediation-worker/tests/unit/test_postmortem_assembly.py
Component:          Postmortem Assembly Unit Tests
Purpose:            Asserts the archival schema, the canonical S3 key convention, and that no
                    unredacted secret can reach the archive.
Interacts With:     None (pure assembly)

Curriculum Project:  Project 3 — Asynchronous Queues & Cloud Operations
Skills:             Structured Archival, Forensic Reporting, Negative Assertions
Tools:              Pytest, Python 3.11
"""

import json
from datetime import UTC, datetime

import pytest

from remediation_worker import postmortem
from remediation_worker.handlers import HANDLERS, ToolContext
from remediation_worker.handlers.types import HandlerResult
from tripleten_contracts import (
    SCENARIO_TOOLS,
    RemediationJob,
    ScenarioId,
    WorkerLogLevel,
    WorkerLogPayload,
    WorkerLogSource,
    postmortem_key,
)

COMPLETED_AT = datetime(2026, 8, 19, 1, 20, 0, tzinfo=UTC)


def make_job(scenario: ScenarioId) -> RemediationJob:
    return RemediationJob(
        incident_id=f"inc-abcd1234-{scenario.value[:3]}",
        thread_id="thread-abc123",
        scenario_id=scenario,
        job_id="job-99214",
        idempotency_key=f"inc-abcd1234-{scenario.value[:3]}:job-99214",
        runbook_id=scenario.runbook,
        tools=list(SCENARIO_TOOLS[scenario]),
    )


def run_handlers(scenario: ScenarioId) -> tuple[RemediationJob, list[HandlerResult], ToolContext]:
    """Runs a scenario's real handlers, which is what a real archive records."""
    job = make_job(scenario)
    ctx = ToolContext(job=job)
    results = [HANDLERS[tool](ctx) for tool in job.tools]
    return job, results, ctx


@pytest.mark.parametrize("scenario", list(ScenarioId), ids=lambda s: s.value)
def test_the_key_follows_the_documented_convention(scenario: ScenarioId):
    """`YYYY-MM-DD-<scenario>.json`, with underscores swapped for hyphens."""
    key = postmortem_key(scenario, COMPLETED_AT.date())
    assert key == f"2026-08-19-{scenario.value.replace('_', '-')}.json"


def test_the_two_keys_named_in_the_docs_are_produced_verbatim():
    """incident-scenarios.md and implementation_plan.md each quote one of these by name."""
    assert (
        postmortem_key(ScenarioId.DB_POOL_EXHAUSTION, COMPLETED_AT.date())
        == "2026-08-19-db-pool-exhaustion.json"
    )
    assert (
        postmortem_key(ScenarioId.PROMPT_INJECTION, COMPLETED_AT.date())
        == "2026-08-19-prompt-injection.json"
    )


@pytest.mark.parametrize("scenario", list(ScenarioId), ids=lambda s: s.value)
def test_the_body_carries_the_full_archival_schema(scenario: ScenarioId):
    """Every field a reader or a test needs to reconstruct what happened."""
    job, results, ctx = run_handlers(scenario)
    body = postmortem.assemble(job, results, ctx.logs, COMPLETED_AT)

    assert body["schema_version"] == postmortem.POSTMORTEM_SCHEMA_VERSION
    assert body["incident_id"] == job.incident_id
    assert body["thread_id"] == job.thread_id
    assert body["scenario_id"] == scenario.value
    assert body["runbook_id"] == scenario.runbook.value
    assert body["job_id"] == job.job_id
    assert body["idempotency_key"] == job.idempotency_key
    assert body["completed_at"] == COMPLETED_AT.isoformat()
    assert body["authorized_by_human"] is True


@pytest.mark.parametrize("scenario", list(ScenarioId), ids=lambda s: s.value)
def test_the_body_records_exactly_the_tools_the_scenario_ran(scenario: ScenarioId):
    job, results, ctx = run_handlers(scenario)
    body = postmortem.assemble(job, results, ctx.logs, COMPLETED_AT)

    assert body["tools_executed"] == [tool.value for tool in SCENARIO_TOOLS[scenario]]
    assert [entry["tool"] for entry in body["operations"]] == body["tools_executed"]
    for entry in body["operations"]:
        assert entry["operation"], "an operation was recorded with no description"


@pytest.mark.parametrize("scenario", list(ScenarioId), ids=lambda s: s.value)
def test_the_body_is_json_serializable(scenario: ScenarioId):
    """It is uploaded with json.dumps; a non-serializable detail would fail at the bucket."""
    job, results, ctx = run_handlers(scenario)
    body = postmortem.assemble(job, results, ctx.logs, COMPLETED_AT)
    assert json.loads(json.dumps(body)) == body


@pytest.mark.parametrize("scenario", list(ScenarioId), ids=lambda s: s.value)
def test_no_unredacted_secret_can_reach_the_archive(scenario: ScenarioId):
    """The archive is durable, so a leak here outlives the run that produced it.

    Asserted against the serialized body rather than a field, and against every scenario's
    secrets rather than only its own — a handler that hardcoded another scenario's credential
    would still be caught.
    """
    from incident_agent_api.scenarios import SCENARIO_SECRETS

    job, results, ctx = run_handlers(scenario)
    serialized = json.dumps(postmortem.assemble(job, results, ctx.logs, COMPLETED_AT))

    for secrets in SCENARIO_SECRETS.values():
        for secret in secrets:
            assert secret not in serialized, f"{secret!r} reached the postmortem archive"


def test_the_execution_log_is_recorded_in_order():
    """The terminal's content is the audit trail; order is what makes it readable."""
    job = make_job(ScenarioId.DB_POOL_EXHAUSTION)
    logs = [
        WorkerLogPayload(source=WorkerLogSource.LOCALSTACK_SQS, level=WorkerLogLevel.INFO, message="first"),
        WorkerLogPayload(source=WorkerLogSource.WORKER, level=WorkerLogLevel.WARN, message="second"),
        WorkerLogPayload(source=WorkerLogSource.LOCALSTACK_S3, level=WorkerLogLevel.INFO, message="third"),
    ]
    body = postmortem.assemble(job, [], logs, COMPLETED_AT)

    assert [entry["message"] for entry in body["execution_log"]] == ["first", "second", "third"]
    assert body["execution_log"][0]["source"] == "LocalStack SQS"
    assert body["execution_log"][1]["level"] == "WARN"


def test_scenario_one_records_the_statement_the_runbook_prescribes():
    """The E2E spec reads `pg_terminate_backend()` off the terminal; the archive keeps it too."""
    job, results, ctx = run_handlers(ScenarioId.DB_POOL_EXHAUSTION)
    body = postmortem.assemble(job, results, ctx.logs, COMPLETED_AT)
    serialized = json.dumps(body)

    assert "pg_terminate_backend" in serialized
    assert "idle in transaction" in serialized


def test_scenario_four_records_all_three_containment_tools():
    """`NO CUSTOMER IMPACT — 0 UNAUTHORIZED ACTIONS` means zero unauthorized, three authorized."""
    job, results, ctx = run_handlers(ScenarioId.PROMPT_INJECTION)
    body = postmortem.assemble(job, results, ctx.logs, COMPLETED_AT)

    assert body["tools_executed"] == ["revoke_session", "block_ip", "archive_forensics"]
    assert body["authorized_by_human"] is True


def test_no_injected_tool_name_appears_in_any_archive():
    """The rejected calls never executed, so they must not appear as operations anywhere."""
    for scenario in ScenarioId:
        job, results, ctx = run_handlers(scenario)
        serialized = json.dumps(postmortem.assemble(job, results, ctx.logs, COMPLETED_AT))
        for injected in ("flush_database_tables", "dump_aws_credentials"):
            assert injected not in serialized
