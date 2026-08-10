"""A mention offset can only be right if nothing rewrites the text after it.

WHY THIS TEST EXISTS
--------------------
A `Mention` is `(start, length, recipient)` over the message body in UTF-16
code units. Those numbers are meaningful against exactly ONE string — the one
the platform receives. Any transformation applied to the text after the offsets
are computed (trimming, reflowing, stripping markdown the group's clients
render as literal asterisks, appending a footer, normalising whitespace) shifts
every offset after the edit.

**And Signal does not reject the result.** It renders the mention chip over the
wrong span, in a real group, with no error in any log, no counter moving and
nothing failing a health check. The only person who can observe it is the one
wrongly tagged. That is R-5's shape applied to a feature rather than to a
failure, and it is precisely the class of bug this repo's "Never" list is made
of — so it gets a structural brake rather than a comment.

WHAT IT PINS
------------
`Deliverer.deliver` must hand `adapter.send` an ATTRIBUTE of the composed
result (`outbound.text`), never a bare local. That single fact is what makes
the hazard non-reintroducible rather than merely documented:

* a transformation added INSIDE `OutboundComposer.compose`, ahead of the offset
  arithmetic, is correct by construction — that is where one belongs;
* a transformation added AFTER the `compose` call in `deliver` cannot reach the
  wire at all, because the wire reads `outbound.text` and not the local it
  rewrote. It is wrong-but-INERT, and inert is the safe failure: the missing
  transformation is visible to everyone in the group as unstripped text,
  whereas a shifted mention is invisible to everyone except its victim.

The same argument covers the audit row, which is asserted for the same reason:
A9 stores what she sent, and a row storing a differently-transformed string
than the wire carried would make the transcript a record of a message that
never existed.

AST, NOT GREP. The property is "which expression is bound to this keyword
argument", which no textual search can answer.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SENDER = ROOT / "nora" / "agent" / "guards.py"


def _deliver() -> ast.AsyncFunctionDef:
    tree = ast.parse(SENDER.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "deliver":
            return node
    raise AssertionError("Deliverer.deliver not found — this test's subject moved")


def _kwarg(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _port_send(fn: ast.AsyncFunctionDef) -> ast.Call:
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "send"
            and {kw.arg for kw in node.keywords} >= {"target", "text"}
        ):
            return node
    raise AssertionError("Deliverer.deliver no longer calls adapter.send(target=, text=)")


def _compose_target(fn: ast.AsyncFunctionDef) -> str:
    """The name `deliver` binds the composed message to (`outbound = ...`)."""
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Await)
            and isinstance(node.value.value, ast.Call)
            and isinstance(node.value.value.func, ast.Attribute)
            and node.value.value.func.attr == "_compose"
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            return node.targets[0].id
    raise AssertionError(
        "Deliverer.deliver no longer composes an Outbound before sending — the "
        "offset-ordering guarantee this test exists for is gone, not merely moved"
    )


def test_the_text_that_goes_on_the_wire_is_the_composed_one():
    """PROBE: change `text=outbound.text` back to `text=text` in
    `Deliverer.deliver` and this goes red. That edit is exactly the one that
    reopens the hazard — it reads as a harmless simplification, and it silently
    decouples the offsets from the string they index."""
    fn = _deliver()
    composed = _compose_target(fn)
    sent = _kwarg(_port_send(fn), "text")

    assert isinstance(sent, ast.Attribute), (
        "adapter.send(text=...) must be handed an attribute of the composed "
        "Outbound, never a bare local — a local can be reassigned between the "
        "compose call and the send, which shifts every mention offset"
    )
    assert isinstance(sent.value, ast.Name) and sent.value.id == composed
    assert sent.attr == "text"


def test_the_mentions_and_the_preview_come_from_that_same_object():
    """One object, so the text and its offsets cannot come from two places.
    Reading `mentions=` off a different value than `text=` is the same bug
    wearing a different shape."""
    fn = _deliver()
    composed = _compose_target(fn)
    call = _port_send(fn)

    for name in ("mentions", "preview"):
        value = _kwarg(call, name)
        assert isinstance(value, ast.Attribute), f"{name}= must come off the Outbound"
        assert isinstance(value.value, ast.Name) and value.value.id == composed


def test_the_audited_body_is_also_the_composed_text():
    """A9 stores what she SENT. A row holding a different string from the one
    the wire carried makes the transcript a record of a message that never
    existed — and `MentionRenderer` cannot repair one of Nora's own rows (her
    `raw` is tag bookkeeping, not an envelope), so nothing downstream would
    ever notice."""
    fn = _deliver()
    composed = _compose_target(fn)
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "NormalizedMessage"
        ):
            body = _kwarg(node, "body")
            assert isinstance(body, ast.Attribute)
            assert isinstance(body.value, ast.Name) and body.value.id == composed
            assert body.attr == "text"
            return
    raise AssertionError("Deliverer.deliver no longer builds the audit row here")


def test_nothing_reassigns_the_raw_text_after_the_compose_call():
    """Belt to the braces above.

    Rebinding `text` after `compose` would be inert as far as the WIRE is
    concerned (the tests above guarantee that), but it would leave two live
    strings in one function with only one of them real — which is how the
    inert-and-safe property silently becomes a reader's trap, and how the next
    edit "fixes" it by wiring the rebound one back into the send.
    """
    fn = _deliver()
    seen_compose = False
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Await)
            and isinstance(node.value.value, ast.Call)
            and isinstance(node.value.value.func, ast.Attribute)
            and node.value.value.func.attr == "_compose"
        ):
            seen_compose = True
            continue
        if seen_compose and isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            assert "text" not in targets, (
                "`text` is rebound after the compose call; the composed "
                "Outbound is the only string that reaches the wire, so this is "
                "a second, dead copy of the reply"
            )
