"""R-100's production wiring: every room-facing `deliver.build` call passes `own_links=`.

`Deliverer` refuses a reply that links to a document that does not exist, but only when it is
handed the check, and the check comes from the `ToolRegistry` at each call site. A unit test that
builds a `Deliverer` by hand cannot see a call site that stopped passing it (review of #698:
deleting either line left the suite green).

This reads the two modules that call `deliver.build` and requires the argument on every call whose
replies reach a room: the ones with a real `composer`. A call with `composer=None` is
operator-facing plain text (the shadow mirror, the gate verdict) and is exempt, because the text it
relays already passed R-100 on its way to the room it mirrors.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CALLERS = ("nora/engagement/pipeline.py", "nora/app/fire.py")


def _build_calls(path: str) -> list[ast.Call]:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute) and node.func.attr == "build"
        and isinstance(node.func.value, ast.Name) and node.func.value.id == "deliver"
    ]


def _keywords(call: ast.Call) -> dict[str, ast.expr]:
    return {kw.arg: kw.value for kw in call.keywords if kw.arg}


def test_every_room_facing_deliver_build_call_is_given_the_document_link_check() -> None:
    room_facing = 0
    for path in CALLERS:
        for call in _build_calls(path):
            kws = _keywords(call)
            composer = kws.get("composer")
            plain = isinstance(composer, ast.Constant) and composer.value is None
            if plain:
                continue
            room_facing += 1
            assert "own_links" in kws, (
                f"{path}:{call.lineno}: a deliver.build call that reaches a room is not given "
                "own_links=registry.own_link_check(), so R-100 is off on that path"
            )
    assert room_facing >= 2, "the tag path and the participation path must both be found"
