"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/tests/unit/test_config.py
Component:          Configuration Contract Tests
Purpose:            Asserts CALLBACK_SECRET is required with no source-code fallback, and that
                    the demo value appears nowhere in the service source tree.
Interacts With:     None (pure configuration assertions)

Curriculum Project:  Project 4 — Security, PII Redaction & Guardrails
Skills:             Twelve-Factor Config, Fail-Fast Startup, Secret Hygiene
Tools:              Pytest, Pydantic Settings, Python 3.11
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from incident_agent_api.config import Settings, get_settings

# The value that used to be hardcoded as an os.getenv fallback in two source files.
DEMO_SECRET = "tt-incident-callback-secret-key-99382"


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """get_settings is lru_cached; a leaked instance would make these tests lie."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_callback_secret_is_required(monkeypatch):
    """No default. A fallback that silently works means /callback auth passes unconfigured."""
    monkeypatch.delenv("CALLBACK_SECRET", raising=False)
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)
    assert "callback_secret" in str(exc.value).lower()


def test_empty_callback_secret_is_rejected(monkeypatch):
    """CALLBACK_SECRET= is the realistic failure: a stray env line, not an absent one."""
    monkeypatch.setenv("CALLBACK_SECRET", "")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_callback_secret_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("CALLBACK_SECRET", "unit-test-secret")
    assert Settings(_env_file=None).callback_secret.get_secret_value() == "unit-test-secret"


def test_callback_secret_does_not_leak_in_repr(monkeypatch):
    """SecretStr so an exception traceback or a debug log cannot print the shared secret."""
    monkeypatch.setenv("CALLBACK_SECRET", "unit-test-secret")
    assert "unit-test-secret" not in repr(Settings(_env_file=None))


def test_connection_defaults_target_the_compose_service_names(monkeypatch):
    monkeypatch.setenv("CALLBACK_SECRET", "unit-test-secret")
    settings = Settings(_env_file=None)
    assert "postgres-vector:5432" in settings.database_url
    assert "redis:6379" in settings.redis_url
    assert settings.localstack_endpoint == "http://localstack:4566"


def test_no_module_hardcodes_the_demo_secret():
    """The demo value belongs in .env.example and nowhere else in the source tree."""
    src = Path(__file__).resolve().parents[2] / "src"
    offenders = [
        str(p) for p in src.rglob("*.py") if DEMO_SECRET in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"demo secret hardcoded in {offenders}"


def test_no_module_calls_os_getenv_outside_config():
    """One config site per service. os.getenv elsewhere is drift waiting to happen."""
    src = Path(__file__).resolve().parents[2] / "src"
    offenders = [
        str(p.relative_to(src))
        for p in src.rglob("*.py")
        if p.name != "config.py" and "os.getenv" in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"os.getenv called outside config.py in {offenders}"


def test_aws_credentials_are_read_from_the_environment(monkeypatch):
    """They were hardcoded as "test" at the boto3 call site, so setting these did nothing.

    .env.example declares both and infra/docker-compose.yml passes both to this service, which
    made the pair a documented knob that was silently inert. LocalStack ignores the values;
    the point is that a declared environment variable must actually reach the client.
    """
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


def test_the_boto3_client_is_built_from_settings_not_literals(monkeypatch):
    """The regression this guards is a client constructed from string literals.

    Asserts the kwargs handed to boto3 rather than reading them back off the constructed
    client: the credentials live behind private attributes there, and pinning those would make
    this test a boto3-version canary rather than a check on our own wiring.
    """
    monkeypatch.setenv("CALLBACK_SECRET", "unit-test-secret")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "unit-access-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "unit-secret-key")
    get_settings.cache_clear()

    from incident_agent_api.infra import sqs

    captured: dict[str, object] = {}
    monkeypatch.setattr(sqs.boto3, "client", lambda service, **kwargs: captured.update(kwargs, service=service))

    sqs.client("sqs")

    assert captured["aws_access_key_id"] == "unit-access-key"
    assert captured["aws_secret_access_key"] == "unit-secret-key"
    assert captured["endpoint_url"] == "http://localstack:4566"
