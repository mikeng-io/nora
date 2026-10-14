"""A full-restore recall case end to end against Postgres.

`sandbox_as_of` fills a sandbox with every store as of an instant; then
`nora.subject.restored_room` hands the drive its row map and transcript, and
`evals.recall.recall_facts` renders the case's evidence for the judge. Three
claims, each its own assertion, and none follows from another:

1. the row map is the IDENTITY, and that is checked against the source rather
   than assumed: a sandbox that lost a row refuses the case;
2. the transcript is joined through the restored `media_json` and
   `media_objects`, so an image row carries its stored reading, not a bare
   handle (what the snapshot path cannot do);
3. the facts are the records AS THEY STOOD AT THE INSTANT: a note revision the
   turn writes into the sandbox afterwards is not evidence, a record the
   sandbox did not hold or one outside the case's room or unit raises, and no
   person id or coordinate reaches the judge.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa

from evals.backbone import _close
from evals.recall import recall_facts
from nora.domain.media_join import HANDLE_CHARS
from nora.infra.sql import create_sql_engine
from nora.infra.sql.notes import SqlNoteStore
from nora.subject import (
    Knowledge,
    Message,
    NoteSeed,
    SubjectSpec,
    obtain,
    restored_room,
    sandbox_as_of,
)
from nora.subject.evidence import EvidenceUnavailableError
from tests.sandbox_fixtures import ROOM, seed_source

MINUTE = 60_000
DAY = 86_400_000
POLICY = (
    'default = "deny"\n'
    '[[conversations]]\nplatform = "signal"\nid = "g1"\nlabel = "g1"\n'
    'persons = ["miles_novak"]\nunit = "nora"\n'
    '[[conversations]]\nplatform = "signal"\nid = "g2"\nlabel = "g2"\n'
    'persons = ["miles_novak"]\nunit = "nora-two"\n'
)
IDENTITY = (
    '[persons.miles_novak]\nname = "Miles"\n'
    '[[persons.miles_novak.signal]]\nphone = "+852111"\n'
)
READING = "a red bicycle against a wall"
PLAN = "friday at seven, by the pier"
LATER = "moved to saturday"
PLACE = "the pier"
LAT = 22.2833117


def _photo_sha(index: int) -> str:
    # `know()`'s own deterministic digest for the i-th row's attachment.
    import hashlib
    return hashlib.sha256(f"{ROOM[1]}:{index}".encode()).hexdigest()


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[Any]:
    eng = create_sql_engine(pg_url)
    try:
        yield eng
    finally:
        await eng.dispose()


async def _seed(
    pg_url: str, pg_template: str, engine: Any, tmp_path: Any,
    extra: Callable[[Any, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """A room with a described photo, a dated note, and two outings of the
    unit (one inside a week, one outside) plus one of another unit; then the
    instant, read off the database's own clock."""
    seeded_until = int(time.time() * 1000) - 5 * MINUTE
    await seed_source(
        pg_url, tmp_path, Knowledge(
            transcript=(
                Message(body="anyone up for friday", at_ms=seeded_until - 30 * MINUTE),
                Message(
                    body=f"[image {_photo_sha(1)[:HANDLE_CHARS]}] this one?", media=True,
                    at_ms=seeded_until - 20 * MINUTE,
                ),
                Message(body="looks good", at_ms=seeded_until - 10 * MINUTE),
            ),
            notes=(NoteSeed(
                title="Friday ride", description="when and where", body=PLAN,
                anchor_date=20260701,
            ),),
        ),
        template=pg_template, before_ms=seeded_until, policy=POLICY, identity=IDENTITY,
    )
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO media_objects (sha256, media_type, size, source, title, "
            "description, created_at_ms) VALUES (:s, 'image/jpeg', 1024, 'inbound', "
            "'a bicycle', :d, :t) ON CONFLICT (sha256) DO UPDATE SET description = :d"
        ), {"s": _photo_sha(1), "d": READING, "t": seeded_until - 20 * MINUTE})
        build = (await conn.execute(sa.text(
            "INSERT INTO rollup_build (code_version, params_sha256, status, through_ms) "
            "VALUES ('t', 't', 'current', :t) RETURNING id"
        ), {"t": seeded_until})).scalar_one()

        async def outing(unit: str, start: int) -> int:
            return int((await conn.execute(sa.text(
                "INSERT INTO rollup_outing (build_id, unit, lineage, valid_from_ms, "
                "valid_to_ms, as_of_ms, persons, lat, lng, place_label) VALUES "
                "(:b, :u, '[]'::jsonb, :f, :t, :t, ARRAY['miles_novak', 'not_in_roster'], "
                ":lat, 114.15, :p) RETURNING id"
            ), {"b": build, "u": unit, "f": start, "t": start + 60 * MINUTE,
                "lat": LAT, "p": PLACE})).scalar_one())

        inside = await outing("nora", seeded_until - 2 * DAY)
        outside = await outing("nora", seeded_until - 10 * DAY)
        foreign = await outing("another-unit", seeded_until - DAY)
        ids = [int(i) for i in (await conn.execute(sa.text(
            "SELECT id FROM messages WHERE conversation_id = 'g1' ORDER BY ts"
        ))).scalars()]
        more = await extra(conn, {"build": build, "seeded_until": seeded_until}) if extra else {}
    [note] = await SqlNoteStore(engine).standing(unit="nora")
    await asyncio.sleep(0.05)
    async with engine.connect() as conn:
        clock = (await conn.execute(sa.text("SELECT clock_timestamp()"))).scalar_one()
    at_ms = int(clock.timestamp() * 1000)
    await asyncio.sleep(0.05)
    return {
        "at_ms": at_ms, "ids": ids, "photo": ids[1], "note": str(note.id)[:8],
        "inside": inside, "outside": outside, "foreign": foreign, **more,
    }


def _spec(url: str, tmp_path: Any, at_ms: int, room: tuple[str, str] = ROOM) -> SubjectSpec:
    return SubjectSpec(
        database_url=url, workdir=tmp_path / f"restored-{room[1]}", room=room,
        policy=POLICY, identity=IDENTITY,
        now=datetime.fromtimestamp(at_ms / 1000, tz=UTC),
    )


@pytest.mark.asyncio
async def test_a_full_restore_hands_the_drive_an_identity_map_and_the_evidence_at_the_instant(
    pg_url: str, pg_template: str, engine: Any, tmp_path: Any, monkeypatch: Any,
) -> None:
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    seed = await _seed(pg_url, pg_template, engine, tmp_path)
    at_ms = seed["at_ms"]
    # The source moves on after the instant: a late line in the room.
    async with engine.begin() as conn:
        late = int((await conn.execute(sa.text(
            "INSERT INTO messages (platform, conversation_id, conversation_type, sender_id, "
            "sender_name, ts, body, is_nora, created_at) VALUES ('signal', 'g1', 'group', "
            "'+852999', 'Late', :ts, 'a late line', 0, '') RETURNING id"
        ), {"ts": at_ms + MINUTE})).scalar_one())

    at = datetime.fromtimestamp(at_ms / 1000, tz=UTC)
    async with sandbox_as_of(pg_url, at, template=pg_template) as (url, _report):
        subject = await obtain(_spec(url, tmp_path, at_ms))
        try:
            seeded = await restored_room(subject, source_url=pg_url, before_ms=at_ms)
            assert seeded.now_ms == at_ms
            assert seeded.rows == {i: i for i in seed["ids"]}, "ids are copied verbatim"
            assert late not in seeded.rows
            shown = "\n".join(seeded.transcript)
            assert READING in shown, "the image row carries its restored reading"

            # THE TURN WRITES: a revision recorded now, long after the instant.
            sandbox = create_sql_engine(url)
            try:
                notes = SqlNoteStore(sandbox)
                [note] = await notes.standing(unit=subject.unit)
                await notes.append_revision(
                    unit=subject.unit, note_id=note.id, body=LATER,
                    parent_revision_id=note.head_revision_id, established_by="turn",
                    now_ms=at_ms,
                )
            finally:
                await sandbox.dispose()

            md = {"criteria": ["recall_grounded"], "evidence": {
                "messages": [seed["photo"]],
                "media": [_photo_sha(1)],
                "notes": [seed["note"]],
                "rollup_outings": [seed["inside"]],
                "rollup_window_days": 7,
            }}
            facts = await recall_facts(subject, md, now_ms=at_ms)
            assert facts is not None
            assert READING in facts
            assert "(current) written" in facts and PLAN in facts
            assert LATER not in facts, "a revision the turn wrote is not evidence"
            assert "Miles" in facts and "miles_novak" not in facts
            assert "someone outside this group's roster" in facts
            assert PLACE in facts and str(LAT) not in facts, "no coordinate reaches the judge"
            window = facts.split("days before the moment:")[1]
            assert f"outing {seed['inside']}:" in window
            assert f"outing {seed['outside']}:" not in window
            assert f"outing {seed['foreign']}:" not in window

            # Not a recall case: nothing is read.
            assert await recall_facts(subject, {"criteria": ["presence"]}, now_ms=at_ms) is None
            # FAIL-CLOSED: a record the sandbox did not hold at the instant, and
            # one outside the case's unit.
            for evidence in (
                {"messages": [late]},
                {"notes": ["00000000"]},
                {"rollup_outings": [seed["foreign"]]},
            ):
                with pytest.raises(EvidenceUnavailableError):
                    await recall_facts(
                        subject, {"criteria": ["recall_grounded"], "evidence": evidence},
                        now_ms=at_ms,
                    )

            # A restore that lost a row is not an identity map: refused.
            sandbox = create_sql_engine(url)
            try:
                async with sandbox.begin() as conn:
                    await conn.execute(
                        sa.text("DELETE FROM messages WHERE id = :i"), {"i": seed["ids"][0]},
                    )
            finally:
                await sandbox.dispose()
            with pytest.raises(ValueError, match="identity"):
                await restored_room(subject, source_url=pg_url, before_ms=at_ms)
        finally:
            await _close(subject)


@pytest.mark.asyncio
async def test_a_full_restore_refuses_a_room_that_held_nothing_and_a_clock_off_the_instant(
    pg_url: str, pg_template: str, engine: Any, tmp_path: Any, monkeypatch: Any,
) -> None:
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    seed = await _seed(pg_url, pg_template, engine, tmp_path)
    at_ms = seed["at_ms"]
    at = datetime.fromtimestamp(at_ms / 1000, tz=UTC)
    async with sandbox_as_of(pg_url, at, template=pg_template) as (url, _report):
        empty = await obtain(_spec(url, tmp_path, at_ms, room=("signal", "g2")))
        try:
            with pytest.raises(ValueError, match="held no row"):
                await restored_room(empty, source_url=pg_url, before_ms=at_ms)
        finally:
            await _close(empty)
        subject = await obtain(_spec(url, tmp_path, at_ms))
        try:
            with pytest.raises(ValueError, match="clock"):
                await restored_room(subject, source_url=pg_url, before_ms=at_ms - 1)
            with pytest.raises(ValueError, match="own database"):
                await restored_room(subject, source_url=url, before_ms=at_ms)
        finally:
            await _close(subject)


def _uuid(head: str, tail: int) -> str:
    """A synthetic uuid whose first 8 hex are `head`, built at runtime so no
    uuid-shaped literal sits in a tracked file (the PII hook refuses one)."""
    return f"{head}-0000-4000-8000-{tail:012x}"


async def _event(conn: Any, event_id: str, *, room: str, unit: str, at: int) -> None:
    await conn.execute(sa.text(
        "INSERT INTO events (id, conversation_id, title, created_by, \"when\", \"end\", "
        "status, created_at, unit_id, people) VALUES (CAST(:i AS uuid), :c, 'dinner', "
        "'miles_novak', to_timestamp(:w / 1000.0), to_timestamp(:e / 1000.0), 'confirmed', "
        "now(), :u, ARRAY['miles_novak'])"
    ), {"i": event_id, "c": room, "u": unit, "w": at, "e": at + 2 * 60 * MINUTE})


async def _refusal_extras(conn: Any, seed: dict[str, Any]) -> dict[str, Any]:
    """Everything the refusals below name, recorded BEFORE the instant: a
    message of the other room, events in and out of the case's scope (two
    sharing an id prefix), a reading that holds an identifier, and an outing
    of a build the Rollup had already replaced."""
    until = seed["seeded_until"]
    other_room = int((await conn.execute(sa.text(
        "INSERT INTO messages (platform, conversation_id, conversation_type, sender_id, "
        "sender_name, ts, body, is_nora, created_at) VALUES ('signal', 'g2', 'group', "
        "'+852999', 'Other', :ts, 'a line in g2', 0, '') RETURNING id"
    ), {"ts": until - 15 * MINUTE})).scalar_one())
    events = {
        "in_room": _uuid("e1e1e1e1", 1),
        "in_unit": _uuid("e2e2e2e2", 2),
        "outside": _uuid("e3e3e3e3", 3),
        "twin_a": _uuid("e4e4e4e4", 4),
        "twin_b": _uuid("e4e4e4e4", 5),
    }
    await _event(conn, events["in_room"], room="g1", unit="", at=until + 2 * DAY)
    await _event(conn, events["in_unit"], room="elsewhere", unit="nora", at=until + DAY)
    await _event(conn, events["outside"], room="g2", unit="nora-two", at=until + DAY)
    await _event(conn, events["twin_a"], room="g1", unit="nora", at=until + DAY)
    await _event(conn, events["twin_b"], room="g1", unit="nora", at=until + DAY)
    # A key-shaped string, built here so no key-shaped literal is tracked.
    secret = "sk_" + "x" * 20
    await conn.execute(sa.text(
        "INSERT INTO media_objects (sha256, media_type, size, source, title, "
        "description, created_at_ms) VALUES (:s, 'image/png', 10, 'inbound', 'a screen', "
        ":d, :t)"
    ), {"s": "c" * 64, "d": f"a screenshot showing {secret}", "t": until - 5 * MINUTE})
    old_build = (await conn.execute(sa.text(
        "INSERT INTO rollup_build (code_version, params_sha256, status, through_ms) "
        "VALUES ('t', 'old', 'superseded', :t) RETURNING id"
    ), {"t": until - DAY})).scalar_one()
    stale = int((await conn.execute(sa.text(
        "INSERT INTO rollup_outing (build_id, unit, lineage, valid_from_ms, valid_to_ms, "
        "as_of_ms, persons, lat, lng, place_label) VALUES (:b, 'nora', '[]'::jsonb, :f, :t, "
        ":t, ARRAY['miles_novak'], 22.1, 114.1, 'somewhere') RETURNING id"
    ), {"b": old_build, "f": until - 3 * DAY, "t": until - 3 * DAY + 60 * MINUTE})).scalar_one())
    return {
        "other_room": other_room, "events": events, "secret": secret, "stale": stale,
    }


@pytest.mark.asyncio
async def test_every_scope_and_screen_on_the_evidence_holds(
    pg_url: str, pg_template: str, engine: Any, tmp_path: Any, monkeypatch: Any,
) -> None:
    """Each refusal `evidence_at` makes, against a record that exists and was
    held at the instant, so only the check itself stands between it and the
    judge: another room's message, an event outside the room and the unit, an
    ambiguous id prefix, an outing of a replaced build. And the media screen:
    a reading holding an identifier is withheld. An event is rendered in the
    deployment's timezone with its distance from the moment."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    seed = await _seed(pg_url, pg_template, engine, tmp_path, extra=_refusal_extras)
    at_ms = seed["at_ms"]
    events = seed["events"]
    at = datetime.fromtimestamp(at_ms / 1000, tz=UTC)
    async with sandbox_as_of(pg_url, at, template=pg_template) as (url, _report):
        subject = await obtain(_spec(url, tmp_path, at_ms))
        try:
            async def facts(evidence: dict[str, Any]) -> str:
                out = await recall_facts(
                    subject, {"criteria": ["recall_grounded"], "evidence": evidence},
                    now_ms=at_ms,
                )
                assert out is not None
                return out

            # Held, in scope: an event in the room and one unit-wide, each in
            # the deployment's timezone with its distance, never a raw ISO.
            shown = await facts({"events": [events["in_room"][:8], events["in_unit"][:8]]})
            stamp = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} \(\w{3}\)"
            assert len(re.findall(
                rf"- event «dinner», confirmed, when {stamp}, [^,]+ after she was asked "
                rf"to {stamp}, [^,]+ after she was asked, at no venue set, with Miles",
                shown,
            )) == 2, shown
            assert "+00:00" not in shown

            withheld = await facts({"media": ["c" * 64]})
            assert "is withheld here" in withheld and seed["secret"] not in withheld

            for evidence, why in (
                ({"messages": [seed["other_room"]]}, "own room"),
                ({"events": [events["outside"][:8]]}, "neither in the case's room"),
                ({"events": ["e4e4e4e4"]}, "2 events rows"),
                ({"rollup_outings": [seed["stale"]]}, "build"),
            ):
                with pytest.raises(EvidenceUnavailableError, match=why):
                    await facts(evidence)
        finally:
            await _close(subject)


@pytest.mark.asyncio
async def test_a_rollup_with_two_current_builds_answers_no_outing(
    pg_url: str, pg_template: str, engine: Any, tmp_path: Any, monkeypatch: Any,
) -> None:
    """Two builds current at the instant is a Rollup no one answer describes:
    neither a window nor a named outing is read from it.

    `uq_rollup_build_current` keeps a table at one current build, and the
    restore writes heads back into one (so a source whose history held two
    would fail the restore itself). The second is therefore written into the
    SANDBOX's history, before the instant, as a `backfill` version: the shape
    a repaired or imported history takes, and the only one that reaches the
    read."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    seed = await _seed(pg_url, pg_template, engine, tmp_path)
    at_ms = seed["at_ms"]
    at = datetime.fromtimestamp(at_ms / 1000, tz=UTC)
    async with sandbox_as_of(pg_url, at, template=pg_template) as (url, _report):
        subject = await obtain(_spec(url, tmp_path, at_ms))
        try:
            for evidence in ({"rollup_window_days": 7}, {"rollup_outings": [seed["inside"]]}):
                assert await recall_facts(
                    subject, {"criteria": ["recall_grounded"], "evidence": evidence},
                    now_ms=at_ms,
                ), "one current build: read"
            sandbox = create_sql_engine(url)
            try:
                async with sandbox.begin() as conn:
                    await conn.execute(sa.text(
                        "INSERT INTO row_history (table_name, row_key, op, image, recorded_at) "
                        "VALUES ('rollup_build', '{\"id\": 999999}'::jsonb, 'backfill', "
                        "jsonb_build_object('id', 999999, 'status', 'current'), "
                        "CAST(:at AS timestamptz) - interval '1 minute')"
                    ), {"at": at})
            finally:
                await sandbox.dispose()
            for evidence in ({"rollup_window_days": 7}, {"rollup_outings": [seed["inside"]]}):
                with pytest.raises(EvidenceUnavailableError, match="2 Rollup builds"):
                    await recall_facts(
                        subject, {"criteria": ["recall_grounded"], "evidence": evidence},
                        now_ms=at_ms,
                    )
        finally:
            await _close(subject)
