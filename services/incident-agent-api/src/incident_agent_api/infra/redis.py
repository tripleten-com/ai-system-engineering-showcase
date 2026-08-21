"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/infra/redis.py
Component:          Redis Connection Lifecycle
Purpose:            Owns the async Redis client created at startup and closed at shutdown.
Interacts With:     redis (:6379)

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             Async Cache Clients, Resource Lifecycle
Tools:              Redis 7, redis-py asyncio, Python 3.11
"""

import redis.asyncio as aioredis

_client: aioredis.Redis | None = None


def init_client(redis_url: str) -> aioredis.Redis:
    """Creates the process-wide Redis client. Called once from the application lifespan."""
    global _client
    _client = aioredis.from_url(redis_url, decode_responses=True)
    return _client


def get_client() -> aioredis.Redis | None:
    """Returns the client, or None before startup. Nullable for the same reason as get_engine."""
    return _client


async def close_client() -> None:
    """Closes the client on shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
