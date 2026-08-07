"""One suite, both implementations. This is what stops `FakeRecordStore`
drifting from `SqlRecordStore` and integration tests going green against
behaviour production does not have (DEC-12).

Every test here is parametrized over both stores. Adding a behaviour to one
without the other fails here, which is the point.
"""

from __future__ import annotations

import json

import pytest

from nora.domain.models import DeliveryStatus, NormalizedMessage, Quote
from nora.domain.ports import RecordStore
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.testing.fakes import FakeRecordStore


def _msg(conv="g1", body="hello", ts=1, sender="+852111", platform="signal",
         raw=None, quote=None) -> NormalizedMessage:
    return NormalizedMessage(
        platform=platform, conversation_id=conv, conversation_type="group",
        sender_id=sender, sender_name="Miles", timestamp=ts, body=body,
        raw={} if raw is None else raw, quote=quote,
    )


@pytest.fixture(params=["sql", "fake"])
async def store(request):
    if request.param == "sql":
        pg_url = request.getfixturevalue("pg_url")
        run_migrations(pg_url)
        s: RecordStore = SqlRecordStore(create_sql_engine(pg_url))
    else:
        s = FakeRecordStore()
    yield s
    await s.aclose()


async def test_append_returns_increasing_row_ids(store):
    first = await store.append(message=_msg())
    second = await store.append(message=_msg())
    assert second > first


async def test_recent_returns_rows_oldest_first(store):
    for i in range(3):
        await store.append(message=_msg(body=f"m{i}", ts=i))
    assert [r.body for r in await store.recent(conversation_id="g1", limit=10)] \
        == ["m0", "m1", "m2"]


async def test_recent_limit_takes_the_newest(store):
    for i in range(5):
        await store.append(message=_msg(body=f"m{i}", ts=i))
    assert [r.body for r in await store.recent(conversation_id="g1", limit=2)] \
        == ["m3", "m4"]


async def test_recent_is_scoped_to_the_conversation(store):
    await store.append(message=_msg(conv="g1", body="a"))
    await store.append(message=_msg(conv="g2", body="b"))
    assert [r.body for r in await store.recent(conversation_id="g2", limit=10)] == ["b"]


async def test_recent_on_an_unknown_conversation_is_empty(store):
    assert await store.recent(conversation_id="nope", limit=10) == []


async def test_recent_with_a_zero_limit_is_empty(store):
    await store.append(message=_msg())
    assert await store.recent(conversation_id="g1", limit=0) == []


async def test_recent_with_a_negative_limit_is_empty(store):
    """The regression this guards: SQLAlchemy renders `.limit(-1)` as
    `LIMIT -1`, which the old SQLite backend treated as unbounded rather than
    zero (Postgres errors on it), so a naive `SqlRecordStore.recent(limit=-1)`
    would dump or crash instead of returning none. `FakeRecordStore` already
    returned `[]` here; `SqlRecordStore` must short-circuit rather than issue
    that query."""
    await store.append(message=_msg())
    assert await store.recent(conversation_id="g1", limit=-1) == []


async def test_appended_rows_default_to_pending(store):
    await store.append(message=_msg())
    assert (await store.recent(conversation_id="g1", limit=1))[0].delivery_status \
        is DeliveryStatus.PENDING


async def test_is_nora_round_trips(store):
    await store.append(message=_msg(), is_nora=True)
    assert (await store.recent(conversation_id="g1", limit=1))[0].is_nora is True


async def test_set_delivery_outcome_is_visible_in_recent(store):
    row_id = await store.append(message=_msg())
    await store.set_delivery_outcome(row_id=row_id, status=DeliveryStatus.SENT)
    assert (await store.recent(conversation_id="g1", limit=1))[0].delivery_status \
        is DeliveryStatus.SENT


async def test_set_delivery_outcome_updates_only_the_matching_row(store):
    first = await store.append(message=_msg(body="first"))
    await store.append(message=_msg(body="second", ts=2))
    await store.set_delivery_outcome(row_id=first, status=DeliveryStatus.SENT)
    by_body = {r.body: r for r in await store.recent(conversation_id="g1", limit=10)}
    assert by_body["first"].delivery_status is DeliveryStatus.SENT
    assert by_body["second"].delivery_status is DeliveryStatus.PENDING


async def test_set_delivery_outcome_corrects_the_rows_timestamp(store):
    """Bug B (design/11 §2.3). `Deliverer` stamps `time.time()` locally at
    record-first time; Signal assigns its own sent-timestamp and that is what a
    reply's `quote.id` points at. The correction rides the post-send update
    that already existed — no second write, so WAL's single writer is
    untouched and A9's record-first ordering is preserved."""
    row_id = await store.append(message=_msg(ts=1785950108283))
    await store.set_delivery_outcome(
        row_id=row_id, status=DeliveryStatus.SENT, ts=1785950108291,
    )
    assert (await store.recent(conversation_id="g1", limit=1))[0].ts == 1785950108291


async def test_set_delivery_outcome_without_a_ts_leaves_the_timestamp_alone(store):
    """A platform that answered without a timestamp must not blank the row —
    `recent` orders on `ts`, so a NULL or 0 there is a corrupt transcript."""
    row_id = await store.append(message=_msg(ts=4242))
    await store.set_delivery_outcome(row_id=row_id, status=DeliveryStatus.FAILED)
    assert (await store.recent(conversation_id="g1", limit=1))[0].ts == 4242


async def test_set_delivery_outcome_on_an_unknown_row_is_a_silent_no_op(store):
    await store.set_delivery_outcome(row_id=9999, status=DeliveryStatus.SENT, ts=1)


# ---------------------------------------------------------------------------
# quoted_ts — the parent pointer, written in the SAME insert (design/11 §2.2)
# ---------------------------------------------------------------------------


async def test_quoted_ts_round_trips_through_recent(store):
    await store.append(message=_msg(quote=Quote(author_id="u-1", text="早晨",
                                                quoted_ts=1785950108291)))
    assert (await store.recent(conversation_id="g1", limit=1))[0].quoted_ts \
        == 1785950108291


async def test_a_message_with_no_quote_has_a_null_quoted_ts(store):
    await store.append(message=_msg())
    assert (await store.recent(conversation_id="g1", limit=1))[0].quoted_ts is None


async def test_quoted_ts_needs_no_extra_argument_because_it_rides_the_message(store):
    """It is a value the parser already extracted, so it lands in the one
    INSERT `append` already performs — exactly what DEC-15 did for `person_id`,
    and for the same reason (this path drops the message on WAL contention)."""
    row_id = await store.append(message=_msg(quote=Quote(author_id="u", text="t", quoted_ts=7)))
    rows = await store.recent(conversation_id="g1", limit=10)
    assert [(r.id, r.quoted_ts) for r in rows] == [(row_id, 7)]


async def test_row_ids_are_positive_and_start_at_one(store):
    """The id sequence starts at 1 (SQLite AUTOINCREMENT then, a Postgres
    sequence now). A 0-based fake would make the first id falsy, so any
    `if row_id:` would behave differently against the fake than against
    production — the drift this suite exists to prevent."""
    assert await store.append(message=_msg()) == 1
    assert await store.append(message=_msg(ts=2)) == 2


async def test_stats_reports_totals(store):
    await store.append(message=_msg(conv="g1"))
    await store.append(message=_msg(conv="g1", body="second"))
    await store.append(message=_msg(conv="g2"), is_nora=True)
    s = await store.stats()
    assert s["messages"] == 3
    assert s["conversations"] == 2
    assert s["nora_messages"] == 1


async def test_recent_rows_are_transcript_rows_not_dicts(store):
    """The regression this guards: `prefill._transcript_block` used `.get()`
    because db.py returned dicts. Attribute access must work on both stores."""
    await store.append(message=_msg())
    row = (await store.recent(conversation_id="g1", limit=1))[0]
    assert row.body == "hello"
    assert not isinstance(row, dict)


# ---------------------------------------------------------------------------
# unattributed / set_person_id — the lazy-adoption pair. Both stores or neither.
# ---------------------------------------------------------------------------


async def test_a_row_appended_without_a_person_id_is_unattributed(store):
    await store.append(message=_msg())
    rows = await store.unattributed(limit=10)
    assert [(r.platform, r.sender_id, r.body) for r in rows] == [("signal", "+852111", "hello")]


async def test_a_row_appended_with_a_person_id_is_never_unattributed(store):
    await store.append(message=_msg(), person_id="miles")
    assert await store.unattributed(limit=10) == []


async def test_unattributed_returns_rows_oldest_first(store):
    """Load-bearing ordering, not cosmetic: `nora.app.adoption` replays these
    into a person's Honcho peer, and a peer's memory must read chronologically.
    Appended newest-first here so insertion order and `ts` order disagree."""
    for i in (3, 1, 2):
        await store.append(message=_msg(body=f"m{i}", ts=i))
    assert [r.body for r in await store.unattributed(limit=10)] == ["m1", "m2", "m3"]


async def test_unattributed_never_returns_noras_own_rows(store):
    """Nora's replies are audited with `person_id` NULL too, and her account is
    never in identity.toml (DEC-9) — so they can never resolve, and would
    otherwise be rescanned on every restart forever."""
    await store.append(message=_msg(body="hers"), is_nora=True)
    await store.append(message=_msg(body="theirs", ts=2))
    assert [r.body for r in await store.unattributed(limit=10)] == ["theirs"]


async def test_unattributed_honours_its_limit(store):
    for i in range(5):
        await store.append(message=_msg(body=f"m{i}", ts=i))
    assert [r.body for r in await store.unattributed(limit=2)] == ["m0", "m1"]


async def test_unattributed_with_a_non_positive_limit_is_empty(store):
    """Same trap `recent` documents: `.limit(-1)` was SQLite's unbounded
    `LIMIT -1` (Postgres errors on it), so a naive implementation dumps or
    crashes; the store must short-circuit."""
    await store.append(message=_msg())
    assert await store.unattributed(limit=0) == []
    assert await store.unattributed(limit=-1) == []


async def test_set_person_id_removes_the_row_from_unattributed(store):
    """`person_id` IS the idempotence marker — a marked row must stop matching."""
    row_id = await store.append(message=_msg())
    await store.set_person_id(row_id=row_id, person_id="miles")
    assert await store.unattributed(limit=10) == []


async def test_set_person_id_marks_only_the_matching_row(store):
    first = await store.append(message=_msg(body="first"))
    await store.append(message=_msg(body="second", ts=2))
    await store.set_person_id(row_id=first, person_id="miles")
    assert [r.body for r in await store.unattributed(limit=10)] == ["second"]


async def test_set_person_id_on_an_unknown_row_is_a_silent_no_op(store):
    """Mirrors `set_delivery_status`: the row may have been pruned between the
    backfill's scan and its write, which is harmless, not a failure."""
    await store.set_person_id(row_id=9999, person_id="miles")
    assert await store.unattributed(limit=10) == []


async def test_unattributed_carries_the_platform_for_re_resolution(store):
    """Resolution is keyed (platform, sender_id) — a row without its platform
    cannot be re-resolved at all, which is why `UnattributedRow` carries one
    where `TranscriptRow` does not."""
    await store.append(message=_msg(platform="telegram", sender="@miles"))
    assert (await store.unattributed(limit=10))[0].platform == "telegram"


async def test_unattributed_carries_the_full_envelope(store):
    """`nora persons pending` reads a sender's OTHER id forms out of
    `raw_json`: under sealed sender the same account arrives as a phone number
    on one message and as an ACI on another, and `sender_id` alone therefore
    lists one account as two pending entries. `FakeRecordStore` used to record
    `raw_json` as None, which nothing noticed while nothing read it back — it
    would have let every pending-sender test pass against a store production
    does not have."""
    envelope = {"envelope": {"source": "+852111", "sourceUuid": "u-1"}}
    await store.append(message=_msg(raw=envelope))
    row = (await store.unattributed(limit=10))[0]
    assert row.raw_json is not None
    assert json.loads(row.raw_json) == envelope


async def test_unattributed_spans_conversations(store):
    """Unlike `recent`, this is a whole-table question: the operator adopted a
    person, not a room."""
    await store.append(message=_msg(conv="g1", body="a"))
    await store.append(message=_msg(conv="g2", body="b", ts=2))
    assert {r.body for r in await store.unattributed(limit=10)} == {"a", "b"}


def test_both_implementations_satisfy_the_port():
    """`RecordStore` is `@runtime_checkable`, so this `isinstance` check verifies
    method **names only** — not signatures, not keyword-only-ness, not return
    types. Static conformance (the part that actually checks shape) is mypy's
    job on `nora.infra`. This is nonetheless the only mechanical check that
    `FakeRecordStore` has every port method at all, since `nora.testing` sits
    outside mypy's gate scope."""
    assert isinstance(FakeRecordStore(), RecordStore)
