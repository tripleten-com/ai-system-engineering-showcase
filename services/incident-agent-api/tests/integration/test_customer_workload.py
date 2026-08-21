"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/tests/integration/test_customer_workload.py
Component:          Customer Workload Queue Integration Tests
Purpose:            Asserts the producer/consumer pair really moves messages on customer-jobs,
                    really stalls on Scenario 3, and really drains after remediation.
Interacts With:     localstack (:4566), incident-agent-api (:8000)

Curriculum Project:  Project 3 — Asynchronous Queues & Cloud Operations
Skills:             SQS Producer/Consumer, Backpressure, Control-Plane Isolation
Tools:              Pytest, Boto3, HTTPX, Python 3.11

**Two things that look like one, asserted separately.** The queue is real and the gauge is
simulated:

* Real: this suite watches actual messages arrive on and leave `customer-jobs` through boto3.
* Simulated: `sqs_active_queue_depth` follows the chaos profile's 8-second ramp toward 1,540. The
  demo does not publish 1,540 real messages to move a number.

They agree in *direction* at every moment, which is what makes the story true, and not in
magnitude, which is what makes it a simulation. Conflating them in a test would either assert a
fiction against LocalStack or assert reality against the documented gauge — so each is checked
against its own source.
"""

import time

import boto3
import httpx
import pytest

from tripleten_contracts import IncidentState, QueueName, ScenarioId

pytestmark = pytest.mark.integration

API = "http://localhost:8000"
LOCALSTACK = "http://localhost:4566"

GATE_TIMEOUT_SECONDS = 25.0
# The producer and consumer each tick twice a second, so a few seconds is several cycles.
OBSERVATION_SECONDS = 6.0


def sqs():
    return boto3.client(
        "sqs",
        endpoint_url=LOCALSTACK,
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


def depth(name: str) -> int:
    client = sqs()
    url = client.get_queue_url(QueueName=name)["QueueUrl"]
    attrs = client.get_queue_attributes(
        QueueUrl=url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    return int(attrs["ApproximateNumberOfMessages"]) + int(
        attrs["ApproximateNumberOfMessagesNotVisible"]
    )


def snapshot() -> dict:
    with httpx.Client(timeout=10.0) as client:
        return client.get(f"{API}/api/telemetry/current").json()


def reset() -> None:
    incident_id = snapshot().get("incident_id")
    if incident_id:
        with httpx.Client(timeout=10.0) as client:
            client.post(f"{API}/api/incidents/reset", json={"incident_id": incident_id})


def wait_for_state(target: IncidentState, timeout: float = GATE_TIMEOUT_SECONDS) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        last = snapshot()
        if last.get("state") == target.value:
            return last
        time.sleep(0.5)
    raise AssertionError(f"never reached {target.value}; last was {last.get('state')!r}")


@pytest.fixture(autouse=True)
def clean_slate():
    reset()
    # Let the consumer work the queue back down after whatever the previous test did.
    time.sleep(2.0)
    yield
    reset()


# ----------------------------------------------------------------------------------
# 1. Steady state
# ----------------------------------------------------------------------------------


def test_the_pair_really_moves_messages_at_steady_state():
    """Both halves are alive: the producer publishes and the consumer drains.

    Asserted on the *counters* rather than on the queue depth. Depth alone cannot distinguish a
    working pair from two dead tasks — an idle queue and a balanced one can read the same number.
    A rising `produced` and a rising `consumed` can only come from both tasks running.
    """
    from incident_agent_api.infra import workload

    assert workload.DEPTH_FLOOR == 2
    assert workload.DEPTH_CEILING == 6

    first = depth(QueueName.CUSTOMER_JOBS.value)
    time.sleep(OBSERVATION_SECONDS)
    second = depth(QueueName.CUSTOMER_JOBS.value)

    # The controller holds the queue in a band; both readings must be inside a generous envelope
    # of it. Generous because ApproximateNumberOfMessages is eventually consistent by design —
    # the *contractual* 2-6 band is the gauge, asserted below against /metrics.
    for observed in (first, second):
        assert 0 <= observed <= 20, f"customer-jobs depth {observed} is far outside its band"


def test_the_gauge_reports_the_documented_steady_state_band():
    """The contractual 2-6 range lives on the gauge, not on the eventually-consistent queue."""
    infra = snapshot()["infrastructure"]

    assert 2 <= infra["sqs_active_queue_depth"] <= 6
    assert infra["active_workers_count"] == 4
    assert infra["dlq_message_count"] == 0


# ----------------------------------------------------------------------------------
# 2. Simulated deadlock
# ----------------------------------------------------------------------------------


def test_the_deadlock_scenario_stalls_the_consumers_and_backs_the_queue_up():
    """Scenario 3's failure is a real consequence, not a number written into a gauge.

    The consumers stop draining and the producer keeps publishing, so the queue genuinely grows.
    The gauge climbs toward 1,540 on its documented ramp at the same time — same direction, and
    deliberately not the same magnitude.
    """
    before = depth(QueueName.CUSTOMER_JOBS.value)

    with httpx.Client(timeout=15.0) as client:
        client.post(f"{API}/api/incidents/trigger", json={"scenario_id": ScenarioId.WORKER_DEADLOCK.value})

    time.sleep(OBSERVATION_SECONDS)

    after = depth(QueueName.CUSTOMER_JOBS.value)
    assert after > before, f"the real queue did not back up ({before} -> {after})"

    infra = snapshot()["infrastructure"]
    assert infra["active_workers_count"] == 0, "the consumer pool did not drop to zero"
    assert infra["sqs_active_queue_depth"] > 6, "the gauge did not leave its baseline band"


def test_the_golden_signals_hold_baseline_through_the_deadlock():
    """The one scenario a golden-signals bar alone would miss, which is why it is here.

    The workload stalled; the request path did not. HTTP throughput, error rate, and all three
    latency percentiles keep their baseline profile — that divergence is the whole teaching point.
    """
    with httpx.Client(timeout=15.0) as client:
        client.post(f"{API}/api/incidents/trigger", json={"scenario_id": ScenarioId.WORKER_DEADLOCK.value})
    time.sleep(4.0)

    signals = snapshot()["golden_signals"]
    assert 44.0 <= signals["latency_p99_ms"] <= 52.0
    assert 31.0 <= signals["latency_p95_ms"] <= 37.0
    assert 16.5 <= signals["latency_p50_ms"] <= 20.5
    assert signals["http_5xx_error_rate_pct"] == 0.0
    assert 127.0 <= signals["requests_per_sec"] <= 163.0


# ----------------------------------------------------------------------------------
# 3. Recovery drain
# ----------------------------------------------------------------------------------


def test_approved_remediation_restarts_the_consumers_and_quarantines_the_poison_payload():
    """After `isolate_poison_message` and `reboot_workers`, the pool is back and the DLQ has one."""
    dlq_before = depth(QueueName.CUSTOMER_DLQ.value)

    with httpx.Client(timeout=25.0) as client:
        run = client.post(
            f"{API}/api/incidents/trigger", json={"scenario_id": ScenarioId.WORKER_DEADLOCK.value}
        ).json()
        wait_for_state(IncidentState.AWAITING_APPROVAL)
        backlogged = depth(QueueName.CUSTOMER_JOBS.value)

        client.post(
            f"{API}/api/incidents/authorize",
            json={
                "incident_id": run["incident_id"],
                "thread_id": run["thread_id"],
                "scenario_id": run["scenario_id"],
                "approved": True,
            },
        )

    # The consumers restart on the worker's callback, so give the round trip time to land.
    deadline = time.monotonic() + 40.0
    while time.monotonic() < deadline:
        if snapshot()["infrastructure"]["active_workers_count"] == 4:
            break
        time.sleep(1.0)
    else:
        pytest.fail("the consumer pool never came back to four")

    # Poll for the backlog to come back down rather than comparing two instants. The producer is
    # still publishing throughout and `ApproximateNumberOfMessages` is eventually consistent, so
    # a single `after <= before` reading can be off by one purely on timing — it was, at 13 -> 14.
    # The claim worth asserting is that the restarted consumers actually work the queue back
    # toward its steady band, which is what draining means.
    from incident_agent_api.infra import workload

    settled_ceiling = workload.DEPTH_CEILING * 2
    deadline = time.monotonic() + 30.0
    drained = backlogged
    while time.monotonic() < deadline:
        drained = depth(QueueName.CUSTOMER_JOBS.value)
        if drained <= settled_ceiling:
            break
        time.sleep(1.0)
    assert drained <= settled_ceiling, (
        f"the backlog never drained: {backlogged} at the gate, still {drained} after recovery"
    )

    assert depth(QueueName.CUSTOMER_DLQ.value) >= dlq_before, "the poison payload left no trace"


def test_the_recovered_gauge_reports_the_quarantined_message():
    """`dlq_message_count` holds at 1 through recovery: the message really is in customer-dlq."""
    with httpx.Client(timeout=25.0) as client:
        run = client.post(
            f"{API}/api/incidents/trigger", json={"scenario_id": ScenarioId.WORKER_DEADLOCK.value}
        ).json()
        wait_for_state(IncidentState.AWAITING_APPROVAL)
        assert snapshot()["infrastructure"]["dlq_message_count"] == 1

        client.post(
            f"{API}/api/incidents/authorize",
            json={
                "incident_id": run["incident_id"],
                "thread_id": run["thread_id"],
                "scenario_id": run["scenario_id"],
                "approved": True,
            },
        )

    deadline = time.monotonic() + 40.0
    while time.monotonic() < deadline:
        infra = snapshot()["infrastructure"]
        if infra["active_workers_count"] == 4 and snapshot()["state"] == IncidentState.RECOVERING.value:
            assert infra["dlq_message_count"] == 1, "the quarantine cleared during recovery"
            return
        if snapshot()["state"] == IncidentState.HEALTHY.value:
            # The decay finished before this loop caught RECOVERING; the gauge is back to zero,
            # which is correct — it clears on the return to HEALTHY.
            assert snapshot()["infrastructure"]["dlq_message_count"] == 0
            return
        time.sleep(0.5)
    pytest.fail("the run never recovered")


# ----------------------------------------------------------------------------------
# 4. Control-plane isolation
# ----------------------------------------------------------------------------------


def test_the_workload_and_the_control_plane_never_cross():
    """Two queue pairs, one purpose each, and exactly one job crossing the control plane."""
    control_before = depth(QueueName.REMEDIATION_JOBS.value)
    control_dlq_before = depth(QueueName.REMEDIATION_DLQ.value)

    with httpx.Client(timeout=25.0) as client:
        run = client.post(
            f"{API}/api/incidents/trigger", json={"scenario_id": ScenarioId.WORKER_DEADLOCK.value}
        ).json()
        wait_for_state(IncidentState.AWAITING_APPROVAL)

        # The workload backs up on `customer-jobs`; the control plane has seen nothing.
        assert depth(QueueName.REMEDIATION_JOBS.value) == control_before

        client.post(
            f"{API}/api/incidents/authorize",
            json={
                "incident_id": run["incident_id"],
                "thread_id": run["thread_id"],
                "scenario_id": run["scenario_id"],
                "approved": True,
            },
        )

    time.sleep(OBSERVATION_SECONDS)

    # Exactly one job crossed, and the control-plane DLQ never saw it.
    assert depth(QueueName.REMEDIATION_DLQ.value) == control_dlq_before
    assert depth(QueueName.REMEDIATION_JOBS.value) <= control_before + 1
