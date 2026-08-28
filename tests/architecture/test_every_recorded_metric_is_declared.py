"""Every metric name a call site RECORDS is declared in `metric_specs`.

CLAUDE.md already carries this rule's mirror image — "a declared metric with no
call site is a lie ... ship the name with its increment, or not yet". This is
the other direction, and it is the one that had an instance sitting in
production. `nora_peer_card_empty_total` was incremented by
`nora/engagement/context.py` from the day the peer-card blindness was found and
never added to `_COUNTERS`, so `Metrics.inc` fell past its counter branch to a
bare `self._gauges[name]` and raised `KeyError`.

**That raise was not loud.** It happens inside `_peer_card`'s own `except
Exception`, which logs "peer-card read failed" and increments
`nora_errors_total{stage="peer_card"}` — so the counter that exists purely to
tell «her card is genuinely empty» apart from «the read failed» reported every
genuinely empty card as a transport failure. Inverted, not merely missing. The
live registry confirmed it: the series was absent from `/metrics` entirely
while the code that writes it ran on every tagged turn.

One line fixed the instance. This file is the reason a second one cannot land
quietly, which is the actual defect — nothing anywhere compared call sites
against the declaration tuple.

WHAT THIS MEASURES, AND WHAT IT CANNOT
--------------------------------------
`Metrics` takes the name as its first positional argument, so the check is a
static read of that argument at every call site under `nora/`. Three argument
shapes are RESOLVED and checked:

  - a string literal                       — `inc("nora_x_total")`
  - a module-level `NAME = "nora_x_total"` — `inc(RELAY_METRIC)`
  - a conditional over two of the above    — `inc("a" if ok else "b")`

Everything else is a name this file CANNOT resolve — overwhelmingly a private
`_inc(self, name, ...)` forwarding wrapper, whose own callers pass literals and
are therefore checked at those call sites instead. Those are pinned in
`_UNRESOLVABLE` by (file, enclosing function, method) rather than by line, so
unrelated edits above them do not churn this test.

The pin is the point. A NEW unresolvable site fails this test rather than
passing it, because "I could not read this call site" is `unavailable`, never
`nothing_found` — a gate that silently skips what it cannot parse reports a
clean sweep over the exact code it did not look at. Adding a row is fine; doing
it deliberately is the whole requirement.

PROBE (run 2026-08-29, both directions, mutating production code only — never
this file; each mutation was asserted to have APPLIED and to still parse before
the run, because a mutation whose anchor silently missed measures the original
code and reports green):
  - Renamed the declared name to `nora_peer_card_empty_total_MUTANT` in
    `nora/obs/metric_specs.py`, so the real name is no longer declared:
    `test_every_resolvable_recorded_metric_name_is_declared` FAILED with
    `nora/engagement/context.py:374 records "nora_peer_card_empty_total"` —
    i.e. it names the live defect this fix closes. Restored.
  - Added `self._metrics.inc("nora_not_a_real_metric_total")` to
    `nora/engagement/context.py`: same test FAILED with
    `nora/engagement/context.py:376 records "nora_not_a_real_metric_total"`.
    Restored.
  - Renamed `_inc` to `_inc2` in `nora/language/note_curator.py` (a pinned
    forwarding wrapper, so the site becomes an unpinned unresolvable one):
    `test_unresolvable_metric_name_sites_are_pinned` FAILED with
    `nora/language/note_curator.py::_inc2 -> .inc()`. Restored.

The stale half of that second test earned its place before it was ever
committed: `_UNRESOLVABLE` was first written from a scan taken before module
constants were resolved, and the stale check immediately failed on three rows
(`relay.py::_count`, `self_id.py::_degraded`, `self_id.py::_publish`) that had
become checkable. Without it those three would have sat here as permanent
excuses for sites that are in fact verified.
"""

from __future__ import annotations

import ast
from pathlib import Path

from nora.obs.metric_specs import DECLARED_NAMES

ROOT = Path(__file__).resolve().parents[2]

# `Metrics`' own recording surface, derived from the class rather than guessed:
# these are the methods that take a metric NAME as their first argument. The
# private aliases are the forwarding wrappers several stages define
# (`self._inc(...)` -> `self._metrics.inc(...)`); including them is what raised
# this gate's coverage from 107 distinct names to 132 — the wrappers were
# exactly where the unchecked names were hiding.
_RECORDING_METHODS = frozenset(
    {"inc", "gauge", "observe", "gauge_function", "_inc", "_gauge", "_observe"}
)

# Call sites whose name argument this file cannot statically resolve, pinned by
# (path, enclosing function, method) — see the module docstring. Every row here
# is a forwarding wrapper taking `name` as a parameter, or `Metrics`' own
# internals calling `.inc()`/`.observe()` on a prometheus-client collector
# object (which takes a VALUE, not a name, and is not a metric-name site at
# all). Neither can be read here; both are checked where their callers pass a
# literal.
_UNRESOLVABLE: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("nora/engagement/recognition.py", "_inc", "inc"),
        ("nora/infra/sql/entities.py", "_inc", "inc"),
        ("nora/infra/sql/entities.py", "_set", "gauge"),
        ("nora/instructions/reload.py", "_on_failure", "inc"),
        ("nora/language/consolidator.py", "consolidate", "observe"),
        ("nora/language/exchange_consolidator.py", "consolidate", "observe"),
        ("nora/language/fact_adaptor.py", "_inc", "inc"),
        ("nora/language/note_curator.py", "_inc", "inc"),
        ("nora/language/note_revisitor.py", "_gauge", "gauge"),
        ("nora/language/note_revisitor.py", "_inc", "inc"),
        # `Metrics.inc` / `Metrics.observe` calling through to the
        # prometheus-client Counter/Gauge/Histogram it just looked up.
        ("nora/obs/metrics.py", "inc", "inc"),
        ("nora/obs/metrics.py", "observe", "observe"),
        ("nora/presence/collector.py", "_count", "inc"),
    }
)


def _production_modules() -> list[Path]:
    return sorted(
        p for p in (ROOT / "nora").rglob("*.py") if ".venv" not in p.parts
    )


def _module_level_strings(tree: ast.Module) -> dict[str, str]:
    """`RELAY_METRIC = "nora_relay_total"` and friends, so a call site that
    names its metric through a module constant is CHECKED rather than excused."""
    found: dict[str, str] = {}
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                found[target.id] = value.value
    return found


def _resolve(arg: ast.expr, consts: dict[str, str]) -> list[str] | None:
    """The metric names this argument can take, or None if it cannot be read.

    None is the `unavailable` answer and it is deliberately distinct from `[]`
    — a site that resolves to no names at all would silently pass, which is the
    failure this whole file exists to prevent.
    """
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return [arg.value]
    if isinstance(arg, ast.Name) and arg.id in consts:
        return [consts[arg.id]]
    if isinstance(arg, ast.IfExp):
        body = _resolve(arg.body, consts)
        orelse = _resolve(arg.orelse, consts)
        if body is None or orelse is None:
            return None
        return body + orelse
    return None


def _enclosing_function(tree: ast.Module, target: ast.Call) -> str | None:
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    current: ast.AST = target
    while current in parents:
        current = parents[current]
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
            return current.name
    return None


def _recording_calls(tree: ast.Module) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _RECORDING_METHODS
    ]


def test_every_resolvable_recorded_metric_name_is_declared() -> None:
    offenders: list[str] = []
    checked = 0
    for path in _production_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        consts = _module_level_strings(tree)
        for call in _recording_calls(tree):
            if not call.args:
                continue
            names = _resolve(call.args[0], consts)
            if names is None:
                continue  # pinned by the test below, not silently skipped
            for name in names:
                checked += 1
                if name not in DECLARED_NAMES:
                    rel = path.relative_to(ROOT)
                    offenders.append(f'{rel}:{call.lineno} records "{name}"')

    # POSITIVE CONTROL. `offenders == []` is also what a walk that matched
    # nothing produces — a renamed recording method, a moved package, an AST
    # change. Assert the sweep actually read something first, so a green result
    # means "checked and clean" rather than "found no code to check".
    assert checked > 150, (
        f"only {checked} metric-name arguments resolved; this sweep is supposed "
        "to see 200+. The walk stopped matching call sites — fix the walk "
        "before trusting a green run."
    )
    assert not offenders, (
        "metric names recorded but never declared in nora/obs/metric_specs.py "
        "(Metrics.inc falls through to a bare dict lookup and raises KeyError, "
        "which a surrounding `except Exception` will happily mistake for the "
        "failure of whatever it wrapped):\n  " + "\n  ".join(sorted(offenders))
    )


def test_unresolvable_metric_name_sites_are_pinned() -> None:
    found: set[tuple[str, str, str]] = set()
    for path in _production_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        consts = _module_level_strings(tree)
        for call in _recording_calls(tree):
            assert isinstance(call.func, ast.Attribute)
            if call.args and _resolve(call.args[0], consts) is not None:
                continue
            rel = str(path.relative_to(ROOT))
            fn = _enclosing_function(tree, call) or "<module>"
            found.add((rel, fn, call.func.attr))

    unpinned = found - _UNRESOLVABLE
    assert not unpinned, (
        "metric recording sites whose name this gate cannot read statically, "
        "and which are not pinned in _UNRESOLVABLE:\n  "
        + "\n  ".join(f"{p}::{fn} -> .{m}()" for p, fn, m in sorted(unpinned))
        + "\n\nA name this file cannot resolve is UNCHECKED, not clean. If it "
        "is a forwarding wrapper whose callers pass literals, add it to "
        "_UNRESOLVABLE with that reason. If it computes a metric name, it "
        "cannot be checked here at all and needs a runtime assertion instead."
    )

    # The pin must not rot in the other direction either: a stale row would
    # quietly excuse a site that no longer exists, and the next real one could
    # land on the same (file, function, method) triple and inherit the excuse.
    stale = _UNRESOLVABLE - found
    assert not stale, (
        "_UNRESOLVABLE rows matching no call site — delete them:\n  "
        + "\n  ".join(f"{p}::{fn} -> .{m}()" for p, fn, m in sorted(stale))
    )
