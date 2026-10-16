"""Every metric name a call site RECORDS is declared in `metric_specs`.

CLAUDE.md carries this rule's mirror image — "a declared metric with no call
site is a lie ... ship the name with its increment, or not yet". This is the
other direction: a name that is recorded but never declared makes
`Metrics.inc` fall past its counter branch to a bare `self._gauges[name]` and
raise `KeyError`.

**That raise is not loud.** A call site inside an `except Exception` (such as
`_peer_card` in `nora/engagement/context.py`, which logs "peer-card read
failed" and increments `nora_errors_total{stage="peer_card"}`) swallows it, so
the series is absent from `/metrics` and the error counter reports a failure
that did not happen. Nothing else compares call sites against the declaration
tuple; this file does.

WHAT THIS MEASURES, AND WHAT IT CANNOT
--------------------------------------
`Metrics` takes the name as its first positional argument, so the check is a
static read of that argument at every recording call site under `nora/`. Three
argument shapes are RESOLVED and checked:

  - a string literal                       — `inc("nora_x_total")`
  - a module-level `NAME = "nora_x_total"` — `inc(RELAY_METRIC)`
  - a conditional over two of the above    — `inc("a" if ok else "b")`

**The set of recording methods is DERIVED, not hand-listed**, and that is
load-bearing rather than tidy. `Metrics` itself offers four
(`_METRICS_METHODS`), but most stages call a private forwarding wrapper —
`self._inc(name, ...)` → `self._metrics.inc(name, ...)` — and those are where
the unchecked names hide. A hand-listed set silently skips any wrapper it does
not name (`nora/presence/collector.py::_count`, say): a clean sweep reported
over code nobody looked at is exactly this file's own subject matter, rebuilt
one level up inside the instrument.

So `_forwarding_wrappers` derives them per file: a method whose FIRST non-self
parameter is passed as the FIRST positional argument to a `Metrics` recording
method. That definition is what discriminates — it finds the real wrappers and
correctly REFUSES `nora/agent/relay.py::_count`, whose first parameter is an
R-27 status (`ok`/`nothing_found`/`unavailable`) forwarded as a LABEL while the
metric name is a module constant. Both sets are DERIVED at runtime, so no count
in this prose is the gate's authority.

Everything the derivation still cannot read is pinned in `_UNRESOLVABLE`. The
pin is keyed on (file, enclosing function, method, the argument's source text)
— NOT on a line number, so unrelated edits above it do not churn, and NOT on
the function alone, because a triple-keyed pin lets a NEW unresolvable site
added inside an already-pinned wrapper collapse onto the existing row and pass
green in both directions. Every pinned row is a live forwarding wrapper or
`Metrics`' own internals, which is precisely where a second computed increment
would naturally be written.

The pin is the point. A NEW unresolvable site fails this test rather than
passing it, because "I could not read this call site" is `unavailable`, never
`nothing_found` — a gate that silently skips what it cannot parse reports a
clean sweep over the exact code it did not look at. Adding a row is fine; doing
it deliberately is the whole requirement.

The stale half of the pin test keeps the pin honest in the other direction: a
row whose site has become checkable (module constants are resolved) would
otherwise sit here as a permanent excuse for a site that is in fact verified.

WHAT IS STILL NOT COVERED, stated rather than left to be discovered:
  - A name bound at CONSTRUCTION rather than at the call —
    `nora/instructions/reload.py`'s `self._metrics.inc(self._failure_counter)`,
    whose value arrives as a constructor kwarg from two call sites. It is
    pinned, and its two names are unchecked here.
  - A genuinely COMPUTED name (an f-string, a concatenation). None exists
    today; one introduced inside an already-pinned function is caught by the
    argument-text key, but it can only ever be pinned, never checked.
  - LABELS. `Metrics._full_labels` defaults a missing label to `""` and drops
    an undeclared one silently, so a call site passing a label the spec does
    not declare is not reported by anything, here or elsewhere.
"""

from __future__ import annotations

import ast
from pathlib import Path

from nora.obs.metric_specs import DECLARED_NAMES

ROOT = Path(__file__).resolve().parents[2]

# `Metrics`' own recording surface — the four methods that take a metric NAME as
# their first argument. Nothing else is hard-coded; see `_forwarding_wrappers`.
_METRICS_METHODS = frozenset({"inc", "gauge", "observe", "gauge_function"})

# The minimum this sweep must see: the thresholds sit just under what the tree
# holds, NOT at a round number far below it. Both axes are asserted because the
# read count alone survives losing the forwarding-wrapper derivation — the exact
# regression this file is about — and the distinct-name count is what actually
# catches it.
_MIN_NAME_READS = 245
_MIN_DISTINCT_NAMES = 117

# Recording sites whose name argument cannot be read statically, pinned by
# (path, enclosing function, method, argument source text). Every row is either
# a forwarding wrapper's own body — the `name` parameter, checked instead at
# the callers that pass it a literal — or `Metrics`' internals calling
# `.inc()`/`.observe()` on a prometheus-client collector object, which takes a
# VALUE and is not a metric-name site at all. `reload.py::_on_failure` is the
# one genuine gap: its name is bound at construction, so it is unchecked here.
_UNRESOLVABLE: frozenset[tuple[str, str, str, str]] = frozenset(
    {
        ("nora/instructions/reload.py", "_on_failure", "inc", "self._failure_counter"),
        ("nora/app/cognition_session.py", "_inc", "inc", "name"),
        ("nora/obs/metrics.py", "inc", "inc", "value"),
        ("nora/obs/metrics.py", "observe", "observe", "value"),
        ("nora/presence/collector.py", "_count", "inc", "name"),
    }
)


def _production_modules() -> list[Path]:
    return sorted(
        p for p in (ROOT / "nora").rglob("*.py") if ".venv" not in p.parts
    )


def _forwarding_wrappers(tree: ast.Module) -> frozenset[str]:
    """Method names in THIS module that forward their first parameter to a
    `Metrics` recording method — `def _inc(self, name, ...): ...inc(name, ...)`.

    Derived rather than listed, per CLAUDE.md's rule that a vocabulary the code
    is checked against is read off the code and never hand-copied. The "first
    parameter forwarded as the first argument" test is what separates a real
    name-forwarding wrapper from the same-named STATUS wrapper in
    `nora/agent/relay.py`, whose first parameter is an R-27 status.
    """
    found: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        params = [a.arg for a in fn.args.args if a.arg not in ("self", "cls")]
        if not params:
            continue
        first = params[0]
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _METRICS_METHODS
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == first
            ):
                found.add(fn.name)
                break
    return frozenset(found)


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
    — a site that resolved to no names at all would silently pass, which is the
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
    """Calls that could be recording a metric, matched by method name.

    **At least one POSITIONAL argument is required, and that is a structural
    fact rather than a convenience.** Every `Metrics` recording method takes the
    name as a required positional parameter — `def inc(self, name, value=1.0,
    **labels)`, and the same shape for `gauge`, `gauge_function` and `observe`.
    A call passing nothing positionally therefore cannot be one of them.

    That is what separates them from an unrelated `.observe(...)` whose
    signature is keyword-only (`async def observe(self, *, batch: ...)`): no
    name, not a metric. Matching on the method name alone would report such
    calls as unreadable metric sites, and pinning them would park a permanent
    excuse on calls that are not in this file's subject matter at all.

    This is the one receiver-blindness the name match still has, and it is
    closed by the callee's signature rather than by guessing at the receiver.
    """
    names = _METRICS_METHODS | _forwarding_wrappers(tree)
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in names
        and node.args
    ]


def test_every_resolvable_recorded_metric_name_is_declared() -> None:
    offenders: list[str] = []
    reads = 0
    distinct: set[str] = set()
    for path in _production_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        consts = _module_level_strings(tree)
        for call in _recording_calls(tree):
            names = _resolve(call.args[0], consts)
            if names is None:
                continue  # pinned by the test below, not silently skipped
            for name in names:
                reads += 1
                distinct.add(name)
                if name not in DECLARED_NAMES:
                    rel = path.relative_to(ROOT)
                    offenders.append(f'{rel}:{call.lineno} records "{name}"')

    # POSITIVE CONTROL, on both axes. An empty `offenders` is also what a walk
    # that matched nothing produces — a renamed recording method, a moved
    # package, an AST change, or the forwarding-wrapper derivation silently
    # ceasing to match. Assert the sweep actually read what it is supposed to,
    # so a green result means "checked and clean" and not "found no code to
    # check". BOTH numbers, because the read count alone stays above a loose
    # threshold when the forwarding-wrapper derivation is lost.
    assert reads >= _MIN_NAME_READS and len(distinct) >= _MIN_DISTINCT_NAMES, (
        f"the sweep resolved {reads} name reads over {len(distinct)} distinct "
        f"names, below the {_MIN_NAME_READS}/{_MIN_DISTINCT_NAMES} this tree "
        "contains. The walk stopped matching call sites — most likely "
        "_forwarding_wrappers no longer recognises the private wrappers. Fix "
        "the walk before trusting a green run."
    )
    assert not offenders, (
        "metric names recorded but never declared in nora/obs/metric_specs.py "
        "(Metrics.inc falls through to a bare dict lookup and raises KeyError, "
        "which a surrounding `except Exception` will happily mistake for the "
        "failure of whatever it wrapped):\n  " + "\n  ".join(sorted(offenders))
    )


def test_unresolvable_metric_name_sites_are_pinned() -> None:
    found: set[tuple[str, str, str, str]] = set()
    for path in _production_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        consts = _module_level_strings(tree)
        for call in _recording_calls(tree):
            assert isinstance(call.func, ast.Attribute)
            if _resolve(call.args[0], consts) is not None:
                continue
            rel = str(path.relative_to(ROOT))
            fn = _enclosing_function(tree, call) or "<module>"
            arg = ast.unparse(call.args[0])
            found.add((rel, fn, call.func.attr, arg))

    unpinned = found - _UNRESOLVABLE
    assert not unpinned, (
        "metric recording sites whose name this gate cannot read statically, "
        "and which are not pinned in _UNRESOLVABLE:\n  "
        + "\n  ".join(f"{p}::{fn} -> .{m}({a})" for p, fn, m, a in sorted(unpinned))
        + "\n\nA name this file cannot resolve is UNCHECKED, not clean. If it "
        "is a forwarding wrapper whose callers pass literals, add it to "
        "_UNRESOLVABLE with that reason. If it computes a metric name, it "
        "cannot be checked here at all and needs a runtime assertion instead."
    )

    # The pin must not rot in the other direction either: a stale row would
    # quietly excuse a site that no longer exists, and the next real one could
    # land on the same key and inherit the excuse.
    stale = _UNRESOLVABLE - found
    assert not stale, (
        "_UNRESOLVABLE rows matching no call site — delete them:\n  "
        + "\n  ".join(f"{p}::{fn} -> .{m}({a})" for p, fn, m, a in sorted(stale))
    )
