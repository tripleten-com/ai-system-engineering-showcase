"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             tests/smoke/test_war_room_delivery.py
Component:          War Room Delivery Surface
Purpose:            Asserts the three things the deployed page needs from its own nginx — the bundle,
                    the API proxy, and the same-origin postmortem window.
Interacts With:     incident-war-room (:3000), incident-agent-api (:8000), localstack (:4566)

Curriculum Project:  Cross-cutting — Marketing Delivery
Skills:             Reverse Proxy Verification, Deployment Gating
Tools:              Pytest, HTTPX, Python 3.11

Everything the browser fetches is same-origin: the bundle from `/`, the API through `/api/`, and the
archived postmortem through `/s3/`. That last one is the reason this module exists.

The `/s3/` proxy shipped broken twice, and both failures were quiet. nginx replaces a matched
location prefix with the `proxy_pass` URI — but not when `proxy_pass` names a variable, which it must
here so the container can start before LocalStack resolves. So LocalStack received `/s3/<bucket>/...`
and answered **200 with an empty body**: a link that looked like it worked and downloaded nothing.
The fix needs a `rewrite`, and `set` has to come *before* it because `break` ends the rewrite phase.

Neither mistake is visible in the UI, in a health check, or in a unit test. A smoke assertion that
actually parses the object is the only thing that catches them, which is why this is a deployment
gate rather than a unit test.
"""

import subprocess

import httpx
import pytest

from tripleten_contracts import BucketName

WAR_ROOM = "http://localhost:3000"
LOCALSTACK = "http://localhost:4566"

# Every container that runs application code. All of them must run as a non-root user; the
# war room's image was the one that did not, and nothing checked.
CODE_CONTAINERS = ("incident-war-room", "incident-agent-api", "remediation-worker")

pytestmark = pytest.mark.smoke


@pytest.mark.parametrize("container", CODE_CONTAINERS)
def test_the_container_runs_as_a_non_root_user(container: str) -> None:
    """A stated standard, asserted rather than trusted.

    Both Python images set `USER appuser`; the war room's ran as root because stock `nginx:alpine`
    needs root to bind :80. It now uses `nginxinc/nginx-unprivileged` on :8080. A regression here is
    a one-line Dockerfile change away and is invisible from the outside, which is exactly why this
    is a smoke gate.
    """
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", container, "id", "-u"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, f"could not read the uid in {container}: {result.stderr.strip()}"

    uid = int(result.stdout.strip())
    assert uid != 0, f"{container} runs as root"


def test_the_bundle_is_served():
    response = httpx.get(f"{WAR_ROOM}/", timeout=10.0)

    assert response.status_code == 200
    assert "<div id=\"root\"" in response.text, "the SPA mount point must be in the served HTML"


def test_the_api_is_reachable_same_origin():
    """The browser never talks to :8000 directly, so this path is the one that has to work."""
    response = httpx.get(f"{WAR_ROOM}/api/telemetry/current", timeout=10.0)

    assert response.status_code == 200
    body = response.json()
    assert "golden_signals" in body and "infrastructure" in body


def test_the_retrieval_probe_is_reachable_same_origin():
    """The RAG Inspector's query box posts here; a broken proxy would make the probe look scripted."""
    response = httpx.post(
        f"{WAR_ROOM}/api/retrieval/search",
        json={"query": "postgres connection pool exhausted", "limit": 1},
        timeout=15.0,
    )

    assert response.status_code == 200
    assert response.json()["results"][0]["runbook_id"] == "RB-104"


def _first_postmortem_key() -> str | None:
    """Returns any archived object key, or None when no run has completed yet.

    Asked of LocalStack directly rather than through the proxy: this is the fixture lookup, and
    proving the proxy works is the *assertion*, so it must not depend on the proxy.
    """
    listing = httpx.get(
        f"{LOCALSTACK}/{BucketName.POSTMORTEMS.value}", params={"list-type": "2"}, timeout=10.0
    )
    if listing.status_code != 200:
        return None
    keys = [
        chunk.split("</Key>")[0]
        for chunk in listing.text.split("<Key>")[1:]
    ]
    return keys[0] if keys else None


def test_the_postmortem_window_serves_the_real_object():
    """Parsed, not merely fetched.

    A status check alone passed while the proxy was returning an empty 200 — the exact bug this
    guards. Only decoding the body proves the object came back.
    """
    key = _first_postmortem_key()
    if key is None:
        pytest.skip("no postmortem archived yet; run a scenario to completion first")

    response = httpx.get(f"{WAR_ROOM}/s3/{BucketName.POSTMORTEMS.value}/{key}", timeout=10.0)

    assert response.status_code == 200
    assert int(response.headers["content-length"]) > 0, "an empty 200 is the failure mode this exists for"

    body = response.json()
    assert body["authorized_by_human"] is True
    assert body["scenario_id"], "the archive must name the scenario it came from"
    assert body["tools_executed"], "the archive must record what actually ran"


def test_the_postmortem_window_is_read_only():
    """Only GET and HEAD reach the bucket.

    It is an unauthenticated window by design, and that is only defensible while it cannot be
    written through.
    """
    key = _first_postmortem_key() or "probe.json"
    url = f"{WAR_ROOM}/s3/{BucketName.POSTMORTEMS.value}/{key}"

    for method in ("PUT", "POST", "DELETE"):
        response = httpx.request(method, url, content=b"{}", timeout=10.0)
        assert response.status_code == 403, f"{method} must be refused, got {response.status_code}"

    assert httpx.head(url, timeout=10.0).status_code in (200, 404), "HEAD must be allowed"
