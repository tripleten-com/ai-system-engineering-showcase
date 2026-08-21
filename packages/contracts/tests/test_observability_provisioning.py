"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             packages/contracts/tests/test_observability_provisioning.py
Component:          Observability Provisioning Contract Tests
Purpose:            Asserts the Prometheus scrape config and the pre-provisioned Grafana
                    datasources and dashboards agree with the closed metric roster in
                    tripleten_contracts, with the palette, and with each other.
Interacts With:     infra/prometheus/, infra/grafana/provisioning/, infra/docker-compose.yml

Curriculum Project:  Project 1 — Diagnostics & Telemetry
Skills:             Observability, Infrastructure as Code, Contract-First Design, PromQL
Tools:              Pytest, PyYAML, Python 3.11

Why these live at the unit tier and in `packages/contracts`. The provisioning assets are the
consumer side of the metric roster: a dashboard that queries `db_pool_utilisation_pct` is
broken in exactly the way a service querying a renamed field is broken, and it fails silently
as an empty panel rather than loudly as an exception. Checking them against `MetricName` makes
the roster genuinely closed at both ends. No container is needed to do it, so nothing here
belongs in the smoke or integration tier — `tests/smoke/test_observability_stack.py` covers
the half that does need a running Grafana.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from tripleten_contracts import (
    HEALTH_DEGRADED,
    HEALTH_DOWN,
    HEALTH_OK,
    METRIC_KINDS,
    MetricKind,
    MetricName,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
INFRA = REPO_ROOT / "infra"
PROMETHEUS_CONFIG = INFRA / "prometheus" / "prometheus.yml"
DATASOURCES_CONFIG = INFRA / "grafana" / "provisioning" / "datasources" / "datasources.yml"
DASHBOARD_PROVIDER_CONFIG = INFRA / "grafana" / "provisioning" / "dashboards" / "dashboards.yml"
DASHBOARD_DIR = INFRA / "grafana" / "provisioning" / "dashboards"
COMPOSE_FILE = INFRA / "docker-compose.yml"

SCRAPE_INTERVAL = "1s"
SCRAPE_TARGET = "incident-agent-api:8000"
PROVIDER_CONTAINER_PATH = "/etc/grafana/provisioning/dashboards"
PROVISIONING_MOUNT = "./grafana/provisioning:/etc/grafana/provisioning:ro"
NAV_LINK_TITLE = "TripleTen Cloud"

# The five status colors from spa-design-guidelines.md §1 plus the two neutrals from §4 that a
# non-status series is allowed to use. A hex outside this set means a panel invented a color,
# which is the failure mode the "color is a status system" rule exists to prevent.
STATUS_COLORS = {
    "#10B981": "healthy",
    "#EF4444": "alarm",
    "#F59E0B": "pending",
    "#3B82F6": "active",
    "#06B6D4": "guard",
}
NEUTRAL_COLORS = {"#9CA3AF", "#6B7280"}
SANCTIONED_COLORS = set(STATUS_COLORS) | NEUTRAL_COLORS

# The keys Grafana reads a colour out of. Collecting by key rather than by pattern is what lets
# the palette gate see Grafana's named palette, which is what the UI writes on export.
COLOR_KEYS = {"color", "fixedColor"}

HEALTH_STATUS_MAPPING = {
    str(HEALTH_DOWN): ("DOWN", "#EF4444"),
    str(HEALTH_OK): ("HEALTHY", "#10B981"),
    str(HEALTH_DEGRADED): ("DEGRADED", "#F59E0B"),
}

# PromQL identifiers that are language, not series. Everything else an expression names must be
# a canonical metric family.
PROMQL_KEYWORDS = {
    "rate",
    "irate",
    "increase",
    "sum",
    "avg",
    "min",
    "max",
    "count",
    "clamp_min",
    "clamp_max",
    "round",
    "by",
    "without",
    "on",
    "ignoring",
    "group_left",
    "group_right",
    "and",
    "or",
    "unless",
    "offset",
}

METRIC_NAMES = {m.value for m in MetricName}
COUNTER_NAMES = {name.value for name, kind in METRIC_KINDS.items() if kind is MetricKind.COUNTER}

# Units that describe a per-second or per-request-derived value. A panel using one of these
# over a counter family must go through rate(); plotting a raw monotonic counter as "reqps"
# renders a ramp that climbs forever and means nothing.
DERIVED_UNITS = {"reqps", "percent"}


def _load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def dashboard_files() -> list[Path]:
    return sorted(DASHBOARD_DIR.glob("*.json"))


def load_dashboards() -> dict[Path, dict[str, Any]]:
    return {path: json.loads(path.read_text(encoding="utf-8")) for path in dashboard_files()}


def provisioned_datasource_uids() -> set[str]:
    config = _load_yaml(DATASOURCES_CONFIG)
    return {ds["uid"] for ds in config["datasources"]}


def iter_panels(dashboard: dict[str, Any]):
    """Yields every panel, descending into rows so a collapsed row cannot hide a broken panel."""
    for panel in dashboard.get("panels", []):
        yield panel
        yield from panel.get("panels", [])


def iter_targets(dashboard: dict[str, Any]):
    """Yields (panel, target) for every query in the dashboard."""
    for panel in iter_panels(dashboard):
        for target in panel.get("targets", []):
            yield panel, target


def metric_names_in(expr: str) -> set[str]:
    """Extracts the series names an expression references, ignoring PromQL syntax."""
    without_selectors = re.sub(r"\{[^}]*\}", "", expr)
    without_windows = re.sub(r"\[[^\]]*\]", "", without_selectors)
    identifiers = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", without_windows))
    return identifiers - PROMQL_KEYWORDS


def color_tokens_in(node: Any, key: str | None = None) -> set[str]:
    """Walks a dashboard subtree and collects every colour token in it, in any notation.

    Two collectors in one walk, because Grafana accepts more than one way to write a colour
    and exported JSON carries whichever the editor produced:

    1. Any string under a `color` or `fixedColor` key, whatever its form. This is the half that
       matters most — the Grafana UI writes its *named* palette on export (`green`, `dark-red`,
       `semi-dark-orange`), so a hex-only scan would wave through the single most likely way
       anyone introduces an off-palette colour: edit a panel in the browser, paste the JSON back.
    2. Any hex literal anywhere, so a colour hiding under a key this function does not know
       about is still caught. The pattern deliberately spans 3-8 digits: `#RRGGBBAA` is legal
       and an earlier `{3}{1,2}\\b` pattern silently matched none of it, because the trailing
       word boundary failed against the alpha pair and backtracking failed the same way.

    Mode strings are not colours and do not appear here: `{"color": {"mode": "fixed"}}` recurses
    into the inner dict, where `mode` is the key, not `color`.
    """
    found: set[str] = set()
    if isinstance(node, str):
        if key in COLOR_KEYS:
            found.add(node)
        found.update(re.findall(r"#[0-9a-fA-F]{3,8}", node))
    elif isinstance(node, dict):
        for child_key, value in node.items():
            found |= color_tokens_in(value, child_key)
    elif isinstance(node, list):
        for value in node:
            found |= color_tokens_in(value, key)
    return found


def interval_seconds(value: str) -> int:
    """Parses a Grafana/Prometheus duration into seconds.

    Not `int(value.removesuffix("s"))`. That crashes on every unit but seconds, so a dashboard
    legitimately set to `refresh: "1m"` — comfortably *above* the 1s floor, and therefore
    correct — failed the check with a bare `ValueError` naming neither the file nor the field.
    """
    units = {"s": 1, "m": 60, "h": 3600}
    suffix = value[-1]
    assert suffix in units, f"unrecognised interval {value!r}: expected a trailing s, m, or h"
    return int(value[:-1]) * units[suffix]


ALL_DASHBOARDS = load_dashboards()
DASHBOARD_IDS = [path.name for path in ALL_DASHBOARDS]


# ==============================================================================
# Prometheus scrape configuration
# ==============================================================================


def test_prometheus_scrapes_once_per_second():
    """A 4-second decay curve needs 1s resolution; 15s would render it as a straight line."""
    config = _load_yaml(PROMETHEUS_CONFIG)
    assert config["global"]["scrape_interval"] == SCRAPE_INTERVAL
    assert config["global"]["evaluation_interval"] == SCRAPE_INTERVAL
    # Explicit, not inherited. The 10s default exceeds the interval and Prometheus clamps it.
    assert config["global"]["scrape_timeout"] == SCRAPE_INTERVAL


def test_prometheus_has_exactly_one_target():
    """incident-agent-api owns the chaos engine, so its /metrics is the whole simulated platform."""
    config = _load_yaml(PROMETHEUS_CONFIG)
    jobs = config["scrape_configs"]
    assert len(jobs) == 1, f"expected one scrape job, found {[j['job_name'] for j in jobs]}"

    job = jobs[0]
    assert job["job_name"] == "incident-agent-api"
    assert job["metrics_path"] == "/metrics"
    assert job["scrape_interval"] == SCRAPE_INTERVAL
    targets = [t for sc in job["static_configs"] for t in sc["targets"]]
    assert targets == [SCRAPE_TARGET]


# ==============================================================================
# Grafana datasource provisioning
# ==============================================================================


def test_every_datasource_declares_an_explicit_uid():
    """Dashboards reference datasources by uid; an auto-generated one breaks every panel."""
    for datasource in _load_yaml(DATASOURCES_CONFIG)["datasources"]:
        uid = datasource.get("uid")
        assert uid, f"datasource {datasource['name']} has no explicit uid"
        assert uid == uid.lower(), f"datasource uid {uid} must be lowercase to stay URL-stable"


def test_prometheus_datasource_points_at_the_stack_and_matches_the_scrape_interval():
    """timeInterval drives Grafana's minimum step. At the 15s default it would over-smooth."""
    sources = {ds["name"]: ds for ds in _load_yaml(DATASOURCES_CONFIG)["datasources"]}
    prometheus = sources["Prometheus"]

    assert prometheus["type"] == "prometheus"
    assert prometheus["url"] == "http://prometheus:9090"
    assert prometheus["isDefault"] is True
    assert prometheus["jsonData"]["timeInterval"] == SCRAPE_INTERVAL


def test_jaeger_datasource_is_provisioned_for_traces():
    """Stage 4 configures Grafana to read metrics *and* Jaeger to read traces."""
    sources = {ds["name"]: ds for ds in _load_yaml(DATASOURCES_CONFIG)["datasources"]}
    jaeger = sources["Jaeger"]

    assert jaeger["type"] == "jaeger"
    assert jaeger["url"] == "http://jaeger:16686"
    assert jaeger.get("isDefault", False) is False


def test_only_prometheus_is_the_default_datasource():
    defaults = [ds["name"] for ds in _load_yaml(DATASOURCES_CONFIG)["datasources"] if ds.get("isDefault")]
    assert defaults == ["Prometheus"]


# ==============================================================================
# Grafana dashboard provider
# ==============================================================================


def test_dashboard_provider_path_is_the_mounted_provisioning_directory():
    """The provider path is a container path; it only resolves because compose mounts it."""
    provider = _load_yaml(DASHBOARD_PROVIDER_CONFIG)["providers"][0]
    assert provider["type"] == "file"
    assert provider["options"]["path"] == PROVIDER_CONTAINER_PATH

    grafana = _load_yaml(COMPOSE_FILE)["services"]["grafana"]
    assert PROVISIONING_MOUNT in grafana["volumes"], (
        f"grafana must mount {PROVISIONING_MOUNT} for {PROVIDER_CONTAINER_PATH} to exist"
    )


def test_dashboards_are_read_only_and_undeletable():
    """The dashboards are a build artifact of this repo, not viewer state in a Grafana volume."""
    provider = _load_yaml(DASHBOARD_PROVIDER_CONFIG)["providers"][0]
    assert provider["allowUiUpdates"] is False
    assert provider["disableDeletion"] is True


def test_at_least_one_dashboard_per_documented_service_view():
    """containers-and-stack.md §grafana promises golden signals plus four service dashboards."""
    uids = {dashboard["uid"] for dashboard in ALL_DASHBOARDS.values()}
    assert uids == {
        "tt-golden-signals",
        "tt-svc-fastapi",
        "tt-svc-postgres",
        "tt-svc-redis",
        "tt-svc-sqs",
    }


def test_home_dashboard_path_resolves_to_a_provisioned_file():
    """GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH must name a file the provider actually loads."""
    grafana = _load_yaml(COMPOSE_FILE)["services"]["grafana"]
    env = dict(entry.split("=", 1) for entry in grafana["environment"])
    home = env["GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH"]

    assert home.startswith(f"{PROVIDER_CONTAINER_PATH}/")
    local = DASHBOARD_DIR / Path(home).name
    assert local in ALL_DASHBOARDS, f"home dashboard {home} is not a provisioned dashboard"


def test_grafana_min_refresh_interval_permits_the_dashboard_refresh_rate():
    """Grafana silently rounds a refresh faster than its floor up to the floor."""
    grafana = _load_yaml(COMPOSE_FILE)["services"]["grafana"]
    env = dict(entry.split("=", 1) for entry in grafana["environment"])
    floor_seconds = interval_seconds(env["GF_DASHBOARDS_MIN_REFRESH_INTERVAL"])

    for path, dashboard in ALL_DASHBOARDS.items():
        requested = interval_seconds(str(dashboard["refresh"]))
        assert requested >= floor_seconds, f"{path.name} refreshes every {requested}s, below the {floor_seconds}s floor"


# ==============================================================================
# Dashboard structure
# ==============================================================================


@pytest.mark.parametrize("path", ALL_DASHBOARDS, ids=DASHBOARD_IDS)
def test_dashboard_declares_no_numeric_id(path: Path):
    """A provisioned dashboard is keyed by uid. A stale numeric id collides on import."""
    assert ALL_DASHBOARDS[path].get("id") is None


@pytest.mark.parametrize("path", ALL_DASHBOARDS, ids=DASHBOARD_IDS)
def test_dashboard_carries_the_shared_tag(path: Path):
    """The cross-dashboard navigation dropdown is a tag query; an untagged dashboard is orphaned."""
    assert "tripleten" in ALL_DASHBOARDS[path]["tags"]


@pytest.mark.parametrize("path", ALL_DASHBOARDS, ids=DASHBOARD_IDS)
def test_navigation_dropdown_is_labelled_for_what_it_lists(path: Path):
    """It is a tag query listing every dashboard, so naming it after one of them misleads.

    The home dashboard shipped titled `FastAPI Service` while opening a list of all five, which
    is the first control a visitor touches.
    """
    for link in ALL_DASHBOARDS[path].get("links", []):
        if link["type"] != "dashboards":
            continue
        assert link["tags"] == ["tripleten"]
        assert link["title"] == NAV_LINK_TITLE, (
            f"{path.name} labels its all-dashboards dropdown {link['title']!r}"
        )


def test_dashboard_uids_and_titles_are_unique():
    uids = [dashboard["uid"] for dashboard in ALL_DASHBOARDS.values()]
    titles = [dashboard["title"] for dashboard in ALL_DASHBOARDS.values()]
    assert len(set(uids)) == len(uids), f"duplicate dashboard uid in {uids}"
    assert len(set(titles)) == len(titles), f"duplicate dashboard title in {titles}"


@pytest.mark.parametrize("path", ALL_DASHBOARDS, ids=DASHBOARD_IDS)
def test_every_panel_and_target_resolves_a_provisioned_datasource(path: Path):
    """A panel pointing at an unknown uid renders 'Datasource ${uid} was not found'."""
    known = provisioned_datasource_uids()
    dashboard = ALL_DASHBOARDS[path]

    for panel in iter_panels(dashboard):
        # Text panels carry no query and legitimately have no datasource.
        if panel["type"] == "text":
            assert "datasource" not in panel
            continue
        assert panel["datasource"]["uid"] in known, f"{path.name}/{panel['title']} references {panel['datasource']}"

    for panel, target in iter_targets(dashboard):
        uid = target["datasource"]["uid"]
        assert uid in known, f"{path.name}/{panel['title']} target {target['refId']} references {uid}"


@pytest.mark.parametrize("path", ALL_DASHBOARDS, ids=DASHBOARD_IDS)
def test_every_panel_has_a_query_or_is_prose(path: Path):
    """A titled panel with neither a target nor markdown content is a blank rectangle."""
    for panel in iter_panels(dashboard := ALL_DASHBOARDS[path]):
        assert panel.get("title"), f"{path.name} has an untitled panel"
        if panel["type"] == "text":
            assert panel["options"]["content"].strip()
        else:
            assert panel.get("targets"), f"{path.name}/{panel['title']} has no targets"
    assert dashboard["panels"], f"{path.name} has no panels at all"


@pytest.mark.parametrize("path", ALL_DASHBOARDS, ids=DASHBOARD_IDS)
def test_panel_ids_are_unique_within_a_dashboard(path: Path):
    ids = [panel["id"] for panel in iter_panels(ALL_DASHBOARDS[path])]
    assert len(set(ids)) == len(ids), f"{path.name} reuses panel ids {ids}"


# ==============================================================================
# Dashboard ↔ metric roster conformance
# ==============================================================================


@pytest.mark.parametrize("path", ALL_DASHBOARDS, ids=DASHBOARD_IDS)
def test_every_queried_series_is_a_canonical_metric_family(path: Path):
    """The roster is closed at the consumer end too: a typo'd series is a silently empty panel."""
    for panel, target in iter_targets(ALL_DASHBOARDS[path]):
        referenced = metric_names_in(target["expr"])
        unknown = referenced - METRIC_NAMES
        assert not unknown, f"{path.name}/{panel['title']} queries non-canonical series {sorted(unknown)}"
        assert referenced, f"{path.name}/{panel['title']} target {target['refId']} names no metric"


def test_every_metric_family_appears_on_at_least_one_dashboard():
    """Eleven families are exported; an unplotted one is telemetry nobody can see."""
    plotted: set[str] = set()
    for dashboard in ALL_DASHBOARDS.values():
        for _panel, target in iter_targets(dashboard):
            plotted |= metric_names_in(target["expr"])

    missing = METRIC_NAMES - plotted
    assert not missing, f"metric families exported but never plotted: {sorted(missing)}"


@pytest.mark.parametrize("path", ALL_DASHBOARDS, ids=DASHBOARD_IDS)
def test_no_dashboard_invents_a_precomputed_rate_series(path: Path):
    """Throughput and error percentage exist only as PromQL ratios, never as exported series."""
    for panel, target in iter_targets(ALL_DASHBOARDS[path]):
        for name in metric_names_in(target["expr"]):
            assert not name.endswith(("_per_second", "_rate_pct")), (
                f"{path.name}/{panel['title']} queries {name}, which would have to be a pre-computed rate"
            )


@pytest.mark.parametrize("path", ALL_DASHBOARDS, ids=DASHBOARD_IDS)
def test_derived_units_go_through_rate(path: Path):
    """A monotonic counter plotted as req/s or a percentage climbs forever and means nothing."""
    for panel in iter_panels(ALL_DASHBOARDS[path]):
        unit = panel.get("fieldConfig", {}).get("defaults", {}).get("unit")
        if unit not in DERIVED_UNITS:
            continue
        for target in panel.get("targets", []):
            expr = target["expr"]
            if metric_names_in(expr) & COUNTER_NAMES:
                assert "rate(" in expr, f"{path.name}/{panel['title']} plots a counter as {unit} without rate()"


@pytest.mark.parametrize("path", ALL_DASHBOARDS, ids=DASHBOARD_IDS)
def test_rate_windows_hold_at_least_two_scrapes(path: Path):
    """rate() over a window shorter than two scrape intervals returns nothing at all."""
    interval = interval_seconds(SCRAPE_INTERVAL)
    for panel, target in iter_targets(ALL_DASHBOARDS[path]):
        for window in re.findall(r"\[(\d+)s\]", target["expr"]):
            assert int(window) >= 2 * interval, (
                f"{path.name}/{panel['title']} uses a {window}s window over a {interval}s scrape"
            )


# ==============================================================================
# Palette & axis conventions
# ==============================================================================


@pytest.mark.parametrize("path", ALL_DASHBOARDS, ids=DASHBOARD_IDS)
def test_dashboards_use_only_the_sanctioned_palette(path: Path):
    """Same rule as the War Room: color is a status system, not decoration.

    Grafana's own named colors fail this deliberately. `"color": "green"` is not the emerald
    the War Room uses, and accepting it would let the two halves of the demo drift apart one
    UI edit at a time.
    """
    used = color_tokens_in(ALL_DASHBOARDS[path])
    unsanctioned = {color for color in used if color.upper() not in SANCTIONED_COLORS}
    assert not unsanctioned, f"{path.name} uses off-palette colors {sorted(unsanctioned)}"


def test_the_palette_gate_rejects_grafanas_own_notations():
    """Guards the gate itself. Each of these passed the original hex-only scan.

    A palette check that cannot see the notation a Grafana export actually produces is worse
    than no check, because it reports success.
    """
    for smuggled in (
        {"fieldConfig": {"defaults": {"color": {"mode": "fixed", "fixedColor": "green"}}}},
        {"thresholds": {"steps": [{"color": "semi-dark-orange", "value": None}]}},
        {"mappings": [{"options": {"1": {"text": "HEALTHY", "color": "#10B98180"}}}]},
        {"panels": [{"fieldConfig": {"defaults": {"color": {"fixedColor": "rgba(255,0,0,0.5)"}}}}]},
    ):
        found = color_tokens_in(smuggled)
        assert found, f"the walker saw no color at all in {smuggled}"
        assert any(color.upper() not in SANCTIONED_COLORS for color in found), (
            f"off-palette color slipped through the gate: {sorted(found)}"
        )


def test_mode_strings_are_not_mistaken_for_colors():
    """The other half of guarding the gate: no false positives on a well-formed color block."""
    legitimate = {"color": {"mode": "thresholds"}, "thresholds": {"steps": [{"color": "#10B981", "value": None}]}}
    assert color_tokens_in(legitimate) == {"#10B981"}


@pytest.mark.parametrize("path", ALL_DASHBOARDS, ids=DASHBOARD_IDS)
def test_millisecond_axes_do_not_auto_scale(path: Path):
    """An auto-scaled axis makes a 4,820ms spike look identical to a 48ms baseline."""
    for panel in iter_panels(ALL_DASHBOARDS[path]):
        defaults = panel.get("fieldConfig", {}).get("defaults", {})
        if defaults.get("unit") != "ms" or panel["type"] != "timeseries":
            continue
        assert defaults.get("max"), f"{path.name}/{panel['title']} lets its millisecond axis auto-scale"


@pytest.mark.parametrize("path", ALL_DASHBOARDS, ids=DASHBOARD_IDS)
def test_health_status_panels_map_the_full_enum(path: Path):
    """0/1/2 must read as DOWN/HEALTHY/DEGRADED. An unmapped value renders as a bare number."""
    for panel in iter_panels(ALL_DASHBOARDS[path]):
        exprs = [t["expr"] for t in panel.get("targets", [])]
        if not any(expr.strip() == MetricName.SYSTEM_HEALTH_STATUS.value for expr in exprs):
            continue

        mappings = panel["fieldConfig"]["defaults"]["mappings"]
        flattened = {key: value for mapping in mappings for key, value in mapping["options"].items()}
        assert set(flattened) == set(HEALTH_STATUS_MAPPING), f"{path.name}/{panel['title']} maps {sorted(flattened)}"

        for value, (text, color) in HEALTH_STATUS_MAPPING.items():
            assert flattened[value]["text"] == text
            assert flattened[value]["color"].upper() == color


def test_cache_hit_ratio_thresholds_are_inverted():
    """High is healthy for this one gauge, so its threshold ladder runs the other way. A copied
    green-base ladder would paint a 14% hit ratio green during a live cache stampede."""
    checked = 0
    for path, dashboard in ALL_DASHBOARDS.items():
        for panel in iter_panels(dashboard):
            exprs = [t["expr"] for t in panel.get("targets", [])]
            if not any(expr.strip() == MetricName.CACHE_HIT_RATIO_PCT.value for expr in exprs):
                continue
            steps = panel["fieldConfig"]["defaults"]["thresholds"]["steps"]
            assert steps[0]["value"] is None
            assert steps[0]["color"].upper() == "#EF4444", (
                f"{path.name}/{panel['title']} colors a collapsed hit ratio as healthy"
            )
            assert steps[-1]["color"].upper() == "#10B981"
            checked += 1

    assert checked >= 2, "expected the hit ratio on both a gauge and a time series"
