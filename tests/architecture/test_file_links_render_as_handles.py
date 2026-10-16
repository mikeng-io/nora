"""Every path that hands a message's text to a MODEL renders a shared file link
as its handle, `[Google Doc link c41d09]`, never as the URL (R-18).

The stored body keeps the URL (Postgres is the record), so the replacement is
a render-time obligation, and `test_media_join_reaches_every_transcript` shows
the failure mode: a change that reaches only the one builder somebody had open.
So this file has the same two halves:

* **BEHAVIOUR**, parametrized over the renderers: each one, handed a row whose
  body holds a Drive URL, must produce the handle and no file id.
* **DISCOVERY**, so a NEW sink cannot be forgotten: every module that reads
  transcript rows, feeds Honcho, or passes a body as a model question/query
  is found from the source and must be classified, and a classified module
  must actually call `render_file_links`.

`render_elements`/`render_rows` in `nora/domain/blocks.py` carry the render
for every transcript built through them (`test_one_transcript_renderer` keeps
every transcript on them), which is why most builders below say «blocks».
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nora.domain.blocks import (
    RowStyle,
    render_elements,
    render_messages,
    render_row_window,
    render_rows,
)
from nora.domain.models import DeliveryStatus, RowEdit, TranscriptRow
from tests.architecture.test_media_join_reaches_every_transcript import (
    _NOT_A_BUILDER,
    _UNJOINED_BY_OWN_SQL,
    _discovered_readers,
)

ROOT = Path(__file__).resolve().parents[2]
ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
URL = f"https://docs.google.com/spreadsheets/d/{ID}/edit#gid=0"
WAS_URL = f"https://drive.google.com/file/d/{ID}/view"


def _row() -> TranscriptRow:
    return TranscriptRow(
        id=7, sender_id="+852", sender_name="A", ts=1_000, body=f"budget {URL} pls",
        is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
        edit=RowEdit(ts=2_000, was=f"old {WAS_URL}"),
    )


def _render_elements() -> str:
    return render_elements(
        [_row()], tag="m", attrs=lambda r, i: (("n", str(i)), ("was", r.edit.was)),
        body=lambda r: r.body,
    )


def _render_messages() -> str:
    return render_messages(
        [_row()], speaker=lambda r: "A", body=lambda r: r.body,
        timestamp=lambda r: r.ts, edited=lambda r: r.edit.ts, was=lambda r: r.edit.was,
        now=datetime(2026, 10, 10, tzinfo=UTC),
    )


def _render_row_window() -> str:
    return render_row_window(
        [_row()], row_id=lambda r: r.id, attrs=lambda r: (("was", r.edit.was),),
        body=lambda r: r.body,
    )


def _render_rows() -> str:
    return render_rows(
        [_row()], style=RowStyle(), speaker=lambda r: "A", body=lambda r: r.body,
    )


def _reaction() -> str:
    from nora.agent.reaction import render_recent

    return render_recent([_row()])


def _tool_result() -> str:
    from nora.agent.tools import _transcript_messages

    return repr(_transcript_messages([_row()], now=datetime(2026, 10, 10, tzinfo=UTC)))


def _memory_backfill() -> str:
    from nora.app.memory_backfill import _items

    return repr(_items(_row()))


def _memory_hits() -> str:
    """search_memory / search_memory_workspace: Honcho's stored words, which
    hold raw links for anything fed before links were rendered as handles."""
    from nora.agent.tools import _memory_hits_result

    return repr(_memory_hits_result(
        hits=[("p1", f"budget {URL} pls")], roster={"p1": "A"},
        record=lambda *_a, **_k: None, partial_view=None,
    ))


def _bullets() -> str:
    """Honcho conclusions and cards (`<peer_card>`, `<people_in_window>`)."""
    from nora.domain.blocks import render_bullets

    return render_bullets([f"shared the budget {URL}"])


def _record_item(text: str):
    from nora.domain.cognition_ground import RecordItem, Room

    return RecordItem(
        ref="messages:1", kind="message", message_id=1, room=Room("signal", "g1"),
        author="A", at_ms=1_000, text=text,
    )


def _cognition_message() -> str:
    from nora.language.cognition_render import _message_row

    return _message_row(
        _record_item(f"budget {URL}"), (), now_ms=2_000, tz=UTC, message_chars=400,
    ).show(None)


def _cognition_own() -> str:
    from nora.language.cognition_render import _own_row

    return _own_row(
        _record_item(f"budget {URL}"), now_ms=2_000, tz=UTC, message_chars=400,
    ).show(None)


RENDERERS = {
    "blocks.render_elements": _render_elements,
    "blocks.render_messages": _render_messages,
    "blocks.render_row_window": _render_row_window,
    "blocks.render_rows": _render_rows,
    "blocks.render_bullets": _bullets,
    "reaction.render_recent": _reaction,
    "tools._transcript_messages": _tool_result,
    "tools._memory_hits_result": _memory_hits,
    "memory_backfill._items": _memory_backfill,
    "cognition_render._message_row": _cognition_message,
    "cognition_render._own_row": _cognition_own,
}


@pytest.mark.parametrize("name", sorted(RENDERERS))
def test_each_renderer_shows_the_handle_and_never_the_file_id(name):
    out = RENDERERS[name]()
    assert ID not in out, f"{name} hands a model the raw file link: {out}"
    assert "Google Sheet link " in out, f"{name} lost the link entirely: {out}"


def _leaks(out: str) -> bool:
    return "google.com" in out.lower() or "https" in out or ID[:4] in out


@pytest.mark.parametrize("own", [False, True])
def test_a_cut_never_splits_a_link_into_a_raw_stub(own):
    """The Cognition agenda swaps the link BEFORE cutting a message to its
    allowance; cutting first would leave half a URL and a prefix of the file id
    for the model, and a stub long enough to parse would carry a wrong code.
    Swap first, then cut, at every length and through `_shrunk`'s search."""
    from nora.language.cognition_render import _message_row, _own_row, _shrunk

    rec = _record_item("y" * 60 + " budget " + URL + " ok")
    for chars in range(1, 160):
        row = (
            _own_row(rec, now_ms=2_000, tz=UTC, message_chars=chars) if own
            else _message_row(rec, (), now_ms=2_000, tz=UTC, message_chars=chars)
        )
        out = row.show(None)
        assert not _leaks(out), (chars, out)
        for room in (60, 120, 180, 240):
            assert not _leaks(_shrunk(row, room)), (chars, room)


def test_the_operator_review_channel_keeps_the_link_clickable():
    """`markup=False` is the operator's Discord review channel, not a prompt:
    the link stays as posted there, and only there."""
    out = render_rows(
        [_row()], style=RowStyle(), speaker=lambda r: "A", body=lambda r: r.body,
        markup=False,
    )
    assert URL in out


#: Hand-built prompt text and tool results, by (module, function): each must
#: call `render_file_links` (or the tools' `_file_handles` wrapper) INSIDE
#: that function, because it builds text without `render_elements`.
HAND_BUILT = {
    ("nora/agent/prefill.py", "build_tag_message"): "<tag_message>, the message she answers",
    ("nora/agent/prefill.py", "_replying_to_block"): "<replying_to>, the quoted parent",
    ("nora/subject/context_filter.py", "router_input"): "the eval router's <tag_message>",
    ("nora/agent/reaction.py", "render_recent"): "the GIF picker's recent lines",
    ("nora/agent/tools.py", "_transcript_messages"):
        "load_transcript / room_read / record_query results",
    ("nora/agent/tools.py", "who_said"): "who_said hits",
    ("nora/agent/tools.py", "_memory_hits_result"): "search_memory(_workspace) hits",
    ("nora/agent/tools.py", "about"): "about's Honcho conclusions and card",
    ("nora/agent/tools.py", "ask_memory"): "ask_memory's Honcho answer",
    ("nora/ingest/stages/memory_feed.py", "run"): "the live Honcho feed",
    ("nora/app/adoption.py", "backfill"): "adoption's Honcho back-feed",
    ("nora/app/memory_backfill.py", "_items"): "`nora memory backfill`",
    ("nora/app/fire.py", "_window_query"): "the fire path's recall search key",
    ("nora/engagement/context.py", "_recall_tagger"): "the tagger recall search key",
    ("nora/language/cognition_render.py", "_words"):
        "the Cognition agenda's message and own-send words (record hits, own_sends)",
    ("nora/domain/blocks.py", "render_rows"): "every line renderer",
    ("nora/domain/blocks.py", "render_elements"): "every element renderer",
    ("nora/domain/blocks.py", "render_bullets"): "every Honcho conclusion and card",
    ("evals/rebuilt/fold_evidence.py", "render_evidence"): "the fold judge's evidence",
}
_RENDER_CALLS = {"render_file_links", "_file_handles"}


def _functions(rel: str) -> dict[str, ast.AST]:
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    return {
        n.name: n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _calls_render(node: ast.AST) -> bool:
    return any(
        isinstance(c, ast.Call) and (
            (isinstance(c.func, ast.Name) and c.func.id in _RENDER_CALLS)
            or (isinstance(c.func, ast.Attribute) and c.func.attr in _RENDER_CALLS)
        )
        for c in ast.walk(node)
    )


@pytest.mark.parametrize(("rel", "fn"), sorted(HAND_BUILT))
def test_each_hand_built_sink_renders_the_handle(rel, fn):
    functions = _functions(rel)
    assert fn in functions, f"{rel}::{fn} is gone: re-point HAND_BUILT at its new name"
    assert _calls_render(functions[fn]), (
        f"{rel}::{fn} ({HAND_BUILT[(rel, fn)]}) hands message text to a model "
        "without `render_file_links`: a shared file link reaches it as a raw URL"
    )


#: Every transcript builder the media-join gate discovers, classified: either
#: it renders through `nora/domain/blocks.py` (which carries the render), or
#: the named functions in it render (checked in `HAND_BUILT`).
BUILDERS: dict[str, str | tuple[str, ...]] = {
    "nora/agent/tools.py": ("_transcript_messages", "who_said"),
    "nora/app/adoption.py": ("backfill",),
    "nora/app/memory_backfill.py": ("_items",),
    "nora/app/fire.py": ("_window_query",),
    "nora/engagement/context.py": ("_recall_tagger",),
    "nora/app/runtime.py": "blocks: the Dreamer's rows reach its adaptors' render_row_window",
    "nora/engagement/stages/debrief.py": "blocks: debrief_render.render_elements",
    "nora/ingest/relevance_gate.py": "blocks: render_messages",
    "nora/subject/dreaming.py": "blocks: the same adaptors as runtime.py",
    "nora/subject/sandbox.py": "blocks: prefill.render_transcript (render_messages)",
    "nora/infra/sql/character.py": "blocks: character.py's render_row_window",
    "nora/infra/sql/snapshot.py": "blocks: the snapshot's rows reach the eval subject's renderers",
}


def test_every_transcript_builder_is_classified():
    discovered = (set(_discovered_readers()) - set(_NOT_A_BUILDER)) | set(_UNJOINED_BY_OWN_SQL)
    unclassified = discovered - set(BUILDERS)
    assert not unclassified, (
        f"{sorted(unclassified)} read transcript rows and are not classified here: "
        "say whether they render through blocks.py or which functions render"
    )
    assert not set(BUILDERS) - discovered, "BUILDERS lists a module that reads no rows"
    for rel, how in BUILDERS.items():
        if isinstance(how, tuple):
            missing = [fn for fn in how if (rel, fn) not in HAND_BUILT]
            assert not missing, f"{rel}: {missing} are not checked in HAND_BUILT"


#: Callers of `MemoryStore.feed` with the function that must render, or the
#: reason the text is not a member's message.
FEEDERS: dict[str, str] = {
    "nora/ingest/stages/memory_feed.py": "run",
    "nora/app/adoption.py": "backfill",
    "nora/app/memory_backfill.py": "_items",
}
_FEED_EXEMPT = {
    "nora/engagement/stages/feedback.py": "feeds Nora's OWN sent replies, never a member's text",
    "nora/ingest/comprehension.py": "feeds a media comprehension, not message text",
}


def _sources() -> list[Path]:
    return sorted((ROOT / "nora").rglob("*.py")) + sorted((ROOT / "evals").rglob("*.py"))


def _feeders() -> set[str]:
    out = set()
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "feed" and any(k.arg == "text" for k in n.keywords)
            for n in ast.walk(tree)
        ):
            out.add(path.relative_to(ROOT).as_posix())
    return out


def test_every_honcho_feed_is_classified_and_its_function_renders():
    """Honcho's deriver is a model and recall hands its text back to her, so a
    fed URL is a URL she reads later."""
    feeders = _feeders()
    assert "nora/ingest/stages/memory_feed.py" in feeders, "the discovery finds nothing"
    assert feeders == set(FEEDERS) | set(_FEED_EXEMPT), (
        f"unclassified feeders: {sorted(feeders - set(FEEDERS) - set(_FEED_EXEMPT))}; "
        f"stale: {sorted((set(FEEDERS) | set(_FEED_EXEMPT)) - feeders)}"
    )
    for rel, fn in FEEDERS.items():
        assert (rel, fn) in HAND_BUILT, f"{rel}::{fn} feeds Honcho and is not checked"


#: Attributes that carry a member's message text.
_MESSAGE_TEXT = {"body", "was", "first_body"}
#: Keyword arguments that hand text to a model or to a model-backed store.
_MODEL_INPUTS = {
    "question", "search_query", "query", "objective", "prompt", "message", "text",
    "content", "input",
}


def _mentions_message_text(node: ast.AST) -> bool:
    return any(isinstance(a, ast.Attribute) and a.attr in _MESSAGE_TEXT for a in ast.walk(node))


#: (module, function, keyword) sites that pass a message's text into a DATA
#: carrier, not a model, each with where the text is rendered later.
_INPUT_EXEMPT = {
    ("nora/app/cognition_grounding.py", "_own_sends", "text"):
        "a RecordItem; cognition_render._words renders it",
    ("nora/infra/sql/character.py", "episodes", "text"):
        "an Episode; character and lessons render it through render_row_window",
    ("nora/ingest/stages/edit_gate.py", "run", "message"):
        "a NormalizedMessage for the trigger gate, which reads no model",
    ("nora/subject/knowledge.py", "know", "message"):
        "a NormalizedMessage the eval seeds into the record",
    ("nora/infra/sql/memory_index.py", "_project_note", "text"):
        "an index node's searchable text (60 §2), stored, never a model input",
    ("nora/infra/sql/memory_index.py", "_project_memo", "text"):
        "an index node's searchable text (60 §2), stored, never a model input",
    ("nora/infra/sql/memory_index.py", "_project_lesson", "text"):
        "an index node's searchable text (60 §2), stored, never a model input",
    ("nora/infra/sql/memory_index.py", "_project_event", "text"):
        "an index node's searchable text (60 §2), stored, never a model input",
    ("nora/infra/sql/memory_index.py", "_project_character", "text"):
        "an index node's searchable text (60 §2), stored, never a model input",
}


def _enclosed_calls(tree: ast.AST):
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    yield fn.name, node


def test_no_message_text_is_passed_raw_as_a_model_input():
    """A member's text passed as `question=` (Honcho's dialectic, the computer
    objective), `search_query=`/`query=` (recall's key) or any other model
    input must go through the render. `nora/` and `evals/`."""
    offenders = set()
    for path in _sources():
        rel = path.relative_to(ROOT).as_posix()
        for fn, node in _enclosed_calls(ast.parse(path.read_text(encoding="utf-8"))):
            for kw in node.keywords:
                if (
                    kw.arg in _MODEL_INPUTS and _mentions_message_text(kw.value)
                    and not _calls_render(kw.value)
                    and (rel, fn, kw.arg) not in _INPUT_EXEMPT
                ):
                    offenders.add(f"{rel}:{node.lineno} {fn}() {kw.arg}=")
    assert not offenders, sorted(offenders)


#: Functions that interpolate a `.body` into an f-string and are NOT handing a
#: member's message to a model, each with the reason.
_FSTRING_EXEMPT = {
    ("nora/app/lessons_cli.py", "render_chain"): "a lesson revision, on the operator CLI",
    ("nora/app/lessons_cli.py", "_line"): "a lesson, on the operator CLI",
    ("nora/domain/blocks.py", "render"): "ContextBlock's own body, already rendered",
    ("nora/domain/notes.py", "__post_init__"): "a length in an error message",
    ("nora/domain/standing.py", "__post_init__"): "a length in an error message",
    ("nora/testing/standing_store.py", "write"): "a length in an error message",
    ("evals/rebuilt/fold_evidence.py", "render_evidence"):
        "ONE f-string here is her prior Standing (`prior.body`, model-written); "
        "the room's rows are rendered in their own f-string, checked by HAND_BUILT",
}


def test_every_message_text_in_an_fstring_is_rendered_or_exempt():
    """The hand-built shape to catch, as found in `evals/`: a transcript line
    built as `f"{who}: {row.body}"`. Every function doing that, in `nora/`
    and `evals/`, renders the handle in that f-string or says why not."""
    offenders = []
    for path in _sources():
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if (rel, fn.name) in _FSTRING_EXEMPT:
                continue
            for js in ast.walk(fn):
                if (
                    isinstance(js, ast.JoinedStr) and _mentions_message_text(js)
                    and not _calls_render(js)
                ):
                    offenders.append(f"{rel}:{js.lineno} in {fn.name}()")
    assert not offenders, offenders
