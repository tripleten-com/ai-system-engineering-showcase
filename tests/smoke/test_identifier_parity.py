"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             tests/smoke/test_identifier_parity.py
Component:          Canonical Identifier Drift Guard
Purpose:            Fails if any canonical identifier is hardcoded in service source instead
                    of imported from tripleten_contracts.
Interacts With:     None (static source analysis)

Curriculum Project:  Cross-cutting — Modular Ports & Contract Design
Skills:             Drift Prevention, Static Analysis, Contract-First Design
Tools:              Pytest, Python 3.11
"""

import re
from pathlib import Path

import pytest

from tripleten_contracts import BucketName, QueueName, RedisKey, RunbookId, ScenarioId, ToolName

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_SRC = sorted((REPO_ROOT / "services").rglob("src/**/*.py"))

# runbooks.py holds the seed corpus verbatim. Its RB-* ids are document content, not
# code referring to an identifier, so it is exempt.
EXEMPT = {"runbooks.py"}

CANONICAL = sorted(
    {q.value for q in QueueName}
    | {b.value for b in BucketName}
    | {s.value for s in ScenarioId}
    | {r.value for r in RunbookId}
    | {t.value for t in ToolName}
)


def test_the_service_source_tree_was_actually_found():
    """Guards the guard: a wrong glob would make every case below pass vacuously."""
    assert len(SERVICE_SRC) > 5, f"expected service sources, found {SERVICE_SRC}"


@pytest.mark.smoke
@pytest.mark.parametrize("literal", CANONICAL)
def test_canonical_literal_is_not_hardcoded_in_service_source(literal):
    pattern = re.compile(rf'["\']{re.escape(literal)}["\']')
    offenders = [
        str(p.relative_to(REPO_ROOT)).replace("\\", "/")
        for p in SERVICE_SRC
        if p.name not in EXEMPT and pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == [], (
        f"{literal!r} is hardcoded in {offenders}; import it from tripleten_contracts instead"
    )


# The heartbeat key is the one canonical literal that cannot live only in the enum. Five things
# read it and three of them — the Compose health check and the two smoke validator scripts —
# cannot import Python. So instead of forbidding the literal outside contracts, assert that
# every site still spells it the same way. The hazard is silent and total: rename the key in
# heartbeat.py and the worker keeps writing happily to the new one while the health check keeps
# reading the old one, so remediation-worker reports unhealthy forever and every service gated
# on `depends_on: service_healthy` never starts.
HEARTBEAT_READERS = [
    "infra/docker-compose.yml",
    "scripts/smoke-test.sh",
    "scripts/smoke-test.ps1",
    "tests/smoke/test_container_health.py",
]


@pytest.mark.smoke
@pytest.mark.parametrize("relative_path", HEARTBEAT_READERS)
def test_heartbeat_key_readers_agree_with_the_contract(relative_path):
    """Every non-importing reader of the heartbeat key must spell it as the contract does."""
    source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    key = RedisKey.WORKER_HEARTBEAT.value

    # The Python reader imports the enum rather than repeating the literal, which is the
    # preferred form; the YAML and the shell have no choice but to repeat it.
    imports_it = "RedisKey.WORKER_HEARTBEAT" in source
    assert imports_it or key in source, (
        f"{relative_path} reads the worker heartbeat but names neither {key!r} nor "
        f"RedisKey.WORKER_HEARTBEAT; a renamed key would leave remediation-worker "
        f"permanently unhealthy with no test failing"
    )


@pytest.mark.smoke
def test_the_heartbeat_reader_list_is_complete():
    """Guards the guard: a new reader added anywhere must be registered above.

    Without this, the check above degrades quietly — someone adds a sixth site, the list still
    names four, and the drift it exists to prevent walks straight back in.
    """
    key = RedisKey.WORKER_HEARTBEAT.value
    searched = [
        *REPO_ROOT.glob("infra/*.yml"),
        *REPO_ROOT.glob("scripts/*.sh"),
        *REPO_ROOT.glob("scripts/*.ps1"),
        *(REPO_ROOT / "tests").rglob("*.py"),
        *SERVICE_SRC,
    ]
    found = {
        str(path.relative_to(REPO_ROOT)).replace("\\", "/")
        for path in searched
        if key in path.read_text(encoding="utf-8") or "RedisKey.WORKER_HEARTBEAT" in path.read_text(encoding="utf-8")
    }
    # heartbeat.py is the writer, and this module names the enum member in its own assertions.
    expected = set(HEARTBEAT_READERS) | {
        "services/remediation-worker/src/remediation_worker/heartbeat.py",
        "tests/smoke/test_identifier_parity.py",
    }
    assert found == expected, f"heartbeat key sites drifted: {sorted(found ^ expected)}"
