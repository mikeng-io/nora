"""A reply's quote resolves through ONE function, and its tie goes to the FIRST row.

`ts` is not unique (a burst, a re-import, a platform that stamps by second), so
a `ts -> row` map has to say which row a duplicated `ts` means. Prefill's
resolver is FIRST-wins; a transcript that builds its own map with a dict
comprehension is LAST-wins, and the same reply then resolves to two different
parents in the two prompts that carry it. A docstring recording the tie-break is
not enough, because the next map is written by someone who has not read it.

So the test reads the tree (`nora/`, `scripts/` and `evals/`). No module may
build a map keyed by a row's timestamp itself; the one place that does is
`first_by_ts`, which does it with `setdefault`. The shapes it recognises, each
with a positive control below: a dict comprehension or literal keyed by `.ts`,
`getattr(x, "ts")` or `x["ts"]`; `dict(...)` over `(ts, row)` pairs or over a
`zip` whose keys are timestamps; `m.update(...)` of the same; `m[key] = row`
where the key is `.ts` or a local that was assigned from one. A `setdefault`
keyed by a timestamp inside a loop over `reversed(...)`, a `[::-1]` slice or
`sorted(..., reverse=True)` is LAST-wins written as first-wins, and so is
`first_by_ts(reversed(rows))`; both are flagged. And every module that resolves
a quote must import `first_by_ts`.

**What this test cannot see**, so a reviewer knows what is left to read:

  - a key that is not spelt `.ts` at the point it is used: a timestamp held in a
    differently named field (`quoted_ts` of another row, `timestamp`,
    `message.timestamp`), a tuple key such as `(conversation_id, ts)`, a key
    computed by a helper or a lambda (`key=lambda r: r.ts` into
    `itertools.groupby` or `sorted`), or a string key (`str(r.ts)`);
  - a reordering that is not spelt `reversed`, `[::-1]` or `reverse=True`: rows
    sorted descending by a key, or built in an order the caller chose, before
    they reach `first_by_ts`, which is first-wins over whatever order it is
    handed;
  - a map built in SQL or in a store (the `rollups` join, the audit store), which
    is first-wins by its own ORDER BY and is outside this rule;
  - an `EXISTS` predicate over rows at a `ts` (`replies_to_nora`: «any row at
    that ts is hers»), which is not a map and has no tie to break;
  - a resolver in a module that is not in `RESOLVERS`: the import check names the
    three that exist, and the tree scan is what catches a fourth that builds its
    own map.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCANNED = ("nora", "scripts", "evals")

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
    """`x.ts`, `getattr(x, "ts")` or `x["ts"]`."""
    if isinstance(node, ast.Attribute):
        return node.attr == "ts"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
        return len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) \
            and node.args[1].value == "ts"
    if isinstance(node, ast.Subscript):
        return isinstance(node.slice, ast.Constant) and node.slice.value == "ts"
    return False


def _is_reorder(node: ast.AST) -> bool:
    """`reversed(x)`, `x[::-1]` or `sorted(x, reverse=True)`."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "reversed":
            return True
        if node.func.id == "sorted":
            return any(
                k.arg == "reverse" and isinstance(k.value, ast.Constant) and k.value.value is True
                for k in node.keywords
            )
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
        step = node.slice.step
        return isinstance(step, ast.UnaryOp) and isinstance(step.op, ast.USub)
    return False


class _Scan(ast.NodeVisitor):
    """Finds the shapes in the docstring. A name counts as a timestamp key in the
    function (or module) that assigned it from `.ts`."""

    def __init__(self) -> None:
        self.lines: list[int] = []
        self._scopes: list[set[str]] = [set()]
        self._reordered_loops = 0

    # -- scope: a local that holds a row's timestamp ---------------------------
    def _key(self, node: ast.AST) -> bool:
        if _is_ts(node):
            return True
        return isinstance(node, ast.Name) and node.id in self._scopes[-1]

    def _function(self, node: ast.AST) -> None:
        self._scopes.append(set())
        self.generic_visit(node)
        self._scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node)

    def _pairs(self, node: ast.AST) -> bool:
        """A generator or list of `(ts, row)` pairs, or a `zip` whose keys are."""
        if isinstance(node, ast.GeneratorExp | ast.ListComp):
            elt = node.elt
            return isinstance(elt, ast.Tuple) and bool(elt.elts) and self._key(elt.elts[0])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "zip":
            first = node.args[0] if node.args else None
            return isinstance(first, ast.GeneratorExp | ast.ListComp) and self._key(first.elt)
        return False

    # -- the shapes ------------------------------------------------------------
    def visit_Assign(self, node: ast.Assign) -> None:
        if _is_ts(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self._scopes[-1].add(target.id)
        if any(isinstance(t, ast.Subscript) and self._key(t.slice) for t in node.targets):
            self.lines.append(node.lineno)
        self.generic_visit(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        if self._key(node.key):
            self.lines.append(node.lineno)
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        if any(k is not None and self._key(k) for k in node.keys):
            self.lines.append(node.lineno)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        arg = node.args[0] if node.args else None
        if isinstance(func, ast.Name) and func.id == "dict" and arg is not None \
                and self._pairs(arg):
            self.lines.append(node.lineno)
        if isinstance(func, ast.Attribute) and func.attr == "update" and arg is not None \
                and self._pairs(arg):
            self.lines.append(node.lineno)
        if isinstance(func, ast.Name) and func.id in (HELPER, "_by_ts") and arg is not None \
                and _is_reorder(arg):
            self.lines.append(node.lineno)
        if isinstance(func, ast.Attribute) and func.attr == "setdefault" and node.args \
                and self._key(node.args[0]) and self._reordered_loops:
            self.lines.append(node.lineno)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        reordered = _is_reorder(node.iter)
        self._reordered_loops += reordered
        self.generic_visit(node)
        self._reordered_loops -= reordered


def _ts_keyed_maps(tree: ast.AST) -> list[int]:
    """The line of every map this module builds keyed by a row's timestamp, and
    of every first-wins written as last-wins."""
    scan = _Scan()
    scan.visit(tree)
    return sorted(set(scan.lines))


def _tree(rel: str) -> ast.AST:
    return ast.parse((ROOT / rel).read_text(encoding="utf-8"))


def _modules() -> list[str]:
    return sorted(
        p.relative_to(ROOT).as_posix()
        for root in SCANNED for p in (ROOT / root).rglob("*.py")
        if ".venv" not in p.parts and "__pycache__" not in p.parts
    )


#: One positive control per shape the scan claims to see. A scan that cannot
#: flag the shapes it exists for is decoration.
FLAGGED = {
    "comprehension": "m = {r.ts: r for r in rows}",
    "comprehension-with-a-filter": "m = {row.ts: row for row in rows if row.body}",
    "dict-over-pairs": "m = dict((r.ts, r) for r in rows)",
    "dict-over-a-list-of-pairs": "m = dict([(r.ts, r) for r in rows])",
    "dict-over-zip": "m = dict(zip([r.ts for r in rows], rows))",
    "literal": "m = {rows[0].ts: rows[0]}",
    "subscript-store": "m = {}\nfor r in rows:\n    m[r.ts] = r",
    "local-key": "m = {}\nfor r in rows:\n    k = r.ts\n    m[k] = r",
    "local-key-in-a-function": "def f(rows):\n    m = {}\n    for r in rows:\n"
                               "        k = r.ts\n        m[k] = r\n    return m",
    "update-over-pairs": "m = {}\nm.update((r.ts, r) for r in rows)",
    "update-over-zip": "m = {}\nm.update(zip([r.ts for r in rows], rows))",
    "getattr-key": "m = {getattr(r, 'ts'): r for r in rows}",
    "item-key": "m = {r['ts']: r for r in rows}",
    "helper-over-reversed": "m = first_by_ts(reversed(rows))",
    "alias-over-reversed": "m = _by_ts(reversed(rows))",
    "helper-over-a-reversed-slice": "m = first_by_ts(rows[::-1])",
    "helper-over-sorted-descending": "m = first_by_ts(sorted(rows, key=f, reverse=True))",
    "setdefault-over-reversed": "m = {}\nfor r in reversed(rows):\n    m.setdefault(r.ts, r)",
    "setdefault-over-a-reversed-slice": "m = {}\nfor r in rows[::-1]:\n    m.setdefault(r.ts, r)",
    "setdefault-over-sorted-descending":
        "m = {}\nfor r in sorted(rows, key=f, reverse=True):\n    m.setdefault(r.ts, r)",
}

#: What it must NOT flag: maps keyed by something else, the helper's own
#: first-wins loop, a plain list of timestamps, and a reversed loop that does not
#: key a map by a timestamp.
CLEAN = {
    "keyed-by-id": "m = {r.id: r.ts for r in rows}",
    "first-wins-loop": "m = {}\nfor r in rows:\n    m.setdefault(r.ts, r)",
    "a-list-of-stamps": "stamps = [r.ts for r in rows]",
    "helper-over-the-rows": "m = first_by_ts(rows)",
    "reversed-loop-keyed-by-id": "m = {}\nfor r in reversed(rows):\n    m.setdefault(r.id, r)",
    "a-local-not-from-ts": "m = {}\nfor r in rows:\n    k = r.id\n    m[k] = r",
    "a-local-from-another-function":
        "def f(r):\n    k = r.ts\n    return k\ndef g(m, r, k):\n    m[k] = r",
    "zip-of-ids": "m = dict(zip([r.id for r in rows], rows))",
}


@pytest.mark.parametrize("shape", sorted(FLAGGED))
def test_the_scan_flags_this_shape(shape: str):
    assert _ts_keyed_maps(ast.parse(FLAGGED[shape])), FLAGGED[shape]


@pytest.mark.parametrize("shape", sorted(CLEAN))
def test_the_scan_leaves_this_shape_alone(shape: str):
    assert not _ts_keyed_maps(ast.parse(CLEAN[shape])), CLEAN[shape]


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
