"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             tests/smoke/test_container_health.py
Component:          Container Health & Port Verification
Purpose:            Validates that all 9 containers are healthy and responding on expected ports.
Interacts With:     All 9 Docker Compose containers

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             Observability, Health Probing, API Architecture, Resilient Services
Tools:              Pytest, HTTPX, Redis, SQLAlchemy, Python 3.11
"""

# Hard imports on purpose. These ship in the workspace dev group, so a missing
# one is a broken environment, not a reason to silently pass an empty run.
import httpx
import pytest
import redis
import sqlalchemy as sa

from tripleten_contracts import RedisKey

create_engine = sa.create_engine
text = sa.text


@pytest.mark.smoke
def test_all_containers_healthy():
    """Validates HTTP, Redis, PostgreSQL, and Worker health across all 9 stack containers."""
    # 1. HTTP endpoints
    endpoints = {
        "war-room": "http://localhost:3000",
        "agent-api": "http://localhost:8000/healthz",
        "grafana": "http://localhost:3001/api/health",
        "prometheus": "http://localhost:9090/-/healthy",
        "jaeger": "http://localhost:16686/",
        "localstack": "http://localhost:4566/_localstack/health",
    }
    with httpx.Client(timeout=5.0) as client:
        for name, url in endpoints.items():
            resp = client.get(url)
            assert resp.status_code == 200, f"Service {name} at {url} returned status {resp.status_code}"

    # 2. Redis PING
    r = redis.Redis(host="localhost", port=6379, decode_responses=True)
    assert r.ping() is True, "Redis ping failed on localhost:6379"

    # 3. Worker Heartbeat in Redis
    hb = r.get(RedisKey.WORKER_HEARTBEAT.value)
    assert hb is not None, "Remediation worker heartbeat missing in Redis"
    assert "healthy" in hb, f"Unexpected worker heartbeat status: {hb}"

    # 4. PostgreSQL TCP Connection
    pg_engine = create_engine("postgresql://postgres:postgres@localhost:5432/tripleten_db")
    with pg_engine.connect() as conn:
        res = conn.execute(text("SELECT 1;")).scalar()
        assert res == 1, "PostgreSQL SELECT 1 failed"
    pg_engine.dispose()
