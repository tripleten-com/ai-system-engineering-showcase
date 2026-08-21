"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/remediation-worker/src/remediation_worker/heartbeat.py
Component:          Worker Liveness Heartbeat
Purpose:            Refreshes the Redis key that serves as this container's health check.
                    remediation-worker publishes no port, so this key IS its liveness signal.
Interacts With:     redis (:6379)

Curriculum Project:  Project 3 — Asynchronous Queues & Cloud Operations
Skills:             Liveness Signalling, TTL Semantics, Resilient Daemons
Tools:              Redis 7, redis-py, Python 3.11
"""

import json
import time

import redis

from remediation_worker.config import get_settings
from tripleten_contracts import RedisKey

# Read verbatim by the Compose health check for this service, by the smoke suite, and by both
# smoke validator scripts. Changing it without changing infra/docker-compose.yml makes the
# container report unhealthy forever and blocks every service gated on it — so the key comes
# from the shared contract, and tests/smoke/test_identifier_parity.py asserts the YAML and the
# shell scripts still agree with it.
HEARTBEAT_KEY = RedisKey.WORKER_HEARTBEAT.value
HEARTBEAT_TTL_SECONDS = 10


def get_redis_client() -> redis.Redis:
    """Creates a synchronous Redis client from the configured URL."""
    return redis.Redis.from_url(get_settings().redis_url, decode_responses=True)


def update_heartbeat(r: redis.Redis) -> None:
    """Refreshes the worker heartbeat key in Redis with a short TTL."""
    payload = json.dumps({"status": "healthy", "timestamp": time.time()})
    r.set(HEARTBEAT_KEY, payload, ex=HEARTBEAT_TTL_SECONDS)
