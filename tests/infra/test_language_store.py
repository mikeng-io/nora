"""`SqlLanguageStore` — the Dreamer's durability layer (Task 7).

SQL-only, like `tests/infra/test_audit_store.py` and its siblings: this is the
one implementation's own behaviour, not the `RoomLanguageReader`/
`RoomConsolidator` contract shared with a fake (that suite is
`tests/contract/test_room_consolidator_contract.py`, which Task 8 wires this
store into on the consolidator side; the degradation guarantee —
`room_language` never raises — is proven there, against a real unreachable
engine, per that suite's own docstring).

`append_observations` joins every candidate back to a REAL `messages` row by
`message_id` to read `conversation_id`/`observed_at` off it (see
`nora.infra.sql.language`'s own module docstring) — so almost every test here
uses the `record` fixture (a real `SqlRecordStore` sharing the same database)
to seed real rows first, via the `_seed` helper below, rather than inventing
`message_id` integers that name nothing.
"""

from __future__ import annotations

import time

import pytest
import sqlalchemy as sa

from nora.domain.models import LexemeCandidate, NormalizedMessage
from nora.domain.ports import RoomLanguageReader
from nora.domain.provenance import instruction_sha256
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.language import SqlLanguageStore
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import LANGUAGE_OBSERVATION, MESSAGES, ROOM_LEXEME

DAY_MS = 86_400_000
NOW = int(time.time() * 1000)

# REAL digests over stand-in instruction text, never 64 characters of filler:
# `language_observation.instruction_sha256` carries a CHECK on the hex shape,
# so a helper minting something Postgres would refuse would hide that
# constraint from every test in this file. Two of them, because provenance
# that cannot tell two instruction revisions apart is the defect the column
# exists to remove.
INSTRUCTION_A = instruction_sha256("stand-in extraction instruction, revision A")
INSTRUCTION_B = instruction_sha256("stand-in extraction instruction, revision B")

# Two model names, for the same reason there are two instruction digests: a
# `model` column that cannot tell two models apart records nothing. They vary
# INDEPENDENTLY of the digests below — the same instruction run against a new
# model is exactly the case `instruction_sha256` alone cannot see.
MODEL_A = "stand-in-model-a"
MODEL_B = "stand-in-model-b"


def observation(
    *,
    message_id: int,
    canonical_form: str = "kam",
    sense: str = "尷",
    surface_form: str | None = None,
    part_of_speech: str = "adjective",
    person_id: str | None = "miles",
    confidence: float = 0.9,
    instruction: str = INSTRUCTION_A,
    model: str = MODEL_A,
) -> LexemeCandidate:
    return LexemeCandidate(
        surface_form=surface_form or canonical_form,
        canonical_form=canonical_form,
        sense=sense,
        part_of_speech=part_of_speech,
        message_id=message_id,
        person_id=person_id,
        confidence=confidence,
        instruction_sha256=instruction,
        model=model,
    )


def _msg(conv: str = "g1", body: str = "hi", ts: int = NOW,
          sender: str = "+852111") -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id=conv, conversation_type="group",
        sender_id=sender, sender_name="Miles", timestamp=ts, body=body, raw={},
    )


async def _seed(
    record: SqlRecordStore, n: int, *, conv: str = "g1", ts: int = NOW, step: int = 0,
) -> list[int]:
    """Insert `n` real human rows into `messages`, returning their ids in
    insertion order. `step` gives each successive row a strictly later `ts`
    (`ts + i*step`) — needed only by tests that care about recency ordering;
    the default `step=0` gives every seeded row the same `ts`, which is
    correct for tests that don't."""
    return [
        await record.append(message=_msg(conv=conv, ts=ts + i * step, body=f"seed{i}"))
        for i in range(n)
    ]


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
    `append_observations`'s own join, plus `lag`/`seconds_since_last_message`/
    `watermark_is_behind_the_record`, all need real rows, not invented ids."""
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


async def test_appending_the_same_observation_twice_stores_one(store, record):
    """ON CONFLICT DO NOTHING against the natural key. This is what makes a
    retried cycle safe, and it is the single guarantee the rendered counts
    depend on."""
    [mid] = await _seed(record, 1)
    obs = [observation(message_id=mid, canonical_form="kam", sense="尷")]
    assert await store.append_observations(scope_id="g1", observations=obs) == 1
    assert await store.append_observations(scope_id="g1", observations=obs) == 0


async def test_appending_an_empty_sequence_stores_nothing(store):
    assert await store.append_observations(scope_id="g1", observations=[]) == 0


async def test_appending_a_batch_with_an_internal_duplicate_stores_one(store, record):
    """The natural key must hold WITHIN one call, not just across calls —
    `ON CONFLICT DO NOTHING` on a multi-row `INSERT` skips duplicates inside
    its own VALUES list too."""
    [mid] = await _seed(record, 1)
    obs = [observation(message_id=mid, canonical_form="kam", sense="尷"),
           observation(message_id=mid, canonical_form="kam", sense="尷")]
    assert await store.append_observations(scope_id="g1", observations=obs) == 1


async def test_observations_in_different_scopes_are_independent(store, record):
    """`message_id` alone anchors the natural key (`language_observation`'s
    own `UniqueConstraint` omits `scope_id` — see `nora/infra/sql/schema.py`'s
    comment on it), because a real `messages.id` is a single global identity
    that pins exactly one conversation. Two DIFFERENT messages carrying the
    same word in two different rooms are therefore independent by
    construction; this uses distinct message ids to stay a realistic case."""
    [mid1] = await _seed(record, 1, conv="g1")
    [mid2] = await _seed(record, 1, conv="g2")
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=mid1, canonical_form="kam", sense="尷")])
    assert await store.append_observations(scope_id="g2", observations=[
        observation(message_id=mid2, canonical_form="kam", sense="尷")]) == 1


async def test_a_candidate_naming_a_nonexistent_message_id_is_dropped(store):
    """The evidence-grounding rule `LexemeObserver._extract` applies to a
    hallucinated `message_id` extends one hop further here: a candidate whose
    `message_id` does not resolve against a real `messages` row (the SELECT
    half of the INSERT joins on it) contributes NOTHING — see the metered
    test below for the fact that this is now OBSERVABLE, not silent."""
    obs = [observation(message_id=999_999, canonical_form="kam", sense="尷")]
    assert await store.append_observations(scope_id="g1", observations=obs) == 0


async def test_a_nonexistent_message_id_is_counted_distinctly_from_a_replay(
    pg_url, record,
):
    """Task 7 carry-forward (folded into Task 11): a candidate whose
    `message_id` no longer resolves must be METERED distinctly from a benign
    idempotent REPLAY (a retried cycle re-appending observations whose
    message_ids resolve fine but conflict on the natural key) — before this,
    both looked identical from `append_observations`'s bare return value: a
    lower count than the number of observations submitted. Only the
    non-resolving case is `reason="not_found"`; replaying a genuinely
    resolvable candidate must NOT be counted as if evidence had vanished."""
    from nora.infra.sql.engine import create_sql_engine
    from nora.obs.metrics import Metrics

    metrics = Metrics()
    engine = create_sql_engine(pg_url)
    metered_store = SqlLanguageStore(engine, metrics=metrics)
    try:
        [mid] = await _seed(record, 1)
        real_obs = [observation(message_id=mid, canonical_form="kam", sense="尷")]
        ghost_obs = [observation(message_id=999_999, canonical_form="lor", sense="語氣")]

        # A REPLAY of a resolvable candidate: appended once, then zero the
        # second time purely from the natural-key conflict — not_found must
        # stay at zero throughout.
        assert await metered_store.append_observations(scope_id="g1", observations=real_obs) == 1
        assert await metered_store.append_observations(scope_id="g1", observations=real_obs) == 0
        assert metrics.get("nora_language_candidates_dropped_total", reason="not_found") == 0

        # A candidate whose message_id never resolves — the DISTINCT case.
        assert await metered_store.append_observations(scope_id="g1", observations=ghost_obs) == 0
        assert metrics.get("nora_language_candidates_dropped_total", reason="not_found") == 1
    finally:
        await engine.dispose()


async def test_a_null_body_does_not_break_the_insert(store):
    """`context` is now drawn from `messages.body` (Finding 1), and that
    column is nullable in the schema even though the normal ingest path
    never writes NULL there — a defensive edge case, not the expected path.
    `sa.func.coalesce(MESSAGES.c.body, "")` is what keeps a candidate naming
    such a row from violating `language_observation.context`'s NOT NULL
    constraint and failing the whole batch's INSERT over one row."""
    async with store._engine.begin() as conn:
        mid = (await conn.execute(
            sa.insert(MESSAGES).values(
                platform="signal", conversation_id="g1", conversation_type="group",
                sender_id="+852111", sender_name="Miles", ts=NOW, body=None,
                is_nora=0, delivery_status="sent",
            ).returning(MESSAGES.c.id)
        )).scalar_one()
    obs = [observation(message_id=mid, canonical_form="kam", sense="尷")]
    assert await store.append_observations(scope_id="g1", observations=obs) == 1


async def test_observed_at_and_conversation_id_come_from_the_real_message_row(
    store, record,
):
    """The regression this guards: a naive implementation stamps this
    process's own clock as `observed_at` instead of the message's own `ts`.
    `observed_at` is the ONLY input to `first_seen_at`/`last_seen_at`, which
    drive the recency term and the `live`/`fading`/`stale` decision — a wrong
    `observed_at` means a lexeme renders (or fails to render) for a reason
    that never happened. An old `ts`, far from `NOW`, proves the value in the
    table is the message's, not the append call's own wall-clock time."""
    old_ts = NOW - 200 * DAY_MS
    [mid] = await _seed(record, 1, conv="g7", ts=old_ts)
    await store.append_observations(scope_id="g7", observations=[
        observation(message_id=mid, canonical_form="kam", sense="尷"),
    ])
    async with store._engine.connect() as conn:
        row = (await conn.execute(
            sa.select(LANGUAGE_OBSERVATION.c.observed_at, LANGUAGE_OBSERVATION.c.conversation_id)
            .where(LANGUAGE_OBSERVATION.c.message_id == mid)
        )).one()
    assert row.observed_at == old_ts
    assert row.conversation_id == "g7"


# ---------------------------------------------------------------------------
# rescore / room_language — the correctness properties CLAUDE.md calls out
# ---------------------------------------------------------------------------


async def test_rescore_counts_distinct_speakers_not_rows(store, record):
    """Three messages from one person is one speaker. The rendered
    «3 people» must mean three people."""
    ids = await _seed(record, 3)
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=mid, person_id="miles", canonical_form="kam")
        for mid in ids
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    [lex] = (await store.room_language(scope_id="g1", limit=10)).lexemes
    assert (lex.occurrence_count, lex.distinct_speakers) == (3, 1)


async def test_distinct_speakers_counts_every_real_person_once(store, record):
    ids = await _seed(record, 3)
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=ids[0], person_id="miles", canonical_form="kam"),
        observation(message_id=ids[1], person_id="alice", canonical_form="kam"),
        observation(message_id=ids[2], person_id="miles", canonical_form="kam"),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    [lex] = (await store.room_language(scope_id="g1", limit=10)).lexemes
    assert (lex.occurrence_count, lex.distinct_speakers) == (3, 2)


async def test_an_unmapped_speaker_counts_toward_occurrences_not_people(store, record):
    """A NULL `person_id` (an unmapped sender) is a real usage — it counts
    toward `occurrence_count` — but nobody can name WHO that speaker was, so
    it must never inflate `distinct_speakers`. `COUNT(DISTINCT person_id)`
    gives this for free by excluding NULL."""
    ids = await _seed(record, 2)
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=ids[0], person_id="miles", canonical_form="kam"),
        observation(message_id=ids[1], person_id=None, canonical_form="kam"),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    [lex] = (await store.room_language(scope_id="g1", limit=10)).lexemes
    assert (lex.occurrence_count, lex.distinct_speakers) == (2, 1)


async def test_two_senses_of_one_word_are_two_rows(store, record):
    """`sense` is in the primary key. Without that this silently overwrites."""
    ids = await _seed(record, 2)
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=ids[0], canonical_form="firm", sense="正/掂"),
        observation(message_id=ids[1], canonical_form="firm", sense="堅硬"),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    lexemes = (await store.room_language(scope_id="g1", limit=10)).lexemes
    assert {lx.sense for lx in lexemes} == {"正/掂", "堅硬"}


async def test_stale_entries_do_not_render(store, record):
    """`state_for` returns `stale` past 120 days of silence, and `room_language`
    filters to `live`/`fading` only — the far-future `now_ms` simulates a word
    nobody has said in a long time, against a message seeded at `NOW`."""
    [mid] = await _seed(record, 1)
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=mid, canonical_form="kam", sense="尷"),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW + 200 * DAY_MS)
    assert not (await store.room_language(scope_id="g1", limit=10)).lexemes


async def test_fading_entries_still_render(store, record):
    [mid] = await _seed(record, 1)
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=mid, canonical_form="kam", sense="尷"),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW + 30 * DAY_MS)
    [lex] = (await store.room_language(scope_id="g1", limit=10)).lexemes
    assert lex.state == "fading"


async def test_room_language_is_empty_for_an_unknown_scope(store):
    assert not await store.room_language(scope_id="nobody", limit=10)


async def test_room_language_orders_by_recognition_score_descending(store, record):
    """The store owns ordering — `RoomLexeme` carries no score field, so
    nothing downstream can re-sort. A word four people share must outrank a
    word one person repeats, exactly as `scoring`'s own weighting intends."""
    tic_ids = await _seed(record, 10)
    shared_ids = await _seed(record, 4)
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=mid, person_id="miles", canonical_form="tic")
        for mid in tic_ids
    ])
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=mid, person_id=f"p{i}", canonical_form="shared")
        for i, mid in enumerate(shared_ids)
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    lexemes = (await store.room_language(scope_id="g1", limit=10)).lexemes
    assert [lx.canonical_form for lx in lexemes] == ["shared", "tic"]


async def test_room_language_honours_its_limit(store, record):
    ids = await _seed(record, 5)
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=mid, canonical_form=f"word{i}", sense="s")
        for i, mid in enumerate(ids)
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    lexemes = (await store.room_language(scope_id="g1", limit=2)).lexemes
    assert len(lexemes) == 2


async def test_room_language_carries_examples_with_attribution(store, record):
    """Finding 1 (final whole-branch review): the rendered example must be
    the message's own BODY, never `LexemeCandidate.surface_form` (the
    model's own echo of the token it saw). `surface_form` here is
    deliberately the bare token production actually sends ("kam"), and the
    seeded message body is a full, DIFFERENT sentence — so this only passes
    if `context` is drawn from `messages.body`. Before the fix, the store
    stamped `context` from `surface_form`, and this test used a `body`
    reading identically to the `surface_form` under test
    (`"好撚kam"` for both), which is exactly why it stayed green through that
    bug: the two values could never be told apart from a passing assertion
    alone."""
    body = "the whole group went quiet, then someone said 好撚kam and it stuck"
    mid = await record.append(message=_msg(conv="g1", body=body))
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=mid, person_id="miles", canonical_form="kam",
                    surface_form="kam"),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    [lex] = (await store.room_language(scope_id="g1", limit=10)).lexemes
    [example] = lex.examples
    assert (example.message_id, example.person_id, example.text) == (mid, "miles", body)


async def test_room_language_caps_examples_and_orders_most_recent_first(store, record):
    """Task 9's renderer needs ONE example per rendered entry, not the whole
    occurrence history — a word said many times must not carry every usage
    into the rendered block, or a JSONB blob that grows without bound into
    every turn's `room_language` read.

    `_seed`'s own bodies ("seed0".."seed4") are what this asserts against
    now — `context` is the message BODY (Finding 1), not `surface_form`, so
    ordering must be provable against the body text, not an
    observation-supplied token."""
    ids = await _seed(record, 5, step=1000)  # strictly increasing ts
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=mid, person_id="miles", canonical_form="kam")
        for mid in ids
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    [lex] = (await store.room_language(scope_id="g1", limit=10)).lexemes
    assert len(lex.examples) == 3
    assert [e.text for e in lex.examples] == ["seed4", "seed3", "seed2"]


async def test_rescore_is_idempotent_and_does_not_inflate_counts(store, record):
    """Running `rescore` twice over the same evidence must not double the
    occurrence count — it is a full recompute, not an accumulator."""
    [mid] = await _seed(record, 1)
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=mid, canonical_form="kam", sense="尷"),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    await store.rescore(scope_id="g1", now_ms=NOW)
    [lex] = (await store.room_language(scope_id="g1", limit=10)).lexemes
    assert lex.occurrence_count == 1


async def test_rescore_over_no_evidence_is_a_silent_no_op(store):
    await store.rescore(scope_id="empty-scope", now_ms=NOW)
    assert not await store.room_language(scope_id="empty-scope", limit=10)


async def test_rescore_lets_a_word_fade_with_no_new_observations(store, record):
    """Decay is time-driven: `_dream_once` calls `consolidate` (and therefore
    `rescore`) even over an EMPTY batch, specifically so a quiet scope's
    lexicon can still age out. Calling `rescore` again with a later `now_ms`
    and no new evidence must be enough to move `live` -> `fading`."""
    [mid] = await _seed(record, 1)
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=mid, canonical_form="kam", sense="尷"),
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
    """`watermark=0` is a scope that has never consolidated anything yet, and
    the `watermark <= 0` guard returns `False` for it unconditionally —
    regardless of where this conversation's own rows happen to sit in the
    shared global id sequence (see `watermark_is_behind_the_record`'s own
    docstring, and `test_watermark_is_behind_the_record_is_false_on_the_
    first_tick_of_an_ordinary_room` below for the case that guard exists to
    prevent)."""
    await record.append(message=_msg())
    assert await store.watermark_is_behind_the_record(
        scope_id="g1", watermark=0) is False


async def test_watermark_is_behind_the_record_is_true_when_rows_were_pruned_unread(store):
    """Simulates `store.prune_enabled` deleting rows 1-6 while the watermark
    still sits at 3: rows 4-6 were never read and are now gone. Inserted
    directly against the schema table so the id sequence starts at 7 without
    six throwaway rows going through `SqlRecordStore`."""
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
    """`watermark=1`, not `0` — a nonzero watermark is what makes this test
    actually reach the per-conversation query rather than short-circuiting on
    the `watermark <= 0` guard, so it genuinely exercises whether `g2`'s row
    leaks into `g1`'s (empty) result."""
    await record.append(message=_msg(conv="g2", body="other room"))
    assert await store.watermark_is_behind_the_record(
        scope_id="g1", watermark=1) is False


async def test_watermark_is_behind_the_record_is_not_masked_by_a_surviving_nora_row(
    store, record,
):
    """A NORA row surviving at the front of a conversation's UNREAD range
    must not mask genuine human-evidence loss behind it. Before the
    `is_nora == 0` predicate, `MIN(id)` over EVERY surviving row (Nora's
    included) could stay pinned at Nora's own untouched row while real human
    evidence just after it (rows 2-4) was pruned unread — and the check would
    never fire, because Nora's row alone made the table look undisturbed.
    `watermark=1`, not `0`: this scope HAS consumed something before (the
    `watermark <= 0` guard below is deliberately not what is under test
    here — that guard's own case is
    `test_watermark_is_behind_the_record_is_false_on_the_first_tick_of_an_
    ordinary_room`)."""
    await record.append(message=_msg(body="hers"), is_nora=True)  # id 1, survives
    for i in range(2, 5):
        await record.append(message=_msg(body=f"lost{i}", ts=i))  # ids 2-4
    async with store._engine.begin() as conn:
        await conn.execute(sa.delete(MESSAGES).where(MESSAGES.c.id.in_([2, 3, 4])))
    await record.append(message=_msg(body="theirs", ts=5))  # id 5, survives
    assert await store.watermark_is_behind_the_record(
        scope_id="g1", watermark=1) is True


async def test_watermark_is_behind_the_record_is_false_on_the_first_tick_of_an_ordinary_room(
    store, record,
):
    """`messages.id` is ONE GLOBAL sequence shared by every conversation and
    sender. For any room other than the very first ever recorded in the whole
    deployment, its oldest human row already has `id >> 1` — so WITHOUT the
    `watermark <= 0` guard, `min_id > 0 + 1` is `True` on the first tick of
    essentially every room, with nothing pruned and nothing lost: not a
    corner case, the guaranteed path for every room in a multi-group
    deployment. `nora_dream_evidence_lost_total` is a MONOTONIC counter, so
    firing it once per room on an ordinary first tick makes it permanently
    non-zero — the one signal meaning "evidence was pruned unread" could
    never again be read as evidence of anything. Ten messages in an unrelated
    conversation push the shared sequence forward before `g1`'s own first
    (and only, nothing pruned) message ever lands, simulating exactly that."""
    for _ in range(10):
        await record.append(message=_msg(conv="other-room"))
    await record.append(message=_msg(conv="g1"))  # g1's own oldest row, id 11
    assert await store.watermark_is_behind_the_record(
        scope_id="g1", watermark=0) is False


# ---------------------------------------------------------------------------
# instruction provenance (2026-08-20 instruction-substrate spec §4.3)
#
# `room_lexeme` holds a model-authored `sense` and, until this column, no
# record of the instruction that asked for it — which is why the `big walk`
# gloss cannot be attributed to a revision. These tests are the ROW half of
# the guarantee; `tests/unit/test_language_observer.py`'s own provenance block
# is the writer half.
# ---------------------------------------------------------------------------


async def _stamps(store) -> list:
    """Every `language_observation.instruction_sha256` in insertion order.
    Read straight off the table — no reader surfaces this column, and none
    should: provenance is for an operator auditing the corpus, never for the
    rendered block."""
    async with store._engine.connect() as conn:
        return list((await conn.execute(
            sa.select(LANGUAGE_OBSERVATION.c.instruction_sha256)
            .order_by(LANGUAGE_OBSERVATION.c.id)
        )).scalars())


async def test_an_appended_observation_carries_its_instruction_stamp(store, record):
    [mid] = await _seed(record, 1)
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=mid, instruction=INSTRUCTION_A),
    ])
    assert await _stamps(store) == [INSTRUCTION_A]


async def test_two_instruction_revisions_are_distinguishable_in_the_evidence(
    store, record,
):
    """The question §3.3 needs answerable: given a corpus, which rows were
    written by which revision. Rows written either side of an instruction edit
    must not read the same."""
    ids = await _seed(record, 2)
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=ids[0], canonical_form="kam", instruction=INSTRUCTION_A),
        observation(message_id=ids[1], canonical_form="bik", instruction=INSTRUCTION_B),
    ])
    assert sorted(await _stamps(store)) == sorted([INSTRUCTION_A, INSTRUCTION_B])


async def test_the_consolidated_lexeme_inherits_the_newest_observations_stamp(
    store, record,
):
    """`rescore` reaches no model, so the honest stamp on `room_lexeme` is the
    one carried by the evidence that most recently produced the gloss — the
    same row `last_seen_at` reports. Two observations of one word, a day
    apart, written under two revisions: the row must name the LATER one, the
    instruction currently keeping the gloss alive."""
    ids = await _seed(record, 2, ts=NOW - DAY_MS, step=DAY_MS)
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=ids[0], canonical_form="kam", instruction=INSTRUCTION_A),
        observation(message_id=ids[1], canonical_form="kam", instruction=INSTRUCTION_B),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    async with store._engine.connect() as conn:
        row = (await conn.execute(sa.select(ROOM_LEXEME))).one()
    assert row.occurrence_count == 2, "both observations consolidated into one row"
    assert row.instruction_sha256 == INSTRUCTION_B


async def test_a_rescored_lexeme_never_loses_its_stamp(store, record):
    """`rescore` runs on every tick, including ticks that append nothing. A
    row that arrived stamped must not come back NULL — that would turn «this
    revision wrote it» into «nobody recorded it» through pure decay."""
    [mid] = await _seed(record, 1)
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=mid, instruction=INSTRUCTION_A),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    await store.rescore(scope_id="g1", now_ms=NOW + DAY_MS)
    async with store._engine.connect() as conn:
        row = (await conn.execute(sa.select(ROOM_LEXEME))).one()
    assert row.instruction_sha256 == INSTRUCTION_A


async def test_the_database_refuses_a_stamp_that_is_not_a_digest(store, record):
    """Fail-closed, structurally: a version string or an empty string would be
    unverifiable provenance that still LOOKS stamped. `alembic check` is blind
    to CHECK constraints, so this is what proves the constraint landed."""
    [mid] = await _seed(record, 1)
    with pytest.raises(sa.exc.IntegrityError):
        await store.append_observations(scope_id="g1", observations=[
            observation(message_id=mid, instruction="v1"),
        ])


# ---------------------------------------------------------------------------
# model provenance (2026-08-20 substrate spec §6.2 question 2, decided in
# favour the same day) — the second half of the pair above. The instruction
# is one input to a row; the model that read it is another, and
# `[language].model` has changed before, so a corpus split by revision alone
# silently mixes two writers.
# ---------------------------------------------------------------------------


async def _models(store) -> list:
    """Every `language_observation.model` in insertion order — read straight
    off the table, for the reason `_stamps` gives (no reader surfaces
    provenance and none should)."""
    async with store._engine.connect() as conn:
        return list((await conn.execute(
            sa.select(LANGUAGE_OBSERVATION.c.model)
            .order_by(LANGUAGE_OBSERVATION.c.id)
        )).scalars())


async def test_an_appended_observation_carries_the_model_that_produced_it(
    store, record,
):
    [mid] = await _seed(record, 1)
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=mid, model=MODEL_A),
    ])
    assert await _models(store) == [MODEL_A]


async def test_two_models_are_distinguishable_under_one_instruction(store, record):
    """The case `instruction_sha256` alone cannot see, and the whole reason
    this column exists: the SAME instruction text run against two models. If
    the model were inferred from the instruction — or defaulted from config —
    these two rows would read identically."""
    ids = await _seed(record, 2)
    await store.append_observations(scope_id="g1", observations=[
        observation(
            message_id=ids[0], canonical_form="kam",
            instruction=INSTRUCTION_A, model=MODEL_A,
        ),
        observation(
            message_id=ids[1], canonical_form="bik",
            instruction=INSTRUCTION_A, model=MODEL_B,
        ),
    ])
    assert sorted(await _models(store)) == sorted([MODEL_A, MODEL_B])
    assert await _stamps(store) == [INSTRUCTION_A, INSTRUCTION_A], (
        "one instruction, two models — the digests must NOT have moved"
    )


async def test_the_consolidated_lexeme_inherits_the_newest_observations_model(
    store, record,
):
    """`rescore` reaches no model, so `room_lexeme.model` is inherited from
    the evidence that most recently produced the gloss — the same `rn = 1`
    row `last_seen_at` and `instruction_sha256` already name."""
    ids = await _seed(record, 2, ts=NOW - DAY_MS, step=DAY_MS)
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=ids[0], canonical_form="kam", model=MODEL_A),
        observation(message_id=ids[1], canonical_form="kam", model=MODEL_B),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    async with store._engine.connect() as conn:
        row = (await conn.execute(sa.select(ROOM_LEXEME))).one()
    assert row.occurrence_count == 2, "both observations consolidated into one row"
    assert row.model == MODEL_B


async def test_the_consolidated_lexemes_two_provenance_fields_name_one_row(
    store, record,
):
    """The pair must be inherited TOGETHER or they describe a run that never
    happened. Three observations of one word, engineered so that «newest
    model» and «newest instruction» would disagree if either were taken from
    anywhere but the one `rn = 1` row: the middle observation is the only one
    pairing B with B, and the newest pairs A with B. Taking each field from
    its own lookup — most recent non-null, most common, anything but the same
    row — yields (B, A) or (A, A); only the shared `latest` CTE yields the
    pairing that was actually run."""
    ids = await _seed(record, 3, ts=NOW - 2 * DAY_MS, step=DAY_MS)
    await store.append_observations(scope_id="g1", observations=[
        observation(
            message_id=ids[0], canonical_form="kam", sense="s1",
            instruction=INSTRUCTION_B, model=MODEL_A,
        ),
        observation(
            message_id=ids[1], canonical_form="kam", sense="s1",
            instruction=INSTRUCTION_B, model=MODEL_B,
        ),
        observation(
            message_id=ids[2], canonical_form="kam", sense="s1",
            instruction=INSTRUCTION_A, model=MODEL_B,
        ),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    async with store._engine.connect() as conn:
        row = (await conn.execute(sa.select(ROOM_LEXEME))).one()
        newest = (await conn.execute(
            sa.select(LANGUAGE_OBSERVATION.c.instruction_sha256, LANGUAGE_OBSERVATION.c.model)
            .order_by(LANGUAGE_OBSERVATION.c.observed_at.desc())
            .limit(1)
        )).one()
    assert row.occurrence_count == 3
    assert (row.instruction_sha256, row.model) == (newest.instruction_sha256, newest.model)
    assert (row.instruction_sha256, row.model) == (INSTRUCTION_A, MODEL_B)


async def test_a_rescored_lexeme_never_loses_its_model(store, record):
    """`rescore` runs on every tick, including ticks that append nothing — a
    row that arrived attributed must not decay back to «nobody recorded it»."""
    [mid] = await _seed(record, 1)
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=mid, model=MODEL_A),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    await store.rescore(scope_id="g1", now_ms=NOW + DAY_MS)
    async with store._engine.connect() as conn:
        row = (await conn.execute(sa.select(ROOM_LEXEME))).one()
    assert row.model == MODEL_A


async def test_the_database_refuses_a_model_that_names_nothing(store, record):
    """The CHECK is NOT the digest's shape check — a model name has no
    provable form — but it does refuse the one thing that is provably wrong:
    a value that reads as recorded and names nothing. `''` is non-NULL to
    every query asking which rows are attributable, which turns R-27's
    `unavailable` into a false `ok`. `alembic check` is blind to CHECK
    constraints, so this is what proves it landed."""
    [mid] = await _seed(record, 1)
    with pytest.raises(sa.exc.IntegrityError):
        await store.append_observations(scope_id="g1", observations=[
            observation(message_id=mid, model=""),
        ])


async def test_the_database_refuses_a_whitespace_only_model(store, record):
    """`'   '` is the same defect wearing a disguise — non-NULL, non-empty by
    `length()`, and naming nothing. `btrim` in the CHECK is what closes it."""
    [mid] = await _seed(record, 1)
    with pytest.raises(sa.exc.IntegrityError):
        await store.append_observations(scope_id="g1", observations=[
            observation(message_id=mid, model="   "),
        ])


# ---------------------------------------------------------------------------
# the correction loop reaches this tier too — 31 §6
# ---------------------------------------------------------------------------


async def test_a_dispute_marks_every_sense_of_the_form(store, record, pg_url):
    """`room_lexeme`'s key includes `sense`, deliberately: a word acquiring a
    second meaning gets a second row rather than overwriting the first.

    So a member saying "no, big walk is a video game" is disputing the FORM.
    Marking one sense would leave its sibling standing and unmarked — and that
    sibling is exactly the thing that let two contradictory glosses coexist in
    the first place, which is the failure this whole loop exists for."""
    ids = await _seed(record, 2)
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=ids[0], person_id="miles",
                    canonical_form="big walk", sense="a walk"),
        observation(message_id=ids[1], person_id="alice",
                    canonical_form="Big  Walk", sense="a video game"),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)

    marked = await store.mark_disputed(
        scope_id="g1", surface="BIG WALK", person_id="grant",
        reason="it's a game", tag_id="tag-9", now_ms=NOW,
    )

    # Both senses, matched on the NORMALISED form — `big walk` and `Big  Walk`
    # are one name written two ways, which is what `normalise_name` is for.
    assert marked == 2
    engine = create_sql_engine(pg_url)
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(sa.select(
                ROOM_LEXEME.c.disputed_at, ROOM_LEXEME.c.disputed_by,
            ))).all()
    finally:
        await engine.dispose()
    assert [r.disputed_at for r in rows] == [NOW, NOW]
    assert {r.disputed_by for r in rows} == {"grant"}


async def test_a_dispute_does_not_reach_a_different_form(store, record, pg_url):
    """The positive control for the test above: a mark that hit everything
    would satisfy it while making the loop unable to dispute anything
    specifically."""
    ids = await _seed(record, 2)
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=ids[0], person_id="miles",
                    canonical_form="big walk", sense="a walk"),
        observation(message_id=ids[1], person_id="alice",
                    canonical_form="打風", sense="a typhoon"),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)

    assert await store.mark_disputed(
        scope_id="g1", surface="big walk", person_id="g", reason=None,
        tag_id="t", now_ms=NOW,
    ) == 1


async def test_rescore_does_not_wipe_a_dispute(store, record, pg_url):
    """**The one that matters, and it holds by accident of a list.**

    `rescore` recomputes this table from `language_observation` on every
    consolidation, and its upsert names its `set_` columns explicitly — so the
    four dispute columns survive because they are absent from that list, not
    because anything says they must be. Adding `disputed_at` to it would erase
    every correction the room has made, silently, on the next tick.

    This is the test that says so."""
    ids = await _seed(record, 2)
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=ids[0], person_id="miles",
                    canonical_form="big walk", sense="a walk"),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    await store.mark_disputed(
        scope_id="g1", surface="big walk", person_id="grant",
        reason="it's a game", tag_id="tag-9", now_ms=NOW,
    )

    # A second consolidation over new evidence — the ordinary path.
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=ids[1], person_id="alice",
                    canonical_form="big walk", sense="a walk"),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW + 1_000)

    engine = create_sql_engine(pg_url)
    try:
        async with engine.connect() as conn:
            row = (await conn.execute(sa.select(
                ROOM_LEXEME.c.disputed_at, ROOM_LEXEME.c.disputed_by,
                ROOM_LEXEME.c.occurrence_count,
            ))).one()
    finally:
        await engine.dispose()
    assert row.disputed_at == NOW
    assert row.disputed_by == "grant"
    # The positive control: the rescore DID run and DID update the row, so the
    # surviving dispute is not simply an upsert that never fired.
    assert row.occurrence_count == 2


async def test_a_second_dispute_leaves_the_first_asserter(store, record, pg_url):
    """Matching the fact tier: the first asserter is the one that holds, and a
    second dispute is recorded on the candidate it proposes."""
    ids = await _seed(record, 1)
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=ids[0], person_id="miles",
                    canonical_form="big walk", sense="a walk"),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    await store.mark_disputed(
        scope_id="g1", surface="big walk", person_id="grant", reason=None,
        tag_id="t1", now_ms=NOW,
    )

    assert await store.mark_disputed(
        scope_id="g1", surface="big walk", person_id="vic", reason=None,
        tag_id="t2", now_ms=NOW + 1,
    ) == 0


async def test_room_language_carries_the_dispute_out_of_the_store(store, record):
    """**The read side of 31 §6, and it did not exist.**

    `mark_disputed` wrote four columns and `room_language` selected the whole
    row and built a `RoomLexeme` that had nowhere to put them — so the mark
    was write-only: the correction landed in Postgres, the counter said `ok`,
    and the prefill kept asserting the contradicted gloss. A round trip
    through the real store is the only thing that can say otherwise; the
    render test one layer up is satisfied by a dataclass nobody populates.
    """
    ids = await _seed(record, 2)
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=ids[0], person_id="miles",
                    canonical_form="big walk", sense="a walk"),
        observation(message_id=ids[1], person_id="alice",
                    canonical_form="打風", sense="a typhoon"),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    await store.mark_disputed(
        scope_id="g1", surface="big walk", person_id="grant",
        reason="it's a game", tag_id="tag-9", now_ms=NOW,
    )

    language = await store.room_language(scope_id="g1", limit=10)

    by_form = {lex.canonical_form: lex for lex in language.lexemes}
    disputed = by_form["big walk"]
    assert disputed.is_disputed
    assert disputed.disputed_at == NOW
    assert disputed.disputed_by == "grant"
    assert disputed.disputed_reason == "it's a game"
    # The positive control, and it is the half a blanket read would fail: the
    # OTHER lexeme in the same scope came back undisputed, so this is the row
    # the correction named rather than every row the query returned.
    assert not by_form["打風"].is_disputed
