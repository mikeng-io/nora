"""`RoomLanguageReader` plus the Dreamer's write side, over SQLAlchemy Core.

Three tables, three jobs (`b974afe`, `nora/infra/sql/schema.py`):
`consolidation_watermark` is the cursor — how far each `(consolidator_name,
scope_id)` has read `messages`; `language_observation` is append-only EVIDENCE,
one row per extracted usage, natural-keyed `(message_id, canonical_form,
sense)` so a retried or overlapping cycle cannot double-count; `room_lexeme` is
the CONSOLIDATED lexicon `room_language` actually reads, keyed `(scope_id,
canonical_form, sense)` so a word acquiring a second sense gets a second row
rather than overwriting or merging into the first.

`rescore` is a full recompute over every surviving observation for one scope,
not an incremental update — the same shape `SqlRecordStore.prune_all` favours
over a running counter (`docs/superpowers/plans/2026-08-15-...md`'s own
argument for `_dream_once`: "Rescore even when nothing was appended: decay is
time-driven, so a quiet scope must still be able to let a word fade"). A
running counter cannot fade a word nobody has mentioned since; reading the
evidence table fresh every time can, because RECENCY is a function of `now_ms`
alone and every surviving observation is already there to recompute from. The
grouping — `occurrence_count`, `distinct_speakers` via `COUNT(DISTINCT
person_id)`, `first_seen_at`/`last_seen_at`, and a CAPPED, most-recent-first
`examples` array — runs IN SQL, not in Python: `idx_language_observation_scope`
(`0014_lang_obs_scope_idx.py`) makes the `WHERE scope_id = :s`
half indexed, and pushing the `GROUP BY`/`ROW_NUMBER` down means this reads
only the aggregates and the top few examples per lexeme off the wire, not
every raw observation a popular word has ever produced. `score`/`state_for`
still run in PYTHON, once per GROUP (never per raw row) — pushing the
arithmetic itself into SQL would be reimplementing it a second time, which
`nora.domain.language_scoring` exists to prevent.

Two decisions worth being explicit about, because nothing in the interface
enforces either and a later reader would otherwise have to reverse-engineer
them from behaviour:

* `conversation_id` AND `observed_at` on `language_observation` are both read
  from the real `messages` row, joined by `LexemeCandidate.message_id` — never
  guessed or stamped from this process's own clock. `append_observations`'s
  fixed signature (`Sequence[LexemeCandidate]`, no batch of `TranscriptRow`)
  carries neither field directly, but `message_id` IS a real `messages.id`,
  and this store already queries `MESSAGES` elsewhere (`lag`,
  `seconds_since_last_message`, `watermark_is_behind_the_record`) — so the
  INSERT is built as `INSERT ... SELECT ... FROM messages WHERE id =
  :message_id`, one `UNION ALL` branch per candidate. A candidate whose
  `message_id` no longer resolves (the row was pruned) contributes NOTHING —
  the same evidence-grounding rule `LexemeObserver._extract` already applies
  to a hallucinated `message_id`, extended to survive one more hop. This
  matters far past tidiness: `observed_at` is the ONLY input to
  `first_seen_at`/`last_seen_at`, which drive `scoring.score`'s recency term
  and the `live`/`fading`/`stale` decision — i.e. whether a lexeme renders at
  all. Stamping the process clock instead would have made the FIRST dream
  cycle on any real room (`watermark_for` returns `0` for an unseen pair)
  consolidate months of history in one batch with every lexeme's
  `last_seen_at` set to "now" — rendering everything `live` with a recency
  that never happened, unable to fade for 21 days — and the identical failure
  on any catch-up after downtime.
* `context` is stamped from `LexemeCandidate.surface_form`
  ("what the model actually saw on the wire", `LexemeCandidate`'s own
  docstring), not from the source message's own `body`. It is the one
  verbatim-ish fragment a candidate carries — `canonical_form`/`sense`/
  `part_of_speech` are all model-authored by contrast — and the NOT NULL
  column has nothing else to draw on without a second join this store has no
  present reason to make.

`score`/`state_for` live in `nora.domain.language_scoring`, not
`nora.language.scoring` where Task 5 first put them (`0a940c8`) — `nora.infra`
cannot import `nora.language` at all (the layers contract; verified with `uv
run lint-imports`, which reports `nora.infra is not allowed to import
nora.language`), the same reason `LexemeCandidate` lives in `nora.domain`
rather than in either package that needs it. The old `nora/language/scoring.py`
is deleted outright rather than kept as a re-export shim: nothing in this repo
ever imported it (only `tests/unit/test_language_scoring.py`, itself moved to
import `nora.domain.language_scoring` directly), so a shim would have bought a
second import path for zero consumers.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.language_scoring import score, state_for
from nora.domain.models import LexemeCandidate, LexemeExample, RoomLanguage, RoomLexeme
from nora.infra.sql.schema import (
    CONSOLIDATION_WATERMARK,
    LANGUAGE_OBSERVATION,
    MESSAGES,
    ROOM_LEXEME,
)

log = logging.getLogger(__name__)

_RENDERED_STATES = ("live", "fading")

# Task 9's renderer needs ONE example per rendered entry, not the whole
# occurrence history — a word said 5,000 times must not rewrite a 5,000-element
# JSONB on every tick and deserialize it in full on every turn's `room_language`
# read. The plan caps rendered ENTRIES for exactly this cost reason ("this
# block sits in the UNCACHED tier — so the cap is a cost control, not
# tidiness"); the same argument applies to examples within one entry.
_EXAMPLES_PER_LEXEME_CAP = 3


def _now_ms() -> int:
    return int(time.time() * 1000)


class SqlLanguageStore:
    """Satisfies `nora.domain.ports.RoomLanguageReader`; also the Dreamer's
    write side and cursor (`RoomConsolidator`'s companion durability, consumed
    by `nora.language.consolidator.LanguageConsolidator` and `dream_loop`).
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def append_observations(
        self, *, scope_id: str, observations: Sequence[LexemeCandidate]
    ) -> int:
        """ONE `INSERT ... SELECT ... ON CONFLICT DO NOTHING`, natural-keyed on
        `(message_id, canonical_form, sense)` — the same key
        `language_observation`'s own `UniqueConstraint` declares. A retried or
        overlapping consolidation cycle re-appending the same candidates
        therefore inserts nothing the second time; `result.rowcount` on an
        `ON CONFLICT DO NOTHING` statement is exactly the count of rows that
        actually landed, which is what a caller needs to know "did this add
        anything" without a second query.

        The SELECT half — one `UNION ALL` branch per candidate, each joined to
        `messages` by `WHERE id = :message_id` — is what supplies
        `conversation_id` and `observed_at` from the REAL message row rather
        than a guess or this process's own clock (see module docstring). A
        candidate whose `message_id` no longer resolves contributes no row at
        all, the same fail-closed drop `LexemeObserver._extract` already
        applies to a hallucinated `message_id`.
        """
        if not observations:
            return 0
        branches = [
            sa.select(
                sa.literal(scope_id, type_=sa.Text).label("scope_id"),
                MESSAGES.c.conversation_id.label("conversation_id"),
                MESSAGES.c.id.label("message_id"),
                sa.literal(obs.person_id, type_=sa.Text).label("person_id"),
                sa.literal(obs.surface_form, type_=sa.Text).label("surface_form"),
                sa.literal(obs.canonical_form, type_=sa.Text).label("canonical_form"),
                sa.literal(obs.sense, type_=sa.Text).label("sense"),
                sa.literal(obs.part_of_speech, type_=sa.Text).label("part_of_speech"),
                sa.literal(obs.surface_form, type_=sa.Text).label("context"),
                MESSAGES.c.ts.label("observed_at"),
                sa.literal(obs.confidence, type_=sa.Float).label("extractor_confidence"),
            ).where(MESSAGES.c.id == obs.message_id)
            for obs in observations
        ]
        combined = sa.union_all(*branches) if len(branches) > 1 else branches[0]
        stmt = (
            pg.insert(LANGUAGE_OBSERVATION)
            .from_select(
                [
                    "scope_id", "conversation_id", "message_id", "person_id",
                    "surface_form", "canonical_form", "sense", "part_of_speech",
                    "context", "observed_at", "extractor_confidence",
                ],
                combined,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    LANGUAGE_OBSERVATION.c.message_id,
                    LANGUAGE_OBSERVATION.c.canonical_form,
                    LANGUAGE_OBSERVATION.c.sense,
                ]
            )
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return int(result.rowcount or 0)

    async def rescore(self, *, scope_id: str, now_ms: int) -> None:
        """Full recompute over every surviving observation for `scope_id`,
        grouped by `(canonical_form, sense)` — see module docstring for why
        this is a recompute, not an incremental update, and for why the
        grouping itself runs in SQL rather than by fetching every raw row.

        `distinct_speakers` is `COUNT(DISTINCT person_id)`, which already
        excludes NULL. An unmapped sender (`person_id IS NULL`,
        `nora.infra.sql.audit.py:210`'s own case) still counts toward
        `occurrence_count` — the word was genuinely said — but never toward
        `distinct_speakers`: nobody can name who that speaker WAS, so counting
        them as one more distinct person would be a claim with no identity
        behind it. The rendered "N people" stays a real count of known people.

        `examples` is capped at `_EXAMPLES_PER_LEXEME_CAP`, most-recent-first:
        a `ROW_NUMBER() OVER (PARTITION BY canonical_form, sense ORDER BY
        observed_at DESC)` window ranks each group's own rows, filtered to
        `rn <= cap` before `jsonb_agg` builds the array — `aggregate_order_by`
        makes the ordering explicit rather than relying on incidental row
        arrival order, which `jsonb_agg` does not guarantee on its own.
        """
        LO = LANGUAGE_OBSERVATION  # noqa: N806 — local alias, three CTEs read it
        ranked = (
            sa.select(
                LO.c.canonical_form,
                LO.c.sense,
                LO.c.message_id,
                LO.c.person_id,
                LO.c.surface_form,
                LO.c.observed_at,
                sa.func.row_number()
                .over(
                    partition_by=(LO.c.canonical_form, LO.c.sense),
                    order_by=LO.c.observed_at.desc(),
                )
                .label("rn"),
            )
            .where(LO.c.scope_id == scope_id)
            .cte("ranked")
        )
        aggregated = (
            sa.select(
                LO.c.canonical_form,
                LO.c.sense,
                sa.func.count().label("occurrence_count"),
                sa.func.count(sa.distinct(LO.c.person_id)).label("distinct_speakers"),
                sa.func.min(LO.c.observed_at).label("first_seen_at"),
                sa.func.max(LO.c.observed_at).label("last_seen_at"),
            )
            .where(LO.c.scope_id == scope_id)
            .group_by(LO.c.canonical_form, LO.c.sense)
            .cte("aggregated")
        )
        example_obj = sa.func.jsonb_build_object(
            "message_id", ranked.c.message_id,
            "person_id", ranked.c.person_id,
            "text", ranked.c.surface_form,
        )
        examples_cte = (
            sa.select(
                ranked.c.canonical_form,
                ranked.c.sense,
                sa.func.jsonb_agg(
                    aggregate_order_by(example_obj, ranked.c.observed_at.desc())
                ).label("examples"),
            )
            .where(ranked.c.rn <= _EXAMPLES_PER_LEXEME_CAP)
            .group_by(ranked.c.canonical_form, ranked.c.sense)
            .cte("examples")
        )
        select_stmt = sa.select(
            aggregated.c.canonical_form,
            aggregated.c.sense,
            aggregated.c.occurrence_count,
            aggregated.c.distinct_speakers,
            aggregated.c.first_seen_at,
            aggregated.c.last_seen_at,
            examples_cte.c.examples,
        ).select_from(
            aggregated.join(
                examples_cte,
                sa.and_(
                    aggregated.c.canonical_form == examples_cte.c.canonical_form,
                    aggregated.c.sense == examples_cte.c.sense,
                ),
            )
        )

        async with self._engine.begin() as conn:
            rows = (await conn.execute(select_stmt)).all()
            if not rows:
                return

            upsert_rows = []
            for r in rows:
                recognition_score = score(
                    occurrences=r.occurrence_count,
                    distinct_speakers=r.distinct_speakers,
                    last_seen_ms=r.last_seen_at,
                    now_ms=now_ms,
                )
                state = state_for(
                    score=recognition_score, last_seen_ms=r.last_seen_at, now_ms=now_ms
                )
                upsert_rows.append({
                    "scope_id": scope_id,
                    "canonical_form": r.canonical_form,
                    "sense": r.sense,
                    "first_seen_at": r.first_seen_at,
                    "last_seen_at": r.last_seen_at,
                    "occurrence_count": r.occurrence_count,
                    "distinct_speakers": r.distinct_speakers,
                    "recognition_score": recognition_score,
                    "state": state,
                    "examples": r.examples,
                })

            upsert_stmt = pg.insert(ROOM_LEXEME).values(upsert_rows)
            upsert_stmt = upsert_stmt.on_conflict_do_update(
                index_elements=[
                    ROOM_LEXEME.c.scope_id,
                    ROOM_LEXEME.c.canonical_form,
                    ROOM_LEXEME.c.sense,
                ],
                set_={
                    "first_seen_at": upsert_stmt.excluded.first_seen_at,
                    "last_seen_at": upsert_stmt.excluded.last_seen_at,
                    "occurrence_count": upsert_stmt.excluded.occurrence_count,
                    "distinct_speakers": upsert_stmt.excluded.distinct_speakers,
                    "recognition_score": upsert_stmt.excluded.recognition_score,
                    "state": upsert_stmt.excluded.state,
                    "examples": upsert_stmt.excluded.examples,
                },
            )
            await conn.execute(upsert_stmt)

    async def room_language(self, *, scope_id: str, limit: int) -> RoomLanguage:
        """Satisfies `RoomLanguageReader.room_language`: NEVER raises. `except
        Exception` is deliberately broad — not `except ConnectionError` the
        way `_FakeRoomConsolidator._read_lexemes` narrows it in
        `tests/contract/test_room_consolidator_contract.py`, which is correct
        there only because that fake can raise nothing else by construction.
        A real database raises timeouts, pool exhaustion, serialization
        failures, `InterfaceError`, and driver errors that are not
        `ConnectionError` — and this read runs inside `TranscriptInjection`'s
        `asyncio.gather` beside four other reads, where one uncaught
        exception cancels every sibling and silences the whole turn. Degrade
        to an empty `RoomLanguage` and log; never let this be the reason she
        says nothing.
        """
        try:
            stmt = (
                sa.select(ROOM_LEXEME)
                .where(
                    ROOM_LEXEME.c.scope_id == scope_id,
                    ROOM_LEXEME.c.state.in_(_RENDERED_STATES),
                )
                .order_by(ROOM_LEXEME.c.recognition_score.desc())
                .limit(limit)
            )
            async with self._engine.connect() as conn:
                rows = (await conn.execute(stmt)).all()
            lexemes = tuple(
                RoomLexeme(
                    canonical_form=r.canonical_form,
                    sense=r.sense,
                    occurrence_count=r.occurrence_count,
                    distinct_speakers=r.distinct_speakers,
                    first_seen_ms=r.first_seen_at,
                    last_seen_ms=r.last_seen_at,
                    state=r.state,
                    examples=tuple(
                        LexemeExample(
                            message_id=e["message_id"],
                            person_id=e.get("person_id"),
                            text=e["text"],
                        )
                        for e in r.examples
                    ),
                )
                for r in rows
            )
            return RoomLanguage(scope_id=scope_id, lexemes=lexemes, generated_ms=_now_ms())
        except Exception:
            log.exception("room_language unavailable for scope %s", scope_id)
            return RoomLanguage(scope_id=scope_id)

    async def watermark_for(self, *, consolidator_name: str, scope_id: str) -> int:
        stmt = sa.select(CONSOLIDATION_WATERMARK.c.last_message_id).where(
            CONSOLIDATION_WATERMARK.c.consolidator_name == consolidator_name,
            CONSOLIDATION_WATERMARK.c.scope_id == scope_id,
        )
        async with self._engine.connect() as conn:
            val = (await conn.execute(stmt)).scalar_one_or_none()
        return int(val) if val is not None else 0

    async def advance_watermark(
        self, *, consolidator_name: str, scope_id: str, last_message_id: int
    ) -> None:
        stmt = (
            pg.insert(CONSOLIDATION_WATERMARK)
            .values(
                consolidator_name=consolidator_name,
                scope_id=scope_id,
                last_message_id=last_message_id,
                updated_at=sa.func.now(),
            )
            .on_conflict_do_update(
                index_elements=[
                    CONSOLIDATION_WATERMARK.c.consolidator_name,
                    CONSOLIDATION_WATERMARK.c.scope_id,
                ],
                set_={"last_message_id": last_message_id, "updated_at": sa.func.now()},
            )
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def watermark_age_seconds(
        self, *, consolidator_name: str, scope_id: str
    ) -> float:
        """`inf` when this `(consolidator_name, scope_id)` has never been
        watermarked — the honest reading of "how long since this last ran" for
        a pair that has never run is "arbitrarily long ago", and `_dream_once`
        relies on exactly that: `age >= max_age_seconds` is one of its three
        fire conditions, deliberately "at lag == 0 on purpose"
        (`docs/superpowers/plans/2026-08-15-...md`), so a scope's very first
        tick must fire even with nothing unread yet.
        """
        stmt = sa.select(CONSOLIDATION_WATERMARK.c.updated_at).where(
            CONSOLIDATION_WATERMARK.c.consolidator_name == consolidator_name,
            CONSOLIDATION_WATERMARK.c.scope_id == scope_id,
        )
        async with self._engine.connect() as conn:
            updated_at = (await conn.execute(stmt)).scalar_one_or_none()
        if updated_at is None:
            return float("inf")
        return float(max((datetime.now(UTC) - updated_at).total_seconds(), 0.0))

    async def lag(self, *, scope_id: str, after_id: int) -> int:
        """`idx_messages_dream_lag` exists for exactly this query."""
        stmt = sa.select(sa.func.count()).select_from(MESSAGES).where(
            MESSAGES.c.conversation_id == scope_id,
            MESSAGES.c.id > after_id,
            MESSAGES.c.is_nora == 0,
        )
        async with self._engine.connect() as conn:
            return int((await conn.execute(stmt)).scalar_one())

    async def seconds_since_last_message(self, *, scope_id: str) -> float:
        """`inf` when the scope has no messages at all — mirrors
        `watermark_age_seconds`'s empty case. Harmless either way at the one
        call site: `_dream_once` only consults this under `lag > 0`, and a
        scope with zero messages ever also has `lag == 0`.
        """
        stmt = sa.select(sa.func.max(MESSAGES.c.ts)).where(
            MESSAGES.c.conversation_id == scope_id
        )
        async with self._engine.connect() as conn:
            latest_ts = (await conn.execute(stmt)).scalar_one_or_none()
        if latest_ts is None:
            return float("inf")
        return float(max(_now_ms() - latest_ts, 0) / 1000.0)

    async def watermark_is_behind_the_record(
        self, *, scope_id: str, watermark: int
    ) -> bool:
        """True when the oldest SURVIVING message's id is more than one past
        `watermark` — i.e. something between what was already read and what
        still exists was deleted before this consolidator ever read it. A
        scope pruned to `id == 7` while the watermark sits at `3` IS behind
        (`7 > 3 + 1`) — rows 4-6 are gone and were never consolidated. No
        surviving rows at all is not "behind" either — there is nothing to
        have lost evidence from.

        `watermark <= 0` returns `False` UNCONDITIONALLY, before the query
        runs at all — this is not an optimisation, it is required for
        correctness. `watermark_for` returns `0` for any scope this
        consolidator has never watermarked, but `messages.id` is ONE GLOBAL
        sequence shared by every conversation and every sender: for any
        conversation other than the very first row ever recorded in the whole
        deployment, its oldest human row already has `id >> 1`. Without this
        early return, `min_id > 0 + 1` is `True` on the first tick of
        essentially every room, with nothing pruned and nothing lost — not a
        corner case, the guaranteed path for every room in a multi-group
        deployment. `nora_dream_evidence_lost_total` is a monotonic counter;
        firing it once per room on an ordinary first tick makes it
        permanently non-zero, and a tell that always fires is not
        fail-closed, it is noise — the one signal that should mean "evidence
        was pruned unread" could never again be read as evidence of anything.
        A scope only has evidence worth losing once IT has actually
        consumed some (`watermark > 0`); before that, "not behind" is simply
        true.

        `is_nora == 0` is part of the (post-guard) predicate for the same
        reason `lag` carries it: the watermark only ever advances over HUMAN
        rows (`RecordStore.since` excludes `is_nora`), so a surviving Nora
        row is not evidence this consolidator could have lost — and the
        predicate is what lets this query use the partial
        `idx_messages_dream_lag` (`(conversation_id, id) WHERE is_nora = 0`)
        instead of scanning the conversation's whole history every tick.
        """
        if watermark <= 0:
            return False
        stmt = sa.select(sa.func.min(MESSAGES.c.id)).where(
            MESSAGES.c.conversation_id == scope_id,
            MESSAGES.c.is_nora == 0,
        )
        async with self._engine.connect() as conn:
            min_id = (await conn.execute(stmt)).scalar_one_or_none()
        if min_id is None:
            return False
        return bool(min_id > watermark + 1)
