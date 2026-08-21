"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             packages/contracts/scripts/export_typescript.py
Component:          TypeScript Contract Generator
Purpose:            Emits the war room's contracts.gen.ts from the Python enums and Pydantic
                    models, so the frontend never re-declares a scenario id, tool name, state,
                    or SSE payload shape by hand.
Interacts With:     incident-war-room (:3000)

Curriculum Project:  Cross-cutting — Modular Ports & Contract Design
Skills:             Code Generation, Contract-First Design, Drift Prevention
Tools:              Python 3.11, Pydantic 2
"""

import sys
from enum import EnumMeta
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from tripleten_contracts import (
    APPROVAL_PROMPT,
    BASELINE_BANDS,
    AgentPhase,
    BucketName,
    EventType,
    GuardrailVerdict,
    IncidentEvent,
    IncidentState,
    MetricName,
    Quantile,
    QueueName,
    RunbookId,
    ScenarioId,
    TelemetrySnapshotResponse,
    ToolName,
    WorkerLogLevel,
    WorkerLogSource,
)

BANNER = """/**
 * DO NOT EDIT — generated file.
 * Source:    packages/contracts/src/tripleten_contracts/
 * Generator: packages/contracts/scripts/export_typescript.py
 * Regenerate with `uv run poe contracts` (or `make contracts`).
 */
"""

EXPORTED_ENUMS: tuple[EnumMeta, ...] = (
    ScenarioId,
    RunbookId,
    QueueName,
    BucketName,
    ToolName,
    IncidentState,
    # Project 1 telemetry identifiers: the War Room labels its charts from these rather
    # than re-typing quantile and metric names by hand.
    MetricName,
    Quantile,
    # Stage 3 — the SSE event contract's closed enums.
    EventType,
    AgentPhase,
    GuardrailVerdict,
    WorkerLogSource,
    WorkerLogLevel,
)

# Root types whose full object graph is emitted as TypeScript interfaces. Everything they
# reference — payloads, envelope variants, the metric sub-objects — is pulled in transitively
# through the shared `$defs` block, so listing a nested model here would be redundant.
EXPORTED_MODELS: tuple[tuple[str, Any], ...] = (
    ("IncidentEvent", IncidentEvent),
    ("TelemetrySnapshotResponse", TelemetrySnapshotResponse),
)

# Constant lookup tables the frontend must not re-type by hand. Each entry is
# (TypeScript name, key type, the Python mapping). The key type has to be an exported enum, so
# the emitted `Record<Key, string>` is exhaustive and a new scenario cannot be forgotten on the
# frontend without a compile error.
EXPORTED_MAPS: tuple[tuple[str, str, dict[Any, str]], ...] = (
    # The HITL button text. Every Playwright spec reads these strings off the DOM, and they had
    # already drifted into three spellings once.
    ("APPROVAL_PROMPT", "ScenarioId", APPROVAL_PROMPT),
)

# Numeric ranges the frontend and the E2E suite assert against. Same reasoning as EXPORTED_MAPS:
# baseline bands are read from the contract and never retyped, and the E2E helpers had retyped all
# three of the ones they used. Keyed by metric name as a plain string, because the
# keys here are `MetricName` *values* that also appear as snapshot field names.
EXPORTED_RANGES: tuple[tuple[str, dict[str, tuple[float, float]]], ...] = (
    ("BASELINE_BANDS", BASELINE_BANDS),
)

_REF_PREFIX = "#/$defs/"


class UnexportedEnumError(RuntimeError):
    """Raised when a model references an enum that is not in EXPORTED_ENUMS.

    Without this the generator would happily emit an interface whose field type names a
    TypeScript type that does not exist in the file, and the failure would surface as a
    frontend build error far from its cause.
    """

    def __init__(self, enum_name: str) -> None:
        super().__init__(
            f"{enum_name} is referenced by an exported model but is not in EXPORTED_ENUMS; "
            "add it there so the generated file declares the type it uses"
        )
        self.enum_name = enum_name


def render_enum(enum_cls: EnumMeta) -> str:
    """Renders one enum as a const object plus its union type."""
    name = enum_cls.__name__
    # Sorted by member name so output is byte-stable across runs and interpreters.
    # An unstable generator would make the CI drift check flap forever.
    members = sorted(enum_cls, key=lambda m: m.name)
    entries = "\n".join(f'  {m.name}: "{m.value}",' for m in members)
    return (
        f"export const {name} = {{\n{entries}\n}} as const;\n\n"
        f"export type {name} = (typeof {name})[keyof typeof {name}];\n"
    )


def render_ranges(name: str, ranges: dict[str, tuple[float, float]]) -> str:
    """Renders a mapping of metric name to inclusive `[min, max]` band.

    `readonly` on both levels so a consumer cannot mutate a shared band in place — these are read by
    assertions, and a test that quietly widened a band would be worse than no test.
    """
    ordered = sorted(ranges.items())
    entries = "\n".join(f'  "{metric}": [{low}, {high}],' for metric, (low, high) in ordered)
    declaration = f"export const {name}: Readonly<Record<string, readonly [number, number]>>"
    return f"{declaration} = {{\n{entries}\n}};\n"


def render_map(name: str, key_type: str, mapping: dict[Any, str]) -> str:
    """Renders one Python mapping as a frozen TypeScript `Record` over an exported enum.

    Keyed by the enum *value* rather than its member name, because that is what arrives on the
    wire and therefore what the frontend has in hand when it does the lookup. Sorted for the same
    reason `render_enum` sorts: an unstable generator makes the CI drift check flap forever.
    """
    ordered = sorted(mapping.items(), key=lambda item: item[0].value)
    entries = "\n".join(f'  "{key.value}": "{value}",' for key, value in ordered)
    return f"export const {name}: Record<{key_type}, string> = {{\n{entries}\n}};\n"


class TypeContext:
    """Everything the mapper needs to turn a `$ref` into a TypeScript type name.

    `enum_defs` names the `$defs` entries that are enums; `exported_enums` names the enums the
    file actually declares. A ref in the first set but not the second is a generator bug, and
    raising on it is what keeps the emitted file self-consistent.
    """

    def __init__(self, enum_defs: set[str], exported_enums: set[str]) -> None:
        self.enum_defs = enum_defs
        self.exported_enums = exported_enums

    def resolve_ref(self, ref: str) -> str:
        """Resolves a `$ref` to the TypeScript type name it points at."""
        name = ref.removeprefix(_REF_PREFIX)
        if name in self.enum_defs and name not in self.exported_enums:
            raise UnexportedEnumError(name)
        return name


def ts_type(schema: dict[str, Any], ctx: TypeContext) -> str:
    """Maps one JSON Schema node to a TypeScript type expression."""
    if "$ref" in schema:
        return ctx.resolve_ref(schema["$ref"])
    if "const" in schema:
        # Literal[EventType.X] on the envelope discriminator. Quoted so the union narrows.
        return f'"{schema["const"]}"'
    if "anyOf" in schema:
        return " | ".join(ts_type(member, ctx) for member in schema["anyOf"])

    json_type = schema.get("type")
    if json_type == "string":
        return "string"
    if json_type in ("integer", "number"):
        return "number"
    if json_type == "boolean":
        return "boolean"
    if json_type == "null":
        return "null"
    if json_type == "array":
        return f"Array<{ts_type(schema.get('items', {}), ctx)}>"
    if json_type == "object":
        additional = schema.get("additionalProperties", True)
        value = "unknown" if additional is True else ts_type(additional, ctx)
        return f"Record<string, {value}>"
    return "unknown"


def render_interface(name: str, schema: dict[str, Any], ctx: TypeContext) -> str:
    """Renders one object `$def` as a TypeScript interface.

    Every property is emitted as required. These are *response* contracts generated in
    serialization mode, and Pydantic writes defaults into its output — so a field with a
    default is always present on the wire, and marking it optional would push the frontend
    into `undefined` checks that can never fire. Absence is modelled where it is real, as an
    explicit `| null` from the Python type.
    """
    lines = [f"export interface {name} {{"]
    for prop_name, prop_schema in schema.get("properties", {}).items():
        lines.append(f"  {prop_name}: {ts_type(prop_schema, ctx)};")
    lines.append("}")
    return "\n".join(lines) + "\n"


def collect_defs() -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Returns the merged `$defs` across every exported root, plus the union roots' variants.

    Serialization mode is deliberate: it is the direction these contracts travel, and it is
    the only mode that includes computed fields — `ToolCall.is_canonical` is generated from
    one, and the UI's strike-through rendering reads it.
    """
    defs: dict[str, dict[str, Any]] = {}
    unions: dict[str, list[dict[str, Any]]] = {}

    for root_name, root_type in EXPORTED_MODELS:
        schema = TypeAdapter(root_type).json_schema(
            ref_template=f"{_REF_PREFIX}{{model}}",
            mode="serialization",
        )
        defs.update(schema.pop("$defs", {}))

        # A tagged union serializes to `oneOf` + `discriminator`; an untagged one to `anyOf`.
        # Both mean the same thing here: every variant is already a $def, so the root is
        # emitted as a type alias over them rather than as an interface.
        variants = schema.get("oneOf") or schema.get("anyOf")
        if variants is not None:
            unions[root_name] = variants
        else:
            # A plain model root is not listed in its own $defs; add it so it is emitted too.
            defs[root_name] = schema

    return defs, unions


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: export_typescript.py <output.ts>")
    out = Path(sys.argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)

    defs, unions = collect_defs()
    ctx = TypeContext(
        enum_defs={name for name, schema in defs.items() if "enum" in schema},
        exported_enums={e.__name__ for e in EXPORTED_ENUMS},
    )

    blocks = [render_enum(e) for e in EXPORTED_ENUMS]
    blocks.extend(render_map(name, key_type, mapping) for name, key_type, mapping in EXPORTED_MAPS)
    blocks.extend(render_ranges(name, ranges) for name, ranges in EXPORTED_RANGES)

    # Enums are emitted from the Python classes above, not from their $defs — the const-object
    # form carries the member names, which a JSON Schema enum list does not.
    for name in sorted(defs):
        if name in ctx.enum_defs:
            if name not in ctx.exported_enums:
                raise UnexportedEnumError(name)
            continue
        blocks.append(render_interface(name, defs[name], ctx))

    for root_name in sorted(unions):
        variants = " | ".join(sorted(ts_type(member, ctx) for member in unions[root_name]))
        blocks.append(f"export type {root_name} = {variants};\n")

    body = "\n".join(blocks)
    # newline="\n" is not optional: without it Python writes CRLF on Windows, the file
    # differs from what CI generates on Linux, and the drift check fails permanently.
    out.write_text(f"{BANNER}\n{body}", encoding="utf-8", newline="\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
