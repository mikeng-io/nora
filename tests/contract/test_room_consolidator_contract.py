"""Shared contract for `RoomConsolidator`. Both implementations run every test.

The idempotency test is the one that matters: `consolidate` is called from a
loop that can legitimately re-fire over an overlapping batch, and the ONLY
thing standing between that and a fabricated «12x, 3 people» is the
natural-key constraint. A fake that simply appended would make this suite
decorative.

Both `"fake"` and `"sql"` now run the FULL `consolidate`+`room_language`
contract. `nora.language.consolidator.LanguageConsolidator` — the real
implementation, pairing `LexemeObserver` (Task 6) with `SqlLanguageStore`
over `b974afe`'s `language_observation`/`room_lexeme` tables (Task 7) — is
Task 8's own class; the `consolidator`/`store` fixtures below join a real
`LanguageConsolidator` to that same `SqlLanguageStore` the same way
`tests/contract/test_artifact_ingest_contract.py` joined `FakeArtifactIngest`
to `DropArtifactIngest` ("shipped with zero implementations… so the shape is
exercised by something rather than declared into a vacuum" — the same
argument in reverse now that the second implementation exists).
`_FakeRoomConsolidator` stays out of `nora.testing` regardless: CLAUDE.md's
contract-suite rule binds a fake to a suite, not to a module, and
`nora/testing/fakes.py`'s own docstring enumerates every fake there as bound
to a *production* implementation it stands in for at OTHER call sites — this
fake has none; its only job is exercising this one contract.

Task 7 (`nora.infra.sql.language.SqlLanguageStore`) DOES exist now, and it
satisfies the `RoomLanguageReader` HALF of this contract on its own —
`SqlLanguageStore` carries no `consolidate`, so it cannot join the
`consolidator`/`store` fixtures above on its own, which is exactly why
`RoomConsolidator`/`RoomLanguageReader` are two Protocols rather than one (see
`nora.domain.ports`' own docstring). What it CAN join today is
`test_an_unavailable_backend_degrades_to_an_empty_room_language` below, which
is parametrized over `["fake", "sql"]` for exactly that reason: a contract
only a fake satisfies is not a contract, and `RoomLanguageReader.room_language`
NEVER raising is the guarantee `TranscriptInjection`'s `asyncio.gather` (a
later task) depends on for every implementation, not just the fake one.

Task 8 (`nora.language.consolidator.LanguageConsolidator`) supplies the
missing write-side pairing: `LanguageConsolidator(observer, store)` wraps a
`LexemeObserver` around exactly that same `SqlLanguageStore`, so the two now
join together into one real `RoomConsolidator`/`RoomLanguageReader` pair.
`_backing` below is the single fixture both `consolidator` and `store` derive
from — mirroring `unavailable_reader`'s own shape (conditional yield, real
`pg_url`-backed engine disposed in a `finally`) rather than inventing a
second one — so `consolidator.consolidate` and `store.room_language` read and
write the SAME backing state for the `"sql"` branch exactly as they already
do for `"fake"` via `_shared_fake`. `_FakeModelClient` stands in for the one
model call `LexemeObserver` makes, applying the identical `"<form>:<sense>"`
rule `_FakeRoomConsolidator._extract` already applies, so a batch produces the
same observed lexeme regardless of which `RoomConsolidator` ran it.
"""

from __future__ import annotations

import json
import time

import pytest

from nora.domain.models import (
    DeliveryStatus,
    LexemeExample,
    NormalizedMessage,
    RoomLanguage,
    RoomLexeme,
    TranscriptRow,
)
from nora.domain.ports import RoomConsolidator, RoomLanguageReader
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.language import SqlLanguageStore
from nora.infra.sql.migrations_runner import run_migrations
from nora.language.consolidator import LanguageConsolidator
from nora.language.observer import LexemeObserver


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


def _now_ms() -> int:
    return int(time.time() * 1000)


def _row(id: int, body: str, ts: int = 1, person_id: str | None = "miles") -> TranscriptRow:
    return TranscriptRow(
        id=id, sender_id="+852111", sender_name="Miles", ts=ts, body=body,
        is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
        person_id=person_id,
    )


class _FakeModelClient:
    """The `LexemeObserver` collaborator for the `"sql"` branch below —
    same structural shape `nora.language.observer._ModelClient` documents
    (one `complete(prompt=...) -> str` call). It re-derives, from the
    rendered prompt text alone, the identical `"<form>:<sense>"` rule
    `_FakeRoomConsolidator._extract` applies directly to a row's body — so a
    batch yields the SAME observed lexeme whether it is run through the fake
    consolidator or through a real `LanguageConsolidator` wrapping this. Each
    prompt line is `nora.language.observer._build_prompt`'s own format,
    `"[<id>] <speaker>: <body>"`; the speakers this suite uses (`"Miles"`)
    never contain a colon, so splitting on the first `": "` recovers the body
    exactly."""

    async def complete(self, *, prompt: str) -> str:
        observations = []
        for line in prompt.splitlines():
            if not line.startswith("["):
                continue
            _, _, rest = line.partition("] ")
            _speaker, sep, body = rest.partition(": ")
            if not sep or ":" not in body:
                continue
            message_id = int(line[1:line.index("]")])
            form, _, sense = body.partition(":")
            observations.append({
                "surface_form": form.strip(),
                "canonical_form": form.strip(),
                "sense": sense.strip(),
                "part_of_speech": "noun",
                "message_id": message_id,
                "confidence": 0.9,
            })
        return json.dumps({"observations": observations})


@pytest.fixture
def _shared_fake() -> _FakeRoomConsolidator:
    return _FakeRoomConsolidator()


@pytest.fixture(params=["fake", "sql"])
async def _backing(request, _shared_fake: _FakeRoomConsolidator):
    """The single object BOTH `consolidator` and `store` derive from, so a
    real implementation's write and read sides share one backing table —
    exactly `unavailable_reader`'s own shape (Task 7): conditional yield, a
    real `pg_url`-backed engine disposed in a `finally` for `"sql"`, nothing
    at all extra for `"fake"`."""
    if request.param == "fake":
        yield _shared_fake
        return
    pg_url = request.getfixturevalue("pg_url")
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        yield SqlLanguageStore(engine)
    finally:
        await engine.dispose()


@pytest.fixture
def consolidator(_backing):
    """`_backing` itself for `"fake"` (it already implements `consolidate`);
    a real `LanguageConsolidator` wrapping it for `"sql"`, since
    `SqlLanguageStore` alone carries no `consolidate` — the write half needs
    Task 8's own class to pair it with a `LexemeObserver`."""
    if isinstance(_backing, SqlLanguageStore):
        return LanguageConsolidator(
            observer=LexemeObserver(_FakeModelClient()), store=_backing)
    return _backing


@pytest.fixture
def store(_backing):
    """The `RoomLanguageReader` half of the SAME backing state `consolidator`
    writes to — a real implementation's write and read sides share a table,
    and a suite that gave them separate state would not be testing what
    `test_scope_is_honoured` claims to test. `SqlLanguageStore` already
    satisfies `RoomLanguageReader` on its own, so `_backing` needs no
    wrapping here even for `"sql"`."""
    return _backing


@pytest.fixture
async def batch_with_one_lexeme(request, _backing) -> list[TranscriptRow]:
    """One row whose body is `"<form>:<sense>"` — the fake's own extraction
    rule, reproduced verbatim by `_FakeModelClient` for the `"sql"` branch.

    For `"sql"`, `message_id` MUST be a real `messages.id`:
    `SqlLanguageStore.append_observations` joins every candidate back to that
    table to read `conversation_id`/`observed_at` off it (its own module
    docstring), so an invented id resolves to zero rows and silently drops
    every observation — the same seeding `tests/infra/test_language_store.py`
    already requires for exactly this reason. A real `SqlRecordStore` sharing
    `_backing`'s own `pg_url` seeds one such row.

    The seeded `timestamp` is `_now_ms()`, not `_row`'s own `ts=1` default —
    `observed_at` rides that real column into `rescore`'s `last_seen_at`
    (`nora.infra.sql.language`'s own module docstring), and
    `nora.domain.language_scoring.state_for` renders nothing older than
    `STALE_AFTER_DAYS`. Seeding near the 1970 epoch would make
    `test_scope_is_honoured` fail for a reason that has nothing to do with
    scope isolation — every lexeme would legitimately be `stale` and
    `room_language` would correctly render it as absent.
    """
    if not isinstance(_backing, SqlLanguageStore):
        return [_row(1, "yyds:so good it doesn't need improving")]
    pg_url = request.getfixturevalue("pg_url")
    record = SqlRecordStore(create_sql_engine(pg_url))
    try:
        message_id = await record.append(message=NormalizedMessage(
            platform="signal", conversation_id="g1", conversation_type="group",
            sender_id="+852111", sender_name="Miles", timestamp=_now_ms(),
            body="yyds:so good it doesn't need improving", raw={},
        ))
    finally:
        await record.aclose()
    return [_row(message_id, "yyds:so good it doesn't need improving")]


def test_both_implementations_satisfy_the_ports():
    """`RoomConsolidator`/`RoomLanguageReader` are `@runtime_checkable`, so
    this `isinstance` check verifies method NAMES only — not signatures, not
    keyword-only-ness. Static conformance is mypy's job; this is the only
    mechanical check that the fake — and the real `LanguageConsolidator` —
    have every port method at all."""
    fake = _FakeRoomConsolidator()
    assert isinstance(fake, RoomConsolidator)
    assert isinstance(fake, RoomLanguageReader)
    store = SqlLanguageStore(create_sql_engine("postgresql+asyncpg://x/x"))
    assert isinstance(store, RoomLanguageReader)
    assert isinstance(
        LanguageConsolidator(observer=LexemeObserver(_FakeModelClient()), store=store),
        RoomConsolidator,
    )


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


@pytest.fixture(params=["fake", "sql"])
async def unavailable_reader(request):
    """A `RoomLanguageReader` guaranteed to fail on `room_language`, one per
    implementation.

    The fake fails by construction (`fail=True` — see
    `_FakeRoomConsolidator._read_lexemes`), the same lever
    `FakeArtifactIngest.fail` pulls. `SqlLanguageStore` gets a REAL engine
    pointed at a host that cannot resolve — `.invalid` is reserved by RFC 2606
    to never resolve, so this fails fast and deterministically rather than
    racing a connect timeout against a live database that might actually be
    reachable. A closed/disposed engine was considered and rejected:
    `SqlRecordStore.aclose`'s own contract (`tests/infra/test_audit_store.py::
    test_aclose_releases_the_connection_pool`) is that `dispose()` does NOT
    poison the engine — a query afterward just opens a fresh connection and
    would succeed against a real database, which would make this degradation
    path untestable that way.
    """
    if request.param == "fake":
        yield _FakeRoomConsolidator(fail=True)
        return
    engine = create_sql_engine(
        "postgresql+asyncpg://nobody:nobody@dreamer-language-unreachable.invalid:5432/nope"
    )
    try:
        yield SqlLanguageStore(engine)
    finally:
        await engine.dispose()


async def test_an_unavailable_backend_degrades_to_an_empty_room_language(unavailable_reader):
    """`RoomLanguageReader.room_language`'s own docstring: "Never raises: an
    unavailable store yields an EMPTY `RoomLanguage`." This is why the read
    side is split from the write side at all — `room_language` runs inside a
    turn's `asyncio.gather` alongside other reads, where one uncaught
    exception cancels every sibling and silences Nora for the whole turn.

    Parametrized over BOTH implementations (`unavailable_reader` above) — a
    contract only the fake satisfies is not a contract. Meaningful for the
    fake only because `_FakeRoomConsolidator._read_lexemes` actually raises
    when `fail=True`; meaningful for `SqlLanguageStore` only because its own
    `except Exception` is genuinely broad rather than narrowed to
    `ConnectionError` — either one deleted turns this red instead of green,
    the fake with a `ConnectionError` escaping, the SQL store with whatever
    driver-level exception an unresolvable host actually raises.
    """
    result = await unavailable_reader.room_language(scope_id="g1", limit=10)
    assert result == RoomLanguage(scope_id="g1")
    assert not result, "__bool__ is what a caller actually branches on"
