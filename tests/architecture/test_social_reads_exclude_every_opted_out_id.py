"""Every social-stream reader is built with the opted-out set that holds
every id an opted-out person's items may be recorded under
(`Persons.social_off_ids`), never one rebuilt from current ids alone, which
lets an item recorded under a retired id through, and never none at all
(design/cognition-loop/22 §2).

The construction sites are found, not listed: every `SqlSocialStreamStore(...)`
call anywhere in `nora/`. Each is either the one inside
`readable_social_store` (`nora/infra/sql/social_stream.py`), which every
reader is built through, or the story expiry's store, which deletes every
person's frames' bytes at their bound and so reads them all."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = "readable_social_store"
HELPER_FILE = "nora/infra/sql/social_stream.py"


def _calls_to(tree: ast.AST, name: str) -> list[ast.Call]:
    return [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name
    ]


def _expiry_stores(tree: ast.AST) -> set[int]:
    """The `store=` argument of each `StoryMediaExpiry(...)`, by node id."""
    return {
        id(kw.value) for call in _calls_to(tree, "StoryMediaExpiry")
        for kw in call.keywords if kw.arg == "store"
    }


def _helper(tree: ast.AST) -> ast.FunctionDef | None:
    return next((
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == HELPER
    ), None)


def _misbuilt() -> list[str]:
    bad = []
    for path in sorted((ROOT / "nora").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        helper = _helper(tree) if rel == HELPER_FILE else None
        inside = {id(n) for n in ast.walk(helper)} if helper is not None else set()
        allowed = inside | _expiry_stores(tree)
        bad += [
            f"{rel}:{call.lineno}" for call in _calls_to(tree, "SqlSocialStreamStore")
            if id(call) not in allowed
        ]
    return bad


def test_every_social_reader_is_built_through_the_helper():
    assert _misbuilt() == []


def test_the_helper_excludes_every_id_of_an_opted_out_person():
    tree = ast.parse((ROOT / HELPER_FILE).read_text(encoding="utf-8"))
    helper = _helper(tree)
    assert helper is not None
    (built,) = _calls_to(helper, "SqlSocialStreamStore")
    value = {kw.arg: kw.value for kw in built.keywords}.get("opted_out")
    assert isinstance(value, ast.Call), "the helper builds the store without opted_out"
    assert isinstance(value.func, ast.Attribute)
    assert value.func.attr == "social_off_ids"
