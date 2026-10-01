"""A reply's quote resolves through ONE function, and its tie goes to the FIRST row.

`ts` is not unique (a burst, a re-import, a platform that stamps by second), so
a `ts -> row` map has to say which row a duplicated `ts` means. Prefill's
resolver was written FIRST-wins after a reply resolved to the wrong row; the
debrief's transcript and the tool-result transcript each built their own map
with a dict comprehension, which is LAST-wins, and kept it for weeks: one
reply, two different parents in the two prompts that carried it (step 4c of
`design/context-assembly/04-every-occasion.md`). A docstring recording the
tie-break was not enough, because the next map is written by someone who has
not read it.

So the test reads the tree. No module in `nora/` may build a map keyed by a
row's `.ts` itself (a dict comprehension, a `dict(...)` over pairs, or a
subscript store); the one place that does is `first_by_ts`, which does it with
`setdefault`. And every module that resolves a quote must import that function.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "nora"

#: The modules that resolve a reply's quote against a window of rows, and so
#: must resolve it the way prefill does.
RESOLVERS = (
    "nora/agent/prefill.py",
    "nora/agent/tools.py",
    "nora/engagement/debrief_render.py",
)

HELPER = "first_by_ts"
HELPER_MODULE = "nora.domain.quote_window"


def _is_ts(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "ts"


def _builds_a_ts_keyed_map(node: ast.AST) -> bool:
    if isinstance(node, ast.DictComp):
        return _is_ts(node.key)
    if isinstance(node, ast.Dict):
        return any(k is not None and _is_ts(k) for k in node.keys)
    if isinstance(node, ast.Assign):
        return any(isinstance(t, ast.Subscript) and _is_ts(t.slice) for t in node.targets)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict":
        arg = node.args[0] if node.args else None
        return (
            isinstance(arg, ast.GeneratorExp | ast.ListComp)
            and isinstance(arg.elt, ast.Tuple) and bool(arg.elt.elts)
            and _is_ts(arg.elt.elts[0])
        )
    return False


def _ts_keyed_maps(tree: ast.AST) -> list[int]:
    """The line of every map this module builds keyed by something's `.ts`:
    `{r.ts: r for r in rows}`, `dict((r.ts, r) for r in rows)` and
    `out[r.ts] = r`."""
    return [n.lineno for n in ast.walk(tree) if _builds_a_ts_keyed_map(n)]  # type: ignore[attr-defined]


def _tree(rel: str) -> ast.AST:
    return ast.parse((ROOT / rel).read_text(encoding="utf-8"))


def _modules() -> list[str]:
    return sorted(p.relative_to(ROOT).as_posix() for p in PKG.rglob("*.py"))


def test_the_detector_sees_every_way_to_build_the_map():
    """A scan that cannot flag the shapes it exists for is decoration."""
    for source in (
        "m = {r.ts: r for r in rows}",
        "m = {row.ts: row for row in rows if row.body}",
        "m = dict((r.ts, r) for r in rows)",
        "m = {}\nfor r in rows:\n    m[r.ts] = r",
        "m = {rows[0].ts: rows[0]}",
    ):
        assert _ts_keyed_maps(ast.parse(source)), source
    for source in (
        "m = {r.id: r.ts for r in rows}",
        "m = {}\nfor r in rows:\n    m.setdefault(r.ts, r)",
        "stamps = [r.ts for r in rows]",
    ):
        assert not _ts_keyed_maps(ast.parse(source)), source


def test_no_module_builds_its_own_ts_keyed_row_map():
    offenders = {
        rel: lines for rel in _modules()
        if rel != "nora/domain/quote_window.py"   # `first_by_ts`, with `setdefault`
        if (lines := _ts_keyed_maps(_tree(rel)))
    }
    assert not offenders, (
        f"{offenders} build a map keyed by a row's `.ts`. A dict comprehension "
        "is LAST-wins for a duplicated `ts` and prefill's resolver is FIRST-wins, "
        f"so one reply names two parents. Use `{HELPER}` from `{HELPER_MODULE}`."
    )


def test_the_helper_is_first_wins():
    """It does what its docstring says, written against the tree's own function."""
    tree = _tree("nora/domain/quote_window.py")
    helper = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == HELPER
    )
    calls = [
        n.func.attr for n in ast.walk(helper)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    ]
    assert "setdefault" in calls, f"{HELPER} no longer keeps the first row at a `ts`"


@pytest.mark.parametrize("rel", RESOLVERS)
def test_every_resolver_of_a_quote_imports_the_one_helper(rel: str):
    tree = _tree(rel)
    imported = any(
        isinstance(n, ast.ImportFrom) and n.level in (0, 2)
        and (n.module or "").endswith("domain.quote_window")
        and any(a.name == HELPER for a in n.names)
        for n in ast.walk(tree)
    )
    assert imported, f"{rel} resolves a reply's quote and does not use `{HELPER}`"
    used = any(
        isinstance(n, ast.Name) and n.id in (HELPER, "_by_ts") and isinstance(n.ctx, ast.Load)
        for n in ast.walk(tree)
    )
    assert used, f"{rel} imports `{HELPER}` and never calls it"
