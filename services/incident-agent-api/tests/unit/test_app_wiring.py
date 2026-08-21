"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/tests/unit/test_app_wiring.py
Component:          Application Wiring Characterization Tests
Purpose:            Locks the public route surface and app metadata so a refactor cannot
                    silently drop, rename, or unregister an endpoint.
Interacts With:     incident-agent-api (:8000)

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             Characterization Testing, API Contract Stability
Tools:              Pytest, FastAPI, HTTPX, Python 3.11
"""

import httpx
import pytest

from incident_agent_api.main import create_app

# The endpoints Stage 1 exposes. Stages 2-5 add /api/stream and the incident routes;
# this set is a floor, never a ceiling.
EXPECTED_PATHS = {"/healthz", "/metrics", "/api/telemetry/current", "/"}


@pytest.fixture
def app():
    return create_app()


def test_openapi_declares_the_expected_paths(app):
    """Asserts the published contract, not the internal route table.

    FastAPI's include_router does not flatten endpoints into app.routes, and how it
    represents them has changed between versions. The OpenAPI schema is the surface
    clients actually code against, so it is what this test pins.
    """
    declared = set(app.openapi()["paths"])
    missing = EXPECTED_PATHS - declared
    assert not missing, f"the refactor dropped these routes: {sorted(missing)}"


@pytest.mark.parametrize("path", ["/", "/metrics", "/api/telemetry/current"])
async def test_dependency_free_endpoints_respond_200(app, path):
    """These three need no database, Redis, or LocalStack, so they answer without lifespan."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path)
    assert response.status_code == 200


async def test_healthz_reports_unavailable_when_dependencies_are_not_initialized(app):
    """Without lifespan there is no engine or Redis client, so readiness must be 503 — not 500.

    This is the degradation path: /healthz has to answer truthfully while the stack is
    still coming up, which is what Compose's start_period depends on.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert set(body["checks"]) == {
        "postgres",
        "redis",
        "localstack",
        "runbooks_seeded",
        "checkpointer_ready",
    }


def test_healthz_declares_its_response_model(app):
    schema = app.openapi()["paths"]["/healthz"]["get"]["responses"]["200"]["content"]
    assert "application/json" in schema


def test_app_title_is_branded(app):
    """Story-first: the OpenAPI title is user-visible surface, not an internal label."""
    assert "TripleTen Cloud Platform" in app.title


def test_lifespan_is_registered(app):
    """Seeding and connection setup hang off lifespan; losing it silently breaks startup."""
    assert app.router.lifespan_context is not None
