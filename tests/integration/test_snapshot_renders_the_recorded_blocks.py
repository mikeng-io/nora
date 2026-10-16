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
window, an older line is pruned, the photo's comprehension is rewritten, the note is
revised, the standing and culture are refolded. Rendering from the moved source must
differ (the positive control: a restore that returned today's state could not pass).
Rendering from a sandbox seeded as of T (`sandbox_as_of`) must not.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa

from evals.backbone import _close
from nora.domain.media_join import HANDLE_CHARS
from nora.domain.models import NormalizedMessage
from nora.domain.standing import Standing
from nora.infra.sql import create_sql_engine
from nora.infra.sql.culture import SqlCultureStore
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.standing import SqlStandingStore
from nora.subject import Knowledge, Message, NoteSeed, SubjectSpec, obtain, sandbox_as_of
from nora.subject.as_of import blocks_of, compare_blocks, render_tag_blocks
from tests.sandbox_fixtures import ROOM, Source, seed_source

BEFORE_MS = 1_752_000_000_000
MINUTE = 60_000
HOUR = 3_600_000
NOW = datetime.fromtimestamp(BEFORE_MS / 1000, tz=UTC)

#: The tiers a tag turn reads from the stores, switched on (all three ship
#: off in the template) so their blocks are rendered and compared.
OVERRIDES = {"notes.enabled": True, "standing.enabled": True, "culture.enabled": True}

#: Blocks that must be COMPARED, not merely allowed to match: a restore that
#: rendered none of them would otherwise pass on an empty comparison.
MUST_COMPARE = {"group_transcript", "notes", "standing", "culture"}


def _spec(url: str, src: Source, workdir: Any) -> SubjectSpec:
    return SubjectSpec(
        database_url=url, workdir=workdir, room=ROOM, policy=src.policy,
        identity=src.identity, now=NOW, overrides=dict(OVERRIDES),
    )


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[Any]:
    eng = create_sql_engine(pg_url)
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


@pytest.mark.asyncio
async def test_a_restore_renders_the_blocks_the_turn_was_handed(
    pg_url: str, pg_template: str, engine: Any, tmp_path: Any, monkeypatch: Any,
) -> None:
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    src = await seed_source(
        pg_url, tmp_path, Knowledge(
            transcript=(
                Message(body="anyone up for friday", at_ms=BEFORE_MS - 30 * MINUTE),
                Message(
                    body=f"[image {_photo_sha(1)[:HANDLE_CHARS]}] this one?", media=True,
                    at_ms=BEFORE_MS - 20 * MINUTE,
                ),
                Message(body="looks good", at_ms=BEFORE_MS - 10 * MINUTE),
            ),
            standing="they are deciding whether friday still works",
            standing_age_hours=3.0,
            culture="they confirm plans late and nobody minds",
            notes=(NoteSeed(
                title="Friday ride", description="whether friday holds",
                body="nobody has confirmed friday", uncertainty="open",
                closing_condition="somebody says yes or no", anchor_date=20260701,
            ),),
        ),
        template=pg_template, before_ms=BEFORE_MS,
        # A room that LISTS its person, with aliases, so `<room_members>`
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

    live = await obtain(_spec(pg_url, src, tmp_path / "live"))
    try:
        tag = NormalizedMessage(
            platform=ROOM[0], conversation_id=ROOM[1], conversation_type="group",
            sender_id="+85200000001", sender_name="Jordan", timestamp=BEFORE_MS,
            body="@nora which bike was it?", raw={},
        )
        await live.deps.audit.append(message=tag, person_id="")
        recorded = await render_tag_blocks(live, message=tag, now=NOW)
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
        notes = SqlNoteStore(engine)
        [note] = await notes.standing(unit=live.unit)
        await notes.append_revision(
            unit=live.unit, note_id=note.id, body="friday is confirmed",
            parent_revision_id=note.head_revision_id, established_by="debrief",
            now_ms=BEFORE_MS - MINUTE,
        )
        # The notebook renders an index of notes, so a note written after T
        # is what changes it; the revision above changes the note's body.
        await notes.create(
            unit=live.unit, title="Saturday ride", description="whether saturday holds",
            uncertainty="open", closing_condition="somebody books it",
            established_in=ROOM[1], body="nobody has booked saturday",
            established_by="debrief", now_ms=BEFORE_MS - MINUTE, anchor_date=20260702,
        )
        await SqlStandingStore(engine).write(standing=Standing(
            platform=ROOM[0], conversation_id=ROOM[1],
            body="friday is settled", fold_count=9, updated_at=BEFORE_MS - MINUTE,
        ))
        await SqlCultureStore(engine).append(
            key=live.culture_key, body="they now confirm early",
            written_at=BEFORE_MS - MINUTE, established_by="test",
        )
        moved = await render_tag_blocks(live, message=tag, now=NOW)
    finally:
        await _close(live)

    # Positive control: what the stores hold NOW is not what the turn saw.
    control = compare_blocks(recorded, moved)
    assert set(control.differs) >= MUST_COMPARE, control

    async with sandbox_as_of(pg_url, at, template=pg_template) as (url, _report):
        restored_subject = await obtain(_spec(url, src, tmp_path / "restored"))
        try:
            restored = await render_tag_blocks(restored_subject, message=tag, now=NOW)
        finally:
            await _close(restored_subject)

    diff = compare_blocks(recorded, restored)
    assert set(diff.compared) >= MUST_COMPARE, diff
    # `<room_members>` is authored (policy + identity, recorded by
    # `config_revision`), so a restore renders it identically; it is compared,
    # not excepted, and does not move with the record (so it is not in the
    # control's `differs` above).
    assert "room_members" in diff.compared, diff
    assert "Miles: 俊爺, 阿Miles" in recorded["room_members"]
    assert diff.equal, {
        name: (recorded.get(name), restored.get(name))
        for name in (*diff.differs, *diff.missing, *diff.extra)
    }
    assert "a red bicycle" in recorded["group_transcript"]


def test_blocks_of_cuts_a_prefill_into_its_blocks() -> None:
    text = (
        "Right now it is noon.\n"
        '<notes description="x">\na &lt;/notes&gt; line\n</notes>\n'
        '<group_transcript description="y">\n<m id="1">hi</m>\n</group_transcript>\n'
    )
    blocks = blocks_of(text)
    assert list(blocks) == ["notes", "group_transcript"]
    assert blocks["notes"].endswith("</notes>")
    with pytest.raises(ValueError, match="twice"):
        blocks_of(text + '<notes description="x">\nagain\n</notes>\n')
