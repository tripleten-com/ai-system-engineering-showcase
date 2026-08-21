"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             packages/contracts/src/tripleten_contracts/security.py
Component:          Redaction Vocabulary Contract
Purpose:            The closed set of entity types the PII sanitizer masks, and the exact
                    replacement token rendered for each one.
Interacts With:     incident-agent-api (:8000), incident-war-room (:3000)

Curriculum Project:  Project 4 — Security, PII Redaction & Guardrails
Skills:             PII Sanitization, Contract-First Design, Drift Prevention
Tools:              Python 3.11
"""

from enum import StrEnum


class RedactionType(StrEnum):
    """The entity classes the sanitizer recognises. Closed: an unknown type cannot be emitted.

    Values are **lowercase**, and that is contractual rather than stylistic. The War Room
    renders `[REDACTED: password]` as a pill and every Playwright spec reads that text off the
    DOM, so casing here is asserted end to end.

    `HOSTNAME` is deliberately distinct from `IP`. `ip-10-0-8-12` is a dashed EC2-style
    hostname, not an IPv4 literal, and an IPv4 regex will not catch it — the two need separate
    patterns and therefore separate tokens.
    """

    PASSWORD = "password"
    IP = "ip"
    HOSTNAME = "hostname"
    JWT = "jwt"
    EMAIL = "email"
    AWS_KEY = "aws_key"


def redaction_token(kind: RedactionType) -> str:
    """Returns the replacement token for a masked entity, e.g. `[REDACTED: password]`."""
    return f"[REDACTED: {kind.value}]"


# Materialized once: the negative assertions in the test suite check that a sanitized payload
# contains nothing outside this vocabulary, which is a per-line membership test.
REDACTION_TOKENS: frozenset[str] = frozenset(redaction_token(kind) for kind in RedactionType)
