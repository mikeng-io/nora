"""Every transcript builder joins the media, not just the tag assembler.

**This file exists because the first cut of issue #116 shipped without it.**
`MediaJoin` was a private method on `TranscriptInjection`, so the tag path saw
what a photo showed and the other five builders — the fire path, the tag-path
debrief, the standing event fold, the note tier's two adaptors, lazy adoption —
rendered bare handles. On master the sticker description and the voice-note
transcript were baked into `messages.body`, which meant every one of them had
it; the change was a regression on both flag settings, and it left the note
tier blind to exactly the content the issue was opened to capture.

Every test above the join was written through ONE consumer, so nothing went
red. That is the defect this file is the brake on.

**A STATIC read of the source, deliberately.** The alternative is six
integration tests, one per builder, each needing that builder's whole
composition — and the thing being asserted is not what one builder does with a
particular row, it is that no builder was FORGOTTEN. A grep-shaped question
gets a grep-shaped gate: `nora.domain.media_join` is imported, and the join is
awaited, in every module that reads transcript rows for a model.

`tests/unit/test_media_join.py` is the other half — what the join DOES.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: Every module that reads transcript rows and renders them for a model, with
#: the read that makes it one. A new entry here is how a SEVENTH transcript
#: builder joins the set; deleting one is how a builder stops being one, and
#: both are edits somebody has to make on purpose.
#:
#: `nora/engagement/context.py` is the tag path; `nora/app/fire.py` the fire
#: path (reminders, announcements, heartbeat, participation);
#: `nora/engagement/stages/debrief.py` the tag-path standing fold;
#: `nora/ingest/stages/standing_event.py` the event-driven fold;
#: `nora/app/runtime.py` the Dreamer's batch, which covers BOTH scope-keyed
#: adaptors (the note candidates pass renders it and holds no store of its
#: own); `nora/language/note_curator.py` its own evidence read; and
#: `nora/app/adoption.py` the Honcho back-feed.
BUILDERS = {
    "nora/engagement/context.py": "recent",
    "nora/app/fire.py": "recent",
    "nora/engagement/stages/debrief.py": "since_including_nora",
    "nora/ingest/stages/standing_event.py": "since_including_nora",
    "nora/app/runtime.py": "since",
    "nora/language/note_curator.py": "recent",
    "nora/app/adoption.py": "unattributed",
}


def _source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", sorted(BUILDERS))
def test_every_transcript_builder_awaits_the_shared_join(rel: str) -> None:
    """`await <something>.enrich(...)` appears in the module, and the name it
    is called on is the shared `MediaJoin` rather than a local reimplementation
    — which the import assertion is what establishes."""
    tree = ast.parse(_source(rel))

    imports_join = any(
        isinstance(node, ast.ImportFrom)
        and (node.module or "").endswith("domain.media_join")
        and any(alias.name == "MediaJoin" for alias in node.names)
        for node in ast.walk(tree)
    )
    assert imports_join, (
        f"{rel} builds a transcript for a model but does not import the "
        f"shared MediaJoin. Either it joins the media or it renders bare "
        f"handles where master rendered the description."
    )

    awaits_enrich = any(
        isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "enrich"
        for node in ast.walk(tree)
    )
    # `debrief.py` wraps it in `asyncio.wait_for`, so the awaited call is the
    # timeout rather than `enrich` itself — accept either shape, since what is
    # being asserted is that the call is MADE.
    calls_enrich = awaits_enrich or any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "enrich"
        for node in ast.walk(tree)
    )
    assert calls_enrich, f"{rel} imports MediaJoin but never calls enrich()"


@pytest.mark.parametrize("rel,read", sorted(BUILDERS.items()))
def test_each_builder_still_makes_the_read_that_makes_it_one(
    rel: str, read: str,
) -> None:
    """The POSITIVE CONTROL for the test above, and it is not decoration: if a
    module stopped reading transcript rows, the first test would keep passing
    on an import nobody uses, and this list would quietly become a list of
    modules that no longer build transcripts."""
    tree = ast.parse(_source(rel))
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == read
        for node in ast.walk(tree)
    ), f"{rel} no longer calls {read}() — is it still a transcript builder?"


def test_the_join_is_constructed_exactly_once() -> None:
    """ONE instance for the process. Six copies would be six chances to
    disagree about what a picture shows — and six render bounds, six screens,
    six places to forget one."""
    composition = _source("nora/app/composition.py")
    assert composition.count("MediaJoin(") == 1, (
        "the composition root builds more than one MediaJoin; every consumer "
        "must hold the same instance"
    )


def test_the_screen_is_injected_rather_than_imported_by_the_join() -> None:
    """`nora/language/` sits two layers below `nora/agent/` in the import
    contract, so a join that imported `scan_identifiers` directly could never
    be reached by the note tier — the layering failure that would have pushed
    the join back to one consumer. `lint-imports` would catch the violation;
    this catches the shape BEFORE somebody writes it and then has to unpick
    five call sites.
    """
    join = _source("nora/domain/media_join.py")
    tree = ast.parse(join)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("nora.agent"), (
                "nora.domain.media_join imports from nora.agent; the note tier "
                "cannot reach that layer, so the screen must stay injected"
            )
    assert "MediaJoin(" in _source("nora/app/composition.py")
    assert "screen=scan_identifiers" in _source("nora/app/composition.py"), (
        "the composition root must hand the join the REAL screen; a join built "
        "without one would render model prose into six prompts unscreened"
    )
