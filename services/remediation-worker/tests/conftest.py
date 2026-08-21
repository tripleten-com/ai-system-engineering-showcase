"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/remediation-worker/tests/conftest.py
Component:          Worker Pytest Fixtures & Environment
Purpose:            Provides reusable fixtures for worker unit and integration testing.
Interacts With:     Redis, LocalStack SQS

Curriculum Project:  Project 3 — Asynchronous Queues & Cloud Operations
Skills:             Test Fixtures, SQS Mocking, Redis Test State
Tools:              Pytest, Redis, Boto3, Python 3.11
"""

import os

import pytest

# Settings requires CALLBACK_SECRET with no fallback, so tests must supply one
# explicitly. Set here rather than relying on a developer's .env: unit tests must
# behave identically on a clean CI checkout where no .env exists.
os.environ.setdefault("CALLBACK_SECRET", "test-callback-secret")


@pytest.fixture
def mock_sqs_message():
    """Returns a sample SQS message fixture for unit tests."""
    return {
        "MessageId": "msg-12345",
        "ReceiptHandle": "handle-67890",
        "Body": '{"scenario_id": "db_pool_exhaustion", "idempotency_key": "inc-1:job-1"}',
    }
