"""`SqlLanguageStore` — the Dreamer's durability layer (Task 7).

SQL-only, like `tests/infra/test_audit_store.py` and its siblings: this is the
one implementation's own behaviour, not the `RoomLanguageReader`/
`RoomConsolidator` contract shared with a fake (that suite is
`tests/contract/test_room_consolidator_contract.py`, which Task 8 wires this
store into on the consolidator side; the degradation guarantee —
`room_language` never raises — is proven there, against a real unreachable
engine, per that suite's own docstring).
"""

from __future__ import annotations

import time

import pytest

from nora.domain.models import LexemeCandidate, NormalizedMessage
from nora.domain.ports import RoomLanguageReader
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.language import SqlLanguageStore
from nora.infra.sql.migrations_runner import run_migrations

DAY_MS = 86_400_000
NOW = int(time.time() * 1000)


def observation(
    *,
    message_id: int,
    canonical_form: str = "kam",
    sense: str = "尷",
    surface_form: str | None = None,
    part_of_speech: str = "adjective",
    person_id: str | None = "miles",
    confidence: float = 0.9,
) -> LexemeCandidate:
    return LexemeCandidate(
        surface_form=surface_form or canonical_form,
        canonical_form=canonical_form,
        sense=sense,
        part_of_speech=part_of_speech,
        message_id=message_id,
        person_id=person_id,
        confidence=confidence,
    )


def _msg(conv: str = "g1", body: str = "hi", ts: int = 1,
          sender: str = "+852111") -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id=conv, conversation_type="group",
        sender_id=sender, sender_name="Miles", timestamp=ts, body=body, raw={},
    )


@pytest.fixture
async def store(pg_url) -> SqlLanguageStore:
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    s = SqlLanguageStore(engine)
    yield s
    await engine.dispose()


@pytest.fixture
async def record(pg_url) -> SqlRecordStore:
    """A real `messages` writer sharing the SAME database `store` reads from —
    `lag`/`seconds_since_last_message`/`watermark_is_behind_the_record` all
    query `messages` directly, so they need real rows, not observations."""
    run_migrations(pg_url)
    s = SqlRecordStore(create_sql_engine(pg_url))
    yield s
    await s.aclose()


def test_the_store_satisfies_the_reader_port():
    """`RoomLanguageReader` is `@runtime_checkable` — this checks method NAMES
    only. Static conformance is mypy's job on `nora.infra`."""
    assert isinstance(SqlLanguageStore(create_sql_engine("postgresql+asyncpg://x/x")),
                      RoomLanguageReader)


# ---------------------------------------------------------------------------
# append_observations
# ---------------------------------------------------------------------------


async def test_appending_the_same_observation_twice_stores_one(store):
    """ON CONFLICT DO NOTHING against the natural key. This is what makes a
    retried cycle safe, and it is the single guarantee the rendered counts
    depend on."""
    obs = [observation(message_id=1, canonical_form="kam", sense="尷")]
    assert await store.append_observations(scope_id="g1", observations=obs) == 1
    assert await store.append_observations(scope_id="g1", observations=obs) == 0


async def test_appending_an_empty_sequence_stores_nothing(store):
    assert await store.append_observations(scope_id="g1", observations=[]) == 0


async def test_appending_a_batch_with_an_internal_duplicate_stores_one(store):
    """The natural key must hold WITHIN one call, not just across calls —
    `ON CONFLICT DO NOTHING` on a multi-row `INSERT` skips duplicates inside
    its own VALUES list too."""
    obs = [observation(message_id=1, canonical_form="kam", sense="尷"),
           observation(message_id=1, canonical_form="kam", sense="尷")]
    assert await store.append_observations(scope_id="g1", observations=obs) == 1


async def test_observations_in_different_scopes_are_independent(store):
    """`message_id` alone anchors the natural key (`language_observation`'s
    own `UniqueConstraint` omits `scope_id` — see `nora/infra/sql/schema.py`'s
    comment on it), because a real `messages.id` is a single global identity
    that pins exactly one conversation. Two DIFFERENT messages carrying the
    same word in two different rooms are therefore independent by
    construction; this uses distinct message ids to stay a realistic case."""
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=1, canonical_form="kam", sense="尷")])
    assert await store.append_observations(scope_id="g2", observations=[
        observation(message_id=2, canonical_form="kam", sense="尷")]) == 1


# ---------------------------------------------------------------------------
# rescore / room_language — the correctness properties CLAUDE.md calls out
# ---------------------------------------------------------------------------


async def test_rescore_counts_distinct_speakers_not_rows(store):
    """Three messages from one person is one speaker. The rendered
    «3 people» must mean three people."""
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=i, person_id="miles", canonical_form="kam")
        for i in (1, 2, 3)
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    [lex] = (await store.room_language(scope_id="g1", limit=10)).lexemes
    assert (lex.occurrence_count, lex.distinct_speakers) == (3, 1)


async def test_distinct_speakers_counts_every_real_person_once(store):
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=1, person_id="miles", canonical_form="kam"),
        observation(message_id=2, person_id="alice", canonical_form="kam"),
        observation(message_id=3, person_id="miles", canonical_form="kam"),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    [lex] = (await store.room_language(scope_id="g1", limit=10)).lexemes
    assert (lex.occurrence_count, lex.distinct_speakers) == (3, 2)


async def test_an_unmapped_speaker_counts_toward_occurrences_not_people(store):
    """A NULL `person_id` (an unmapped sender) is a real usage — it counts
    toward `occurrence_count` — but nobody can name WHO that speaker was, so
    it must never inflate `distinct_speakers`. This mirrors Postgres's own
    `COUNT(DISTINCT person_id)`, which already excludes NULL."""
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=1, person_id="miles", canonical_form="kam"),
        observation(message_id=2, person_id=None, canonical_form="kam"),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    [lex] = (await store.room_language(scope_id="g1", limit=10)).lexemes
    assert (lex.occurrence_count, lex.distinct_speakers) == (2, 1)


async def test_two_senses_of_one_word_are_two_rows(store):
    """`sense` is in the primary key. Without that this silently overwrites."""
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=1, canonical_form="firm", sense="正/掂"),
        observation(message_id=2, canonical_form="firm", sense="堅硬"),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    lexemes = (await store.room_language(scope_id="g1", limit=10)).lexemes
    assert {lx.sense for lx in lexemes} == {"正/掂", "堅硬"}


async def test_stale_entries_do_not_render(store):
    """`state_for` returns `stale` past 120 days of silence, and `room_language`
    filters to `live`/`fading` only — the far-future `now_ms` simulates a word
    nobody has said in a long time regardless of when it was actually stored."""
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=1, canonical_form="kam", sense="尷"),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW + 200 * DAY_MS)
    assert not (await store.room_language(scope_id="g1", limit=10)).lexemes


async def test_fading_entries_still_render(store):
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=1, canonical_form="kam", sense="尷"),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW + 30 * DAY_MS)
    [lex] = (await store.room_language(scope_id="g1", limit=10)).lexemes
    assert lex.state == "fading"


async def test_room_language_is_empty_for_an_unknown_scope(store):
    assert not await store.room_language(scope_id="nobody", limit=10)


async def test_room_language_orders_by_recognition_score_descending(store):
    """The store owns ordering — `RoomLexeme` carries no score field, so
    nothing downstream can re-sort. A word four people share must outrank a
    word one person repeats, exactly as `scoring`'s own weighting intends."""
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=i, person_id="miles", canonical_form="tic")
        for i in range(1, 11)
    ])
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=i, person_id=f"p{i}", canonical_form="shared")
        for i in range(11, 15)
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    lexemes = (await store.room_language(scope_id="g1", limit=10)).lexemes
    assert [lx.canonical_form for lx in lexemes] == ["shared", "tic"]


async def test_room_language_honours_its_limit(store):
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=i, canonical_form=f"word{i}", sense="s")
        for i in range(1, 6)
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    lexemes = (await store.room_language(scope_id="g1", limit=2)).lexemes
    assert len(lexemes) == 2


async def test_room_language_carries_examples_with_attribution(store):
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=1, person_id="miles", canonical_form="kam",
                    surface_form="好撚kam"),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    [lex] = (await store.room_language(scope_id="g1", limit=10)).lexemes
    [example] = lex.examples
    assert (example.message_id, example.person_id, example.text) == (1, "miles", "好撚kam")


async def test_rescore_is_idempotent_and_does_not_inflate_counts(store):
    """Running `rescore` twice over the same evidence must not double the
    occurrence count — it is a full recompute, not an accumulator."""
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=1, canonical_form="kam", sense="尷"),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    await store.rescore(scope_id="g1", now_ms=NOW)
    [lex] = (await store.room_language(scope_id="g1", limit=10)).lexemes
    assert lex.occurrence_count == 1


async def test_rescore_over_no_evidence_is_a_silent_no_op(store):
    await store.rescore(scope_id="empty-scope", now_ms=NOW)
    assert not await store.room_language(scope_id="empty-scope", limit=10)


async def test_rescore_lets_a_word_fade_with_no_new_observations(store):
    """Decay is time-driven: `_dream_once` calls `consolidate` (and therefore
    `rescore`) even over an EMPTY batch, specifically so a quiet scope's
    lexicon can still age out. Calling `rescore` again with a later `now_ms`
    and no new evidence must be enough to move `live` -> `fading`."""
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=1, canonical_form="kam", sense="尷"),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    [lex] = (await store.room_language(scope_id="g1", limit=10)).lexemes
    assert lex.state == "live"

    await store.rescore(scope_id="g1", now_ms=NOW + 30 * DAY_MS)
    [lex] = (await store.room_language(scope_id="g1", limit=10)).lexemes
    assert lex.state == "fading"


# ---------------------------------------------------------------------------
# watermark_for / advance_watermark / watermark_age_seconds
# ---------------------------------------------------------------------------


async def test_watermark_for_an_unknown_pair_is_zero(store):
    assert await store.watermark_for(consolidator_name="language", scope_id="g1") == 0


async def test_advance_watermark_then_watermark_for_round_trips(store):
    await store.advance_watermark(
        consolidator_name="language", scope_id="g1", last_message_id=42)
    assert await store.watermark_for(
        consolidator_name="language", scope_id="g1") == 42


async def test_advance_watermark_upserts_rather_than_duplicating(store):
    await store.advance_watermark(
        consolidator_name="language", scope_id="g1", last_message_id=5)
    await store.advance_watermark(
        consolidator_name="language", scope_id="g1", last_message_id=9)
    assert await store.watermark_for(
        consolidator_name="language", scope_id="g1") == 9


async def test_watermarks_are_independent_per_scope_and_consolidator(store):
    await store.advance_watermark(
        consolidator_name="language", scope_id="g1", last_message_id=5)
    assert await store.watermark_for(consolidator_name="language", scope_id="g2") == 0
    assert await store.watermark_for(consolidator_name="other", scope_id="g1") == 0


async def test_watermark_age_seconds_is_infinite_before_any_watermark(store):
    age = await store.watermark_age_seconds(consolidator_name="language", scope_id="g1")
    assert age == float("inf")


async def test_watermark_age_seconds_is_finite_and_small_right_after_advancing(store):
    await store.advance_watermark(
        consolidator_name="language", scope_id="g1", last_message_id=1)
    age = await store.watermark_age_seconds(consolidator_name="language", scope_id="g1")
    assert 0.0 <= age < 30.0


# ---------------------------------------------------------------------------
# lag / seconds_since_last_message / watermark_is_behind_the_record — all
# read `messages` directly, so these use the `record` fixture to write real rows.
# ---------------------------------------------------------------------------


async def test_lag_counts_human_rows_after_the_watermark(store, record):
    for i in range(4):
        await record.append(message=_msg(body=f"m{i}", ts=i))
    assert await store.lag(scope_id="g1", after_id=2) == 2


async def test_lag_excludes_noras_own_messages(store, record):
    id1 = await record.append(message=_msg(body="human", ts=1))
    await record.append(message=_msg(body="nora", ts=2), is_nora=True)
    assert await store.lag(scope_id="g1", after_id=id1 - 1) == 1


async def test_lag_is_zero_with_nothing_newer(store, record):
    row_id = await record.append(message=_msg())
    assert await store.lag(scope_id="g1", after_id=row_id) == 0


async def test_lag_is_zero_for_an_unknown_scope(store):
    assert await store.lag(scope_id="nobody", after_id=0) == 0


async def test_seconds_since_last_message_is_infinite_with_no_messages(store):
    age = await store.seconds_since_last_message(scope_id="g1")
    assert age == float("inf")


async def test_seconds_since_last_message_reports_a_real_recent_age(store, record):
    now_ms = int(time.time() * 1000)
    await record.append(message=_msg(ts=now_ms))
    age = await store.seconds_since_last_message(scope_id="g1")
    assert 0.0 <= age < 30.0


async def test_watermark_is_behind_the_record_is_false_for_an_empty_scope(store):
    assert await store.watermark_is_behind_the_record(scope_id="g1", watermark=0) is False


async def test_watermark_is_behind_the_record_is_false_when_caught_up(store, record):
    row_id = await record.append(message=_msg())
    assert await store.watermark_is_behind_the_record(
        scope_id="g1", watermark=row_id) is False


async def test_watermark_is_behind_the_record_is_false_for_a_fresh_watermark(store, record):
    """`watermark=0` with the FIRST surviving row at `id == 1` is the ordinary
    "never consolidated yet" state, not evidence loss."""
    await record.append(message=_msg())
    assert await store.watermark_is_behind_the_record(
        scope_id="g1", watermark=0) is False


async def test_watermark_is_behind_the_record_is_true_when_rows_were_pruned_unread(store):
    """Simulates `store.prune_enabled` deleting rows 1-6 while the watermark
    still sits at 3: rows 4-6 were never read and are now gone. Inserted
    directly against the schema table so the id sequence starts at 7 without
    six throwaway rows going through `SqlRecordStore`."""
    import sqlalchemy as sa

    from nora.infra.sql.schema import MESSAGES

    async with store._engine.begin() as conn:
        await conn.execute(sa.text("ALTER TABLE messages ALTER COLUMN id RESTART WITH 7"))
        await conn.execute(sa.insert(MESSAGES).values(
            platform="signal", conversation_id="g1", conversation_type="group",
            sender_id="+852111", sender_name="Miles", ts=1, body="survivor",
            is_nora=0, delivery_status="sent",
        ))
    assert await store.watermark_is_behind_the_record(
        scope_id="g1", watermark=3) is True


async def test_watermark_is_behind_the_record_is_scoped_to_the_conversation(store, record):
    await record.append(message=_msg(conv="g2", body="other room"))
    assert await store.watermark_is_behind_the_record(
        scope_id="g1", watermark=0) is False
