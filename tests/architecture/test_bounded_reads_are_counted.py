"""A wired bounded read over-fetches, cuts, and counts — all three or none.

`nora.domain.bounds` splits one operation across two calls: `over_fetch(limit)`
sizes the query and `cut(rows, limit=...)` trims it back and records whether
the bound bit. That split is forced by where the SQL lives, and it is exactly
the shape that rots — a site can call `over_fetch` and forget `cut`, which
silently returns one row too many, or call `cut` on rows fetched at plain
`limit`, which can then never observe a truncation and reports a permanent,
confident zero. The second is worse: it looks like a measurement.

So this file asserts the pairing structurally, and asserts the label vocabulary
in BOTH directions:

  - every `site=` passed to `cut` is a declared `BoundSite`  (no typo'd series)
  - every declared `BoundSite` has a real `cut` call site     (no lie)

The second is CLAUDE.md's "a declared metric with no call site is a lie",
applied one level down to a label value rather than a metric name. A label
that is declared and never emitted renders as a series that is genuinely
always zero, which reads as "this bound never bites" — the most misleading
possible answer, and indistinguishable from the truth without this test.

`BoundSite` is deliberately NOT the full inventory of bounded reads in the
repo. It names the sites actually wired, and grows as more are. That is why
the "every declared site has a call site" direction is the load-bearing one
here: it is what stops someone declaring the whole inventory up front and
shipping sixteen series that mean nothing.

WHAT FALSIFIES EACH CHECK (production code only, anchors asserted to apply and
to still parse):
  - Adding `"notes.search"` to `BoundSite` with no call site anywhere:
    `test_every_declared_bound_site_has_a_call_site` fails naming it.
  - Changing `site="audit.recent"` to `site="audit.recnt"` at its call site:
    `test_every_cut_site_is_declared` fails naming the typo.
  - Removing the `over_fetch(...)` wrapper from `nora/infra/sql/audit.py`'s
    `recent`, leaving the `cut` in place — the shape that makes truncation
    permanently unobservable: `test_a_cut_site_also_over_fetches` fails.
"""

from __future__ import annotations

import ast
from pathlib import Path

from nora.domain.bounds import BOUND_SITES

ROOT = Path(__file__).resolve().parents[2]


def _production_modules() -> list[Path]:
    return sorted(
        p for p in (ROOT / "nora").rglob("*.py") if ".venv" not in p.parts
    )


def _calls_named(tree: ast.AST, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == name)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == name)
        )
    ]


def _site_of(call: ast.Call) -> str | None:
    for kw in call.keywords:
        if kw.arg == "site" and isinstance(kw.value, ast.Constant):
            value = kw.value.value
            return value if isinstance(value, str) else None
    return None


def _enclosing_function(
    tree: ast.Module, target: ast.Call
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    current: ast.AST = target
    while current in parents:
        current = parents[current]
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
            return current
    return None


def _cut_sites() -> list[tuple[Path, ast.Module, ast.Call, str | None]]:
    found: list[tuple[Path, ast.Module, ast.Call, str | None]] = []
    for path in _production_modules():
        if path.name == "bounds.py":
            continue  # the definition, not a call site
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in _calls_named(tree, "cut"):
            if _site_of(call) is None and not call.keywords:
                continue  # some unrelated `.cut()` with no keywords
            found.append((path, tree, call, _site_of(call)))
    return found


def test_every_cut_site_is_declared() -> None:
    offenders: list[str] = []
    for path, _tree, call, site in _cut_sites():
        rel = path.relative_to(ROOT)
        if site is None:
            offenders.append(f"{rel}:{call.lineno} passes no literal site=")
        elif site not in BOUND_SITES:
            offenders.append(f'{rel}:{call.lineno} uses undeclared site "{site}"')
    assert not offenders, (
        "bounded-read cut sites whose `site=` is not a declared BoundSite "
        "(nora/domain/bounds.py) — the label would render as its own series "
        "that nothing else ever emits:\n  " + "\n  ".join(sorted(offenders))
    )


def test_every_declared_bound_site_has_a_call_site() -> None:
    emitted = {site for _p, _t, _c, site in _cut_sites() if site is not None}

    # POSITIVE CONTROL. An empty `unused` is also what a walk that found no
    # cut sites at all produces — a renamed helper, a moved package. Assert
    # the sweep actually located call sites before trusting its verdict.
    assert emitted, (
        "the sweep found no bounded-read cut sites anywhere under nora/. "
        "`cut` was renamed or moved; fix this walk before trusting a green run."
    )

    unused = BOUND_SITES - emitted
    assert not unused, (
        "BoundSite members with no `cut(site=...)` call site — each renders as "
        "a series that is genuinely always zero, which reads as «this bound "
        "never bites». Wire it or delete it:\n  " + "\n  ".join(sorted(unused))
    )


def test_a_cut_site_also_over_fetches() -> None:
    offenders: list[str] = []
    for path, tree, call, site in _cut_sites():
        fn = _enclosing_function(tree, call)
        rel = path.relative_to(ROOT)
        where = f"{rel}::{fn.name if fn else '<module>'} (site={site})"
        if fn is None:
            offenders.append(f"{where} — cut called outside any function")
            continue
        if not _calls_named(fn, "over_fetch"):
            offenders.append(where)
    assert not offenders, (
        "bounded reads that CUT without OVER-FETCHING. `cut` compares "
        "len(rows) to limit, so rows fetched at plain `limit` can never "
        "exceed it: the truncation counter is then permanently zero and reads "
        "as «this bound never bites» rather than «I never looked». Fetch "
        "`over_fetch(limit)`:\n  " + "\n  ".join(sorted(offenders))
    )
