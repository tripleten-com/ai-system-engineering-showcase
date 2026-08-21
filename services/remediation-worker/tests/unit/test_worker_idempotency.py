"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/remediation-worker/tests/unit/test_worker_idempotency.py
Component:          Idempotency Lock Unit Tests
Purpose:            Asserts a job is claimed atomically once, released on a retryable failure,
                    and refused outright when the lock store is unavailable.
Interacts With:     None (fake Redis)

Curriculum Project:  Project 3 — Asynchronous Queues & Cloud Operations
Skills:             Idempotent Processing, Distributed Locking, At-Least-Once Semantics
Tools:              Pytest, Python 3.11
"""

import pytest

from remediation_worker import idempotency


class FakeRedis:
    """A minimal Redis honouring the NX semantics the claim depends on."""

    def __init__(self, fail: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.fail = fail
        self.set_calls: list[dict] = []

    def set(self, key, value, nx=False, ex=None):
        if self.fail:
            raise ConnectionError("redis is down")
        self.set_calls.append({"key": key, "nx": nx, "ex": ex})
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def delete(self, key):
        if self.fail:
            raise ConnectionError("redis is down")
        self.store.pop(key, None)


KEY = "inc-abcd1234-db:job-00001"


def test_the_first_claim_wins_and_every_repeat_loses():
    """The core property: one delivery executes, the rest report."""
    client = FakeRedis()
    assert idempotency.claim(client, KEY) is True
    assert idempotency.claim(client, KEY) is False
    assert idempotency.claim(client, KEY) is False


def test_the_claim_is_a_single_atomic_set_with_nx_and_a_ttl():
    """A GET-then-SET would let two concurrent deliveries both believe they won.

    That race is not hypothetical: it is exactly what a visibility-timeout expiry produces while
    the first attempt is still running.
    """
    client = FakeRedis()
    idempotency.claim(client, KEY)

    assert len(client.set_calls) == 1
    assert client.set_calls[0]["nx"] is True
    assert client.set_calls[0]["ex"] == idempotency.CLAIM_TTL_SECONDS


def test_the_claim_ttl_outlives_the_retry_budget():
    """A claim that expired mid-retry would let the same job run twice."""
    from remediation_worker import retry

    assert idempotency.CLAIM_TTL_SECONDS > retry.VISIBILITY_TIMEOUT_SECONDS
    assert idempotency.CLAIM_TTL_SECONDS > retry.worst_case_elapsed_seconds()


def test_releasing_lets_the_next_delivery_execute():
    """Used on a retryable failure. Without it a transient error becomes a silent skip."""
    client = FakeRedis()
    assert idempotency.claim(client, KEY) is True
    idempotency.release(client, KEY)
    assert idempotency.claim(client, KEY) is True


def test_distinct_jobs_do_not_collide():
    client = FakeRedis()
    assert idempotency.claim(client, "inc-1:job-1") is True
    assert idempotency.claim(client, "inc-1:job-2") is True
    assert idempotency.claim(client, "inc-2:job-1") is True


@pytest.mark.parametrize("client", [None, FakeRedis(fail=True)], ids=["missing", "unreachable"])
def test_an_unavailable_lock_store_refuses_the_job(client):
    """The safe answer when the safety mechanism is gone is to not act.

    Deliberately the opposite of the API's idempotency layer, where refusing would take the demo
    down to prevent a duplicate. Here, proceeding would run a state-changing remediation with no
    replay protection at all.
    """
    assert idempotency.claim(client, KEY) is False


def test_releasing_against_an_unavailable_store_does_not_raise():
    """Release is best-effort: it runs on a failure path that must not fail again."""
    idempotency.release(None, KEY)
    idempotency.release(FakeRedis(fail=True), KEY)


def test_the_key_is_namespaced_and_derived_from_the_idempotency_key():
    """Namespaced so a job claim cannot collide with the heartbeat or a rate-limit key."""
    key = idempotency.claim_key(KEY)
    assert key.startswith("worker:job:")
    assert key.endswith(KEY)
