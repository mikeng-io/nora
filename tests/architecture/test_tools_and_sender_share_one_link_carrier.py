"""DEC-89: a sending path that builds tools must hand them the carrier it kept.

WHY THIS TEST EXISTS
--------------------
The link carry has two halves in two different modules, and either half alone
is silent. `place_info` offering a url into a `LinkCarry` that no `Deliverer`
ever reads collects perfectly and appends nothing; a `Deliverer` holding an
empty carrier sends exactly the message it sent before the feature existed.
Neither raises, neither logs, neither moves a counter — the whole feature is
present, wired, and inert.

That is not a hypothetical failure mode in this repo, it is the recorded one.
DEC-42 found multi-message replies structurally dead for a release because a
number nobody had edited was doing the refusing, and `deliver.build`'s
`composer` parameter has no default *for this exact reason*: the seam both
senders pass through is where a decision gets forced. `links` cannot use the
same trick — it is minted inside `build`, not passed to it — so the forcing
happens here instead.

WHAT IT PINS
------------
For every module that calls `deliver.build` (there are exactly two, and
`test_only_two_paths_can_send.py` is what keeps that true), every
`ToolRegistry.build(...)` call in that module passes `links=`. The pairing is
what matters: a module that sends AND builds tools has both halves in scope, so
there is no honest reason for it to wire only one.

AST, not grep, for the reason the sibling test gives — `build` is an extremely
common method name and only the keyword shape identifies these two calls.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NORA = ROOT / "nora"

# Both halves of the pairing, identified by keyword SHAPE.
#
# `deliverer=` alone is not enough and the first cut of this test was wrong for
# assuming it was: `ReplyGuard(...)` and `deliver.check_outcome(...)` both take
# a `deliverer=` too, and both were reported as offenders that cannot possibly
# want a carrier. `room=` is what separates a tools build from those two —
# `ToolRegistry.build`/`.specs` are the only calls in this codebase taking both.
# `audience=` is `deliver.build`'s alone, and has no default precisely so it is
# always present at a call site.
_TOOLS_BUILD_MARKERS = frozenset({"deliverer", "room"})
_DELIVER_BUILD_MARKER = "audience"


def _calls(tree: ast.Module) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]


def _keywords(node: ast.Call) -> set[str]:
    return {kw.arg for kw in node.keywords if kw.arg is not None}


def test_every_sending_path_hands_its_tools_the_carrier_it_kept():
    offenders = []
    for path in sorted(NORA.rglob("*.py")):
        tree = ast.parse(path.read_text())
        calls = _calls(tree)
        sends = [c for c in calls if _DELIVER_BUILD_MARKER in _keywords(c)]
        if not sends:
            continue
        for call in calls:
            keywords = _keywords(call)
            if keywords >= _TOOLS_BUILD_MARKERS and "links" not in keywords:
                offenders.append(f"{path.relative_to(ROOT)}:{call.lineno}")
    assert not offenders, (
        "these modules send AND build tools but wired the tools to a link "
        f"carrier the sender never reads, so DEC-89 is inert there: {offenders}. "
        "Pass links=handle.links from the DeliveryHandle you already have."
    )


def test_the_pairing_would_actually_be_detected():
    """The instrument can measure the thing (CLAUDE.md's null-result rule).

    A test that scans for a mistake nobody has made returns an empty list
    whether it works or not. This states what a POSITIVE looks like: the same
    predicate over a module shaped like a sending path with the keyword removed
    must produce an offender — AND must leave the two `deliverer=`-taking calls
    that are not tool builds alone, which is what the first cut got wrong.
    """
    tree = ast.parse(
        "handle = deliver.build(store=s, audience='tag', composer=None)\n"
        "tools = registry.build(store=s, deliverer=handle.deliverer, room=r)\n"
        "guard = ReplyGuard(budgets=b, deliverer=handle.deliverer)\n"
        "ok = deliver.check_outcome(deliverer=handle.deliverer, tag_id=t)\n"
    )
    calls = _calls(tree)
    assert [c for c in calls if _DELIVER_BUILD_MARKER in _keywords(c)]
    bad = [c for c in calls
           if _keywords(c) >= _TOOLS_BUILD_MARKERS and "links" not in _keywords(c)]
    assert len(bad) == 1
