"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             packages/contracts/tests/test_typescript_export.py
Component:          TypeScript Contract Generator — Output Guarantees
Purpose:            Asserts the generated contracts.gen.ts declares everything the War Room reads
                    off it, and that regenerating twice produces the same bytes.
Interacts With:     incident-war-room (:3000)

Curriculum Project:  Cross-cutting — Modular Ports & Contract Design
Skills:             Code Generation, Drift Prevention, Determinism
Tools:              pytest, Python 3.11

CI already fails on a *stale* generated file (`git diff --exit-code` on it). What it cannot catch is
a generator that emits something the frontend does not compile against, or one whose output depends
on dict ordering — that would make the drift check flap forever rather than fail once. These are
those two properties.
"""

from pathlib import Path

import pytest

from tripleten_contracts import APPROVAL_PROMPT, BASELINE_BANDS, IncidentState, ScenarioId, ToolName

GENERATED = (
    Path(__file__).resolve().parents[3]
    / "services"
    / "incident-war-room"
    / "src"
    / "types"
    / "contracts.gen.ts"
)


@pytest.fixture(scope="module")
def generated_source() -> str:
    assert GENERATED.exists(), f"the generated contract file is missing: {GENERATED}"
    return GENERATED.read_text(encoding="utf-8")


def test_the_file_warns_against_hand_editing(generated_source: str) -> None:
    assert "DO NOT EDIT" in generated_source
    assert "export_typescript.py" in generated_source


@pytest.mark.parametrize("scenario_id", list(ScenarioId))
def test_every_approval_prompt_is_exported_verbatim(generated_source: str, scenario_id: ScenarioId) -> None:
    """The HITL button text reaches the frontend generated, never retyped.

    This is the string every Playwright spec reads off the DOM, and it had already drifted into
    three spellings once when it lived in prose. Generating it means a change to the Python table is
    the only way to change the button.
    """
    assert f'"{scenario_id.value}": "{APPROVAL_PROMPT[scenario_id]}"' in generated_source


def test_the_prompt_map_is_typed_over_the_scenario_enum(generated_source: str) -> None:
    # `Record<ScenarioId, string>` rather than a loose object: a fifth scenario added to the Python
    # enum then becomes a frontend compile error instead of an `undefined` button label.
    assert "export const APPROVAL_PROMPT: Record<ScenarioId, string> = {" in generated_source


@pytest.mark.parametrize("state", list(IncidentState))
def test_every_incident_state_is_exported(generated_source: str, state: IncidentState) -> None:
    assert f'{state.name}: "{state.value}",' in generated_source


@pytest.mark.parametrize("tool", list(ToolName))
def test_every_tool_name_is_exported(generated_source: str, tool: ToolName) -> None:
    assert f'{tool.name}: "{tool.value}",' in generated_source


@pytest.mark.parametrize("metric", sorted(BASELINE_BANDS))
def test_every_baseline_band_is_exported(generated_source: str, metric: str) -> None:
    """The bands reach the frontend and the E2E suite generated, never retyped.

    Baseline bands are read from the contract; `tests/e2e/helpers.ts` had
    transcribed three of them by hand. The values happened to match, which is the dangerous case —
    a band widened here and not there would leave the E2E assertions agreeing with nothing.
    """
    low, high = BASELINE_BANDS[metric]
    assert f'"{metric}": [{low}, {high}],' in generated_source


def test_the_band_map_is_readonly(generated_source: str) -> None:
    # These are read by assertions. A consumer that mutated a shared band in place would quietly
    # widen a test rather than fail it.
    assert (
        "export const BASELINE_BANDS: Readonly<Record<string, readonly [number, number]>> = {"
        in generated_source
    )


def test_the_generator_is_deterministic(tmp_path: Path) -> None:
    """Two runs produce identical bytes.

    An unstable generator would make CI's drift check fail on an unrelated commit and keep failing
    until someone regenerated, which trains people to ignore it.
    """
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[1] / "scripts" / "export_typescript.py"
    outputs = []
    for run in ("first", "second"):
        target = tmp_path / f"{run}.ts"
        subprocess.run([sys.executable, str(script), str(target)], check=True, capture_output=True)
        outputs.append(target.read_bytes())

    assert outputs[0] == outputs[1]


def test_the_committed_file_matches_a_fresh_generation(tmp_path: Path, generated_source: str) -> None:
    """The same check CI runs, available locally without a git working tree."""
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[1] / "scripts" / "export_typescript.py"
    target = tmp_path / "fresh.ts"
    subprocess.run([sys.executable, str(script), str(target)], check=True, capture_output=True)

    # Compared as text with normalised newlines: the generator writes LF explicitly, but a Windows
    # checkout may hold the committed file as CRLF and that is not drift.
    assert target.read_text(encoding="utf-8").splitlines() == generated_source.splitlines()
