"""One suite, both implementations. This is what stops `FakeAuditStore`
drifting from `SqlAuditStore` and integration tests going green against
behaviour production does not have (DEC-12).

Every test here is parametrized over both stores. Adding a behaviour to one
without the other fails here, which is the point.
"""

from __future__ import annotations

import pytest

from nora.domain.models import DeliveryStatus, NormalizedMessage
from nora.domain.ports import AuditStore
from nora.infra.sql.audit import SqlAuditStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.testing.fakes import FakeAuditStore


def _msg(conv="g1", body="hello", ts=1, sender="+852111", platform="signal") -> NormalizedMessage:
    return NormalizedMessage(
        platform=platform, conversation_id=conv, conversation_type="group",
        sender_id=sender, sender_name="Miles", timestamp=ts, body=body, raw={},
    )


@pytest.fixture(params=["sql", "fake"])
async def store(request, tmp_path):
    if request.param == "sql":
        path = tmp_path / "messages.db"
        run_migrations(path)
        s: AuditStore = SqlAuditStore(create_sql_engine(path))
    else:
        s = FakeAuditStore()
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
    `LIMIT -1`, which SQLite treats as unbounded rather than zero, so a
    naive `SqlAuditStore.recent(limit=-1)` would return every row in the
    conversation instead of none. `FakeAuditStore` already returned `[]`
    here; `SqlAuditStore` must short-circuit rather than issue that query."""
    await store.append(message=_msg())
    assert await store.recent(conversation_id="g1", limit=-1) == []


async def test_appended_rows_default_to_pending(store):
    await store.append(message=_msg())
    assert (await store.recent(conversation_id="g1", limit=1))[0].delivery_status \
        is DeliveryStatus.PENDING


async def test_is_nora_round_trips(store):
    await store.append(message=_msg(), is_nora=True)
    assert (await store.recent(conversation_id="g1", limit=1))[0].is_nora is True


async def test_set_delivery_status_is_visible_in_recent(store):
    row_id = await store.append(message=_msg())
    await store.set_delivery_status(row_id=row_id, status=DeliveryStatus.SENT)
    assert (await store.recent(conversation_id="g1", limit=1))[0].delivery_status \
        is DeliveryStatus.SENT


async def test_set_delivery_status_updates_only_the_matching_row(store):
    first = await store.append(message=_msg(body="first"))
    await store.append(message=_msg(body="second", ts=2))
    await store.set_delivery_status(row_id=first, status=DeliveryStatus.SENT)
    by_body = {r.body: r for r in await store.recent(conversation_id="g1", limit=10)}
    assert by_body["first"].delivery_status is DeliveryStatus.SENT
    assert by_body["second"].delivery_status is DeliveryStatus.PENDING


async def test_row_ids_are_positive_and_start_at_one(store):
    """SQLite AUTOINCREMENT starts at 1. A 0-based fake would make the first
    id falsy, so any `if row_id:` would behave differently against the fake
    than against production — the drift this suite exists to prevent."""
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
    """Same trap `recent` documents: SQLAlchemy renders `.limit(-1)` as SQLite's
    unbounded `LIMIT -1`, so a naive implementation returns everything."""
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


async def test_unattributed_spans_conversations(store):
    """Unlike `recent`, this is a whole-table question: the operator adopted a
    person, not a room."""
    await store.append(message=_msg(conv="g1", body="a"))
    await store.append(message=_msg(conv="g2", body="b", ts=2))
    assert {r.body for r in await store.unattributed(limit=10)} == {"a", "b"}


def test_both_implementations_satisfy_the_port():
    """`AuditStore` is `@runtime_checkable`, so this `isinstance` check verifies
    method **names only** — not signatures, not keyword-only-ness, not return
    types. Static conformance (the part that actually checks shape) is mypy's
    job on `nora.infra`. This is nonetheless the only mechanical check that
    `FakeAuditStore` has every port method at all, since `nora.testing` sits
    outside mypy's gate scope."""
    assert isinstance(FakeAuditStore(), AuditStore)
