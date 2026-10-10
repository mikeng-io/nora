"""Every path that hands a message's text to a MODEL renders a shared file link
as its handle, `[Google Doc link c41d09]`, never as the URL
(design/external-files/00-design.md §1, R-18).

The stored body keeps the URL (Postgres is the record), so the replacement is
a render-time obligation, and `test_media_join_reaches_every_transcript`'s
history says how that goes: shipped to the one builder somebody had open. So
this file has the same two halves:

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


RENDERERS = {
    "blocks.render_elements": _render_elements,
    "blocks.render_messages": _render_messages,
    "blocks.render_row_window": _render_row_window,
    "blocks.render_rows": _render_rows,
    "reaction.render_recent": _reaction,
    "tools._transcript_messages": _tool_result,
    "memory_backfill._items": _memory_backfill,
}


@pytest.mark.parametrize("name", sorted(RENDERERS))
def test_each_renderer_shows_the_handle_and_never_the_file_id(name):
    out = RENDERERS[name]()
    assert ID not in out, f"{name} hands a model the raw file link: {out}"
    assert "Google Sheet link " in out, f"{name} lost the link entirely: {out}"


#: Hand-built prompt text and tool results, by (module, function): each must
#: call `render_file_links` (or the tools' `_file_handles` wrapper) itself,
#: because it builds text without `render_elements`.
HAND_BUILT = {
    ("nora/agent/prefill.py", "build_tag_message"): "<tag_message>, the message she answers",
    ("nora/agent/prefill.py", "_replying_to_block"): "<replying_to>, the quoted parent",
    ("nora/subject/context_filter.py", "router_input"): "the eval router's <tag_message>",
    ("nora/agent/reaction.py", "render_recent"): "the GIF picker's recent lines",
    ("nora/agent/tools.py", "_transcript_messages"):
        "load_transcript / room_read / record_query results",
    ("nora/agent/tools.py", "who_said"): "who_said hits",
    ("nora/ingest/stages/memory_feed.py", "run"): "the live Honcho feed",
    ("nora/app/adoption.py", "backfill"): "adoption's Honcho back-feed",
    ("nora/app/memory_backfill.py", "_items"): "`nora memory backfill`",
    ("nora/app/fire.py", "_window_query"): "the fire path's recall search key",
    ("nora/domain/blocks.py", "render_rows"): "every line renderer",
    ("nora/domain/blocks.py", "render_elements"): "every element renderer",
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
#: it calls `render_file_links` itself (checked).
BUILDERS = {
    "nora/agent/tools.py": "direct",
    "nora/app/adoption.py": "direct",
    "nora/app/memory_backfill.py": "direct",
    "nora/app/fire.py": "direct",
    "nora/engagement/context.py": "direct",
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
        "say whether they render through blocks.py or call render_file_links"
    )
    assert not set(BUILDERS) - discovered, "BUILDERS lists a module that reads no rows"


@pytest.mark.parametrize("rel", sorted(r for r, how in BUILDERS.items() if how == "direct"))
def test_a_direct_builder_calls_the_render(rel):
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    assert _calls_render(tree), f"{rel} is classified direct but never renders a file link"


#: Callers of `MemoryStore.feed` that feed something other than a member's
#: message text, each with the reason.
_FEED_EXEMPT = {
    "nora/engagement/stages/feedback.py": "feeds Nora's OWN sent replies, never a member's text",
    "nora/ingest/comprehension.py": "feeds a media comprehension, not message text",
}


def _feeders() -> set[str]:
    out = set()
    for path in sorted((ROOT / "nora").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "feed" and any(k.arg == "text" for k in n.keywords)
            for n in ast.walk(tree)
        ):
            out.add(path.relative_to(ROOT).as_posix())
    return out


def test_every_honcho_feed_renders_the_handle_or_says_why_not():
    """Honcho's deriver is a model and recall hands its text back to her, so a
    fed URL is a URL she reads later."""
    feeders = _feeders()
    assert "nora/ingest/stages/memory_feed.py" in feeders, "the discovery finds nothing"
    for rel in sorted(feeders - set(_FEED_EXEMPT)):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        assert _calls_render(tree), f"{rel} feeds Honcho message text without the render"
    assert not set(_FEED_EXEMPT) - feeders, "a _FEED_EXEMPT module no longer feeds"


def test_no_body_is_passed_raw_as_a_model_question_or_query():
    """`question=` reaches Honcho's dialectic and the computer objective;
    `search_query=` is recall's key. A `.body` passed to either must go through
    the render."""
    offenders = []
    for path in sorted((ROOT / "nora").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg not in ("question", "search_query"):
                    continue
                mentions_body = any(
                    isinstance(a, ast.Attribute) and a.attr == "body"
                    for a in ast.walk(kw.value)
                )
                if mentions_body and not _calls_render(kw.value):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} {kw.arg}=")
    assert not offenders, offenders
