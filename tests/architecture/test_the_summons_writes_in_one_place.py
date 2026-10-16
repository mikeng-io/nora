"""The public summons writes a comment from ONE place: the flush.

`InstagramSummonsClient.post_comment` is the only public write Nora has on
Instagram, and the account it writes with also
carries her group chats. So the call is pinned structurally, the way
`test_only_two_paths_can_send.py` pins `adapter.send`: exactly one call site
under `nora/`, inside `SummonsOccasion._flush`, and every call of `_flush`
hands it either the FIRST held reply (`held[0]`, straight from the staging
deliverer's `held_texts`) or the row that reply was stored in. Nothing else —
not the model's `final_text`, not a join of every held message — can reach
the wire. The behaviour half is in `tests/unit/test_summons_occasion.py`.

AST, not grep: a docstring naming `post_comment` is not a call.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NORA = ROOT / "nora"
OCCASION = NORA / "app" / "summons.py"


def _calls_named(tree: ast.AST, attr: str) -> list[ast.Call]:
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attr
    ]


def _function(tree: ast.Module, name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {OCCASION.relative_to(ROOT)}")


def _own_returns(fn: ast.AsyncFunctionDef) -> list[ast.Return]:
    """`fn`'s own `return`s, not those of a function nested in it (`_turn`'s
    `follow_up` callback returns the follow-up message, which is not what
    `_turn` hands back)."""
    found: list[ast.Return] = []
    stack: list[ast.AST] = list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Return):
            found.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return found


def test_post_comment_has_exactly_one_call_site_and_it_is_the_flush():
    sites = [
        (path.relative_to(ROOT), call)
        for path in sorted(NORA.rglob("*.py"))
        for call in _calls_named(ast.parse(path.read_text()), "post_comment")
    ]
    assert [str(p) for p, _ in sites] == ["nora/app/summons.py"], (
        f"post_comment is called from {[str(p) for p, _ in sites]}; the flush "
        "is the one place a public comment may be written"
    )
    flush = _function(ast.parse(OCCASION.read_text()), "_flush")
    assert len(_calls_named(flush, "post_comment")) == 1


def test_the_flush_is_handed_the_first_held_reply_or_its_stored_row():
    tree = ast.parse(OCCASION.read_text())
    calls = _calls_named(tree, "_flush")
    assert len(calls) == 2, "positive control: the answer path and the staged retry"
    texts = []
    for call in calls:
        text = next(k.value for k in call.keywords if k.arg == "text")
        texts.append(ast.unparse(text))
    assert sorted(texts) == ["held[0]", "row.reply_text or ''"], texts
    # And `held` IS the staging deliverer's `held_texts`, via `_turn` (whose
    # second value is how its one follow-up ended: an outcome word, never
    # her text, so the model's `final_text` still has no route to the flush).
    answer = _function(tree, "_answer")
    held = [
        n for n in ast.walk(answer)
        if isinstance(n, ast.Assign)
        and any(
            (isinstance(t, ast.Name) and t.id == "held")
            or (isinstance(t, ast.Tuple) and isinstance(t.elts[0], ast.Name)
                and t.elts[0].id == "held")
            for t in n.targets
        )
    ]
    assert len(held) == 1 and "self._turn(" in ast.unparse(held[0].value)
    turn = _function(tree, "_turn")
    returns = _own_returns(turn)
    assert [ast.unparse(r.value) for r in returns if r.value is not None] == [
        "(handle.deliverer.held_texts, follow_up_end)",
    ]
