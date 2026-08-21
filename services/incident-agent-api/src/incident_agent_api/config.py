"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/config.py
Component:          Service Configuration
Purpose:            The single site in this service that reads the environment. Every other
                    module imports Settings from here rather than calling os.getenv itself.
Interacts With:     postgres-vector (:5432), redis (:6379), localstack (:4566), jaeger (:4317)

Curriculum Project:  Cross-cutting — Clean Code & Modular Ports
Skills:             Twelve-Factor Config, Fail-Fast Startup, Secret Hygiene
Tools:              Pydantic Settings, Python 3.11
"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, resolved once from the environment (and .env if present)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    database_url: str = "postgresql+asyncpg://postgres:postgres@postgres-vector:5432/tripleten_db"
    redis_url: str = "redis://redis:6379/0"
    localstack_endpoint: str = "http://localstack:4566"
    aws_region: str = "us-east-1"
    # LocalStack ignores the values, but .env.example declares both and Compose passes both to
    # this service, so reading them here is what makes those declarations true. Hardcoding
    # "test" at the boto3 call sites made a documented knob silently inert.
    aws_access_key_id: str = "test"
    aws_secret_access_key: SecretStr = SecretStr("test")

    # Deliberately no default. A fallback that silently works would let the
    # POST /api/incidents/{id}/callback auth gate pass on a stack nobody configured,
    # so the service refuses to start instead. The demo value lives in .env.example.
    # min_length guards the empty string: SecretStr("") is a perfectly valid SecretStr,
    # so requiredness alone would still accept CALLBACK_SECRET= from a stray env line.
    callback_secret: SecretStr = Field(..., min_length=1)

    otel_exporter_otlp_endpoint: str = "http://jaeger:4317"
    openai_api_key: SecretStr | None = None
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Returns the process-wide Settings instance."""
    # callback_secret is a required field with no default, and pydantic-settings resolves it
    # from the environment at construction time — which mypy cannot see, so it reads the call
    # as missing an argument.
    return Settings()  # type: ignore[call-arg]
