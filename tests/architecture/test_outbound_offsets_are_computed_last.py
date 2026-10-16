"""A range can only be right if nothing rewrites the text after it.

WHY THIS TEST EXISTS
--------------------
Two independent sources of ranges index one message: a `Mention` is
`(start, length, recipient)` and a `TextStyle` is `(start, length, style)`, both
in UTF-16 code units over the message body. Those numbers are meaningful
against exactly ONE string — the one the platform receives. Any transformation
applied to the text after the offsets are computed (trimming, reflowing,
rewriting markdown, appending a footer, normalising whitespace) shifts every
offset after the edit.

**And Signal does not reject the result.** It renders the mention chip over the
wrong span, or the bold over the wrong words, in a real group, with no error in
any log, no counter moving and nothing failing a health check. The only person
who can observe it is the one wrongly tagged. That is R-5's shape applied to a
feature rather than to a failure, and it is precisely the class of bug this
repo's "Never" list is made of — so it gets a structural brake rather than a
comment.

WHAT IT PINS, AND WHY THE PINS ARE SHAPED THIS WAY
--------------------------------------------------- The hazard is not merely tested
against but UNREACHABLE, in four layers. Three of them are properties of code shape,
which is what this file asserts; the fourth is a runtime invariant on the type
(`Styled.__post_init__`) and lives with its own tests.

1. **`Deliverer.deliver` must hand `adapter.send` an ATTRIBUTE of the composed
   result** (`outbound.text`), never a bare local — so a transformation added
   after the compose call cannot reach the wire at all. It is wrong-but-INERT,
   and inert is the safe failure: the missing transformation is visible to
   everyone in the group as raw markdown, whereas a shifted range is invisible
   to everyone except its victim. The same argument covers the audit row (A9
   stores what she sent) and every decoration keyword.
2. **`deliver` must interpret the reply exactly once**, and must hand the
   composer that whole value rather than its `.text`. `render_markdown` both
   deletes characters and produces offsets into what is left; calling it twice
   would be two models of one message, and unpacking it would strand the ranges
   in this function while the text travelled on.
3. **The guards must measure the value that gets composed.** A text
   transformation in front of a fail-closed guard defeats it
   (`"*+85298765432*a"` scans clean once the asterisks are gone and would have
   been SENT), so R-6 and R-18 read `<the rendered value>.text`, and this
   asserts they read THAT and not some other string.
4. **`OutboundComposer.compose` must not be able to see an unrendered string.**
   It takes one parameter and reads nothing else, so the draft is not in scope
   to be handed to the mention scanner by mistake. This is the pin that makes
   "the mention offsets and the style offsets index the same string" a fact
   about scope rather than about care.

Plus one ownership pin: **`Styled` is constructed in exactly one production
module.** It is the type that pairs a text with ranges over it, so a second
place that builds one is a second place that can pair them wrongly.

AST, NOT GREP. Every property here is "which expression is bound to this name",
which no textual search can answer.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SENDER = ROOT / "nora" / "agent" / "guards.py"
COMPOSER = ROOT / "nora" / "agent" / "outbound.py"
RENDERER = ROOT / "nora" / "agent" / "markdown.py"

#: The one call that turns a drafted reply into its final text plus ranges.
RENDER = "render_markdown"


def _deliver() -> ast.AsyncFunctionDef:
    tree = ast.parse(SENDER.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "deliver":
            return node
    raise AssertionError("Deliverer.deliver not found — this test's subject moved")


def _compose() -> ast.AsyncFunctionDef:
    tree = ast.parse(COMPOSER.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "compose":
            return node
    raise AssertionError("OutboundComposer.compose not found — this test's subject moved")


def _render_target(fn: ast.AsyncFunctionDef) -> str:
    """The name `deliver` binds the rendered reply to (`styled = ...`).

    Also the place the "exactly once" rule is enforced, because the two
    questions have one answer: if there were two render calls there would be no
    single name to return, and the function's whole comprehend-once claim would
    already be false.
    """
    targets = [
        node.targets[0].id
        for node in ast.walk(fn)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == RENDER
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ]
    calls = [
        node for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == RENDER
    ]
    assert len(calls) == 1, (
        f"Deliverer.deliver calls {RENDER} {len(calls)} times. It must interpret "
        "the reply ONCE: the call both deletes characters and produces offsets "
        "into what is left, so a second interpretation is a second model of one "
        "message, and the guards would then be measuring a different object from "
        "the one that reaches the wire"
    )
    assert len(targets) == 1, (
        f"Deliverer.deliver no longer binds {RENDER}(...) to a plain name — the "
        "rendered text and its ranges have to stay one value all the way to the "
        "composer, and an inlined call cannot be handed to it whole"
    )
    return targets[0]


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


def test_every_decoration_comes_from_that_same_object():
    """One object, so the text and its offsets cannot come from two places.
    Reading `mentions=` or `styles=` off a different value than `text=` is the
    same bug wearing a different shape — and `styles=` is the sharper case,
    because those ranges were computed BEFORE the composer ran and would look
    perfectly valid attached to any string."""
    fn = _deliver()
    composed = _compose_target(fn)
    call = _port_send(fn)

    for name in ("mentions", "styles", "preview"):
        value = _kwarg(call, name)
        assert isinstance(value, ast.Attribute), f"{name}= must come off the Outbound"
        assert isinstance(value.value, ast.Name) and value.value.id == composed


def test_the_audited_body_is_the_composed_text_or_an_explicit_override():
    """A9 stores what she SENT. A row holding a different string from the one
    the wire carried makes the transcript a record of a message that never
    existed — and `MentionRenderer` cannot repair one of Nora's own rows (her
    `raw` is tag bookkeeping, not an envelope), so nothing downstream would
    ever notice.

    There is ONE DELIBERATE EXCEPTION, and this test admits exactly it and
    nothing else. `send_gif` audits `"<caption> [GIF: <reaction>]"` while the
    wire carries the caption alone, because the row has to record that a GIF
    was sent — the bytes are not in the transcript and the caption by itself
    would read as a bare remark. So the body is `<composed>.text` UNLESS the
    caller passed an explicit `record_text`.

    The shape is pinned rather than loosened: both branches are checked, so a
    THIRD string cannot appear here."""
    fn = _deliver()
    composed = _compose_target(fn)
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "NormalizedMessage"
        ):
            body = _kwarg(node, "body")
            if isinstance(body, ast.IfExp):
                # `<composed>.text if record_text is None else record_text`
                assert isinstance(body.body, ast.Attribute), (
                    "the default branch must still be the composed text"
                )
                assert isinstance(body.body.value, ast.Name)
                assert body.body.value.id == composed
                assert body.body.attr == "text"
                assert isinstance(body.orelse, ast.Name), (
                    "the override branch must be a plain name the caller passed "
                    "in, never an expression built here"
                )
                assert body.orelse.id == "record_text"
                return
            assert isinstance(body, ast.Attribute), (
                "body= must be the composed text, or that text with an explicit "
                "record_text override — nothing else"
            )
            assert isinstance(body.value, ast.Name) and body.value.id == composed
            assert body.attr == "text"
            return
    raise AssertionError("Deliverer.deliver no longer builds the audit row here")


def test_nothing_reassigns_the_reply_after_the_compose_call():
    """Belt to the braces above.

    Rebinding `text` or the rendered value after `compose` would be inert as far
    as the WIRE is concerned (the tests above guarantee that), but it would
    leave two live copies of one reply in one function with only one of them
    real — which is how the inert-and-safe property silently becomes a reader's
    trap, and how the next edit "fixes" it by wiring the rebound one back into
    the send.
    """
    fn = _deliver()
    forbidden = {"text", _render_target(fn)}
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
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
            clash = targets & forbidden
            assert not clash, (
                f"{sorted(clash)} is rebound after the compose call; the composed "
                "Outbound is the only reply that reaches the wire, so this is "
                "a second, dead copy of it"
            )


# ------------------------------------------------------------------ the render

def test_the_composer_is_handed_the_rendered_value_and_not_a_bare_string():
    """The pin that keeps the style ranges attached to their text.

    PROBE: change `self._compose(styled)` to `self._compose(styled.text)` in
    `Deliverer.deliver` and this goes red. That edit reads as a tidy-up — the
    composer only needs the text, after all — and it silently strands every
    style range in `deliver` while the message travels on without them. Nothing
    errors; the group just stops seeing bold, which is the mild half. The sharp
    half is the version of that edit which then re-attaches the ranges to the
    composed text further down, where nothing checks that they still fit.
    """
    fn = _deliver()
    rendered = _render_target(fn)

    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_compose"
        ):
            assert len(node.args) == 1 and not node.keywords, (
                "_compose takes exactly the rendered reply, positionally"
            )
            arg = node.args[0]
            assert isinstance(arg, ast.Name) and arg.id == rendered, (
                "Deliverer.deliver must hand `_compose` the WHOLE rendered value "
                f"(`{rendered}`), not an attribute of it and not another local — "
                "the text and the ranges over it may not be separated"
            )
            return
    raise AssertionError("Deliverer.deliver no longer calls self._compose(...)")


def test_the_fail_closed_guards_measure_the_value_that_gets_composed():
    """R-6 and R-18 must see the bytes that LEAVE, not only the draft.

    A text transformation in front of a fail-closed guard defeats it:
    `"*+85298765432*a"` scans clean once the asterisks are gone and would be
    sent. `deliver` therefore checks both forms — and this asserts
    that the converted form it checks is the SAME object it composes, not a
    second render or a stale local. Each guard is also required to be called at
    least twice, because checking either form alone is a hole in a fail-closed
    guard and neither is a superset of the other.

    PROBE: replace `scan_reply(styled.text)` with `scan_reply(text)` and this
    goes red, while every behavioural test that feeds a plain reply stays green.
    """
    fn = _deliver()
    rendered = _render_target(fn)

    def _arguments(predicate) -> list[ast.expr]:
        return [
            node.args[0]
            for node in ast.walk(fn)
            if isinstance(node, ast.Call) and node.args and predicate(node.func)
        ]

    guards = {
        "scan_reply": _arguments(
            lambda f: isinstance(f, ast.Name) and f.id == "scan_reply"
        ),
        "LeakGuard.check": _arguments(
            lambda f: isinstance(f, ast.Attribute) and f.attr == "check"
        ),
    }

    for name, args in guards.items():
        assert len(args) >= 2, (
            f"{name} is called {len(args)} time(s) in deliver; it must measure "
            "the drafted AND the converted form — neither is a superset of the "
            "other, so checking one alone is a hole"
        )
        assert any(
            isinstance(a, ast.Attribute)
            and isinstance(a.value, ast.Name)
            and a.value.id == rendered
            and a.attr == "text"
            for a in args
        ), f"{name} never measures `{rendered}.text`, the string that leaves"


def test_compose_can_see_nothing_but_the_value_it_was_handed():
    """The scope pin: there is no draft in `compose` to get the offsets wrong with.

    `OutboundComposer.compose` takes one parameter and reads no other name, so
    the mention offsets it computes and the style offsets that arrived with the
    text are ranges over the same string BY SCOPE. That is a stronger claim than
    "the code currently passes the right variable", and it is the one this
    design rests on.

    PROBE: add a `raw: str` parameter to `compose` and pass it to `_mentions`,
    which is exactly how a string-taking signature would be reintroduced,
    and this goes red on the parameter count before it even reaches the reads.
    """
    fn = _compose()
    taken = [a for a in fn.args.args if a.arg != "self"]
    assert len(taken) == 1, (
        f"compose takes {[a.arg for a in taken]}; it must take exactly ONE value "
        "— the rendered reply — so no unrendered string is in scope to index"
    )
    assert not fn.args.kwonlyargs and fn.args.vararg is None and fn.args.kwarg is None
    annotation = taken[0].annotation
    assert isinstance(annotation, ast.Name) and annotation.id == "Styled", (
        "compose's parameter must be annotated `Styled`. A `str` here is the old "
        "signature, and it is the one under which the style ranges have no way in"
    )

    parameter = taken[0].arg
    # `fn.body`, not `fn`: the signature's own annotation mentions `Styled`, and
    # a name that only appears in a type is not a value this method can index.
    body = [node for stmt in fn.body for node in ast.walk(stmt)]
    assigned = {
        t.id
        for node in body
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Name)
    }
    allowed = {"self", parameter, "Outbound"} | assigned
    read = {
        node.id for node in body
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    assert read <= allowed, (
        f"compose reads {sorted(read - allowed)}; anything beyond its own "
        "parameter is a second string it could compute an offset against"
    )


def test_only_one_production_module_may_pair_a_text_with_its_ranges():
    """`Styled` is the type that says «these offsets belong to that string».

    Exactly one place in `nora/` may assert that, and it is the function that
    produces both halves in the same breath. A second producer is a second
    opportunity to pair a text with ranges computed against a different one —
    which is the entire failure this design exists to make unwritable, so it is
    pinned rather than left to review.

    Test code may build one: `Styled.__post_init__` refuses a range that does
    not fit, so a hand-written pairing in a test is checked at construction, and
    the alternative is tests that cannot express a bad value to assert against.
    """
    offenders = []
    for path in (ROOT / "nora").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Styled"
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")

    allowed = f"{RENDERER.relative_to(ROOT)}"
    assert offenders, "nothing constructs a Styled — this test's subject moved"
    assert all(o.startswith(allowed) for o in offenders), (
        f"Styled is constructed outside {allowed}: {offenders}. Only the "
        "function that computes the text and its ranges together may claim they "
        "belong to each other"
    )
