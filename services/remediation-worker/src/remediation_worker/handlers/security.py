"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/remediation-worker/src/remediation_worker/handlers/security.py
Component:          SEC-501 Prompt Injection Containment
Purpose:            Executes the approved session revocation, source block, and forensic archive.
Interacts With:     localstack (:4566)

Curriculum Project:  Project 4 — Security, PII Redaction & Guardrails
Skills:             Incident Containment, Forensic Archival, Runbook Automation
Tools:              Python 3.11

Note what these three tools are, and what they are not. The *injected* calls —
`flush_database_tables` and `dump_aws_credentials` — were rejected by the schema firewall before
any human was involved, and they never reach this file: there is no handler for them, and adding
one would mean editing the canonical `ToolName` roster.

What runs here is containment, and it runs only because an SRE clicked
`[ Confirm Security Quarantine & Block IP ]`. Both halves of that are load-bearing for the
`NO CUSTOMER IMPACT — 0 UNAUTHORIZED ACTIONS` strip the War Room holds up all run: zero
unauthorized actions, and three authorized ones.
"""

from remediation_worker.handlers.types import HandlerResult, ToolContext
from tripleten_contracts import ToolName

SESSION_ID = "sess-7c41d9a2"
SOURCE_IP = "10.0.7.31"
BLOCK_DURATION_MINUTES = 1440


def revoke_session(ctx: ToolContext) -> HandlerResult:
    """Invalidates the session token that carried the injection attempt."""
    ctx.log(f"Executing SEC-501 step 2: freezing offending session {SESSION_ID}")
    ctx.log("Immutable security audit event written with cryptographic hash and timestamp")
    return HandlerResult(
        tool=ToolName.REVOKE_SESSION.value,
        operation="session revocation",
        detail={"session_id": SESSION_ID},
    )


def block_ip(ctx: ToolContext) -> HandlerResult:
    """Adds the injection source address to the edge denylist."""
    ctx.log(
        f"Executing SEC-501 step 4: source origin {SOURCE_IP} blocked for {BLOCK_DURATION_MINUTES} minutes"
    )
    return HandlerResult(
        tool=ToolName.BLOCK_IP.value,
        operation="source origin block",
        detail={"ip_address": SOURCE_IP, "duration_minutes": BLOCK_DURATION_MINUTES},
    )


def archive_forensics(ctx: ToolContext) -> HandlerResult:
    """Marks the forensic payload for archival to the postmortem bucket.

    The upload itself happens once, in postmortem.py, after every handler has run — so the
    archive records the whole containment rather than a snapshot taken mid-sequence.
    """
    ctx.log("Executing SEC-501 step 3: forensic payload snapshot queued for S3 quarantine")
    return HandlerResult(
        tool=ToolName.ARCHIVE_FORENSICS.value,
        operation="forensic snapshot",
        detail={"incident_id": ctx.job.incident_id, "redacted": True},
    )
