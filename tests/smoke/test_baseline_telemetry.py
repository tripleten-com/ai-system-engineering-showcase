"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             tests/smoke/test_baseline_telemetry.py
Component:          Baseline Telemetry Smoke Test
Purpose:            Validates steady-state telemetry output and system_health_status value.
Interacts With:     incident-agent-api (:8000)

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             Telemetry Validation, Golden Signals Smoke Testing
Tools:              Pytest, HTTPX, Python 3.11
"""

import time

import httpx
import pytest

from tripleten_contracts import BASELINE_BANDS

SNAPSHOT_URL = "http://localhost:8000/api/telemetry/current"


@pytest.mark.smoke
def test_baseline_telemetry_snapshot():
    """Validates that GET /api/telemetry/current returns healthy baseline status."""
    with httpx.Client(timeout=5.0) as client:
        resp = client.get(SNAPSHOT_URL)
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "HEALTHY"
        assert data["infrastructure"]["system_health_status"] == 1
        assert 16.5 <= data["golden_signals"]["latency_p50_ms"] <= 20.5
        assert data["golden_signals"]["http_5xx_error_rate_pct"] == 0.0


@pytest.mark.smoke
def test_every_baseline_signal_sits_inside_its_documented_band():
    """Checks the whole roster against the shared bands, not just p50."""
    with httpx.Client(timeout=5.0) as client:
        data = client.get(SNAPSHOT_URL).json()

    for field, (low, high) in BASELINE_BANDS.items():
        value = data["golden_signals"].get(field, data["infrastructure"].get(field))
        assert value is not None, f"{field} missing from the snapshot"
        assert low <= value <= high, f"{field}={value} outside [{low}, {high}]"


@pytest.mark.smoke
def test_the_generator_is_live_not_a_frozen_literal():
    """Two polls a second apart must differ — the assertion the Stage 1 stub could not make."""
    with httpx.Client(timeout=5.0) as client:
        first = client.get(SNAPSHOT_URL).json()
        time.sleep(1.2)
        second = client.get(SNAPSHOT_URL).json()

    low, high = BASELINE_BANDS["latency_p50_ms"]
    for sample in (first, second):
        assert low <= sample["golden_signals"]["latency_p50_ms"] <= high

    assert first["golden_signals"]["latency_p50_ms"] != second["golden_signals"]["latency_p50_ms"], (
        "the telemetry generator is not running: two polls returned identical jitter"
    )
    assert first["timestamp"] != second["timestamp"]
