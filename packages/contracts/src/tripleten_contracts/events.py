"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             packages/contracts/src/tripleten_contracts/events.py
Component:          SSE Event Contract — Envelope, Payloads & Wire Framing
Purpose:            Single source of truth for the multiplexed Server-Sent Events channel: the
                    envelope every frame carries, the five typed payloads it discriminates on,
                    and the pure function that renders a frame to the wire.
Interacts With:     incident-agent-api (:8000), incident-war-room (:3000)

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             Contract-First Design, Discriminated Unions, Event Streaming
Tools:              Pydantic 2, Python 3.11
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, computed_field

from tripleten_contracts.identifiers import RunbookId, ToolName
from tripleten_contracts.telemetry import MetricsSnapshot


class EventType(StrEnum):
    """The five multiplexed channels carried by GET /api/stream — no more, no fewer.

    Every frame rides the default SSE `message` event; this field is what the browser
    demultiplexes on. Adding a sixth member is a contract change, not an implementation detail.
    """

    METRICS_UPDATE = "METRICS_UPDATE"
    LOG_STREAM = "LOG_STREAM"
    RAG_MATCH = "RAG_MATCH"
    AGENT_THOUGHT = "AGENT_THOUGHT"
    WORKER_LOG = "WORKER_LOG"


class AgentPhase(StrEnum):
    """Where a LangGraph reasoning step sits in the run. Closed: an unknown phase is rejected."""

    ANALYZING = "ANALYZING"
    RETRIEVING = "RETRIEVING"
    PLANNING = "PLANNING"
    TOOL_SELECTION = "TOOL_SELECTION"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"


class GuardrailVerdict(StrEnum):
    """The Pydantic tool-argument firewall's ruling on a proposed call."""

    PASSED = "PASSED"
    BLOCKED = "BLOCKED"


class WorkerLogSource(StrEnum):
    """Origin of an execution-terminal line. Values contain spaces: that is the contract."""

    LOCALSTACK_SQS = "LocalStack SQS"
    LOCALSTACK_S3 = "LocalStack S3"
    WORKER = "Worker"


class WorkerLogLevel(StrEnum):
    """Severity of an execution-terminal line."""

    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


# ----------------------------------------------------------------------------------
# Payloads
# ----------------------------------------------------------------------------------


class ToolCall(BaseModel):
    """A tool invocation as the agent proposed it, canonical or not.

    `name` is a plain string and deliberately not `ToolName`, the one place in this system a
    canonical enum is not used. Scenario 4's whole demonstration is rendering the *injected*
    calls — `flush_database_tables(confirm=True)` and `dump_aws_credentials()` — struck through
    and tagged BLOCKED BY SCHEMA FIREWALL. Typing this field as `ToolName` would make the frame
    carrying them unserializable, so the attack could not be shown; adding them to `ToolName`
    would poison the canonical roster with attacker-supplied names, which is exactly what that
    roster exists to prevent. The field carries untrusted input, and `is_canonical` is the
    machine-checkable verdict the UI strikes through on.
    """

    name: str = Field(min_length=1, description="Tool name as proposed; not necessarily canonical")
    args: dict[str, Any] = Field(default_factory=dict, description="Arguments as proposed")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_canonical(self) -> bool:
        """True when `name` is one of the nine real agent tools."""
        return self.name in _CANONICAL_TOOL_NAMES


# Materialized once. `in` against a StrEnum works but walks the members on every call, and this
# is evaluated per rendered tool call in the reasoning chain.
_CANONICAL_TOOL_NAMES: frozenset[str] = frozenset(tool.value for tool in ToolName)


class LogStreamPayload(BaseModel):
    """One line of platform log output, after inbound PII sanitization (Project 4).

    `sanitized` reports whether the redaction middleware altered this line, which is what the
    War Room's "N Sensitive Tokens Masked" badge counts. It is not a promise that the line was
    inspected — every line is — but that something was actually masked.
    """

    message: str
    sanitized: bool


class RagMatchPayload(BaseModel):
    """One retrieved runbook (Project 2), emitted once per retrieval.

    `cosine_similarity` and `rrf_rank` are separate fields on purpose: the fused rank is the
    meaningful ordering, while raw RRF score is bounded by (0, 2/(k+1)] and is not a confidence.
    """

    runbook_id: RunbookId
    title: str
    cosine_similarity: float
    rrf_rank: Annotated[int, Field(ge=1)]
    excerpt: str
    source: str


class AgentThoughtPayload(BaseModel):
    """One LangGraph reasoning step (Project 5).

    A BLOCKED verdict keeps its `tool_call` intact rather than clearing it — the rejected call
    is the evidence the guardrail fired, and the UI needs it to render the strike-through.
    """

    step: Annotated[int, Field(ge=1)]
    phase: AgentPhase
    text: str
    tool_call: ToolCall | None = None
    guardrail: GuardrailVerdict = GuardrailVerdict.PASSED


class WorkerLogPayload(BaseModel):
    """One line in the bottom execution terminal (Project 3)."""

    source: WorkerLogSource
    level: WorkerLogLevel
    message: str


# The five payload models, in the order their EventType members are declared.
EventPayload = MetricsSnapshot | LogStreamPayload | RagMatchPayload | AgentThoughtPayload | WorkerLogPayload


# ----------------------------------------------------------------------------------
# Envelope
# ----------------------------------------------------------------------------------


class BaseIncidentEvent(BaseModel):
    """Fields every frame carries, whatever its channel."""

    event_id: str = Field(description="Process-monotonic frame id, evt-<n>")
    incident_id: str | None = Field(description="The run this frame belongs to; null at baseline")
    timestamp: datetime


class MetricsUpdateEvent(BaseIncidentEvent):
    """A telemetry sample. Emitted every 1000ms in every state, including HEALTHY."""

    type: Literal[EventType.METRICS_UPDATE] = EventType.METRICS_UPDATE
    data: MetricsSnapshot


class LogStreamEvent(BaseIncidentEvent):
    """A sanitized platform log line."""

    type: Literal[EventType.LOG_STREAM] = EventType.LOG_STREAM
    data: LogStreamPayload


class RagMatchEvent(BaseIncidentEvent):
    """A retrieved runbook."""

    type: Literal[EventType.RAG_MATCH] = EventType.RAG_MATCH
    data: RagMatchPayload


class AgentThoughtEvent(BaseIncidentEvent):
    """An agent reasoning step."""

    type: Literal[EventType.AGENT_THOUGHT] = EventType.AGENT_THOUGHT
    data: AgentThoughtPayload


class WorkerLogEvent(BaseIncidentEvent):
    """An execution-terminal line."""

    type: Literal[EventType.WORKER_LOG] = EventType.WORKER_LOG
    data: WorkerLogPayload


# Discriminated on `type`, so the pairing of channel and payload is enforced by the type system
# rather than by convention. An AGENT_THOUGHT carrying a WorkerLogPayload is a ValidationError
# at construction and at parse — the frontend gets the same narrowing from the generated union.
IncidentEvent = Annotated[
    MetricsUpdateEvent | LogStreamEvent | RagMatchEvent | AgentThoughtEvent | WorkerLogEvent,
    Field(discriminator="type"),
]

INCIDENT_EVENT_ADAPTER: TypeAdapter[Any] = TypeAdapter(IncidentEvent)

# Producers publish a payload and never name a channel; the bus resolves the channel here. That
# makes a type/data mismatch unreachable rather than merely tested for.
EVENT_TYPE_BY_PAYLOAD: dict[type[BaseModel], EventType] = {
    MetricsSnapshot: EventType.METRICS_UPDATE,
    LogStreamPayload: EventType.LOG_STREAM,
    RagMatchPayload: EventType.RAG_MATCH,
    AgentThoughtPayload: EventType.AGENT_THOUGHT,
    WorkerLogPayload: EventType.WORKER_LOG,
}

EVENT_CLASS_BY_TYPE: dict[EventType, type[BaseIncidentEvent]] = {
    EventType.METRICS_UPDATE: MetricsUpdateEvent,
    EventType.LOG_STREAM: LogStreamEvent,
    EventType.RAG_MATCH: RagMatchEvent,
    EventType.AGENT_THOUGHT: AgentThoughtEvent,
    EventType.WORKER_LOG: WorkerLogEvent,
}


class UnknownPayloadError(TypeError):
    """Raised when a payload type has no registered channel — a contract gap, not a runtime input."""

    def __init__(self, payload_type: type) -> None:
        super().__init__(f"{payload_type.__name__} is not a registered SSE payload type")
        self.payload_type = payload_type


def event_type_for(payload: BaseModel) -> EventType:
    """Returns the channel a payload belongs to.

    Exact class lookup, not isinstance: a subclass of one payload model is a different contract
    and must register itself rather than silently inherit another channel's type.
    """
    try:
        return EVENT_TYPE_BY_PAYLOAD[type(payload)]
    except KeyError as err:
        raise UnknownPayloadError(type(payload)) from err


def build_event(payload: BaseModel, event_id: str, incident_id: str | None, timestamp: datetime) -> BaseIncidentEvent:
    """Wraps a payload in the envelope variant its type maps to."""
    event_type = event_type_for(payload)
    return EVENT_CLASS_BY_TYPE[event_type](
        event_id=event_id,
        incident_id=incident_id,
        timestamp=timestamp,
        data=payload,
    )


# ----------------------------------------------------------------------------------
# Wire framing
# ----------------------------------------------------------------------------------

# Written once when a stream opens, before any frame: EventSource's reconnect floor in ms.
SSE_RETRY_MS = 3000

# Every frame rides the default `message` event so the browser attaches one onmessage handler
# and switches on the envelope's `type`. Naming each SSE event after its channel would force
# five listeners and silently drop any channel added later.
SSE_EVENT_NAME = "message"


class MalformedFrameError(ValueError):
    """Raised when a serialized payload contains a newline, which would corrupt the SSE frame."""


def sse_retry_preamble(retry_ms: int = SSE_RETRY_MS) -> str:
    """Returns the `retry:` directive that opens a stream."""
    return f"retry: {retry_ms}\n\n"


def sse_format(event: BaseIncidentEvent) -> str:
    """Renders one envelope as an SSE frame parseable by a standard EventSource client.

    JSON escapes control characters, so the body is always a single `data:` line and no
    multi-line continuation handling is needed. The guard below therefore cannot fire against
    Pydantic's serializer — it is here because a raw newline is the one way to split a frame
    into two malformed ones, and that failure surfaces in the browser as silently missing
    events rather than as an error. It costs one comparison per frame to make a future change
    of serializer fail loudly instead.
    """
    body = event.model_dump_json()
    if "\n" in body or "\r" in body:
        raise MalformedFrameError(f"serialized {type(event).__name__} contains a newline")
    return f"id: {event.event_id}\nevent: {SSE_EVENT_NAME}\ndata: {body}\n\n"
