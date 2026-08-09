"""D2: the intent graph + the three trajectory (routing) evaluators.

These grade tool ROUTING, not turn mechanics: given a user journey, did she
reach the right tool (`PathMatchesIntent`), avoid the plausible-but-wrong
neighbour (`NoSiblingLeakage`, derived from the `IntentGraph`), and converge
without flailing (`ConvergesInBudget`)? The "same same but different" cases live
in `tests/test_evals_journeys.py` (D3) against the real seed graph; this file
pins the mechanism.
"""

from evals.evaluators import (
    SEED_INTENT_GRAPH,
    UNMEASURABLE_LIVE,
    ConvergesInBudget,
    Intent,
    IntentGraph,
    NoSiblingLeakage,
    PathMatchesIntent,
)
from evals.harness import Trajectory, _tool_calls_from_metrics
from nora.app.eval_support import Metrics


def _t(tool_calls=()) -> Trajectory:
    return Trajectory(tool_calls=list(tool_calls), sent=["reply"], reply="reply", honcho_feeds=1)


# A tiny graph: weather and where_is are each confusable with web_search, and
# with each other only through web_search (no direct edge) — so a weather
# journey's siblings are {web_search}, NOT {where_is}.
GRAPH = IntentGraph([("weather", "web_search"), ("where_is", "web_search")])
WEATHER = Intent(name="weather", required=frozenset({"weather"}), budget=2)


# --- IntentGraph -----------------------------------------------------------


def test_graph_is_undirected():
    g = IntentGraph([("a", "b")])
    assert g.neighbours("a") == frozenset({"b"})
    assert g.neighbours("b") == frozenset({"a"})


# --- live tool_calls reconstruction (offline, deterministic) ---------------


def test_tool_calls_reconstructed_from_both_counters():
    """`_tool_calls_from_metrics` must read BOTH per-tool counters: native tools
    record `nora_tool_calls_total`, but the room/memory tools record the SEPARATE
    `nora_room_lookups_total`. Reading only the first is blind to the memory
    cluster — reverting the union in the helper reddens this (the `about` call
    would vanish)."""
    m = Metrics()
    m.inc("nora_tool_calls_total", tool="weather", status="ok")
    m.inc("nora_tool_calls_total", tool="weather", status="ok")  # multiset: twice
    m.inc("nora_room_lookups_total", tool="about", status="ok")
    calls = _tool_calls_from_metrics(m)
    assert sorted(calls) == ["about", "weather", "weather"]


def test_no_intent_graph_tool_is_live_unmeasurable():
    """Every tool the seed graph grades must be reconstructable from metrics in a
    live run (the CLAUDE.md instrument rule: don't grade what you can't measure).
    A tool in `UNMEASURABLE_LIVE` (media_fetch — its counter carries no `tool`
    label) as a graph node would let the live gate read a blindness as a verdict."""
    assert SEED_INTENT_GRAPH.nodes().isdisjoint(UNMEASURABLE_LIVE)


def test_graph_ignores_self_edges():
    assert IntentGraph([("a", "a")]).neighbours("a") == frozenset()


def test_siblings_are_neighbours_minus_required():
    # weather's neighbour is web_search; weather itself is required, so it is not
    # its own sibling.
    assert GRAPH.siblings_of({"weather"}) == frozenset({"web_search"})


def test_required_tools_shadow_each_others_edges():
    # A journey that legitimately needs BOTH ends of an edge is not leaking by
    # calling either — required tools cancel out of the sibling set.
    assert IntentGraph([("a", "b")]).siblings_of({"a", "b"}) == frozenset()


# --- PathMatchesIntent -----------------------------------------------------


def test_path_matches_when_required_tool_called():
    assert PathMatchesIntent(WEATHER).evaluate(_t(["weather", "send_reply"])).passed


def test_path_fails_when_required_tool_missing():
    r = PathMatchesIntent(WEATHER).evaluate(_t(["web_search", "send_reply"]))
    assert not r.passed
    assert "weather" in r.reason


# --- NoSiblingLeakage ------------------------------------------------------


def test_no_leakage_when_only_required_called():
    assert NoSiblingLeakage(WEATHER, GRAPH).evaluate(_t(["weather", "send_reply"])).passed


def test_leakage_when_sibling_called():
    # Reached the RIGHT tool but ALSO the wrong neighbour — still a leak.
    r = NoSiblingLeakage(WEATHER, GRAPH).evaluate(_t(["weather", "web_search", "send_reply"]))
    assert not r.passed
    assert "web_search" in r.reason


def test_non_sibling_tool_is_not_leakage():
    # where_is is not a sibling of weather in this graph (no direct edge), so
    # calling it is not flagged by THIS check (PathMatchesIntent would catch a
    # wrong path separately).
    r = NoSiblingLeakage(WEATHER, GRAPH).evaluate(_t(["weather", "where_is", "send_reply"]))
    assert r.passed


# --- ConvergesInBudget -----------------------------------------------------


def test_converges_within_budget():
    assert ConvergesInBudget(WEATHER).evaluate(_t(["weather", "send_reply"])).passed


def test_send_reply_does_not_count_against_budget():
    # Three replies (multi-message) plus one work call, budget 2 → still converges:
    # only the work call counts.
    assert ConvergesInBudget(WEATHER).evaluate(
        _t(["weather", "send_reply", "send_reply", "send_reply"])
    ).passed


def test_over_budget_is_flail():
    # Three work calls against a budget of 2 — the runaway-search shape.
    r = ConvergesInBudget(WEATHER).evaluate(
        _t(["web_search", "web_search", "web_search", "send_reply"])
    )
    assert not r.passed
    assert "budget 2" in r.reason
