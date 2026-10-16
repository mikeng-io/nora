"""One suite, both implementations. This is what stops `FakeRecordStore`
drifting from `SqlRecordStore` and integration tests going green against
behaviour production does not have (DEC-12).

Every test here is parametrized over both stores. Adding a behaviour to one
without the other fails here, which is the point.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from nora.domain.media import MediaBlob
from nora.domain.models import DeliveryStatus, EditTarget, NormalizedMessage, Quote, RowEdit
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


async def test_recent_before_takes_the_newest_strictly_before_the_bound(store):
    """The transcript tools' `before`: exclusive at the bound, newest `limit` below it,
    oldest-first, one conversation."""
    for i in range(6):
        await store.append(message=_msg(body=f"m{i}", ts=i * 10))
    await store.append(message=_msg(conv="g2", body="other", ts=5))
    rows = await store.recent_before(conversation_id="g1", before_ts=40, limit=2)
    assert [r.body for r in rows] == ["m2", "m3"]          # ts 20, 30; not 40
    assert await store.recent_before(conversation_id="g1", before_ts=0, limit=5) == []
    assert await store.recent_before(conversation_id="g1", before_ts=40, limit=0) == []


def _with_media(conv="g1", ts=1, sha="ab" * 32, sender="+852111") -> NormalizedMessage:
    return dataclasses.replace(
        _msg(conv=conv, ts=ts, sender=sender),
        media_blobs=(MediaBlob(sha256=sha, media_type="image/jpeg", size=4),),
    )


async def test_media_rows_reach_past_the_recent_window_newest_first(store):
    """The reason `media_rows` exists: a photo that has scrolled out of
    `recent` is still a row. Scoped to the conversation, human rows with media
    only, bounded below by `since_ts` (inclusive), newest first."""
    await store.append(message=_with_media(ts=10, sha="01" * 32))
    await store.append(message=_with_media(ts=20, sha="02" * 32))
    await store.append(message=_with_media(ts=30, sha="03" * 32))
    for i in range(5):                       # text-only rows push the photos out
        await store.append(message=_msg(ts=40 + i))
    await store.append(message=_with_media(conv="g2", ts=25, sha="04" * 32))
    await store.append(message=_with_media(ts=35, sha="05" * 32), is_nora=True)
    assert [r.ts for r in await store.recent(conversation_id="g1", limit=3)] == [42, 43, 44]

    rows = await store.media_rows(conversation_id="g1", since_ts=20, limit=10)
    assert [r.ts for r in rows] == [30, 20]
    assert all(r.media_json for r in rows)
    assert [r.ts for r in await store.media_rows(conversation_id="g1", since_ts=0, limit=2)] \
        == [30, 20]
    assert await store.media_rows(conversation_id="g1", since_ts=0, limit=0) == []
    assert await store.media_rows(conversation_id="nope", since_ts=0, limit=5) == []


_FILE_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"


def _with_link(conv="g1", ts=1, file_id=_FILE_ID) -> NormalizedMessage:
    from nora.domain.external_files import file_refs_in

    body = f"see https://docs.google.com/document/d/{file_id}/edit"
    return dataclasses.replace(
        _msg(conv=conv, ts=ts, body=body), external_files=file_refs_in(body),
    )


async def test_file_links_are_stored_beside_the_row_and_read_back(store):
    """Both stores write the same `external_files_json` text (one domain function), every
    transcript read carries it, and `external_file_rows` is `media_rows`' shape over it."""
    from nora.domain.external_files import external_files_json

    expected = external_files_json(_with_link().external_files)
    await store.append(message=_with_link(ts=10))
    await store.append(message=_msg(ts=20))
    await store.append(message=_with_link(ts=30, file_id="1Second00000000000000xyz"))
    await store.append(message=_with_link(conv="g2", ts=25))
    await store.append(message=_with_link(ts=35), is_nora=True)

    recent = await store.recent(conversation_id="g1", limit=10)
    assert [r.external_files_json for r in recent][:2] == [expected, None]
    rows = await store.external_file_rows(conversation_id="g1", since_ts=0, limit=10)
    assert [r.ts for r in rows] == [30, 10]
    assert rows[1].external_files_json == expected
    assert [r.ts for r in await store.external_file_rows(
        conversation_id="g1", since_ts=20, limit=10)] == [30]
    assert await store.external_file_rows(conversation_id="g1", since_ts=0, limit=0) == []


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
    """`SqlRecordStore.recent(limit=-1)` returns none, like `FakeRecordStore`.

    SQLAlchemy renders `.limit(-1)` as `LIMIT -1`, which an unbounded-on-negative backend
    treats as unbounded rather than zero (Postgres errors on it), so a naive
    implementation would dump or crash. `SqlRecordStore` must short-circuit rather than
    issue that query."""
    await store.append(message=_msg())
    assert await store.recent(conversation_id="g1", limit=-1) == []


async def test_depth_to_counts_the_recent_size_that_reaches_that_ts(store):
    """`depth_to` answers the same question `recent`'s own ordering does: how
    large a `recent(limit=...)` call must be to include the row carrying this
    `ts`. Five rows, asking about the third-newest (`ts=2`) must report 3 —
    `recent(limit=3)` is exactly the smallest window that reaches it."""
    for i in range(5):
        await store.append(message=_msg(body=f"m{i}", ts=i))
    assert await store.depth_to(conversation_id="g1", ts=2) == 3


async def test_depth_to_is_none_for_a_ts_no_row_carries(store):
    await store.append(message=_msg(ts=1))
    assert await store.depth_to(conversation_id="g1", ts=999) is None


async def test_depth_to_is_scoped_to_the_conversation(store):
    """A `ts` that exists, but only in a DIFFERENT conversation, must answer
    `None` here too — the same conversation-scoping every other verb on this
    port holds, and the one this exists to serve (a reply's quote must never
    resolve across a conversation boundary)."""
    await store.append(message=_msg(conv="g1", ts=1))
    await store.append(message=_msg(conv="g2", ts=5))
    assert await store.depth_to(conversation_id="g1", ts=5) is None


async def test_depth_to_on_a_duplicated_ts_matches_recents_own_tie_break(store):
    """Two rows can legitimately share a `ts`. `depth_to` must count through to the LAST of
    them in `recent`'s own `ts DESC, id DESC` order (the older row by insertion, since a
    smaller `id` sorts after a larger one for a tied `ts`), because that is the row
    `nora.agent.prefill._by_ts` resolves a quote to (`setdefault` over an oldest-first
    list keeps the first-seen, i.e. lowest-id, row)."""
    await store.append(message=_msg(body="older", ts=5))
    await store.append(message=_msg(body="newer-same-ts", ts=5))
    await store.append(message=_msg(body="after", ts=6))
    assert await store.depth_to(conversation_id="g1", ts=5) == 3


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
    """`Deliverer` stamps `time.time()` locally at record-first time; Signal assigns its own
    sent-timestamp and that is what a reply's `quote.id` points at. The correction rides
    the post-send update, with no second write, so WAL's single writer is untouched and
    the record-first ordering is preserved."""
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
# quoted_ts — the parent pointer, written in the SAME insert
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
    """The id sequence starts at 1 (a Postgres sequence). A 0-based fake would make the first
    id falsy, so any `if row_id:` would behave differently against the fake than against
    production, the drift this suite exists to prevent."""
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
    """Same trap `recent` documents: `.limit(-1)` is unbounded on some backends (Postgres
    errors on it), so a naive implementation dumps or crashes; the store must
    short-circuit."""
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
    """`nora persons pending` reads a sender's OTHER id forms out of `raw_json`: under sealed
    sender the same account arrives as a phone number on one message and as an ACI on
    another, and `sender_id` alone therefore lists one account as two pending entries.
    `FakeRecordStore` must record `raw_json`, or every pending-sender test passes against
    a store production does not have."""
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


async def test_recent_carries_the_resolved_person_id(store):
    """`person_id` is written in the same INSERT as the row (identity resolves
    before the audit write), so a transcript row must be able to report it.
    Unmapped senders keep `None` — that is a real state, not a gap."""
    await store.append(
        message=NormalizedMessage(
            "signal", "g1", "group", "+852111", "Miles", 1, "congee", {}),
        person_id="miles",
    )
    await store.append(
        message=NormalizedMessage(
            "signal", "g1", "group", "+852999", None, 2, "who dis", {}),
    )
    rows = await store.recent(conversation_id="g1", limit=10)
    assert [r.person_id for r in rows] == ["miles", None]


# ---------------------------------------------------------------------------
# since — the consolidation cursor (ascending, human-only, opposite of `recent`)
# ---------------------------------------------------------------------------


async def test_since_returns_human_rows_ascending_after_a_cursor(store):
    """The consolidation cursor. Ascending by id (NOT recent's newest-first),
    and `is_nora` rows are excluded IN THE QUERY — her own words are never
    evidence, and a caller that had to remember to filter would eventually
    forget."""
    ids = []
    for i, (body, is_nora) in enumerate(
        [("a", False), ("b", True), ("c", False), ("d", False)], start=1
    ):
        ids.append(await store.append(
            message=NormalizedMessage(
                "signal", "g1", "group", "+852111", "Miles", i, body, {}),
            is_nora=is_nora,
        ))

    rows = await store.since(conversation_id="g1", after_id=ids[0], limit=10)
    assert [r.body for r in rows] == ["c", "d"], "ascending, human-only, after the cursor"

    assert await store.since(conversation_id="g1", after_id=0, limit=0) == []
    assert await store.since(conversation_id="g1", after_id=ids[-1], limit=10) == []


async def test_since_including_nora_returns_nora_rows_ascending_after_a_cursor(store):
    """The standing fold's read: the same cursor shape as `since`, but Nora's OWN rows are
    included, because the fold needs her replies to re-judge her position, and `since`
    must keep excluding them for the Dreamer. One method, one difference, and this test
    pins exactly that difference against BOTH implementations."""
    ids = []
    for i, (body, is_nora) in enumerate(
        [("a", False), ("b", True), ("c", False), ("d", True)], start=1
    ):
        ids.append(await store.append(
            message=NormalizedMessage(
                "signal", "g1", "group", "+852111", "Miles", i, body, {}),
            is_nora=is_nora,
        ))

    rows = await store.since_including_nora(
        conversation_id="g1", after_id=ids[0], limit=10)
    assert [r.body for r in rows] == ["b", "c", "d"], (
        "ascending, INCLUDING is_nora rows, after the cursor"
    )

    assert await store.since_including_nora(
        conversation_id="g1", after_id=0, limit=0) == []
    assert await store.since_including_nora(
        conversation_id="g1", after_id=ids[-1], limit=10) == []


async def test_since_including_nora_is_scoped_to_the_conversation(store):
    """The cursor is per-conversation: another room's rows must never leak
    into a fold's batch, exactly as `since` scopes its own read."""
    other = await store.append(message=_msg(conv="g2", body="other"))
    mine = await store.append(message=_msg(body="mine"), is_nora=True)
    rows = await store.since_including_nora(
        conversation_id="g1", after_id=0, limit=10)
    assert [r.id for r in rows] == [mine]
    assert other not in [r.id for r in rows]


def test_both_implementations_satisfy_the_port():
    """`RecordStore` is `@runtime_checkable`, so this `isinstance` check verifies
    method **names only** — not signatures, not keyword-only-ness, not return
    types. Static conformance (the part that actually checks shape) is mypy's
    job on `nora.infra`. This is nonetheless the only mechanical check that
    `FakeRecordStore` has every port method at all, since `nora.testing` sits
    outside mypy's gate scope."""
    assert isinstance(FakeRecordStore(), RecordStore)


async def test_an_unattributed_row_carries_its_custody_index(store):
    """`UnattributedRow.media_json`. These rows are FED TO HONCHO by lazy adoption, so a
    back-filled row must reach memory with its photo description or voice words. The join
    keys on `media_json` and NEVER on the body (a handle in prose is forgeable), so
    without this column adoption could not join at all and every repaired row would reach
    memory as `[image a3f2c1]` and nothing else.

    Selecting seven columns where the row has eight leaves the whole suite green, which is
    why this is asserted rather than assumed."""
    blob = MediaBlob(
        sha256="a3f2c1" + "0" * 58, media_type="image/png", size=4)
    await store.append(message=NormalizedMessage(
        "signal", "g1", "group", "+852111", "Miles", 1_000,
        "睇下 [image a3f2c1]", {}, media_blobs=(blob,),
    ))

    rows = await store.unattributed(limit=10)

    assert len(rows) == 1
    assert rows[0].media_json is not None
    assert blob.sha256 in rows[0].media_json


# ── edits ──────────────────────────────────────────────────────────────────


def _edit(body: str, ts: int, *, conv="g1", sender="+852111", raw=None,
          mentions=frozenset()) -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id=conv, conversation_type="group",
        sender_id=sender, sender_name="Miles", timestamp=ts, body=body,
        raw={"edit": ts} if raw is None else raw, mentions=mentions,
        edit_of=EditTarget(sent_ts=1),
    )


async def test_an_edit_folds_the_latest_text_onto_the_original_position(store):
    """The row keeps its id, its ts and its OWN raw_json (the envelope a reply
    or a reaction reads the platform handle from); the body is the latest
    edit's; `edit` carries when, what it first said, and the latest envelope."""
    await store.append(message=_msg(body="before", ts=0))
    original = await store.append(message=_msg(body="dinner at 7", ts=1, raw={"orig": 1}))
    await store.append(message=_msg(body="after", ts=2))
    await store.append_edit(message_id=original, message=_edit("dinner at 8", 5))
    await store.append_edit(message_id=original, message=_edit("dinner at 9", 9))

    rows = await store.recent(conversation_id="g1", limit=10)
    assert [r.body for r in rows] == ["before", "dinner at 9", "after"], \
        "the edit must fold AT the original's position, not append at the end"
    edited = rows[1]
    assert edited.id == original and edited.ts == 1
    assert json.loads(edited.raw_json) == {"orig": 1}
    assert edited.edit == RowEdit(ts=9, was="dinner at 7", raw_json=json.dumps({"edit": 9}))
    assert rows[0].edit is None and rows[2].edit is None


async def test_since_reads_fold_edits_too(store):
    """One select for every transcript read: the Dreamer's cursor and the
    standing fold must not read the text the member replaced."""
    original = await store.append(message=_msg(body="dinner at 7", ts=1))
    await store.append_edit(message_id=original, message=_edit("dinner at 8", 5))
    for rows in (
        await store.since(conversation_id="g1", after_id=0, limit=10),
        await store.since_including_nora(conversation_id="g1", after_id=0, limit=10),
    ):
        assert [r.body for r in rows] == ["dinner at 8"]
        assert rows[0].edit is not None and rows[0].edit.was == "dinner at 7"


async def test_an_edit_is_found_by_the_platforms_handle(store):
    """WhatsApp, Instagram and Discord name the edited message by their own handle, which
    `append` stores from `NormalizedMessage.message_id`."""
    base = _msg(platform="whatsapp", body="hi", ts=10)
    row = await store.append(message=dataclasses.replace(base, message_id="3EB0A"))
    found = await store.find_edited(
        conversation_id="g1", target=EditTarget(message_id="3EB0A"), sender_id="",
    )
    assert found is not None and found.id == row
    assert found.platform_message_id == "3EB0A"
    assert await store.find_edited(
        conversation_id="g2", target=EditTarget(message_id="3EB0A"), sender_id="",
    ) is None, "a handle must never resolve across a conversation"


async def test_a_signal_edit_is_found_by_author_and_sent_timestamp(store):
    row = await store.append(message=_msg(body="hi", ts=1700, sender="+852111"))
    await store.append(message=_msg(body="other", ts=1700, sender="+852999"))
    found = await store.find_edited(
        conversation_id="g1", target=EditTarget(sent_ts=1700), sender_id="+852111",
    )
    assert found is not None and found.id == row
    assert await store.find_edited(
        conversation_id="g1", target=EditTarget(sent_ts=1700), sender_id="",
    ) is None, "the timestamp alone is not Signal's address for a message"


async def test_find_edited_returns_the_current_version_and_the_first(store):
    """The gate compares an edit against the version it REPLACES, so the
    lookup carries the newest edit's body and mentions — and the first text
    for Honcho's framing."""
    row = await store.append(message=dataclasses.replace(
        _msg(body="dinner at 7", ts=1), mentions=frozenset({"+852000"}),
    ))
    found = await store.find_edited(
        conversation_id="g1", target=EditTarget(sent_ts=1), sender_id="+852111",
    )
    assert found is not None
    assert (found.body, found.mentions, found.first_body) == \
        ("dinner at 7", frozenset({"+852000"}), "dinner at 7")

    await store.append_edit(message_id=row, message=_edit(
        "dinner at 8", 5, mentions=frozenset({"+852000", "+852555"}),
    ))
    found = await store.find_edited(
        conversation_id="g1", target=EditTarget(sent_ts=1), sender_id="+852111",
    )
    assert found is not None
    assert (found.body, found.mentions, found.first_body) == \
        ("dinner at 8", frozenset({"+852000", "+852555"}), "dinner at 7")


async def test_a_message_that_mentioned_nobody_is_known_not_unknown(store):
    """`[]` and NULL are different answers: NULL is «stored before the column
    existed», which the gate refuses on. A message written today that mentions
    nobody must read as an EMPTY set."""
    await store.append(message=_msg(body="hi", ts=1))
    found = await store.find_edited(
        conversation_id="g1", target=EditTarget(sent_ts=1), sender_id="+852111",
    )
    assert found is not None and found.mentions == frozenset()


async def test_find_edited_knows_whether_the_message_replies_to_nora(store):
    """Read off the RECORDED quote, because an edit event need not repeat it."""
    await store.append(message=_msg(body="hi from nora", ts=1, sender="nora"), is_nora=True)
    await store.append(message=_msg(body="hi back", ts=2, quote=Quote(
        author_id="nora", text="", quoted_ts=1,
    )))
    await store.append(message=_msg(body="unrelated", ts=3))
    reply = await store.find_edited(
        conversation_id="g1", target=EditTarget(sent_ts=2), sender_id="+852111",
    )
    plain = await store.find_edited(
        conversation_id="g1", target=EditTarget(sent_ts=3), sender_id="+852111",
    )
    assert reply is not None and reply.replies_to_nora
    assert plain is not None and not plain.replies_to_nora


async def test_find_edited_carries_the_newest_edit_time(store):
    row = await store.append(message=_msg(body="v1", ts=1))
    found = await store.find_edited(
        conversation_id="g1", target=EditTarget(sent_ts=1), sender_id="+852111",
    )
    assert found is not None and found.edited_ts is None
    await store.append_edit(message_id=row, message=_edit("v2", 7))
    found = await store.find_edited(
        conversation_id="g1", target=EditTarget(sent_ts=1), sender_id="+852111",
    )
    assert found is not None and found.edited_ts == 7


async def test_an_edit_that_could_not_see_mentions_records_unknown(store):
    """`EditTarget.mentions_known=False` with nothing carried forward: NULL,
    never `[]` — the platform never said the edit mentioned nobody."""
    row = await store.append(message=_msg(body="v1", ts=1))
    await store.append_edit(message_id=row, message=dataclasses.replace(
        _edit("v2", 7), edit_of=EditTarget(sent_ts=1, mentions_known=False),
    ))
    found = await store.find_edited(
        conversation_id="g1", target=EditTarget(sent_ts=1), sender_id="+852111",
    )
    assert found is not None and found.mentions is None


async def test_a_second_signal_edit_naming_the_first_edits_timestamp_finds_the_message(store):
    """A Signal client edits the revision in front of it, so a second edit can name the FIRST
    EDIT's sent-timestamp. Matching the original's `ts` alone would orphan every edit
    after the first. Still keyed on the author: another sender's edit at that timestamp
    finds nothing."""
    row = await store.append(message=_msg(body="v1", ts=1))
    await store.append_edit(message_id=row, message=_edit("v2", 7))
    found = await store.find_edited(
        conversation_id="g1", target=EditTarget(sent_ts=7), sender_id="+852111",
    )
    assert found is not None and found.id == row and found.body == "v2"


async def test_a_signal_lookup_prefers_the_same_sender_but_does_not_require_it(store):
    """signal-cli can name one sender by phone on a message and by ACI on its
    edit, so an exact-id filter orphaned the edit. The lookup PREFERS the
    same id when two messages share a millisecond, and otherwise returns the
    message for the caller to judge authorship by person."""
    a = await store.append(message=_msg(body="from a", ts=50, sender="+852111"))
    b = await store.append(message=_msg(body="from b", ts=50, sender="+852999"))
    for sender, expected in (("+852111", a), ("+852999", b), ("aci-of-a", a)):
        found = await store.find_edited(
            conversation_id="g1", target=EditTarget(sent_ts=50), sender_id=sender,
        )
        assert found is not None and found.id == expected, sender


async def test_quoted_ts_of_points_a_revision_at_its_message(store):
    """Signal quotes the revision the replier saw; the transcript keeps the
    message at its original ts. An exact message match always wins."""
    row = await store.append(message=_msg(body="v1", ts=1))
    await store.append_edit(message_id=row, message=_edit("v2", 7))
    await store.append(message=_msg(body="other", ts=9))
    assert await store.quoted_ts_of(conversation_id="g1", ts=7) == 1
    assert await store.quoted_ts_of(conversation_id="g1", ts=9) == 9
    assert await store.quoted_ts_of(conversation_id="g1", ts=12345) == 12345
    assert await store.quoted_ts_of(conversation_id="g2", ts=7) == 7


# ---------------------------------------------------------------------------
# activity — how busy a room is, for `room_list`
# ---------------------------------------------------------------------------


async def test_activity_counts_human_rows_since_the_cutoff_and_the_newest_ts(store):
    """`room_list`'s busyness read: how many PEOPLE's messages arrived at or
    after `since_ts`, and when a person last spoke. Nora's own rows are not
    the room being busy, so they count for neither."""
    for ts, is_nora in [(10, False), (20, False), (30, True), (40, False)]:
        await store.append(message=_msg(ts=ts), is_nora=is_nora)

    got = await store.room_activity(conversation_id="g1", since_ts=20)

    assert got.count == 2, "ts 20 and 40; the Nora row at 30 is not counted"
    assert got.last_ts == 40


async def test_activity_is_scoped_to_the_conversation(store):
    await store.append(message=_msg(conv="g1", ts=5))
    await store.append(message=_msg(conv="g2", ts=50))
    await store.append(message=_msg(conv="g2", ts=60))

    got = await store.room_activity(conversation_id="g1", since_ts=0)

    assert (got.count, got.last_ts) == (1, 5)


async def test_activity_of_a_room_with_no_human_rows_is_zero_and_no_time(store):
    """«Nobody has said anything» is `last_ts is None`, never a zero timestamp
    the tool would render as 1970."""
    await store.append(message=_msg(ts=7), is_nora=True)

    got = await store.room_activity(conversation_id="g1", since_ts=0)

    assert (got.count, got.last_ts) == (0, None)
    empty = await store.room_activity(conversation_id="nope", since_ts=0)
    assert (empty.count, empty.last_ts) == (0, None)


async def test_activity_counts_distinct_speakers_in_the_window(store):
    """People, not rows: asked which group has the most PEOPLE talking, a message count lets
    one person account for twenty lines. Distinct speakers at or after the cutoff; Nora is
    not one of them; a speaker seen only before the cutoff does not count."""
    for ts, sender in [(20, "+852111"), (21, "+852111"), (22, "+852111"),
                       (25, "+852222"), (5, "+852333")]:
        await store.append(message=_msg(ts=ts, sender=sender))
    await store.append(message=_msg(ts=30, sender="nora"), is_nora=True)

    got = await store.room_activity(conversation_id="g1", since_ts=20)

    assert got.count == 4
    assert got.speakers == 2


async def test_activity_counts_one_person_with_two_accounts_once(store):
    """A resolved `person_id` is the person; the raw sender id stands in only
    for a row nobody resolved (DEC-14: one human, several accounts)."""
    await store.append(message=_msg(ts=10, sender="+852111"), person_id="miles")
    await store.append(message=_msg(ts=11, sender="+852999"), person_id="miles")
    await store.append(message=_msg(ts=12, sender="+852222"))

    got = await store.room_activity(conversation_id="g1", since_ts=0)

    assert got.speakers == 2


async def test_activity_falls_back_to_the_sender_only_on_a_null_person_id(store):
    """COALESCE semantics, pinned on both stores so they cannot drift: only a
    NULL `person_id` falls through to the sender id; any stored value, even an
    empty one, is the key as it stands."""
    await store.append(message=_msg(ts=10, sender="+852111"), person_id="")
    await store.append(message=_msg(ts=11, sender="+852222"), person_id="")

    got = await store.room_activity(conversation_id="g1", since_ts=0)

    assert got.speakers == 1


async def test_activity_last_ts_is_not_bounded_by_the_window(store):
    """The newest message can be older than the window: a room quiet for a
    week still says when it last spoke, with a count of zero."""
    await store.append(message=_msg(ts=3))

    got = await store.room_activity(conversation_id="g1", since_ts=100)

    assert (got.count, got.last_ts) == (0, 3)


# ── reactions and deletes ──────────────────────────────────────────────────


def _touch(kind: str, *, ts: int, sender: str = "+852222", emoji: str = "",
           removed: bool = False) -> NormalizedMessage:
    from nora.domain.models import Touch

    return NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id=sender, sender_name=None, timestamp=ts, body="",
        raw={"touch": kind, "ts": ts},
        touch=Touch(kind=kind, target=EditTarget(sent_ts=1), emoji=emoji,  # type: ignore[arg-type]
                    removed=removed),
    )


async def _touch_rows(store) -> tuple[list[tuple], list[tuple]]:
    """(reactions, deletes) as tuples, read from whichever store this is —
    the port has no read for them on purpose (nothing reads them yet)."""
    if isinstance(store, FakeRecordStore):
        return (
            [(r["message_id"], r["sender_id"], r["person_id"], r["emoji"],
              bool(r["removed"]), r["ts"], json.loads(r["raw_json"]))
             for r in store.reactions],
            [(d["message_id"], d["sender_id"], d["person_id"], d["ts"],
              json.loads(d["raw_json"])) for d in store.deletes],
        )
    import sqlalchemy as sa

    from nora.infra.sql.schema import MESSAGE_DELETE, MESSAGE_REACTION

    async with store._engine.connect() as conn:
        reactions = (await conn.execute(sa.select(
            MESSAGE_REACTION.c.message_id, MESSAGE_REACTION.c.sender_id,
            MESSAGE_REACTION.c.person_id, MESSAGE_REACTION.c.emoji,
            MESSAGE_REACTION.c.removed, MESSAGE_REACTION.c.ts,
            MESSAGE_REACTION.c.raw_json,
        ).order_by(MESSAGE_REACTION.c.id))).all()
        deletes = (await conn.execute(sa.select(
            MESSAGE_DELETE.c.message_id, MESSAGE_DELETE.c.sender_id,
            MESSAGE_DELETE.c.person_id, MESSAGE_DELETE.c.ts, MESSAGE_DELETE.c.raw_json,
        ).order_by(MESSAGE_DELETE.c.id))).all()
    return (
        [(r[0], r[1], r[2], r[3], bool(r[4]), r[5], json.loads(r[6])) for r in reactions],
        [(d[0], d[1], d[2], d[3], json.loads(d[4])) for d in deletes],
    )


async def test_a_reaction_and_its_removal_are_two_rows_beside_the_message(store):
    """Append-only: taking a reaction back is its own row, and neither is a
    `messages` row, so the transcript is unchanged."""
    target = await store.append(message=_msg(body="dinner at 8", ts=1))
    first = await store.append_reaction(
        message_id=target, message=_touch("reaction", ts=5, emoji="👍"), person_id="p2")
    second = await store.append_reaction(
        message_id=target, message=_touch("reaction", ts=9, emoji="👍", removed=True),
        person_id="p2")
    assert second > first
    reactions, deletes = await _touch_rows(store)
    assert reactions == [
        (target, "+852222", "p2", "👍", False, 5, {"touch": "reaction", "ts": 5}),
        (target, "+852222", "p2", "👍", True, 9, {"touch": "reaction", "ts": 9}),
    ]
    assert deletes == []
    rows = await store.recent(conversation_id="g1", limit=10)
    assert [r.body for r in rows] == ["dinner at 8"]


async def test_hide_message_hides_once_and_find_edited_says_so(store):
    """A platform delete hides its target (`hide_message`): the first call hides it, a
    second says False (the first timestamp is kept), a row that does not exist says False,
    and the lookup an edit uses still finds the row but reports it hidden."""
    target = await store.append(message=_msg(body="dinner at 8", ts=1))
    found = await store.find_edited(
        conversation_id="g1", target=EditTarget(sent_ts=1), sender_id="+852111")
    assert found is not None and found.hidden is False
    assert await store.hide_message(message_id=target, now_ms=100) is True
    assert await store.hide_message(message_id=target, now_ms=200) is False
    assert await store.hide_message(message_id=target + 999, now_ms=100) is False
    found = await store.find_edited(
        conversation_id="g1", target=EditTarget(sent_ts=1), sender_id="+852111")
    assert found is not None and found.hidden is True


async def test_a_delete_is_recorded_and_the_message_still_reads(store):
    """The delete is the Record item; hiding the row is `hide_message`'s."""
    target = await store.append(message=_msg(body="dinner at 8", ts=1))
    did = await store.append_delete(
        message_id=target, message=_touch("delete", ts=7, sender=""), person_id=None)
    assert did >= 1
    _, deletes = await _touch_rows(store)
    assert deletes == [(target, "", None, 7, {"touch": "delete", "ts": 7})]
    rows = await store.recent(conversation_id="g1", limit=10)
    assert [r.body for r in rows] == ["dinner at 8"]
