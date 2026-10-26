"""R-1b: the tag path and the heartbeat's fire path are the only two senders.

WHY THIS TEST EXISTS
--------------------
R-1 says *"Only the tagging path may cause a send; ambient traffic never
reaches the agent"*. `test_ambient_reaches_the_agent_only_through_participation_gating`
(`tests/integration/test_ambient.py`) asserts the ambient clause, but only
over AMBIENT input: a reminders fire path never enters its harness. This test
is the brake on a third send path: there are TWO senders, both named, and
adding a third is a test failure rather than a review comment. (The
participation occasion speaks THROUGH `FirePath.speak` — it is a fifth occasion
on the second path, not a third path, and this test is the pin that keeps it
so.)

WHAT IT PINS, AND WHY THESE THREE FACTS
---------------------------------------
Sending is a three-link chain, and each link is checked because breaking any
one of them alone would open a path:

1. **`Deliverer` is the only caller of `adapter.send`.** That is the claim
   `nora/agent/guards.py` and `nora/engagement/stages/indicate.py` both already
   make in prose; here it is a fact. Every guard that stands between a model and
   the wire — R-2's latch, R-4's empty-reply check, R-6's leak scan, R-18's
   egress scan, A9's audit-first write — lives inside `Deliverer.deliver`, so a
   direct `adapter.send` elsewhere would bypass all five at once.
2. **`Deliverer` is constructed in exactly one place**, `deliver.build`, which
   is what makes (1) enforceable: a second construction site is a second set of
   arguments, and `audience` (which labels every silent-failure counter) has no
   default precisely so it cannot be forgotten there.
3. **`deliver.build` has exactly two callers** — the tag pipeline and the fire
   path. That is the rule itself. A third would be a third way for Nora to
   speak, and unprompted speech is the one capability this repo adds most
   carefully.

AST, NOT GREP. `nora/platform/signal/adapter.py` contains `self._sender.send(...)`
— the adapter's OWN implementation of the port, delegating to its transport,
which is not a *caller* of `PlatformAdapter.send` at all. A grep for `.send(`
cannot tell the two apart; matching the keyword shape `send(target=..., text=...)`
can, and that shape is the port's signature.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NORA = ROOT / "nora"

# The one module that may reach the wire: `Deliverer.deliver`.
SENDER = "nora/agent/guards.py"
# The one module that may construct a `Deliverer`.
BUILDER = "nora/engagement/stages/deliver.py"
# The paths that may cause a send. THIS IS THE RULE.
CALLERS = {
    "nora/engagement/pipeline.py",
    "nora/app/fire.py",
    # DEC-212 amends R-1b: a public summons is a third sending path,
    # delivering only through the instagram-bridge's guarded comment write.
    # Its deliverer is a STAGING one (`send_permitted=False`): every guard
    # runs, the text is held, no adapter and no audit store is ever reached,
    # and `tests/architecture/test_the_summons_writes_in_one_place.py` pins
    # the one call that publishes what it held.
    "nora/app/summons.py",
    # DEC-287 amends R-1b: a one-time code is a
    # fourth sending path. A fixed template, no model, into a direct message
    # to somebody in identity.toml only; it goes through `deliver.build` so
    # every guard and the audit-first write still stand between it and the
    # wire (`nora.app.otp`).
    "nora/app/otp.py",
}


def _sources() -> list[tuple[str, ast.Module]]:
    return [
        (str(p.relative_to(ROOT)), ast.parse(p.read_text()))
        for p in sorted(NORA.rglob("*.py"))
    ]


def _modules_where(sources, predicate) -> set[str]:
    return {
        name
        for name, tree in sources
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and predicate(node)
    }


def _is_port_send(node: ast.Call) -> bool:
    """`something.send(target=..., text=...)` — the `PlatformAdapter.send`
    signature, which the adapter's own `self._sender.send(target, text)`
    delegation (positional, a different port) does not match."""
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "send":
        return False
    return {kw.arg for kw in node.keywords} >= {"target", "text"}


def _is_deliverer_construction(node: ast.Call) -> bool:
    return isinstance(node.func, ast.Name) and node.func.id == "Deliverer"


def _is_deliver_build(node: ast.Call) -> bool:
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "build"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "deliver"
    )


def test_only_the_tag_path_and_the_fire_path_can_send():
    """R-1b, all three links in one assertion set.

    If this fails on a module you just added, the question is not "how do I
    make the test pass" — it is whether Nora should be able to speak from
    there at all, and if she should, the decision index's R-1 row and this
    file's `CALLERS` are the two places that have to say so.
    """
    sources = _sources()
    assert _modules_where(sources, _is_port_send) == {SENDER}, (
        "something other than Deliverer.deliver reaches adapter.send — that "
        "bypasses R-2's latch, R-4, R-6's leak scan, R-18's egress scan and "
        "A9's audit-first write, all five at once"
    )
    assert _modules_where(sources, _is_deliverer_construction) == {BUILDER}, (
        "a second Deliverer construction site — `audience` has no default "
        "there precisely so a new one cannot silently report its failures as "
        "the tag path's"
    )
    assert _modules_where(sources, _is_deliver_build) == CALLERS, (
        "the tag path, the fire path, the public summons and the one-time code "
        "are the only senders; another caller of deliver.build is another way "
        "for Nora to speak unprompted"
    )


@pytest.mark.parametrize(("source", "predicate"), [
    ("adapter.send(target=room, text=reply)", _is_port_send),
    ("Deliverer(audit=store)", _is_deliverer_construction),
    ("deliver.build(audit=store)", _is_deliver_build),
])
def test_each_link_detects_an_unapproved_module(source, predicate):
    sources = [
        ("unapproved.py", ast.parse(source)),
        ("transport.py", ast.parse("self._sender.send(target, text)")),
    ]
    assert _modules_where(sources, predicate) == {"unapproved.py"}
