"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/tests/unit/test_sse_event_envelope.py
Component:          SSE Event Envelope Serialization Tests
Purpose:            Unit test suite for Server-Sent Event envelope schema conformance and
                    wire framing, per testing-strategy-and-specs.md §4.E.
Interacts With:     None (pure serialization)

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             SSE Schema Modeling, Event Serialization, Discriminated Unions
Tools:              Pytest, Pydantic 2, Python 3.11
"""

import json
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError

from tripleten_contracts import (
    INCIDENT_EVENT_ADAPTER,
    SSE_RETRY_MS,
    AgentPhase,
    AgentThoughtEvent,
    AgentThoughtPayload,
    EventType,
    GoldenSignals,
    GuardrailVerdict,
    IncidentState,
    InfrastructureMetrics,
    LogStreamEvent,
    LogStreamPayload,
    MetricsSnapshot,
    MetricsUpdateEvent,
    RagMatchEvent,
    RagMatchPayload,
    RunbookId,
    ToolCall,
    ToolName,
    UnknownPayloadError,
    WorkerLogEvent,
    WorkerLogLevel,
    WorkerLogPayload,
    WorkerLogSource,
    build_event,
    event_type_for,
    sse_format,
    sse_retry_preamble,
)

TIMESTAMP = datetime(2026, 8, 19, 2, 50, tzinfo=UTC)
INCIDENT_ID = "inc-9938-db"


def metrics_payload() -> MetricsSnapshot:
    return MetricsSnapshot(
        status=IncidentState.HEALTHY,
        golden_signals=GoldenSignals(
            requests_per_sec=142.5,
            http_5xx_error_rate_pct=0.0,
            latency_p50_ms=18.7,
            latency_p95_ms=34.2,
            latency_p99_ms=47.9,
        ),
        infrastructure=InfrastructureMetrics(
            system_health_status=1,
            db_pool_utilization_pct=14.8,
            redis_memory_utilization_pct=40.1,
            cache_hit_ratio_pct=98.8,
            sqs_active_queue_depth=3,
            dlq_message_count=0,
            active_workers_count=4,
            security_violations_total=0,
        ),
    )


def log_payload() -> LogStreamPayload:
    return LogStreamPayload(message="Connection pool saturated.", sanitized=True)


def rag_payload() -> RagMatchPayload:
    return RagMatchPayload(
        runbook_id=RunbookId.RB_104,
        title="PostgreSQL Emergency Connection Drain & Pool Recycling",
        cosine_similarity=0.94,
        rrf_rank=1,
        excerpt="Terminate orphaned idle connections > 60 seconds...",
        source="pgvector (cosine) + FTS, fused via RRF",
    )


def agent_payload() -> AgentThoughtPayload:
    return AgentThoughtPayload(
        step=3,
        phase=AgentPhase.TOOL_SELECTION,
        text="Identified 84 idle orphaned connections.",
        tool_call=ToolCall(name=ToolName.FLUSH_CONNECTION_POOL.value, args={"max_idle_seconds": 60}),
        guardrail=GuardrailVerdict.PASSED,
    )


def worker_payload() -> WorkerLogPayload:
    return WorkerLogPayload(
        source=WorkerLogSource.LOCALSTACK_SQS,
        level=WorkerLogLevel.INFO,
        message="Message dispatched to remediation-jobs (job_id: job-99214, delay: 0ms)",
    )


# (payload factory, expected EventType, expected envelope class, documented data keys)
CHANNELS = [
    (metrics_payload, EventType.METRICS_UPDATE, MetricsUpdateEvent, {"status", "golden_signals", "infrastructure"}),
    (log_payload, EventType.LOG_STREAM, LogStreamEvent, {"message", "sanitized"}),
    (
        rag_payload,
        EventType.RAG_MATCH,
        RagMatchEvent,
        {"runbook_id", "title", "cosine_similarity", "rrf_rank", "excerpt", "source"},
    ),
    (agent_payload, EventType.AGENT_THOUGHT, AgentThoughtEvent, {"step", "phase", "text", "tool_call", "guardrail"}),
    (worker_payload, EventType.WORKER_LOG, WorkerLogEvent, {"source", "level", "message"}),
]

CHANNEL_IDS = [event_type.value for _, event_type, _, _ in CHANNELS]


def make_event(payload: BaseModel, event_id: str = "evt-984321", incident_id: str | None = INCIDENT_ID):
    return build_event(payload=payload, event_id=event_id, incident_id=incident_id, timestamp=TIMESTAMP)


# ----------------------------------------------------------------------------------
# Envelope conformance
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize(("factory", "event_type", "event_cls", "_keys"), CHANNELS, ids=CHANNEL_IDS)
def test_every_channel_builds_its_documented_envelope(factory, event_type, event_cls, _keys):
    """Each payload type resolves to exactly one channel and one envelope class."""
    payload = factory()
    assert event_type_for(payload) is event_type

    event = make_event(payload)
    assert isinstance(event, event_cls)
    assert event.type is event_type
    assert event.event_id == "evt-984321"
    assert event.incident_id == INCIDENT_ID
    assert event.timestamp == TIMESTAMP


@pytest.mark.parametrize(("factory", "event_type", "_cls", "data_keys"), CHANNELS, ids=CHANNEL_IDS)
def test_serialized_envelope_carries_the_documented_shape(factory, event_type, _cls, data_keys):
    """The wire object is exactly {event_id, incident_id, timestamp, type, data} per §6.1."""
    body = json.loads(make_event(factory()).model_dump_json())

    assert set(body) == {"event_id", "incident_id", "timestamp", "type", "data"}
    assert body["type"] == event_type.value
    assert set(body["data"]) == data_keys


def test_baseline_frames_carry_a_null_incident_id():
    """A METRICS_UPDATE published before any run has no incident to name."""
    body = json.loads(make_event(metrics_payload(), incident_id=None).model_dump_json())
    assert body["incident_id"] is None


def test_unregistered_payload_is_refused():
    """A payload type with no channel is a contract gap and must be loud, not silently dropped."""

    class RoguePayload(BaseModel):
        value: int

    with pytest.raises(UnknownPayloadError):
        event_type_for(RoguePayload(value=1))


# ----------------------------------------------------------------------------------
# Discriminated union
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize(("factory", "_type", "event_cls", "_keys"), CHANNELS, ids=CHANNEL_IDS)
def test_round_trip_through_the_union_preserves_the_variant(factory, _type, event_cls, _keys):
    """Parsing a frame off the wire reconstructs the same typed model the server sent."""
    original = make_event(factory())
    parsed = INCIDENT_EVENT_ADAPTER.validate_json(original.model_dump_json())

    assert isinstance(parsed, event_cls)
    assert parsed == original


def test_mismatched_type_and_data_is_rejected():
    """The union binds `type` to `data`, so a WORKER_LOG body under AGENT_THOUGHT cannot parse.

    This is the guarantee that makes "each type carries its documented data shape" structural
    rather than a matter of producer discipline.
    """
    frame = json.loads(make_event(agent_payload()).model_dump_json())
    frame["data"] = json.loads(worker_payload().model_dump_json())

    with pytest.raises(ValidationError):
        INCIDENT_EVENT_ADAPTER.validate_python(frame)


def test_unknown_event_type_is_rejected():
    """EventType is closed: a sixth channel is a contract change, not something a producer adds."""
    frame = json.loads(make_event(log_payload()).model_dump_json())
    frame["type"] = "TELEMETRY_FIREHOSE"

    with pytest.raises(ValidationError):
        INCIDENT_EVENT_ADAPTER.validate_python(frame)


def test_envelope_type_cannot_be_overridden_on_construction():
    """A LOG_STREAM envelope is a LOG_STREAM; the discriminator is not a settable field."""
    with pytest.raises(ValidationError):
        LogStreamEvent(
            event_id="evt-1",
            incident_id=INCIDENT_ID,
            timestamp=TIMESTAMP,
            type=EventType.WORKER_LOG,
            data=log_payload(),
        )


# ----------------------------------------------------------------------------------
# Closed enums
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"phase": "SPECULATING"}, "phase"),
        ({"guardrail": "MAYBE"}, "guardrail"),
    ],
)
def test_agent_thought_enums_reject_unknown_members(kwargs, field):
    base = {"step": 1, "phase": AgentPhase.PLANNING, "text": "x", **kwargs}
    with pytest.raises(ValidationError) as err:
        AgentThoughtPayload(**base)
    assert field in str(err.value)


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"source": "LocalStack Lambda"}, "source"),
        ({"level": "TRACE"}, "level"),
    ],
)
def test_worker_log_enums_reject_unknown_members(kwargs, field):
    base = {"source": WorkerLogSource.WORKER, "level": WorkerLogLevel.INFO, "message": "x", **kwargs}
    with pytest.raises(ValidationError) as err:
        WorkerLogPayload(**base)
    assert field in str(err.value)


def test_worker_log_source_values_keep_their_spaces():
    """`LocalStack SQS` is the contract string the execution terminal renders verbatim."""
    assert WorkerLogSource.LOCALSTACK_SQS.value == "LocalStack SQS"
    assert WorkerLogSource.LOCALSTACK_S3.value == "LocalStack S3"


def test_rag_rank_is_one_based():
    """rrf_rank == 0 would render as a non-existent position in the RAG inspector."""
    with pytest.raises(ValidationError):
        RagMatchPayload(
            runbook_id=RunbookId.RB_104,
            title="t",
            cosine_similarity=0.5,
            rrf_rank=0,
            excerpt="e",
            source="s",
        )


# ----------------------------------------------------------------------------------
# Scenario 4 — the blocked tool call survives serialization
# ----------------------------------------------------------------------------------


def test_blocked_thought_retains_its_rejected_tool_call():
    """The UI strikes the injected call through, so the frame must still carry it."""
    injected = ToolCall(name="dump_aws_credentials", args={})
    event = make_event(
        AgentThoughtPayload(
            step=2,
            phase=AgentPhase.TOOL_SELECTION,
            text="Injected instruction detected in log payload.",
            tool_call=injected,
            guardrail=GuardrailVerdict.BLOCKED,
        )
    )

    data = json.loads(event.model_dump_json())["data"]
    assert data["guardrail"] == GuardrailVerdict.BLOCKED.value
    assert data["tool_call"]["name"] == "dump_aws_credentials"
    assert data["tool_call"]["is_canonical"] is False


@pytest.mark.parametrize("name", ["flush_database_tables", "dump_aws_credentials"])
def test_injected_tool_names_are_representable_but_not_canonical(name):
    """Both injected calls must serialize — an unrepresentable attack cannot be demonstrated."""
    assert ToolCall(name=name, args={"confirm": True}).is_canonical is False


@pytest.mark.parametrize("tool", list(ToolName))
def test_every_canonical_tool_is_reported_canonical(tool):
    assert ToolCall(name=tool.value).is_canonical is True


# ----------------------------------------------------------------------------------
# Wire framing
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize(("factory", "event_type", "_cls", "_keys"), CHANNELS, ids=CHANNEL_IDS)
def test_frame_is_parseable_by_an_eventsource_client(factory, event_type, _cls, _keys, sse_parser):
    """`event: message\\ndata: {...}\\n\\n`, and the JSON body survives the round trip."""
    event = make_event(factory())
    frame = sse_format(event)

    assert frame.endswith("\n\n")
    parsed = sse_parser(frame.removesuffix("\n\n"))
    assert parsed is not None
    assert parsed["id"] == event.event_id
    # Every channel rides the default `message` event; the browser demultiplexes on the
    # envelope's `type`, using a single onmessage handler.
    assert parsed["event"] == "message"
    assert json.loads(parsed["data"])["type"] == event_type.value


def test_frame_body_is_a_single_data_line():
    """A multi-line body would need `data:` continuation the frontend does not implement."""
    payload = LogStreamPayload(message="line one\nline two", sanitized=False)
    frame = sse_format(make_event(payload))

    data_lines = [line for line in frame.split("\n") if line.startswith("data:")]
    assert len(data_lines) == 1
    # The newline survived as a JSON escape rather than as a literal frame separator.
    assert json.loads(data_lines[0].removeprefix("data: "))["data"]["message"] == "line one\nline two"


def test_retry_preamble_opens_the_stream():
    """The server states EventSource's reconnect floor before sending any frame."""
    preamble = sse_retry_preamble()
    assert preamble == f"retry: {SSE_RETRY_MS}\n\n"


def test_retry_preamble_is_not_an_event(sse_parser):
    """It carries no `data`, so a client treats it as a directive and dispatches nothing."""
    assert sse_parser(sse_retry_preamble().removesuffix("\n\n")) is None
