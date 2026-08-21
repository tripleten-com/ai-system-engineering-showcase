"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/remediation-worker/src/remediation_worker/config.py
Component:          Worker Configuration
Purpose:            The single site in this service that reads the environment. Every other
                    module takes Settings as an argument rather than calling os.getenv itself.
Interacts With:     redis (:6379), localstack (:4566), incident-agent-api (:8000)

Curriculum Project:  Cross-cutting — Clean Code & Modular Ports
Skills:             Twelve-Factor Config, Fail-Fast Startup, Secret Hygiene
Tools:              Pydantic Settings, Python 3.11
"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from tripleten_contracts import QueueName


class Settings(BaseSettings):
    """Runtime configuration, resolved once from the environment (and .env if present)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    redis_url: str = "redis://redis:6379/0"
    localstack_endpoint: str = "http://localstack:4566"
    aws_region: str = "us-east-1"
    # See the matching note in the API's config.py: Compose passes both of these and
    # .env.example declares both, so they are read here rather than hardcoded at the client.
    aws_access_key_id: str = "test"
    aws_secret_access_key: SecretStr = SecretStr("test")
    agent_api_url: str = "http://incident-agent-api:8000"
    # Overridable, but the default comes from the shared contract rather than a literal.
    sqs_remediation_jobs_queue: str = QueueName.REMEDIATION_JOBS.value

    # Deliberately no default — see the matching note in the API's config.py. The worker
    # signs its completion callback with this; starting without it would mean every
    # callback is rejected at runtime instead of failing loudly at boot.
    # min_length guards the empty string, same as the API's config.
    callback_secret: SecretStr = Field(..., min_length=1)

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Returns the process-wide Settings instance."""
    # callback_secret is a required field with no default, and pydantic-settings resolves it
    # from the environment at construction time — which mypy cannot see, so it reads the call
    # as missing an argument.
    return Settings()  # type: ignore[call-arg]


# Daemon loop cadence. Not configurable: the Compose health check's 10s heartbeat TTL
# assumes the loop refreshes well inside that window.
POLL_INTERVAL_SECONDS = 2
