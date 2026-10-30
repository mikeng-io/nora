"""The SigNoz deployment config, as declared — the parts whose semantics
invisible tests cannot see but a typo silently breaks:

* `deploy/signoz/alerts/rules.yaml` — the `for: Nm` hold maps to
  `all_the_times` + `min_points` over an UNFILTERED 0/1 series; `for: 0m`
  maps to `last`. A comparison baked into the query produces a filtered
  series that fires on one bad point and never recovers — the defect the
  independent review found, and the reason every rule here is checked by
  name rather than by shape alone.
* `deploy/signoz/deployment/ingester/ingester.yaml` — the collector-side
  cache-key normalization (SDK flat keys → UI dotted keys on model leaves
  only, agent aggregates excluded) and the one canonical log source
  (journald CONTAINER_TAG matches, no unscoped filelog).
* `deploy/signoz/docker-compose.signoz.yaml` — the owned bridge producers
  carry journald tags matching the receiver's match list.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
RULES = yaml.safe_load((ROOT / "deploy/signoz/alerts/rules.yaml").read_text())["rules"]
INGESTER = yaml.safe_load(
    (ROOT / "deploy/signoz/deployment/ingester/ingester.yaml").read_text())
OVERLAY = yaml.safe_load(
    (ROOT / "deploy/signoz/docker-compose.signoz.yaml").read_text())
COMPOSE = yaml.safe_load(
    (ROOT / "deploy/signoz/deployment/compose.yaml").read_text())

_FOR = re.compile(r"for (\d+)m")
def _comparisons_without_bool(query: str) -> list[str]:
    """PromQL comparisons OUTSIDE label selectors — `{x!="y"}` is a matcher,
    not a filter. Every real comparison must carry `bool`."""
    scrubbed = re.sub(r"\{[^}]*\}", "", query)
    return re.findall(r"[!<>=]=?", scrubbed)


def _by_name(name: str) -> dict:
    return next(r for r in RULES if r["name"] == name)


def test_every_rule_declares_its_hold_or_is_a_page():
    """`for Nm` -> all_the_times + matching window + min_points covering the
    whole window (60s eval step); `for 0m`/no-for -> `last`. The only
    exception is the logs-based probe rule, whose intent is page-on-any."""
    assert len(RULES) == 20
    for rule in RULES:
        port = rule["port"]
        hold = _FOR.search(port)
        if rule["name"] == "SessionProbeAlert":
            assert rule["match"] == "at_least_once"
            continue
        if hold is None or hold.group(1) == "0":
            assert rule["match"] == "last", rule["name"]
            assert rule["window"] == "1m", rule["name"]
            continue
        minutes = int(hold.group(1))
        assert rule["match"] == "all_the_times", rule["name"]
        assert rule["window"] == f"{minutes}m", rule["name"]
        # window-seconds/60 + 1 — the full-window coverage `for` enforces.
        assert rule["min_points"] == minutes + 1, rule["name"]


def test_no_query_filters_its_own_condition():
    """A comparison inside the query (without `bool`), a bare `unless`/`and`
    set op, or a bare `absent()` produces a series that exists ONLY while
    bad — one bad point fires, and the last bad point keeps firing after
    recovery. Every compound query must emit an unfiltered 0/1."""
    for rule in RULES:
        if rule.get("signal") == "logs":
            continue  # clickhouse_sql, not promql
        query = rule["query"]
        assert " unless " not in query, rule["name"]
        assert " and " not in query, rule["name"]
        if "absent(" in query:
            assert "or vector(0)" in query, rule["name"]
        for hit in _comparisons_without_bool(query):
            # Every comparison must carry `bool` — a filtered series is the
            # exact defect under test.
            assert re.search(r"[!<>=]=?\s*bool", query), (
                f"{rule['name']}: comparison {hit!r} without bool")


def test_annotations_never_use_dotted_label_access():
    """`{{ $labels.service.name }}` renders EMPTY — the measured bug; the
    working form for dotted names is `{{ index $labels "service.name" }}`."""
    for rule in RULES:
        for field in ("summary", "description"):
            text = rule.get(field) or ""
            assert not re.search(r"\$labels\.[A-Za-z_]+\.", text), (
                f"{rule['name']}.{field}: dotted label access")


def test_min_points_lands_on_the_rule_condition():
    """The API field placement — condition.requireMinPoints +
    requiredNumPoints, not a threshold field (PostableRule v2alpha1)."""
    import scripts.signoz_provision as prov

    rule = {
        "query": "up", "op": "equal", "target": 0,
        "match": "all_the_times", "window": "5m", "min_points": 6,
        "severity": "critical",
    }
    payload = prov._rule_payload("X", rule, {}, "nora-alerts")
    condition = payload["condition"]
    assert condition["requireMinPoints"] is True
    assert condition["requiredNumPoints"] == 6
    # A `for 0m` rule carries no point requirement.
    rule.pop("min_points")
    rule["match"] = "last"
    payload = prov._rule_payload("X", rule, {}, "nora-alerts")
    assert "requireMinPoints" not in payload["condition"]


def test_the_declared_matches_are_the_backends_five():
    """`min`/`max` look plausible but the pinned threshold evaluator does
    not implement them — the closed set exists so a typo fails HERE."""
    import scripts.signoz_provision as prov

    assert {
        "at_least_once", "all_the_times", "on_average", "in_total",
        "last",
    } == prov._MATCHES


def test_cache_keys_are_normalized_only_on_model_leaves():
    """The SDK emits `cache_read_input_tokens`/`cache_write_input_tokens`;
    the UI and signozllmpricing read the DOTTED keys. The aliases must fire
    on leaves and stay off the invoke_agent parent, whose flat usage was
    already moved to gen_ai.agent.usage.* — a second copy would double the
    totals."""
    statements = []
    for stmt in INGESTER["processors"]["transform/normalize_llm"][
            "trace_statements"]:
        statements += stmt["statements"]

    def finds(src: str, dst: str, guard: str) -> bool:
        return any(
            f'set(attributes["{dst}"], attributes["{src}"])' in s
            and guard in s
            for s in statements)

    leaf = 'attributes["gen_ai.operation.name"] != "invoke_agent"'
    assert finds("gen_ai.usage.cache_read_input_tokens",
                 "gen_ai.usage.cache_read.input_tokens", leaf)
    assert finds("gen_ai.usage.cache_write_input_tokens",
                 "gen_ai.usage.cache_creation.input_tokens", leaf)
    agent = 'attributes["gen_ai.operation.name"] == "invoke_agent"'
    # The agent parent keeps its usage under the agent.* namespace — and the
    # FLAT keys are gone from gen_ai.usage.* so nothing reads them twice.
    assert finds("gen_ai.usage.cache_write_input_tokens",
                 "gen_ai.agent.usage.cache_write_input_tokens", agent)
    assert any(
        'delete_key(attributes, "gen_ai.usage.cache_write_input_tokens")' in s
        and agent in s
        for s in statements)


def test_journald_is_the_one_log_source_scoped_to_owned_producers():
    """No filelog receiver: docker json-file logs carry no container name,
    so a glob can never scope them. The journald match list IS the scope —
    every owned producer's tag must be listed, nothing else may be."""
    receivers = INGESTER["receivers"]
    assert not any(name.startswith("filelog") for name in receivers)
    logs = INGESTER["service"]["pipelines"]["logs/containers"]["receivers"]
    assert logs == ["journald/nora"]
    tags = [m["CONTAINER_TAG"] for m in receivers["journald/nora"]["matches"]
            if "CONTAINER_TAG" in m]
    assert set(tags) == {
        "nora", "presence", "analytics",
        "signal-cli", "whatsapp-bridge", "instagram-bridge",
    }
    assert any(m.get("SYSLOG_IDENTIFIER") == "nora-session-probe"
               for m in receivers["journald/nora"]["matches"])


def test_the_compose_mount_and_overlay_match_the_journald_source():
    """The deployment mounts the journal, not the docker container dir;
    the overlay tags exactly the owned producers the match list names."""
    volumes = COMPOSE["services"]["ingester"]["volumes"]
    assert not any(v.startswith("/var/lib/docker/containers") for v in volumes)
    assert "/var/log/journal:/var/log/journal:ro" in volumes
    tagged = {
        name for name, svc in OVERLAY["services"].items()
        if (svc.get("logging") or {}).get("driver") == "journald"
    }
    assert tagged == {
        "nora", "presence", "analytics",
        "instagram-bridge", "whatsapp-bridge", "signal-cli",
    }
    for name in tagged:
        assert OVERLAY["services"][name]["logging"]["options"]["tag"] == name
