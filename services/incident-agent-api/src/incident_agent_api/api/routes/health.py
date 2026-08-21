"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/api/routes/health.py
Component:          Readiness & Liveness Probe
Purpose:            GET /healthz — returns 503 until PostgreSQL, Redis, LocalStack, the runbook
                    seed, and the LangGraph checkpointer are all ready.
Interacts With:     postgres-vector (:5432), redis (:6379), localstack (:4566)

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             Health Probing, Readiness Gating, Resilient Services
Tools:              FastAPI, SQLAlchemy, redis-py, Python 3.11
"""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from incident_agent_api.constants import MINIMUM_SEEDED_RUNBOOKS
from incident_agent_api.infra.db import get_engine
from incident_agent_api.infra.redis import get_client
from incident_agent_api.infra.sqs import check_localstack_sync

logger = logging.getLogger("incident-agent-api")

router = APIRouter()


class HealthCheckDetails(BaseModel):
    """Detailed health check statuses across platform dependencies."""

    postgres: str = Field(description="PostgreSQL + pgvector connection status")
    redis: str = Field(description="Redis cache and state connection status")
    localstack: str = Field(description="LocalStack SQS and S3 readiness status")
    runbooks_seeded: int | str = Field(description="Number of seeded runbooks in pgvector")
    checkpointer_ready: bool | str = Field(description="LangGraph checkpointer table readiness")


class HealthResponse(BaseModel):
    """Liveness and readiness response model for /healthz probe."""

    status: str = Field(description="Overall health status: ok or unavailable")
    checks: HealthCheckDetails = Field(description="Per-dependency health check breakdown")


@router.get("/healthz", response_model=HealthResponse)
async def healthz(response: Response) -> HealthResponse:
    """Evaluates readiness of PostgreSQL, Redis, LocalStack, knowledge seed, and checkpointer."""
    checks: dict[str, Any] = {
        "postgres": "unavailable",
        "redis": "unavailable",
        "localstack": "unavailable",
        "runbooks_seeded": "unavailable",
        "checkpointer_ready": "unavailable",
    }
    all_ok = True

    # 1. PostgreSQL Check (Read-Only)
    db_engine = get_engine()
    if db_engine is not None:
        try:
            async with db_engine.connect() as conn:
                await conn.execute(text("SELECT 1;"))
                checks["postgres"] = "ok"

                # Check seed runbooks
                count_res = await conn.execute(
                    text("SELECT count(*) FROM knowledge_runbooks WHERE embedding IS NOT NULL;")
                )
                count = count_res.scalar() or 0
                checks["runbooks_seeded"] = count

                # Check checkpointer table
                # Qualified to the search path rather than matching table_name across every
                # schema: an unrelated `checkpoints` table anywhere in the database would
                # otherwise report the LangGraph checkpointer ready before setup() had run.
                chk_res = await conn.execute(text("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_name = 'checkpoints'
                          AND table_schema = current_schema()
                    );
                """))
                chk_exists = chk_res.scalar() or False
                checks["checkpointer_ready"] = bool(chk_exists)
        except Exception as e:
            logger.debug(f"PostgreSQL health check failed: {e}")
            all_ok = False
    else:
        all_ok = False

    # 2. Redis Check
    redis_client = get_client()
    if redis_client is not None:
        try:
            ping_res = await redis_client.ping()
            if ping_res:
                checks["redis"] = "ok"
            else:
                all_ok = False
        except Exception as e:
            logger.debug(f"Redis health check failed: {e}")
            all_ok = False
    else:
        all_ok = False

    # 3. LocalStack SQS & S3 Check (offloaded so boto3's blocking IO stays off the event loop)
    localstack_ok = await asyncio.to_thread(check_localstack_sync)
    if localstack_ok:
        checks["localstack"] = "ok"
    else:
        all_ok = False

    # Readiness invariant assertions
    if not isinstance(checks["runbooks_seeded"], int) or checks["runbooks_seeded"] < MINIMUM_SEEDED_RUNBOOKS:
        all_ok = False
    if checks["checkpointer_ready"] is not True:
        all_ok = False

    overall_status = "ok" if all_ok else "unavailable"
    if not all_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status=overall_status,
        checks=HealthCheckDetails(
            postgres=checks["postgres"],
            redis=checks["redis"],
            localstack=checks["localstack"],
            runbooks_seeded=checks["runbooks_seeded"],
            checkpointer_ready=checks["checkpointer_ready"],
        ),
    )
