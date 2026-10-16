"""Every social-stream reader is built with the opted-out set that holds
every id an opted-out person's items may be recorded under
(`Persons.social_off_ids`), never one rebuilt from current ids alone, which
lets an item recorded under a retired id through (design/cognition-loop/22
§2).

The construction sites are found, not listed: every `SqlSocialStreamStore(...)`
call in the composition roots that passes `opted_out=`, one of them inside
`readable_social_store`, which builds the poller's, `social_read`'s and
Character's store."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROOTS = ("nora/app/composition.py", "nora/analytics/composition.py")


def _sites() -> list[tuple[str, int, ast.expr]]:
    found = []
    for rel in ROOTS:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "SqlSocialStreamStore"):
                for kw in node.keywords:
                    if kw.arg == "opted_out":
                        found.append((rel, node.lineno, kw.value))
    return found


def _helper_calls() -> int:
    tree = ast.parse((ROOT / ROOTS[0]).read_text(encoding="utf-8"))
    return sum(
        1 for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "readable_social_store"
    )


def test_every_social_reader_excludes_every_id_of_an_opted_out_person():
    sites = _sites()
    # The reader helper's own construction and the Rollup's; the helper is
    # called for the poller's, `social_read`'s and Character's store.
    assert len(sites) >= 2, sites
    assert _helper_calls() >= 3
    for rel, line, value in sites:
        ok = (
            isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
            and value.func.attr == "social_off_ids"
        )
        assert ok, f"{rel}:{line} builds `opted_out` without `social_off_ids()`"
