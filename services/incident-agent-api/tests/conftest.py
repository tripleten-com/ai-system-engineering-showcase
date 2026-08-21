"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/tests/conftest.py
Component:          Global Pytest Fixtures & Environment Config
Purpose:            Provides reusable fixtures for backend unit and integration testing.
Interacts With:     FastAPI, PostgreSQL, Redis, LocalStack

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             Test Fixtures, Async Client Setup, Isolation
Tools:              Pytest, Pytest-Asyncio, HTTPX, Python 3.11
"""

import json
import os
import subprocess
from functools import partial
from pathlib import Path
from typing import NamedTuple

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

# Settings requires CALLBACK_SECRET with no fallback, so tests must supply one
# explicitly. Set here rather than relying on a developer's .env: unit tests must
# behave identically on a clean CI checkout where no .env exists.
os.environ.setdefault("CALLBACK_SECRET", "test-callback-secret")

# The live stack the integration tier drives.
LIVE_API_URL = "http://localhost:8000"

# Generous on purpose: the engine ticks once a second, so reading N frames takes at least N
# seconds. A timing-flaky failure here reads as a broken stream and would cost far more to
# diagnose than the seconds a tight bound would save.
SSE_READ_TIMEOUT = 20.0


@pytest.fixture
async def async_client():
    """Provides an asynchronous test HTTP client bound to FastAPI application via ASGITransport."""
    from incident_agent_api.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def parse_sse_frame(raw: str) -> dict[str, str] | None:
    """Parses one SSE frame the way a standard EventSource client does.

    Implements the subset of the WHATWG line-parsing rules this contract uses: `field: value`
    with one optional leading space stripped from the value, `:`-prefixed comments ignored, and
    repeated `data` fields joined with newlines. Returns None for a frame carrying no `data` —
    the `retry:` preamble is exactly that, and it is a directive rather than an event.

    Tests parse with this rather than with a regex or a naive split so "parseable by a standard
    EventSource client" is actually demonstrated, not assumed.
    """
    fields: dict[str, str] = {}
    data_lines: list[str] = []
    for line in raw.split("\n"):
        if not line or line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if name == "data":
            data_lines.append(value)
        else:
            fields[name] = value

    if not data_lines:
        return None
    fields["data"] = "\n".join(data_lines)
    return fields


class StreamCapture(NamedTuple):
    """What one read of GET /api/stream observed."""

    headers: httpx.Headers
    preamble: str
    """Raw text of every non-event block, which is where the `retry:` directive arrives."""
    frames: list[dict]
    """Parsed JSON envelopes of the data-carrying frames, in arrival order."""


def read_sse_stream(base_url: str, count: int, incident_id: str | None = None) -> StreamCapture:
    """Opens /api/stream, reads until `count` data frames arrive, then closes it.

    Builds its own client rather than taking one, so several reads can run concurrently in
    threads to prove the stream broadcasts rather than serving each client privately.
    """
    params = {"incident_id": incident_id} if incident_id is not None else None
    frames: list[dict] = []
    preamble = ""
    buffer = ""

    with httpx.Client(base_url=base_url, timeout=SSE_READ_TIMEOUT) as client:
        with client.stream("GET", "/api/stream", params=params) as response:
            response.raise_for_status()
            for chunk in response.iter_text():
                buffer += chunk
                while "\n\n" in buffer:
                    block, buffer = buffer.split("\n\n", 1)
                    parsed = parse_sse_frame(block)
                    if parsed is None:
                        preamble += block
                        continue
                    frames.append(json.loads(parsed["data"]))
                    if len(frames) >= count:
                        return StreamCapture(response.headers, preamble, frames)

    raise AssertionError(f"stream closed after {len(frames)} of {count} expected frames")


@pytest.fixture
def sse_parser():
    """Exposes the frame parser to the unit tier."""
    return parse_sse_frame


@pytest.fixture
def read_stream():
    """Exposes the stream reader, bound to the live stack, to the integration tier.

    Handed out as a fixture rather than imported: the root pytest config runs with
    `--import-mode=importlib` and the tests packages carry no `__init__.py`, so `conftest`
    is not importable by name from a test module.
    """
    return partial(read_sse_stream, LIVE_API_URL)

def stack_callback_secret() -> str:
    """Returns the secret the *running* incident-agent-api container is actually using.

    Read out of the container rather than guessed, because every cheaper source is wrong in some
    environment this suite runs in:

    * `os.environ` — the unit-tier setdefault above installs a test value so an offline checkout
      works, and that shadows the container's on a local integration run.
    * `.env` / `.env.example` — Compose interpolates `${CALLBACK_SECRET}` from the *shell* first,
      so CI's job-level `CALLBACK_SECRET: ci-callback-secret` wins over the file and the file is
      then misleading. This is the failure that reached CI twice.

    `printenv` inside the container is authoritative by construction: it is the value the process
    verifying the bearer token holds. The integration tier already depends on Docker.
    """
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "incident-agent-api", "printenv", "CALLBACK_SECRET"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    secret = result.stdout.strip()
    if not secret:
        raise AssertionError(
            "could not read CALLBACK_SECRET from the incident-agent-api container; "
            f"is the stack up? stderr={result.stderr.strip()!r}"
        )
    return secret


@pytest.fixture(scope="session")
def callback_secret() -> str:
    """Session-scoped: one `docker compose exec` for the whole integration run."""
    return stack_callback_secret()
