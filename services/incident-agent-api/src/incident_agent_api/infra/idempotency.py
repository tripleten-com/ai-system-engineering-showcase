"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/infra/idempotency.py
Component:          Redis Idempotency Locks
Purpose:            Claims a one-shot key in Redis so a repeated /authorize or a redelivered
                    worker callback reports the first outcome instead of re-running it.
Interacts With:     redis (:6379)

Curriculum Project:  Project 3 — Asynchronous Queues & Cloud Operations
Skills:             Idempotent Processing, Distributed Locking, At-Least-Once Semantics
Tools:              Redis 7, redis-py asyncio, Python 3.11

Two callers, two different reasons, one mechanism:

* `/authorize` — a double-clicked button must not enqueue a second remediation job. The key is
  the `(incident_id, thread_id)` pair the telemetry spec names.
* `/callback` — SQS is at-least-once, so the same completion can arrive twice. The key is the
  worker's `idempotency_key`, and a repeat must not restart the decay loop or re-transition the
  state machine.

`SET key value NX EX ttl` is the whole implementation. It is a single round trip and atomic, so
two concurrent requests cannot both believe they won — which a `GET` followed by a `SET` would
allow, and which is exactly the race a double click produces.

Redis is the right store here and Postgres is not, which is the mirror image of the checkpointer
decision: these keys are safety rails on a retry, not durable state. Losing one to an eviction
costs a duplicate job at worst; losing a checkpoint would lose the run.
"""

import logging

from redis.asyncio import Redis

logger = logging.getLogger("incident-agent-api")

# Long enough to outlive any realistic demo run, short enough that a day of demos does not
# accumulate keys. A run that somehow outlives this simply loses replay protection, so the TTL
# is generous rather than tight.
DEFAULT_TTL_SECONDS = 3600

_AUTHORIZE_PREFIX = "idem:authorize"
_CALLBACK_PREFIX = "idem:callback"


def authorize_key(incident_id: str, thread_id: str) -> str:
    """Returns the Redis key guarding one run's approval decision."""
    return f"{_AUTHORIZE_PREFIX}:{incident_id}:{thread_id}"


def callback_key(idempotency_key: str) -> str:
    """Returns the Redis key guarding one worker callback delivery."""
    return f"{_CALLBACK_PREFIX}:{idempotency_key}"


async def claim(client: Redis | None, key: str, value: str = "1", ttl: int = DEFAULT_TTL_SECONDS) -> bool:
    """Atomically claims a key, returning True to the first caller and False to every repeat.

    A missing Redis returns True — the operation proceeds. That is the deliberate choice: with
    no lock available, refusing every request would take the whole demo down to protect against
    a duplicate, which trades a certainty for a possibility. The callers are all idempotent at
    the state-machine level too (an illegal transition is refused regardless), so the lock is
    defence in depth rather than the only guard.
    """
    if client is None:
        logger.warning("Idempotency claim for %s proceeded unguarded: no Redis client", key)
        return True
    try:
        acquired = await client.set(key, value, nx=True, ex=ttl)
    except Exception:
        logger.exception("Idempotency claim for %s failed; proceeding unguarded", key)
        return True
    return bool(acquired)


async def release(client: Redis | None, key: str) -> None:
    """Drops a claim, so a genuinely retryable failure can be retried.

    Used when the work behind a claim did not happen — a dispatch that raised, say. Without
    this, a transient failure would be remembered as a completed attempt and the operator's
    second click would be silently ignored.
    """
    if client is None:
        return
    try:
        await client.delete(key)
    except Exception:
        logger.exception("Releasing idempotency claim %s failed", key)
