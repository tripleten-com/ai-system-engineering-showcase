"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/remediation-worker/src/remediation_worker/idempotency.py
Component:          Redis Idempotency Lock
Purpose:            Claims a job's idempotency key before any state-changing work, so an
                    at-least-once redelivery re-reports instead of re-remediating.
Interacts With:     redis (:6379)

Curriculum Project:  Project 3 — Asynchronous Queues & Cloud Operations
Skills:             Idempotent Processing, Distributed Locking, At-Least-Once Semantics
Tools:              Redis 7, redis-py, Python 3.11

SQS is at-least-once, which is not a caveat to work around but the delivery contract. A job can
arrive twice because the visibility timeout expired while the first attempt was still running,
because the delete call was lost, or because the worker restarted mid-flight. Every one of those
is normal, and the difference between a correct worker and a dangerous one is whether the second
delivery re-runs `flush_connection_pool`.

`SET key value NX EX ttl` in one round trip, atomically. A `GET` then a `SET` would let two
concurrent deliveries both see an empty key and both proceed, which is precisely the race a
visibility-timeout expiry produces.
"""

import logging

import redis

logger = logging.getLogger("remediation-worker")

# Comfortably longer than the 30s visibility timeout plus the 17.5s worst-case retry elapsed
# time, so a claim cannot expire while the delivery that made it is still being retried.
CLAIM_TTL_SECONDS = 900

_PREFIX = "worker:job"


def claim_key(idempotency_key: str) -> str:
    """Returns the Redis key guarding one job's execution."""
    return f"{_PREFIX}:{idempotency_key}"


def claim(client: redis.Redis | None, idempotency_key: str) -> bool:
    """Atomically claims a job, returning True to the first delivery and False to every repeat.

    A missing or unreachable Redis returns **False** — the opposite of the API's idempotency
    layer, and deliberately so. There, refusing would take the demo down to prevent a duplicate
    job; here, proceeding would run a state-changing remediation with no replay protection at
    all. When the safety mechanism is unavailable, the safe answer is to not act.
    """
    if client is None:
        logger.error("Refusing job %s: no Redis client to claim idempotency with", idempotency_key)
        return False
    try:
        return bool(client.set(claim_key(idempotency_key), "1", nx=True, ex=CLAIM_TTL_SECONDS))
    except Exception:
        logger.exception("Refusing job %s: idempotency claim failed", idempotency_key)
        return False


def release(client: redis.Redis | None, idempotency_key: str) -> None:
    """Drops a claim so a retryable failure can genuinely be retried.

    Called when execution failed and the retry budget still has attempts left. Without it the
    second delivery would see the key from the first and skip the work while reporting success —
    turning a transient failure into a silently unremediated incident.
    """
    if client is None:
        return
    try:
        client.delete(claim_key(idempotency_key))
    except Exception:
        logger.exception("Releasing idempotency claim for %s failed", idempotency_key)
