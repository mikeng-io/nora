"""The snapshot gate: a tag turn's blocks, rendered from a snapshot at its moment, equal the
blocks the turn was handed — memory and the Live blocks excepted, the transcript rendered
through the media join as of T.

THE RECORDING. No Langfuse trace exists offline, so the turn is recorded the way its trace
records it: the production assembler and `build_prefill`, composed as the deployed graph
(`obtain`), render the blocks for a tagging line at T on a seeded source — that text is
what the trace's generation carries as its input. `nora.subject.as_of.blocks_of` cuts it
into blocks, the same function the live procedure runs over a real trace
(`scripts/snapshot_block_equivalence.py`).

THEN THE SOURCE MOVES ON, in every tier a tag block reads: a late line lands inside the
window, an older line is pruned, the photo's comprehension is rewritten, the open
intention gains a sibling, the situation and profile claims are refolded. Rendering from
the moved source must differ (the positive control: a restore that returned today's state
could not pass). Rendering from a sandbox seeded as of T (`sandbox_as_of`) must not.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa

from nora.domain.media_join import HANDLE_CHARS
from nora.domain.models import NormalizedMessage
from nora.infra.sql import create_sql_engine
from nora.infra.sql.cognitive_graph_schema import CLAIM, EDGE, ENTITY, INTENTION
from nora.infra.sql.cognitive_graph_store import SqlCognitiveGraph
from nora.subject import Knowledge, Message, SubjectSpec, sandbox_as_of
from nora.subject.as_of import blocks_of, compare_blocks, render_tag_blocks
from tests.sandbox_fixtures import ROOM, Source, offline_subject, seed_source

BEFORE_MS = 1_752_000_000_000
MINUTE = 60_000
HOUR = 3_600_000
NOW = datetime.fromtimestamp(BEFORE_MS / 1000, tz=UTC)

#: The graph ids of the pointer entities the seed writes — the subjects the
#: profile presentations are rendered `of`.
ROOM_ENTITY = "nora:room:signal:g1"
UNIT_ENTITY = "nora:unit:nora"

#: Blocks that must be COMPARED, not merely allowed to match: a restore that
#: rendered none of them would otherwise pass on an empty comparison. The named
#: presentations are the graph's Standing and profile accounts — what `notes`,
#: `standing`, `culture` and `unit_culture` became (design/cognition/context.md's
#: «Today's blocks» table).
MUST_COMPARE = {
    "transcript", "graph_standing",
    "graph_profile of=" + ROOM_ENTITY,
    "graph_profile of=" + UNIT_ENTITY,
}


def _spec(url: str, src: Source, workdir: Any) -> SubjectSpec:
    return SubjectSpec(
        database_url=url, workdir=workdir, room=ROOM, policy=src.policy,
        identity=src.identity, now=NOW,
    )


@pytest.fixture
async def engine(clean_pg_url: str) -> AsyncIterator[Any]:
    eng = create_sql_engine(clean_pg_url)
    try:
        yield eng
    finally:
        await eng.dispose()


def _photo_sha(index: int) -> str:
    # `know()`'s own deterministic digest for the i-th row's attachment.
    import hashlib
    return hashlib.sha256(f"{ROOM[1]}:{index}".encode()).hexdigest()


async def _describe(engine: Any, sha: str, text: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO media_objects (sha256, media_type, size, source, title, "
            "description, created_at_ms) VALUES (:s, 'image/jpeg', 1024, 'inbound', "
            "'a bicycle', :d, :t) ON CONFLICT (sha256) DO UPDATE SET description = :d"
        ), {"s": sha, "d": text, "t": BEFORE_MS - 20 * MINUTE})


async def _clock(engine: Any) -> datetime:
    async with engine.connect() as conn:
        return (await conn.execute(sa.text("SELECT clock_timestamp()"))).scalar_one()


async def _graph_knowledge(engine: Any) -> SqlCognitiveGraph:
    """The room's knowledge as graph rows: the claims and open intention a
    `called` pass reaches from its room anchor and presents as Standing and
    the room and unit profiles (design/cognition/context.md's migration table).
    """
    graph = SqlCognitiveGraph(engine)
    unit = "nora"

    async def entity(eid: str, name: str, pointer: str | None = None,
                     ref: str | None = None) -> None:
        await graph.create(ENTITY, {
            "id": eid, "kind": "entity", "basis": "computed" if pointer else "inferred",
            "state": "admitted", "unit": unit, "name": name, "pointer_kind": pointer,
            "pointer_ref": ref, "persons": [], "exposure": "internal", "writer": "test",
            "asserted_at": datetime.fromtimestamp(
                (BEFORE_MS - 30 * 24 * HOUR) / 1000, tz=UTC),
        })

    async def edge(eid: str, kind: str, src: str, dst: str,
                   basis: str = "inferred") -> None:
        detail = ({"reasoning": "deduced", "confidence": "high"}
                  if basis == "inferred" else {})
        await graph.create(EDGE, {
            "id": eid, "kind": "edge", "basis": basis, "state": "live", "unit": unit, "type": kind,
            "src_id": src, "dst_id": dst, "exposure": "internal", "writer": "test",
            "asserted_at": datetime.fromtimestamp(
                (BEFORE_MS - 30 * 24 * HOUR) / 1000, tz=UTC),
            **detail,
        })

    async def claim(cid: str, part: str, text: str, *, age_ms: int,
                    about: str, cite: str | None = None) -> None:
        await graph.create(CLAIM, {
            "id": cid, "kind": "claim", "basis": "inferred", "state": "live", "unit": unit,
            "part": part, "content": {"text": text}, "reasoning": "deduced",
            "confidence": "high", "exposure": "internal", "writer": "test",
            "origin_room": "g1",
            "asserted_at": datetime.fromtimestamp(
                (BEFORE_MS - age_ms) / 1000, tz=UTC),
        })
        await edge(f"{cid}:about", "about", cid, about)
        if cite is not None:
            await edge(f"{cid}:cites", "cites", cid, cite)

    # The pointers the pass's anchors resolve by (pointer_kind, pointer_ref):
    # the room is a seed, the unit and Nora are subjects the walk must reach.
    await entity("nora:nora:nora", "Nora", pointer="nora", ref="nora")
    await entity(ROOM_ENTITY, "g1", pointer="room", ref="signal:g1")
    await entity(UNIT_ENTITY, "nora", pointer="unit", ref="nora")
    # The folded position and the room's account.
    await claim("claim:situation:friday", "situation",
                "they are deciding whether friday still works",
                age_ms=3 * HOUR, about=ROOM_ENTITY)
    await claim("claim:room:culture", "fact",
                "they confirm plans late and nobody minds",
                age_ms=5 * HOUR, about=ROOM_ENTITY)
    # The unit's own account — about the unit pointer, reached once it cites
    # a message item the pass anchored (wired by `_cite_tag`).
    await claim("claim:unit:culture", "fact", "the unit keeps loose plans",
                age_ms=5 * HOUR, about=UNIT_ENTITY)
    # The open note — an intention with a free-text closing condition and a
    # deadline (the untyped form's own rule), rendered by Standing until it
    # closes.
    await graph.create(INTENTION, {
        "id": "intention:friday", "kind": "intention", "basis": "committed",
        "state": "open", "unit": unit, "owner": "nora", "owed_to": "nora",
        "form": "achieve", "done_when_form": "untyped", "origin_room": "g1",
        "done_when": {"text": "Friday ride - somebody says yes or no"},
        "deadline": datetime.fromtimestamp((BEFORE_MS + 24 * HOUR) / 1000, tz=UTC),
        "delivery_mode": "when_it_comes_up", "exposure": "internal", "writer": "test",
        "asserted_at": datetime.fromtimestamp((BEFORE_MS - 2 * HOUR) / 1000, tz=UTC),
    })
    await edge("intention:friday:about", "about", "intention:friday", ROOM_ENTITY,
               basis="committed")
    return graph


async def _cite_tag(graph: SqlCognitiveGraph, engine: Any) -> None:
    """Link the unit account to the tag's item so the walk reaches it — a
    `called` pass anchors pointer entities, and claims off a pointer travel
    only `about` edges, so unit account rows cite the message that stands as
    their evidence here.
    """
    async with engine.connect() as conn:
        item_id = (await conn.execute(sa.text(
            "SELECT graph_id FROM messages WHERE conversation_id = :c "
            "AND body = :b"
        ), {"c": ROOM[1], "b": "@nora which bike was it?"})).scalar_one()
    await graph.create(EDGE, {
        "id": "edge:claim:unit:culture:cites", "kind": "edge", "basis": "inferred",
        "state": "live", "unit": "nora", "type": "cites", "src_id": "claim:unit:culture",
        "dst_id": item_id, "reasoning": "deduced", "confidence": "high",
        "exposure": "internal", "writer": "test",
        "asserted_at": datetime.fromtimestamp(
            (BEFORE_MS - 30 * 24 * HOUR) / 1000, tz=UTC),
    })


async def _graph_moves(graph: SqlCognitiveGraph, *, at_ms: int,
                       item_id: str) -> None:
    """The source moving on in the rows the graph presentations read."""
    unit = "nora"
    when = datetime.fromtimestamp(at_ms / 1000, tz=UTC)

    async def claim(cid: str, part: str, text: str, *, about: str,
                    cite: str | None = None) -> None:
        await graph.create(CLAIM, {
            "id": cid, "kind": "claim", "basis": "inferred", "state": "live", "unit": unit,
            "part": part, "content": {"text": text}, "reasoning": "deduced",
            "confidence": "high", "exposure": "internal", "writer": "test",
            "origin_room": "g1", "asserted_at": when,
        })
        await graph.create(EDGE, {
            "id": f"edge:{cid}:about", "kind": "edge", "basis": "inferred", "state": "live",
            "unit": unit, "type": "about", "src_id": cid, "dst_id": about,
            "reasoning": "deduced", "confidence": "high",
            "exposure": "internal", "writer": "test", "asserted_at": when,
        })
        if cite is not None:
            await graph.create(EDGE, {
                "id": f"edge:{cid}:cites", "kind": "edge", "basis": "inferred",
                "state": "live", "unit": unit, "type": "cites", "src_id": cid, "dst_id": cite,
                "reasoning": "deduced", "confidence": "high",
                "exposure": "internal", "writer": "test", "asserted_at": when,
            })

    # Friday settles; the room and unit accounts revise; a second intention
    # opens for Saturday.
    await claim("claim:situation:settled", "situation", "friday is settled",
                about=ROOM_ENTITY)
    await claim("claim:room:early", "fact", "they now confirm early",
                about=ROOM_ENTITY)
    await claim("claim:unit:early", "fact", "the unit confirms early now",
                about=UNIT_ENTITY, cite=item_id)
    await graph.create(INTENTION, {
        "id": "intention:saturday", "kind": "intention", "basis": "committed",
        "state": "open", "unit": unit, "owner": "nora", "owed_to": "nora",
        "form": "achieve", "done_when_form": "untyped", "origin_room": "g1",
        "done_when": {"text": "Saturday ride - somebody books it"},
        "deadline": datetime.fromtimestamp((BEFORE_MS + 48 * HOUR) / 1000, tz=UTC),
        "delivery_mode": "when_it_comes_up", "exposure": "internal", "writer": "test",
        "asserted_at": when,
    })
    await graph.create(EDGE, {
        "id": "edge:intention:saturday:about", "kind": "edge", "basis": "committed",
        "state": "live", "unit": unit, "type": "about", "src_id": "intention:saturday",
        "dst_id": ROOM_ENTITY, "exposure": "internal", "writer": "test",
        "asserted_at": when,
    })


@pytest.mark.asyncio
async def test_a_restore_renders_the_blocks_the_turn_was_handed(
    clean_pg_url: str, pg_template: str, engine: Any, tmp_path: Any, monkeypatch: Any,
) -> None:
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    src = await seed_source(
        clean_pg_url, tmp_path, Knowledge(
            transcript=(
                Message(body="anyone up for friday", at_ms=BEFORE_MS - 30 * MINUTE),
                Message(
                    body=f"[image {_photo_sha(1)[:HANDLE_CHARS]}] this one?", media=True,
                    at_ms=BEFORE_MS - 20 * MINUTE,
                ),
                Message(body="looks good", at_ms=BEFORE_MS - 10 * MINUTE),
            ),
        ),
        template=pg_template, before_ms=BEFORE_MS,
        # A room that LISTS its person, with aliases, so `<members>`
        # renders (a `persons = "*"` room, the default, has none).
        policy=(
            'default = "deny"\n[[conversations]]\nplatform = "signal"\n'
            'id = "g1"\nlabel = "g1"\npersons = ["miles_novak"]\nunit = "nora"\n'
        ),
        identity=(
            '[persons.miles_novak]\nname = "Miles"\naliases = ["俊爺", "阿Miles"]\n'
            '[[persons.miles_novak.signal]]\nphone = "+852111"\n'
        ),
    )
    await _describe(engine, _photo_sha(1), "a red bicycle against a wall")
    graph = await _graph_knowledge(engine)

    async with offline_subject(_spec(clean_pg_url, src, tmp_path / "live")) as live:
        tag = NormalizedMessage(
            platform=ROOM[0], conversation_id=ROOM[1], conversation_type="group",
            sender_id="+85200000001", sender_name="Jordan", timestamp=BEFORE_MS,
            body="@nora which bike was it?", raw={},
        )
        tag_id = await live.deps.audit.append(message=tag, person_id="")
        await _cite_tag(graph, engine)
        recorded = await render_tag_blocks(live, message=tag, now=NOW, row_id=tag_id)
        # The open intention and the folded position are Standing now; the
        # room and unit accounts are profile presentations
        # (design/cognition/context.md's «Today's blocks» table).
        assert "Friday ride" in recorded["graph_standing"]
        assert "they are deciding whether friday still works" in recorded[
            "graph_standing"]
        assert ("they confirm plans late and nobody minds"
                in recorded["graph_profile of=" + ROOM_ENTITY])
        at = await _clock(engine)

        # THE SOURCE MOVES ON, in every tier the blocks read.
        await live.deps.audit.append(
            message=replace(tag, sender_id="+85200000002", sender_name="Sam",
                            timestamp=BEFORE_MS - 5 * MINUTE, body="a late line"),
            person_id="",
        )
        async with engine.begin() as conn:
            await conn.execute(sa.text(
                "DELETE FROM messages WHERE conversation_id = :c AND body = :b"
            ), {"c": ROOM[1], "b": "anyone up for friday"})
        await _describe(engine, _photo_sha(1), "a blue bicycle, repainted")
        # Friday settles, the accounts revise, and a second intention opens —
        # the graph rows the Standing and profile blocks read.
        async with engine.connect() as conn:
            item_id = (await conn.execute(sa.text(
                "SELECT graph_id FROM messages WHERE conversation_id = :c "
                "AND body = :b"
            ), {"c": ROOM[1], "b": "@nora which bike was it?"})).scalar_one()
        await _graph_moves(graph, at_ms=BEFORE_MS - MINUTE, item_id=item_id)
        moved = await render_tag_blocks(live, message=tag, now=NOW, row_id=tag_id)

    # Positive control: what the stores hold NOW is not what the turn saw.
    control = compare_blocks(recorded, moved)
    assert set(control.differs) >= MUST_COMPARE, control

    async with (
        sandbox_as_of(clean_pg_url, at, template=pg_template) as (url, _report),
        offline_subject(_spec(url, src, tmp_path / "restored")) as restored_subject,
    ):
        restored = await render_tag_blocks(
            restored_subject, message=tag, now=NOW, row_id=tag_id)

    diff = compare_blocks(recorded, restored)
    assert set(diff.compared) >= MUST_COMPARE, diff
    # `<members>` is authored (policy + identity, recorded by
    # `config_revision`), so a restore renders it identically; it is compared,
    # not excepted, and does not move with the record (so it is not in the
    # control's `differs` above).
    assert "members" in diff.compared, diff
    assert 'id="miles_novak" name="Miles" aliases="俊爺, 阿Miles"' in recorded["members"]
    assert diff.equal, {
        name: (recorded.get(name), restored.get(name))
        for name in (*diff.differs, *diff.missing, *diff.extra)
    }
    assert "a red bicycle" in recorded["transcript"]


def test_blocks_of_cuts_a_prefill_into_its_blocks() -> None:
    text = (
        "Right now it is noon.\n"
        '<graph_profile of="a:room:b">\na &lt;/graph_profile&gt; line\n'
        '</graph_profile>\n'
        '<graph_profile of="a:unit:c">\nunit account\n</graph_profile>\n'
        '<transcript description="y">\n<m id="1">hi</m>\n</transcript>\n'
    )
    blocks = blocks_of(text)
    assert list(blocks) == [
        "graph_profile of=a:room:b", "graph_profile of=a:unit:c", "transcript"]
    assert blocks["graph_profile of=a:room:b"].endswith("</graph_profile>")
    with pytest.raises(ValueError, match="twice"):
        blocks_of(text + '<transcript description="y">\nagain\n</transcript>\n')


def test_an_unavailable_presentation_is_not_an_empty_one() -> None:
    """A self-closed block carries its status: a timed-out read and a
    successful empty read differ, so the gate cannot call them equal."""
    recorded = blocks_of(
        "<transcript>\nx\n</transcript>\n"
        '<graph_standing status="unavailable" cause="graph_read_timeout" />')
    restored = blocks_of("<transcript>\nx\n</transcript>\n<graph_standing />")
    diff = compare_blocks(recorded, restored)
    assert "graph_standing" in diff.differs
    assert "graph_standing" in diff.unavailable
    assert not diff.equal


def test_unavailable_on_both_sides_is_still_unavailable() -> None:
    """Identical `status="unavailable"` text is still an unavailable read:
    the gate reports it, never EQUAL, never absent."""
    text = ("<transcript>\nx\n</transcript>\n"
            '<graph_standing status="unavailable" cause="graph_read_timeout" />')
    diff = compare_blocks(blocks_of(text), blocks_of(text))
    assert diff.unavailable == ("graph_standing",)
    assert not diff.equal


def test_a_quoted_status_string_is_not_the_blocks_status() -> None:
    """A message body quoting `status="unavailable"` is content, not the
    block's status: the same transcript compared with itself reads equal."""
    from nora.agent.prefill import render_transcript
    from nora.domain.models import DeliveryStatus, TranscriptRow

    rows = render_transcript(
        [TranscriptRow(
            id=1, sender_id="+85200000001", sender_name="Jordan", ts=1_700_000_000_000,
            body='The exporter printed status="unavailable" yesterday',
            is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None)],
        display_name="nora", now=datetime.fromtimestamp(1_700_000_000, tz=UTC))
    text = f"<transcript>\n{rows}\n</transcript>"
    diff = compare_blocks(blocks_of(text), blocks_of(text))
    assert diff.unavailable == ()
    assert diff.equal


def test_a_nested_marker_is_not_a_block() -> None:
    """`<source_reads unavailable/>` inside a transcript body is a marker of
    the block it sits in, not a top-level block — the count must not gain it."""
    from nora.agent.prefill import render_transcript
    from nora.domain.models import DeliveryStatus, TranscriptRow

    rows = render_transcript(
        [TranscriptRow(
            id=1, sender_id="+85200000001", sender_name="Jordan", ts=1_700_000_000_000,
            body="hi", is_nora=False, delivery_status=DeliveryStatus.SENT,
            raw_json=None)],
        display_name="nora", now=datetime.fromtimestamp(1_700_000_000, tz=UTC))
    text = f"<transcript>\n{rows}\n<source_reads unavailable/>\n</transcript>"
    blocks = blocks_of(text)
    assert list(blocks) == ["transcript"]


async def test_the_snapshot_render_forwards_the_row_and_a_recorded_roster(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """`render_tag_blocks` hands the called pass the SAME inputs the live
    pass gets — the tagging row's `messages.id` and a `Room` on the recorded
    roster — through `called_pass_prompt`, not the pipeline's privates."""
    from nora.subject.sandbox_roster import RecordedRoster

    tag = NormalizedMessage(
        platform="signal", conversation_id="room-1", conversation_type="group",
        sender_id="+85200000001", sender_name="Jordan",
        timestamp=1_700_000_000_000, body="@nora hi", raw={})
    ctx = SimpleNamespace(message=tag, transcript=(), tagger=None,
                          room_members=frozenset(), tag_id="t")
    monkeypatch.setattr("nora.subject.as_of.engagement_floor",
                        lambda *a, **k: ())
    prompt = AsyncMock(
        return_value=([], "<transcript>\nx\n</transcript>", frozenset()))
    subject = SimpleNamespace(
        database_url="postgresql:///snap",
        cfg=SimpleNamespace(doctrine=SimpleNamespace(display_name="nora"),
                            store=SimpleNamespace(context_window=50)),
        deps=SimpleNamespace(
            assembler=SimpleNamespace(assemble=AsyncMock(return_value=ctx)),
            access=None,
            registry=SimpleNamespace(
                identity_for=lambda _p: SimpleNamespace(aliases=())),
            engagement=SimpleNamespace(called_pass_prompt=prompt)))
    await render_tag_blocks(subject, message=tag, now=datetime.now(UTC),
                            row_id=42)
    kwargs = prompt.await_args.kwargs
    assert kwargs["row_id"] == 42
    assert isinstance(kwargs["room"]._roster_source, RecordedRoster)
