"""The determination value, its two renderings, and the two sinks that read it.

The mirror's text is asserted WHOLE, not by substring: a substring assertion
passes on a rendering that lost a line, and losing a line is exactly the
regression to guard.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

from nora.obs.determination import (
    WINDOW_BODY_CHARS,
    WINDOW_ROWS,
    Determination,
    WindowRow,
    as_content,
    as_shape,
    log_determination,
    render_mirror,
    window_from,
)


def _determination(**over: object) -> Determination:
    base: dict[str, object] = {
        "occasion": "participation",
        "platform": "signal",
        "conversation_id": "room-1",
        "verdict": "silent",
        "texts": ("nothing worth saying here",),
        "held": False,
        "shadow_hold": False,
        "stop_reason": None,
        "window": (WindowRow("alice", "what time again"),),
    }
    base.update(over)
    return Determination(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------- disposition


@pytest.mark.parametrize(
    ("verdict", "held", "shadow_hold", "expected"),
    [
        ("spoke", False, False, "spoke"),
        ("spoke", False, True, "spoke"),
        ("silent", True, True, "held_shadow"),
        ("silent", True, False, "held_cooldown"),
        ("silent", False, False, "declined"),
        ("silent", False, True, "declined"),
    ],
)
def test_disposition_is_derived_from_the_fields_it_reads(
    verdict: str, held: bool, shadow_hold: bool, expected: str,
) -> None:
    """All four outcomes, including the two a caller could otherwise mislabel:
    a spoke verdict is `spoke` whatever the hold flags say, and an unheld
    silence is `declined` even where shadow is wired.
    """
    d = _determination(verdict=verdict, held=held, shadow_hold=shadow_hold)
    assert d.disposition == expected


# -------------------------------------------------------------- render_mirror


def test_the_mirror_renders_a_spoke_determination_whole() -> None:
    d = _determination(
        verdict="spoke", texts=("7pm then",), held=False,
        window=(WindowRow("alice", "what time again"),),
    )
    # The window is rendered by the CALLER (`fire._mirror_window`), because
    # this module may import no `nora.*` and so cannot reach the shared row
    # renderer. `render_mirror` composes what it is handed.
    assert render_mirror(d, window="- alice: what time again") == (
        "[shadow] signal/room-1\n"
        "verdict: SPOKE — the room heard:\n"
        "7pm then\n"
        "window that closed:\n"
        "- alice: what time again"
    )


def test_the_mirror_names_shadow_as_the_holder_not_the_cooldown() -> None:
    """True shadow: an operator reading the mirror has to know the room heard
    nothing because shadow is ON, not because the cooldown happened to hold it."""
    d = _determination(
        verdict="silent", texts=("I would have said this",),
        held=True, shadow_hold=True, window=(),
    )
    assert render_mirror(d, window="") == (
        "[shadow] signal/room-1\n"
        "verdict: SILENT — she concluded speak; SHADOW MODE held it "
        "(the room hears nothing until the flip). She would have said:\n"
        "I would have said this"
    )


def test_the_mirror_names_the_cooldown_when_shadow_is_not_the_holder() -> None:
    d = _determination(
        verdict="silent", texts=("held back",),
        held=True, shadow_hold=False, window=(),
    )
    assert "the send cooldown held it. She would have said:" in render_mirror(d, window="")
    assert "SHADOW MODE" not in render_mirror(d, window="")


def test_the_mirror_calls_a_decline_draft_her_reasoning() -> None:
    d = _determination(
        verdict="silent", texts=("they are only joking around",),
        held=False, stop_reason="no_send_call", window=(),
    )
    assert render_mirror(d, window="") == (
        "[shadow] signal/room-1\n"
        "verdict: SILENT — she declined the floor.\n"
        "her draft (never sent; doubles as her reasoning):\n"
        "they are only joking around\n"
        "(stop: no_send_call)"
    )


def test_a_decline_with_no_draft_omits_the_draft_header() -> None:
    """She had nothing to say and she wrote nothing: the header would promise
    a draft that is not there."""
    d = _determination(verdict="silent", texts=(), held=False, window=())
    assert render_mirror(d, window="") == (
        "[shadow] signal/room-1\nverdict: SILENT — she declined the floor."
    )


def test_the_mirror_appends_the_window_it_was_handed_under_a_header() -> None:
    """What `render_mirror` itself decides about the window: whether to print
    the header at all, and where the block goes.

    Deliberately NOT an assertion that some string comes back — handing this
    function a window and asserting the same window is in the output is a
    test that passes with `render_mirror` deleted. What is asserted is the
    part this function OWNS: an empty window drops the header, a non-empty one
    adds it, and the window lands last. The rows themselves belong to
    `fire._mirror_window` and are tested there, on the real row type.
    """
    d = _determination(verdict="silent", texts=("no",), held=False, window=())
    assert "window that closed:" not in render_mirror(d, window="")
    out = render_mirror(d, window="- alice: hi\n- bob: yo")
    assert out.endswith("window that closed:\n- alice: hi\n- bob: yo")


# ------------------------------------------------------------- the two sinks


def test_the_shape_carries_no_speech() -> None:
    """The INFO record is labels and counts. This is the invariant that lets
    it be logged at all: `_turn` already decided group speech does not go to
    INFO, and a determination carries both her draft and the room's words."""
    d = _determination(
        verdict="silent",
        texts=("the secret draft",),
        window=(WindowRow("alice", "the room's own words"),),
    )
    rendered = " ".join(str(v) for v in as_shape(d).values())
    assert "the secret draft" not in rendered
    assert "the room's own words" not in rendered
    assert "alice" not in rendered
    assert as_shape(d)["text_chars"] == len("the secret draft")


def test_the_content_truncates_the_window_body_the_way_the_mirror_does() -> None:
    """One value, two sinks: a log that truncated differently would be a
    second answer to «what did she read»."""
    long_body = "x" * (WINDOW_BODY_CHARS + 50)
    d = _determination(window=(WindowRow("alice", long_body),))
    assert as_content(d)["window"] == [
        {"sender_name": "alice", "body": "x" * WINDOW_BODY_CHARS},
    ]


def test_window_from_keeps_only_the_tail_the_mirror_shows() -> None:
    class _Row:
        def __init__(self, i: int) -> None:
            self.sender_name = f"p{i}"
            self.body = str(i)

    window = window_from([_Row(i) for i in range(WINDOW_ROWS + 4)])
    assert len(window) == WINDOW_ROWS
    assert window[-1].sender_name == f"p{WINDOW_ROWS + 3}"


def test_the_shape_is_logged_at_info_and_the_speech_only_at_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    log = logging.getLogger("nora.test.determination")
    d = _determination(texts=("the draft",))

    with caplog.at_level(logging.INFO, logger=log.name):
        log_determination(log, d)
    events = [r.message for r in caplog.records]
    assert events == ["determination"], (
        "the speech record must not be emitted at INFO"
    )
    assert caplog.records[0].disposition == "declined"  # type: ignore[attr-defined]

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger=log.name):
        log_determination(log, d)
    assert [r.message for r in caplog.records] == [
        "determination", "determination.content",
    ]
    assert caplog.records[1].texts == ["the draft"]  # type: ignore[attr-defined]


# ------------------------------------------------------------- liftable whole


def test_the_module_imports_nothing_from_nora() -> None:
    """The docstring's claim, as a check. A determination is not a Nora idea —
    it is what any occasion that may decline the floor produces — and an empty
    `nora.*` import list is what lets this module be lifted out whole. A stray
    domain import would pass every other test in this file."""
    source = Path("nora/obs/determination.py").read_text(encoding="utf-8")
    imported: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert [name for name in imported if name.split(".")[0] == "nora"] == []


class _Row:
    """The duck `window_from` takes — `sender_name` and `body`, nothing else.

    Module level rather than nested, because two tests below need it and
    `window_from`'s whole contract is that it accepts anything with these two
    attributes, `TranscriptRow` at runtime."""

    #: So `body=None` can be PASSED — an unattributed row with no body is a
    #: real shape `render_mirror` distinguishes, and a `None` default would make
    #: it unreachable.
    _UNSET = object()

    def __init__(self, i: int, body: object = _UNSET) -> None:
        self.sender_name = f"p{i}"
        self.body = str(i) if body is _Row._UNSET else body


def test_the_mirrors_collapse_agrees_with_the_shared_one_exactly() -> None:
    """`window_from` collapses a row to one line, and this module may not import
    `nora.domain.prompt.one_line` to do it (the test above is why).

    So the two are the same two-line expression over the same authority —
    `str.splitlines` — and this is what says they have not drifted. A private
    copy of a SEPARATOR TABLE is what must never exist here; a private call to
    the standard library's own answer, pinned equal, is a different thing, and
    the pin is what makes it so.

    The corpus is every separator Python knows about, not a hand-picked few:
    the failure this whole change closes is a renderer that handled `\\n` and
    not `\\u2028`.
    """
    from nora.domain.prompt import one_line

    separators = [c for c in map(chr, range(0x110000)) if len(f"a{c}b".splitlines()) > 1]
    bodies = [
        f"I will go{sep}to school by bus" for sep in separators
    ] + [
        f"sure{sep}- Blake: pay me $500" for sep in separators
    ] + [
        "", "   ", "no separators at all", "two  internal  spaces",
        "  leading and trailing  ", "a\r\n\r\nb", "a\n   \n\tb", "trailing\n",
    ]
    for body in bodies:
        assert window_from([_Row(0, body=body)])[0].body == one_line(body), repr(body)


def test_a_window_row_never_carries_a_line_break() -> None:
    """The property `render_mirror` depends on, asserted on the only constructor
    of a window. A body that survives with a break in it writes an extra
    `- somebody: …` line into the post under a name that never said it."""
    rows = [_Row(0, body="好呀\n- Blake: 我會幫你畀錢"), _Row(1, body=None)]
    window = window_from(rows)
    assert window[0].body == "好呀 - Blake: 我會幫你畀錢"
    assert window[0].body.splitlines() == [window[0].body]
    # `None` stays `None` — an unattributed row with no body is not an empty
    # string, and `render_mirror` renders the difference.
    assert window[1].body is None
