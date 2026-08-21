"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/remediation-worker/tests/unit/test_config.py
Component:          Worker Configuration Contract Tests
Purpose:            Asserts the worker reads its LocalStack credentials and its queue name from
                    Settings rather than from literals at the boto3 call site.
Interacts With:     None (pure configuration assertions)

Curriculum Project:  Cross-cutting — Clean Code & Modular Ports
Skills:             Twelve-Factor Config, Secret Hygiene, Drift Prevention
Tools:              Pytest, Pydantic Settings, Python 3.11

The API half of this contract lives in services/incident-agent-api/tests/unit/test_config.py.
Both exist because both services built their boto3 client from hardcoded "test" credentials
while Compose passed the configured ones in — covering only one of them would leave the other
free to regress the same way.
"""

import pytest

from remediation_worker.config import Settings, get_settings
from tripleten_contracts import QueueName


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """get_settings is lru_cached; a leaked instance would make these tests lie."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_aws_credentials_are_read_from_the_environment(monkeypatch):
    """They were hardcoded as "test" at the boto3 call site, so setting these did nothing."""
    monkeypatch.setenv("CALLBACK_SECRET", "unit-test-secret")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "unit-access-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "unit-secret-key")

    settings = Settings(_env_file=None)
    assert settings.aws_access_key_id == "unit-access-key"
    assert settings.aws_secret_access_key.get_secret_value() == "unit-secret-key"


def test_aws_secret_key_does_not_leak_in_repr(monkeypatch):
    """SecretStr for the same reason as CALLBACK_SECRET: it is a credential, dummy or not."""
    monkeypatch.setenv("CALLBACK_SECRET", "unit-test-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "unit-secret-key")
    assert "unit-secret-key" not in repr(Settings(_env_file=None))


def test_aws_credentials_default_to_the_localstack_dummies(monkeypatch):
    """Unset, they must still work offline: LocalStack accepts anything, but boto3 needs a value."""
    monkeypatch.setenv("CALLBACK_SECRET", "unit-test-secret")
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)

    settings = Settings(_env_file=None)
    assert settings.aws_access_key_id == "test"
    assert settings.aws_secret_access_key.get_secret_value() == "test"


def test_the_sqs_client_is_built_from_settings_not_literals(monkeypatch):
    """The regression this guards is a client constructed from string literals.

    Asserts the kwargs handed to boto3 rather than reading them back off the constructed
    client: the credentials live behind private attributes there, and pinning those would make
    this test a boto3-version canary rather than a check on our own wiring.
    """
    monkeypatch.setenv("CALLBACK_SECRET", "unit-test-secret")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "unit-access-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "unit-secret-key")
    get_settings.cache_clear()

    from remediation_worker import consumer

    captured: dict[str, object] = {}
    monkeypatch.setattr(consumer.boto3, "client", lambda service, **kwargs: captured.update(kwargs, service=service))

    consumer.get_sqs_client()

    assert captured["service"] == "sqs"
    assert captured["aws_access_key_id"] == "unit-access-key"
    assert captured["aws_secret_access_key"] == "unit-secret-key"
    assert captured["endpoint_url"] == "http://localstack:4566"


def test_the_remediation_queue_default_comes_from_the_contract(monkeypatch):
    """A retyped queue name is how the control plane and the workload queue get crossed."""
    monkeypatch.setenv("CALLBACK_SECRET", "unit-test-secret")
    monkeypatch.delenv("SQS_REMEDIATION_JOBS_QUEUE", raising=False)
    assert Settings(_env_file=None).sqs_remediation_jobs_queue == QueueName.REMEDIATION_JOBS.value
