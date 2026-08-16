"""Shared contract for `RoomConsolidator`. Both implementations run every test.

The idempotency test is the one that matters: `consolidate` is called from a
loop that can legitimately re-fire over an overlapping batch, and the ONLY
thing standing between that and a fabricated «12x, 3 people» is the
natural-key constraint. A fake that simply appended would make this suite
decorative.

Only `"fake"` runs today. `RoomConsolidatorSQL` — the real implementation over
`b974afe`'s `language_observation`/`room_lexeme` tables — is Task 8's job; no
concrete class satisfies `RoomConsolidator` anywhere in this repo yet. The
`consolidator` fixture is parametrized already so Task 8 adds an `"sql"`
branch to it rather than writing a second suite, the same precedent
`tests/contract/test_artifact_ingest_contract.py` set for `ArtifactIngest`
("shipped with zero implementations… paired with `FakeArtifactIngest` anyway,
… so the shape is exercised by something rather than declared into a
vacuum"). `_FakeRoomConsolidator` is deliberately NOT in `nora.testing` yet —
CLAUDE.md's contract-suite rule binds a fake to a suite, not to a module, and
`nora/testing/fakes.py`'s own docstring enumerates every fake there as bound
to a *production* implementation by name; moving this one there before Task 8
exists would document a pairing that isn't real yet.
"""

from __future__ import annotations

import pytest

from nora.domain.models import (
    DeliveryStatus,
    LexemeExample,
    RoomLanguage,
    RoomLexeme,
    TranscriptRow,
)
from nora.domain.ports import RoomConsolidator, RoomLanguageReader


class _FakeRoomConsolidator:
    """A same-process double for both `RoomConsolidator` and
    `RoomLanguageReader`, backed by an in-memory dict keyed exactly the way
    `room_lexeme`'s primary key is: `(scope_id, canonical_form, sense)`.

    `_extract` is NOT a lexeme extractor — the real one (Task 8) is a model
    call over a batch of messages. It is the minimum deterministic rule that
    lets this suite exercise the CONTRACT (idempotency, scope isolation,
    empty-batch) without a model in the loop: a row whose body is
    `"<form>:<sense>"` yields exactly one observation; everything else yields
    none. Natural key for idempotency mirrors `language_observation`'s own
    UNIQUE constraint, `(message_id, canonical_form, sense)` — schema.py
    omits `scope_id` from it because `message_id` already pins the
    conversation, and this fake holds itself to the same key.

    `fail` simulates the read side's backing store being unreachable — the
    same role `FakeArtifactIngest.fail` plays for `ArtifactIngest.register`,
    but the INVERSE contract: `ArtifactIngest.register` documents raising on
    a backend failure, while `RoomLanguageReader.room_language` documents the
    opposite — it must NEVER raise, degrading to an empty `RoomLanguage`
    instead. `_read_lexemes` is split out so `room_language` has something
    concrete to catch: with `fail=True` it raises, and `room_language`'s own
    try/except is what turns that into the honest empty degradation a live
    turn's `asyncio.gather` needs (one raised exception there cancels its
    sibling reads and silences the whole turn).
    """

    def __init__(self, *, fail: bool = False) -> None:
        self.name = "fake"
        self._fail = fail
        self._lexemes: dict[tuple[str, str, str], RoomLexeme] = {}
        self._natural_keys: set[tuple[int, str, str]] = set()

    @staticmethod
    def _extract(row: TranscriptRow) -> tuple[str, str] | None:
        if not row.body or ":" not in row.body:
            return None
        form, _, sense = row.body.partition(":")
        return form.strip(), sense.strip()

    async def consolidate(self, *, scope_id: str, batch: list[TranscriptRow]) -> int:
        appended = 0
        for row in batch:
            extracted = self._extract(row)
            if extracted is None:
                continue
            canonical_form, sense = extracted
            natural_key = (row.id, canonical_form, sense)
            if natural_key in self._natural_keys:
                continue
            self._natural_keys.add(natural_key)

            key = (scope_id, canonical_form, sense)
            existing = self._lexemes.get(key)
            example = LexemeExample(message_id=row.id, person_id=row.person_id,
                                     text=row.body or "")
            if existing is None:
                self._lexemes[key] = RoomLexeme(
                    canonical_form=canonical_form,
                    sense=sense,
                    occurrence_count=1,
                    distinct_speakers=1 if row.person_id else 0,
                    first_seen_ms=row.ts,
                    last_seen_ms=row.ts,
                    state="live",
                    examples=(example,),
                )
            else:
                speakers = {e.person_id for e in existing.examples if e.person_id}
                if row.person_id:
                    speakers.add(row.person_id)
                self._lexemes[key] = RoomLexeme(
                    canonical_form=canonical_form,
                    sense=sense,
                    occurrence_count=existing.occurrence_count + 1,
                    distinct_speakers=len(speakers),
                    first_seen_ms=min(existing.first_seen_ms, row.ts),
                    last_seen_ms=max(existing.last_seen_ms, row.ts),
                    state="live",
                    examples=(*existing.examples, example),
                )
            appended += 1
        return appended

    def _read_lexemes(self, scope_id: str) -> list[RoomLexeme]:
        if self._fail:
            raise ConnectionError("room_lexeme unreachable")
        return [lx for (sid, _, _), lx in self._lexemes.items() if sid == scope_id]

    async def room_language(self, *, scope_id: str, limit: int) -> RoomLanguage:
        try:
            lexemes = self._read_lexemes(scope_id)
        except ConnectionError:
            return RoomLanguage(scope_id=scope_id)
        lexemes.sort(key=lambda lx: lx.occurrence_count, reverse=True)
        return RoomLanguage(scope_id=scope_id, lexemes=tuple(lexemes[:limit]))


def _row(id: int, body: str, ts: int = 1, person_id: str | None = "miles") -> TranscriptRow:
    return TranscriptRow(
        id=id, sender_id="+852111", sender_name="Miles", ts=ts, body=body,
        is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
        person_id=person_id,
    )


@pytest.fixture
def _shared_fake() -> _FakeRoomConsolidator:
    return _FakeRoomConsolidator()


@pytest.fixture(params=["fake"])
def consolidator(request, _shared_fake: _FakeRoomConsolidator):
    return _shared_fake


@pytest.fixture
def store(_shared_fake: _FakeRoomConsolidator) -> _FakeRoomConsolidator:
    """The `RoomLanguageReader` half of the SAME backing state `consolidator`
    writes to — a real implementation's write and read sides share a table,
    and a suite that gave them separate state would not be testing what
    `test_scope_is_honoured` claims to test."""
    return _shared_fake


@pytest.fixture
def batch_with_one_lexeme() -> list[TranscriptRow]:
    return [_row(1, "yyds:so good it doesn't need improving")]


def test_both_implementations_satisfy_the_ports():
    """`RoomConsolidator`/`RoomLanguageReader` are `@runtime_checkable`, so
    this `isinstance` check verifies method NAMES only — not signatures, not
    keyword-only-ness. Static conformance is mypy's job; this is the only
    mechanical check that the fake has every port method at all."""
    fake = _FakeRoomConsolidator()
    assert isinstance(fake, RoomConsolidator)
    assert isinstance(fake, RoomLanguageReader)


async def test_an_empty_batch_appends_nothing(consolidator):
    assert await consolidator.consolidate(scope_id="g1", batch=[]) == 0


async def test_the_same_batch_twice_appends_once(consolidator, batch_with_one_lexeme):
    first = await consolidator.consolidate(scope_id="g1", batch=batch_with_one_lexeme)
    assert first > 0
    second = await consolidator.consolidate(scope_id="g1", batch=batch_with_one_lexeme)
    assert second == 0, "the natural key must make a re-run a no-op"


async def test_scope_is_honoured(consolidator, store, batch_with_one_lexeme):
    """Evidence from one room must never surface in another. This is the whole
    reason `scope_id` is a parameter rather than instance state."""
    await consolidator.consolidate(scope_id="g1", batch=batch_with_one_lexeme)
    assert not await store.room_language(scope_id="g2", limit=10)
    g1_language = await store.room_language(scope_id="g1", limit=10)
    assert g1_language, "the batch DID contain a lexeme — g1 must not be empty too"
    assert g1_language.lexemes[0].canonical_form == "yyds"


async def test_an_unavailable_backend_degrades_to_an_empty_room_language():
    """`RoomLanguageReader.room_language`'s own docstring: "Never raises: an
    unavailable store yields an EMPTY `RoomLanguage`." This is why the read
    side is split from the write side at all — `room_language` runs inside a
    turn's `asyncio.gather` alongside other reads, where one uncaught
    exception cancels every sibling and silences Nora for the whole turn.

    Meaningful only because `_FakeRoomConsolidator._read_lexemes` actually
    raises when `fail=True` (mirroring `FakeArtifactIngest.fail`) — if
    `room_language`'s own try/except were deleted and the exception allowed
    to escape, this test goes red with a `ConnectionError` instead of green.
    """
    failing = _FakeRoomConsolidator(fail=True)
    result = await failing.room_language(scope_id="g1", limit=10)
    assert result == RoomLanguage(scope_id="g1")
    assert not result, "__bool__ is what a caller actually branches on"
