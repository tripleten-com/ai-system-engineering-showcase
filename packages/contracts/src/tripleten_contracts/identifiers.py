"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             packages/contracts/src/tripleten_contracts/identifiers.py
Component:          Canonical Identifier Registry
Purpose:            Single source of truth for the scenario, runbook, queue, bucket, tool, and
                    Redis key names shared by incident-agent-api and remediation-worker.
Interacts With:     incident-agent-api (:8000), remediation-worker (internal)

Curriculum Project:  Cross-cutting — Modular Ports & Contract Design
Skills:             Contract-First Design, Enum Modelling, Drift Prevention
Tools:              Python 3.11, Pydantic 2
"""

from datetime import date
from enum import StrEnum


class RunbookId(StrEnum):
    """The four seeded emergency runbooks in the `knowledge_runbooks` pgvector table."""

    RB_104 = "RB-104"
    RB_208 = "RB-208"
    RB_312 = "RB-312"
    SEC_501 = "SEC-501"


class ScenarioId(StrEnum):
    """The four demo incident scenarios."""

    DB_POOL_EXHAUSTION = "db_pool_exhaustion"
    CACHE_THUNDERING_HERD = "cache_thundering_herd"
    WORKER_DEADLOCK = "worker_deadlock"
    PROMPT_INJECTION = "prompt_injection"

    @property
    def runbook(self) -> RunbookId:
        """The runbook this scenario retrieves."""
        return _SCENARIO_RUNBOOK[self]

    @property
    def causes_outage(self) -> bool:
        """Scenario 4 is containment-only: no chaos math, no metric spike, no recovery decay."""
        return self is not ScenarioId.PROMPT_INJECTION


_SCENARIO_RUNBOOK: dict[ScenarioId, RunbookId] = {
    ScenarioId.DB_POOL_EXHAUSTION: RunbookId.RB_104,
    ScenarioId.CACHE_THUNDERING_HERD: RunbookId.RB_208,
    ScenarioId.WORKER_DEADLOCK: RunbookId.RB_312,
    ScenarioId.PROMPT_INJECTION: RunbookId.SEC_501,
}


class QueueName(StrEnum):
    """LocalStack SQS queues. Each source queue redrives to its DLQ at maxReceiveCount=3."""

    CUSTOMER_JOBS = "customer-jobs"
    CUSTOMER_DLQ = "customer-dlq"
    REMEDIATION_JOBS = "remediation-jobs"
    REMEDIATION_DLQ = "remediation-dlq"


class BucketName(StrEnum):
    """LocalStack S3 buckets."""

    POSTMORTEMS = "tripleten-cloud-postmortems"


class RedisKey(StrEnum):
    """Redis keys read by more than one component.

    `WORKER_HEARTBEAT` is the liveness signal for `remediation-worker`, which publishes no
    host port. Five things read it — the worker that writes it, the Compose health check that
    gates `depends_on: service_healthy` on it, the smoke suite, and both smoke validator
    scripts. Only two of those can import Python, so this enum cannot be the *only* copy; what
    it can do is make the two Python sites derive from one declaration and give
    `test_identifier_parity.py` a literal to police the YAML and the shell against.
    """

    WORKER_HEARTBEAT = "worker:heartbeat"


class ToolName(StrEnum):
    """The nine canonical agent tools.

    The six remediation tools execute only after an explicit
    ``POST /api/incidents/authorize``; ``check_health`` and ``read_runbook`` are read-only
    diagnostics available during planning.
    """

    CHECK_HEALTH = "check_health"
    READ_RUNBOOK = "read_runbook"
    FLUSH_CONNECTION_POOL = "flush_connection_pool"
    WARM_CACHE = "warm_cache"
    ISOLATE_POISON_MESSAGE = "isolate_poison_message"
    REBOOT_WORKERS = "reboot_workers"
    REVOKE_SESSION = "revoke_session"
    BLOCK_IP = "block_ip"
    ARCHIVE_FORENSICS = "archive_forensics"


# The nine tools split in two, and the split is a safety boundary rather than a taxonomy.
# check_health and read_runbook are diagnostics the agent may call while it is still planning;
# the other seven change state and may not run before POST /api/incidents/authorize. The HITL
# gate test asserts against exactly these two sets, which is why they live here and not in a
# service module.
READ_ONLY_TOOLS: frozenset[ToolName] = frozenset({ToolName.CHECK_HEALTH, ToolName.READ_RUNBOOK})
REMEDIATION_TOOLS: frozenset[ToolName] = frozenset(ToolName) - READ_ONLY_TOOLS


# The remediation tools each scenario dispatches, in execution order. Scenario 3 quarantines
# before it reboots: fresh consumers must not pick the poison payload back up, which is also
# the order RB-312's mitigation procedure gives and the order its approval prompt names.
SCENARIO_TOOLS: dict[ScenarioId, tuple[ToolName, ...]] = {
    ScenarioId.DB_POOL_EXHAUSTION: (ToolName.FLUSH_CONNECTION_POOL,),
    ScenarioId.CACHE_THUNDERING_HERD: (ToolName.WARM_CACHE,),
    ScenarioId.WORKER_DEADLOCK: (ToolName.ISOLATE_POISON_MESSAGE, ToolName.REBOOT_WORKERS),
    ScenarioId.PROMPT_INJECTION: (ToolName.REVOKE_SESSION, ToolName.BLOCK_IP, ToolName.ARCHIVE_FORENSICS),
}


# The exact text on the human-in-the-loop button, per scenario.
#
# Contractual, not cosmetic: every Playwright spec in tests/e2e/ reads this string off the DOM,
# the agent puts it in the plan it drafts, and the War Room renders it. It had drifted into
# three different spellings, with "Authorize Poison Pill DLQ Quarantine & Worker Reboot" and
# "Authorize Worker Reboot & Message Isolation" both naming the same button. This table is
# now the only place the text exists.
APPROVAL_PROMPT: dict[ScenarioId, str] = {
    ScenarioId.DB_POOL_EXHAUSTION: "Authorize DB Pool Drain & Recycle",
    ScenarioId.CACHE_THUNDERING_HERD: "Authorize Cache Warm-Up & Orphan Purge",
    ScenarioId.WORKER_DEADLOCK: "Authorize DLQ Quarantine & Worker Reboot",
    ScenarioId.PROMPT_INJECTION: "Confirm Security Quarantine & Block IP",
}


def postmortem_key(scenario_id: ScenarioId, day: date) -> str:
    """Returns the S3 object key for a run's postmortem: YYYY-MM-DD-<scenario>.json.

    The scenario segment is the scenario id with underscores swapped for hyphens, which is what
    produces the `2026-08-19-db-pool-exhaustion.json` and `YYYY-MM-DD-prompt-injection.json`
    names quoted in incident-scenarios.md and the implementation plan.
    """
    return f"{day.isoformat()}-{scenario_id.value.replace('_', '-')}.json"
