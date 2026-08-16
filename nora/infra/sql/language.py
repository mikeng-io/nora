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
alone and every surviving observation is already there to recompute from.

Two decisions worth being explicit about, because nothing in the interface
enforces either and a later reader would otherwise have to reverse-engineer
them from behaviour:

* `conversation_id` on `language_observation` is written as `scope_id`, not
  joined from `messages`. This is not a shortcut — it is what `scope_id` IS in
  V1: `nora.language`'s own dream-loop plan (`_dream_once`) calls
  `record.since(conversation_id=scope_id, ...)`, i.e. every scope this
  consolidator ever runs against already equals a Signal group's
  `conversation_id`. `append_observations`'s fixed signature
  (`Sequence[LexemeCandidate]`, no batch of `TranscriptRow`) gives it nothing
  else to derive `conversation_id` or a timestamp from in any case — see the
  next point.
* `observed_at`/`context` are stamped from THIS process's own clock and from
  `LexemeCandidate.surface_form` respectively, not from the source message's
  own `ts`/`body`. `LexemeObserver.observe` (`nora/language/observer.py`)
  already joins every candidate back to real evidence by `message_id` before
  one reaches here, and `LexemeCandidate` itself carries no timestamp or body
  field to thread through — the fixed `append_observations(*, scope_id,
  observations: Sequence[LexemeCandidate])` signature has no room for one
  either. `observed_at` feeds `scoring.score`'s recency decay, whose half-life
  is 45 DAYS; the gap between a message's own `ts` and the moment its batch
  gets consolidated is bounded by the dream loop's own tick cadence (minutes,
  configured in `LanguageConfig.tick_seconds`), which is immaterial against
  that half-life. `context` — a NOT NULL column — has only `surface_form`
  ("what the model actually saw on the wire", `LexemeCandidate`'s own
  docstring) to draw on; it is the one verbatim-ish fragment a candidate
  carries, `canonical_form`/`sense`/`part_of_speech` all being model-authored
  by contrast.

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
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
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
        """ONE multi-row `INSERT ... ON CONFLICT DO NOTHING`, natural-keyed on
        `(message_id, canonical_form, sense)` — the same key
        `language_observation`'s own `UniqueConstraint` declares. A retried or
        overlapping consolidation cycle re-appending the same candidates
        therefore inserts nothing the second time; `result.rowcount` on an
        `ON CONFLICT DO NOTHING` statement is exactly the count of rows that
        actually landed, which is what a caller needs to know "did this add
        anything" without a second query.
        """
        if not observations:
            return 0
        now_ms = _now_ms()
        rows = [
            {
                "scope_id": scope_id,
                "conversation_id": scope_id,
                "message_id": obs.message_id,
                "person_id": obs.person_id,
                "surface_form": obs.surface_form,
                "canonical_form": obs.canonical_form,
                "sense": obs.sense,
                "part_of_speech": obs.part_of_speech,
                "context": obs.surface_form,
                "observed_at": now_ms,
                "extractor_confidence": obs.confidence,
            }
            for obs in observations
        ]
        stmt = (
            pg.insert(LANGUAGE_OBSERVATION)
            .values(rows)
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
        this is a recompute, not an incremental update.

        `distinct_speakers` is `len({person_id for ... if person_id is not
        None})` — a Python-side mirror of Postgres's own `COUNT(DISTINCT
        person_id)`, which already excludes NULL. An unmapped sender
        (`person_id IS NULL`, `nora.infra.sql.audit.py:210`'s own case) still
        counts toward `occurrence_count` — the word was genuinely said — but
        never toward `distinct_speakers`: nobody can name who that speaker
        WAS, so counting them as one more distinct person would be a claim
        with no identity behind it, and two unmapped senders in one group
        would silently collapse into "one more person" that isn't true either
        way. The rendered "N people" stays a real count of known people.
        """
        select_stmt = sa.select(
            LANGUAGE_OBSERVATION.c.canonical_form,
            LANGUAGE_OBSERVATION.c.sense,
            LANGUAGE_OBSERVATION.c.message_id,
            LANGUAGE_OBSERVATION.c.person_id,
            LANGUAGE_OBSERVATION.c.surface_form,
            LANGUAGE_OBSERVATION.c.observed_at,
        ).where(LANGUAGE_OBSERVATION.c.scope_id == scope_id)

        async with self._engine.begin() as conn:
            rows = (await conn.execute(select_stmt)).all()
            groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
            for r in rows:
                groups[(r.canonical_form, r.sense)].append(r)

            for (canonical_form, sense), group_rows in groups.items():
                occurrence_count = len(group_rows)
                distinct_speakers = len(
                    {r.person_id for r in group_rows if r.person_id is not None}
                )
                first_seen_at = min(r.observed_at for r in group_rows)
                last_seen_at = max(r.observed_at for r in group_rows)
                recognition_score = score(
                    occurrences=occurrence_count,
                    distinct_speakers=distinct_speakers,
                    last_seen_ms=last_seen_at,
                    now_ms=now_ms,
                )
                state = state_for(
                    score=recognition_score, last_seen_ms=last_seen_at, now_ms=now_ms
                )
                examples = [
                    {"message_id": r.message_id, "person_id": r.person_id, "text": r.surface_form}
                    for r in group_rows
                ]
                upsert_values = {
                    "first_seen_at": first_seen_at,
                    "last_seen_at": last_seen_at,
                    "occurrence_count": occurrence_count,
                    "distinct_speakers": distinct_speakers,
                    "recognition_score": recognition_score,
                    "state": state,
                    "examples": examples,
                }
                upsert_stmt = (
                    pg.insert(ROOM_LEXEME)
                    .values(
                        scope_id=scope_id,
                        canonical_form=canonical_form,
                        sense=sense,
                        **upsert_values,
                    )
                    .on_conflict_do_update(
                        index_elements=[
                            ROOM_LEXEME.c.scope_id,
                            ROOM_LEXEME.c.canonical_form,
                            ROOM_LEXEME.c.sense,
                        ],
                        set_=upsert_values,
                    )
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
        fresh scope with `watermark == 0` and a first surviving row at `id ==
        1` is NOT behind (`1 > 0 + 1` is false); a scope pruned to `id == 7`
        while the watermark sits at `3` IS (`7 > 3 + 1`) — rows 4-6 are gone
        and were never consolidated. No surviving rows at all is not "behind"
        either — there is nothing to have lost evidence from.
        """
        stmt = sa.select(sa.func.min(MESSAGES.c.id)).where(
            MESSAGES.c.conversation_id == scope_id
        )
        async with self._engine.connect() as conn:
            min_id = (await conn.execute(stmt)).scalar_one_or_none()
        if min_id is None:
            return False
        return bool(min_id > watermark + 1)
