"""The receipt/Record writer contract, shared by SQL and the in-memory double."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.memory_graph import Admission, Evidence, Relation
from nora.infra.sql.comprehend import SqlComprehendRecords
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import (
    COMPREHEND_RESULT,
    COMPREHEND_WEB_PAGE,
    EXTRACTION,
    EXTRACTION_CLAIM,
)
from nora.testing.comprehend import InMemoryComprehendRecords
from tests.conftest import _async_url
from tests.unit.test_comprehend import ITEM, PERSON, PLACE, SOURCE, UNIT, WORK


@pytest.fixture(scope="module")
def database(pg_admin, pg_template):
    name = "comprehend_" + uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (pg_template,),
        )
        conn.execute(f'CREATE DATABASE "{name}" TEMPLATE "{pg_template}"')
    yield _async_url(pg_admin, name)
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (name,),
        )
        conn.execute(f'DROP DATABASE "{name}"')


@pytest.fixture(params=["fake", "sql"])
async def store(request, database):
    if request.param == "fake":
        yield InMemoryComprehendRecords(), None
        return
    engine = create_sql_engine(database)
    # Schema is module-scoped; isolate rows, not schemas.
    async with engine.begin() as conn:
        # History tables refuse TRUNCATE; DELETE retains their audit history.
        await conn.execute(sa.delete(COMPREHEND_RESULT))
        await conn.execute(sa.delete(COMPREHEND_WEB_PAGE))
        await conn.execute(sa.delete(EXTRACTION_CLAIM))
        await conn.execute(sa.delete(EXTRACTION))
    yield (
        SqlComprehendRecords(engine, media_join=None, rooms_by_unit={}, persons_by_unit={}),
        engine,
    )
    await engine.dispose()


async def test_retained_page_is_exact_read_text_with_hash_and_tool_call(store):
    records, engine = store
    text = "Generated public venue page"
    call = {
        "name": "web_fetch",
        "toolUseId": "generated-call",
        "input": {"url": "https://example.invalid"},
    }
    key = await records.web_page(
        unit=UNIT,
        session_id=1,
        url="https://example.invalid",
        fetched_at=datetime.now(UTC),
        text=text,
        tool_call=call,
    )
    assert key[0] == "web_page"
    if engine:
        async with engine.connect() as conn:
            page = (await conn.execute(sa.select(COMPREHEND_WEB_PAGE))).mappings().one()
    else:
        page = records.pages[0]
    import hashlib

    assert page["text"] == text and page["tool_call"] == call
    assert page["content_hash"] == hashlib.sha256(text.encode()).hexdigest()


async def test_graph_receipt_is_retained_and_shadow_is_a_would_write(store):
    records, engine = store
    relation = Relation(
        "generated-relation",
        PERSON.key,
        "likes",
        PLACE.key,
        "notebook",
        UNIT,
        asserted_at=WORK.as_of,
    )
    admission = Admission(
        relations=(relation,), evidence=(Evidence(SOURCE.key, relation_id=relation.id),)
    )
    await records.graph_write(WORK, 1, admission)
    await records.shadow(WORK, 2, PERSON.key, [{"part": "trait", "text": "Generated trait"}], 1)
    if engine:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(COMPREHEND_RESULT.c.payload).where(
                        COMPREHEND_RESULT.c.part == "reshape"
                    )
                )
            ).scalar_one()
    else:
        row = records.shadows[0]
    assert row["would_writes"][0]["text"] == "Generated trait"


async def test_reading_writer_is_idempotent_per_work_hash(store):
    records, engine = store
    claims = [{"what": "Generated claim", "source_ids": [1]}]
    await records.readings(WORK, 1, ITEM, claims)
    await records.readings(WORK, 2, ITEM, claims)
    if engine:
        async with engine.connect() as conn:
            rows = (await conn.execute(sa.select(EXTRACTION_CLAIM.c.content))).scalars().all()
        assert len(rows) == 1
        assert rows[0]["writer"] == "cognition:comprehend"
    else:
        assert len(records.claims) == 1


async def test_a_retry_persists_additional_distinct_claims(store):
    """A partial completion retry for the same work must not drop the new
    claim — the writer merges additional distinct admitted claims."""
    records, engine = store
    first = [{"what": "Generated first claim", "source_ids": [1]}]
    second = [{"what": "Generated second claim", "source_ids": [1]}]
    await records.readings(WORK, 1, ITEM, first)
    stored = await records.readings(WORK, 2, ITEM, second)
    if engine:
        async with engine.connect() as conn:
            rows = (await conn.execute(
                sa.select(EXTRACTION_CLAIM.c.content)
                .order_by(EXTRACTION_CLAIM.c.ordinal))).scalars().all()
        assert len(rows) == 2
    else:
        assert len(records.claims[(WORK.id, WORK.input_hash)]) == 2
    assert first[0] in stored and second[0] in stored


async def test_refusals_keep_reason_without_private_content(store):
    records, engine = store
    await records.record_refusal(1, "relation", "unseen_source")
    if engine:
        async with engine.connect() as conn:
            row = (await conn.execute(sa.select(COMPREHEND_RESULT.c.payload))).scalar_one()
        assert row == {"part": "relation", "reason": "unseen_source"}
    else:
        assert records.refusals == [(1, "relation", "unseen_source")]


async def test_load_missing_message_projection_raises_no_result_found(store):
    """An exact `("message", <id>)` source with no projection row is
    `NoResultFound` in BOTH implementations — the wake's grounded-text
    fallback (`nora.app.cognition_memory`) catches exactly that, so the fake
    cannot silently return a different failure shape than SQL. The SQL
    positive control is `test_sql_source_reader_joins_...` above."""
    records, engine = store
    from sqlalchemy.exc import NoResultFound

    from nora.domain.memory_graph import WorkItem

    work = WorkItem(
        "generated-missing-work", ("message", "404042042"), UNIT,
        "generated-hash", WORK.as_of,
    )
    with pytest.raises(NoResultFound):
        await records.load(work)
    if engine is None:
        # The fake distinguishes absent from present rather than always
        # raising: seed the key and the same call returns the item.
        records.items[work.source] = ITEM
        assert (await records.load(work)).work == work


async def test_sql_source_reader_joins_the_reply_parent_and_refuses_another_unit(store):
    records, engine = store
    if engine is None:
        # The fake's source inventory is explicit; SQL's projection join is
        # separately exercised here rather than simulated by a constant answer.
        return
    from dataclasses import replace

    from nora.domain.memory_graph import WorkItem
    from nora.domain.models import NormalizedMessage, Quote
    from nora.infra.sql.audit import SqlRecordStore

    async def enrich(rows, **kw):
        return tuple(rows)

    records.media_join = SimpleNamespace(enrich=enrich)
    records.rooms_by_unit = {UNIT: [("signal", "generated-room")]}
    record = SqlRecordStore(engine)
    msg = NormalizedMessage(
        "signal",
        "generated-room",
        "group",
        "generated-sender",
        "Generated Speaker",
        1000,
        "Generated parent",
        {},
    )
    parent = await record.append(message=msg, person_id="generated-person")
    child = await record.append(
        message=replace(
            msg,
            timestamp=2000,
            body="Generated reply",
            quote=Quote(quoted_ts=1000, author_id="generated-sender", text="Untrusted fake parent"),
        ),
        person_id="generated-person",
    )
    work = WorkItem(
        "sql-generated-work", ("message", str(child)), UNIT, "generated-hash", WORK.as_of
    )
    item = await records.load(work)
    assert item.reply_parent == ("message", str(parent))
    assert [s.text for s in item.sources] == ["Generated reply", "Generated parent"]
    assert "Untrusted fake parent" not in item.text
    assert item.snapshot["messages"][0]["reply_to"] == parent
    import json
    batch = await records.load(replace(work, source=("message_batch", json.dumps([parent, child]))))
    assert {r["id"] for r in batch.snapshot["messages"]} == {parent, child}
    assert {s.key for s in batch.sources} == {("message", str(parent)), ("message", str(child))}
    with pytest.raises(ValueError, match="unscoped source"):
        await records.load(replace(work, unit="another-unit"))


async def test_historical_source_rows_do_not_read_a_future_edit(store):
    _, engine = store
    if engine is None:
        return
    from nora.domain.models import NormalizedMessage
    from nora.infra.sql.audit import SqlRecordStore
    from nora.infra.sql.extraction import source_rows
    from nora.infra.sql.schema import MESSAGE_EDIT

    record = SqlRecordStore(engine)
    mid = await record.append(
        message=NormalizedMessage(
            "signal",
            "clock-room",
            "group",
            "generated-sender",
            "Generated Speaker",
            1000,
            "Original generated text",
            {},
        ),
        person_id="generated-person",
    )
    async with engine.begin() as conn:
        await conn.execute(
            sa.insert(MESSAGE_EDIT).values(message_id=mid, ts=3000, body="Later generated text")
        )
        old = await source_rows(conn, [mid], as_of_ms=2000)
        new = await source_rows(conn, [mid], as_of_ms=4000)
    assert old[0]["body"] == "Original generated text"
    assert new[0]["body"] == "Later generated text"
    assert new[0]["ts"] == 3000
    assert old[0]["ts"] == 1000
    from nora.infra.sql.extraction import link_replies
    reply = {"id": 999, "ts": 3500, "quoted_ts": 1000}
    link_replies([reply], new)
    assert reply["reply_to"] == mid


async def test_stimulus_failures_park_at_five_and_operator_requeue_resets(store):
    records, _ = store
    for n in range(5):
        assert await records.failures(UNIT, 17) == n
        await records.fail_stimulus(UNIT, 17, 0)
    assert await records.failures(UNIT, 17) == 5
    assert await records.failures(UNIT, 18) == 0
    assert await records.failures(UNIT, 17, reset=True) == 0


async def test_register_retry_retains_existing_row_id(store):
    records, _ = store
    assert await records.act_result("act:generated", 0) is None
    result = {"status": "ok", "note_id": "generated-existing-note"}
    await records.act_result("act:generated", 0, result)
    assert await records.act_result("act:generated", 1) == result


async def test_a_withheld_social_item_is_skipped_but_projection_lag_still_fails(store):
    """Node 33 audit: a hidden social item has no projection by design and is
    withheld (skipped, never read); a visible item that is not projected yet
    still fails so the wake retries. SQL only: the fake has no social rows."""
    records, engine = store
    if engine is None:
        pytest.skip("social rows exist only in the SQL store")
    from sqlalchemy.exc import NoResultFound

    from nora.domain.comprehend import WithheldSourceError
    from nora.domain.memory_graph import WorkItem
    from nora.infra.sql.schema import SOCIAL_ITEM, SOCIAL_ITEM_STATE

    read = str(uuid4())
    at = datetime(2026, 1, 2, tzinfo=UTC)
    async with engine.begin() as conn:
        for item in ("generated-visible", "generated-hidden"):
            await conn.execute(sa.insert(SOCIAL_ITEM).values(
                network="instagram", item_id=item, person="generated-person",
                account="generated-account", source_kind="post", taken_at=at, seen_at=at,
                origin="poll", read_id=read))
        await conn.execute(sa.insert(SOCIAL_ITEM_STATE).values(
            network="instagram", item_id="generated-hidden", source_state="hidden"))

    def work(source):
        return WorkItem("generated-social-work", source, UNIT, "generated-hash", WORK.as_of)

    with pytest.raises(WithheldSourceError):
        await records.load(work(("social", "instagram:generated-hidden")))
    with pytest.raises(NoResultFound):  # absent is unknown, never withheld
        await records.load(work(("social", "instagram:never-recorded")))
    with pytest.raises(NoResultFound):
        await records.load(work(("social", "instagram:generated-visible")))
    async with engine.begin() as conn:
        await conn.execute(sa.update(SOCIAL_ITEM).where(
            SOCIAL_ITEM.c.item_id == "generated-visible").values(read_id=None))
    with pytest.raises(WithheldSourceError):
        await records.load(work(("social_read", read)))


async def test_a_quiet_session_has_one_completion_receipt_independent_of_readings(store):
    records, engine = store
    await records.complete_session("generated-unit", 77, {"1": "digest-one", "2": "digest-two"})
    if engine is None:
        payloads = [r["payload"] for r in records.receipts if r["part"] == "session_complete"]
    else:
        async with engine.connect() as conn:
            payloads = list((await conn.execute(sa.select(COMPREHEND_RESULT.c.payload).where(
                COMPREHEND_RESULT.c.part == "session_complete"))).scalars())
    assert payloads == [{"unit": "generated-unit",
                         "inputs": {"1": "digest-one", "2": "digest-two"}}]


# ---- selected managed backend: SQL-only — `access` answers membership and
# preference at read time; the fake's contract is the shared suite above.
_MUNIT = "generated-managed-unit"
_MROOM = "generated-managed-room"
_MP = "generated-managed-person"
_MF = "generated-managed-foreign"


async def _managed_records(engine, *, social, members, unit=_MUNIT, room=_MROOM):
    """`SqlComprehendRecords` on real SQL + the in-memory authority fakes."""
    from nora.access import IdentityAccess
    from nora.domain.identity import Identifier
    from nora.domain.managed import (
        AccountState,
        ManagedAccount,
        ManagedPerson,
        OutcomeStatus,
        PersonLifecycle,
    )
    from nora.domain.management import Scope, ScopeKind
    from nora.people.app.managed_authority import AuthorityService
    from nora.testing.managed import (
        FakeAuthorityBackend,
        FakeBindingStore,
        FakeIdentityRegistry,
        FakeTopologySource,
    )

    async def enrich(rows, **kw):
        return tuple(rows)

    bindings = FakeBindingStore()
    backend = FakeAuthorityBackend()
    topology = FakeTopologySource(
        rooms={f"signal/{room}": (unit, False)},
        units={unit: "generated-workspace"}, workspaces={"generated-workspace"})
    registry = FakeIdentityRegistry()
    for pid, traits in social.items():
        assert (await bindings.register(
            person=ManagedPerson(pid, PersonLifecycle.ACTIVE),
            account=ManagedAccount(
                str(uuid4()), pid, "signal",
                frozenset({Identifier("signal", "uuid", f"{pid}-account")}),
                AccountState.BOUND),
            membership=None,
        )).status is OutcomeStatus.COMPLETE
        outcome, _ = await registry.ensure_identity(external_id=pid, traits=traits)
        assert outcome.status is OutcomeStatus.COMPLETE
    for pid in members:
        assert (await bindings.set_membership(
            person_id=pid, scope=Scope(ScopeKind.ROOM, f"signal/{room}"),
            member=True, source="generated-source",
        )).status is OutcomeStatus.COMPLETE
    access = IdentityAccess(
        bindings=bindings, authority=AuthorityService(bindings, backend, topology),
        topology=topology, registry=registry)
    records = SqlComprehendRecords(
        engine, media_join=SimpleNamespace(enrich=enrich),
        rooms_by_unit={unit: [("signal", room)]}, persons_by_unit={},
        access=access)
    return records, bindings, registry


async def _insert_node(engine, *, kind, source_id, unit, persons=(), room="", text):
    from nora.infra.sql.schema import MEMORY_NODE

    async with engine.begin() as conn:
        await conn.execute(sa.insert(MEMORY_NODE).values(
            kind=kind, source_id=source_id, unit=unit, origin_room=room,
            persons=list(persons), text=text))


async def _insert_social(engine, *, network="instagram", item, person):
    from nora.infra.sql.schema import SOCIAL_ITEM

    at = datetime(2026, 1, 2, tzinfo=UTC)
    async with engine.begin() as conn:
        await conn.execute(sa.insert(SOCIAL_ITEM).values(
            network=network, item_id=item, person=person,
            account="generated-account", kind="post", taken_at=at, seen_at=at,
            origin="poll"))


def _mwork(source):
    from nora.domain.memory_graph import WorkItem

    return WorkItem("generated-managed-work", source, _MUNIT, "generated-hash", WORK.as_of)


async def test_managed_social_read_checks_the_live_preference_at_read_time(store):
    """An opt-out recorded AFTER the records store was built still withholds:
    the preference is read for the source's own people on every load."""
    _records, engine = store
    if engine is None:
        return
    records, _bindings, registry = await _managed_records(
        engine, social={_MP: {"display_name": _MP, "social": True}}, members=(_MP,))
    await _insert_social(engine, item="generated-item", person=_MP)
    await _insert_node(
        engine, kind="social", source_id="instagram:generated-item",
        unit=_MUNIT, persons=(_MP,), room="signal:generated-managed-room",
        text="Generated caption")
    item = await records.load(_mwork(("social", "instagram:generated-item")))
    assert item.sources[0].text == "Generated caption"
    assert item.sources[0].persons == (_MP,)
    # The construction-time answer would still say enabled; the live read says
    # opted out, and the withheld distinction is the existing ValueError.
    registry.identities[_MP]["traits"]["social"] = False
    with pytest.raises(ValueError, match="social consent withdrawn"):
        await records.load(_mwork(("social", "instagram:generated-item")))


async def test_managed_social_read_is_unavailable_when_the_trait_is_missing(store):
    """A missing or malformed `social` trait is unavailable — never inferred
    enabled, never a withheld-looking refusal either."""
    _records, engine = store
    if engine is None:
        return
    from nora.access import SocialPreferenceUnavailableError

    for i, traits in enumerate(
        ({"display_name": _MP}, {"display_name": _MP, "social": "yes"})
    ):
        item_id = f"generated-broken-{i}"
        records, _bindings, registry = await _managed_records(
            engine, social={_MP: traits}, members=(_MP,))
        await _insert_social(engine, item=item_id, person=_MP)
        await _insert_node(
            engine, kind="social", source_id=f"instagram:{item_id}",
            unit=_MUNIT, persons=(_MP,), room="signal:generated-managed-room",
            text="Generated caption")
        with pytest.raises(SocialPreferenceUnavailableError):
            await records.load(_mwork(("social", f"instagram:{item_id}")))
        # Positive control: the same source reads once the trait answers.
        registry.identities[_MP]["traits"]["social"] = True
        item = await records.load(_mwork(("social", f"instagram:{item_id}")))
        assert item.sources[0].text == "Generated caption"


async def test_managed_source_admission_uses_current_room_membership(store):
    """A global person-scoped source admits through the unit's configured
    rooms' CURRENT managed memberships — the file `persons` list wired as
    `persons_by_unit` is never consulted, and a post-construction departure
    closes it."""
    _records, engine = store
    if engine is None:
        return
    from nora.domain.managed import OutcomeStatus
    from nora.domain.management import Scope, ScopeKind

    records, bindings, _ = await _managed_records(
        engine,
        social={_MP: {"display_name": _MP, "social": True},
                _MF: {"display_name": _MF, "social": True}},
        members=(_MP,),
        # `_MF` is a member of a room in ANOTHER unit — never of this one.
    )
    await _insert_node(
        engine, kind="note", source_id="generated-note", unit=None,
        persons=(_MP,), text="Generated note text")
    item = await records.load(_mwork(("note", "generated-note")))
    assert item.sources[0].text == "Generated note text"
    assert item.sources[0].persons == (_MP,)
    # A departure recorded after construction denies the next read.
    assert (await bindings.set_membership(
        person_id=_MP, scope=Scope(ScopeKind.ROOM, "signal/generated-managed-room"),
        member=False, source="generated-source",
    )).status is OutcomeStatus.COMPLETE
    with pytest.raises(ValueError, match="unscoped source"):
        await records.load(_mwork(("note", "generated-note")))


async def test_managed_source_admission_denies_unit_and_guild_only_members(store):
    """Membership of a room in a SIBLING unit is not membership here: a
    globally person-scoped node naming only that person stays unscoped."""
    _records, engine = store
    if engine is None:
        return
    from nora.domain.managed import OutcomeStatus
    from nora.domain.management import Scope, ScopeKind

    records, bindings, _ = await _managed_records(
        engine,
        social={_MP: {"display_name": _MP, "social": True},
                _MF: {"display_name": _MF, "social": True}},
        members=(_MP,),
    )
    assert (await bindings.set_membership(
        person_id=_MF, scope=Scope(ScopeKind.ROOM, "signal/generated-elsewhere"),
        member=True, source="generated-source",
    )).status is OutcomeStatus.COMPLETE
    await _insert_node(
        engine, kind="note", source_id="generated-sibling-note", unit=None,
        persons=(_MF,), text="Generated sibling note")
    with pytest.raises(ValueError, match="unscoped source"):
        await records.load(_mwork(("note", "generated-sibling-note")))
    # Positive control: the actual member's node reads.
    await _insert_node(
        engine, kind="note", source_id="generated-member-note", unit=None,
        persons=(_MP,), text="Generated member note")
    item = await records.load(_mwork(("note", "generated-member-note")))
    assert item.sources[0].text == "Generated member note"


async def test_managed_admission_folds_a_retired_alias_for_permission(store):
    """A node recorded under a retired id admits when its canonical survivor
    is a member — the historical reference itself is never rewritten."""
    _records, engine = store
    if engine is None:
        return
    from nora.domain.managed import OutcomeStatus

    former = "generated-managed-former"
    records, bindings, _ = await _managed_records(
        engine,
        social={_MP: {"display_name": _MP, "social": True},
                former: {"display_name": former, "social": True}},
        members=(_MP,),
    )
    assert (await bindings.consolidate(survivor=_MP, former=former)
            ).status is OutcomeStatus.COMPLETE
    await _insert_node(
        engine, kind="note", source_id="generated-alias-note", unit=None,
        persons=(former,), text="Generated aliased note")
    item = await records.load(_mwork(("note", "generated-alias-note")))
    assert item.sources[0].persons == (former,)
    assert item.sources[0].text == "Generated aliased note"


async def test_a_retired_opted_out_identity_still_withholds_social_sources(store):
    """Consolidation folds the opt-out too: a social node recorded under the
    retired id is withheld because the retired identity's `social = false`
    still applies — the opt-out is the person's, not the id's."""
    _records, engine = store
    if engine is None:
        return
    from nora.domain.managed import OutcomeStatus

    former = "generated-managed-former"
    records, bindings, _ = await _managed_records(
        engine,
        social={_MP: {"display_name": _MP, "social": True},
                former: {"display_name": former, "social": False},
                _MF: {"display_name": _MF, "social": True}},
        members=(_MP, _MF),
    )
    assert (await bindings.consolidate(survivor=_MP, former=former)
            ).status is OutcomeStatus.COMPLETE
    await _insert_social(engine, item="generated-former-item", person=former)
    await _insert_node(
        engine, kind="social", source_id="instagram:generated-former-item",
        unit=_MUNIT, persons=(former,), room="signal:generated-managed-room",
        text="Generated former caption")
    with pytest.raises(ValueError, match="social consent withdrawn"):
        await records.load(_mwork(("social", "instagram:generated-former-item")))
    # Positive control: another member with no opted-out identity reads.
    await _insert_social(engine, item="generated-live-item", person=_MF)
    await _insert_node(
        engine, kind="social", source_id="instagram:generated-live-item",
        unit=_MUNIT, persons=(_MF,), room="signal:generated-managed-room",
        text="Generated live caption")
    item = await records.load(_mwork(("social", "instagram:generated-live-item")))
    assert item.sources[0].text == "Generated live caption"


async def test_managed_authority_outage_is_unavailable_and_recovers(store):
    """A binding-store outage on the membership or preference read propagates
    as a failure (the wake retries it) — never an allowed answer; recovery
    restores the same positive read."""
    _records, engine = store
    if engine is None:
        return
    records, bindings, _ = await _managed_records(
        engine, social={_MP: {"display_name": _MP, "social": True}}, members=(_MP,))
    await _insert_node(
        engine, kind="note", source_id="generated-outage-note", unit=None,
        persons=(_MP,), text="Generated outage note")
    positive = await records.load(_mwork(("note", "generated-outage-note")))
    assert positive.sources[0].text == "Generated outage note"
    bindings.down = True
    with pytest.raises(RuntimeError):
        await records.load(_mwork(("note", "generated-outage-note")))
    bindings.down = False
    recovered = await records.load(_mwork(("note", "generated-outage-note")))
    assert recovered.sources[0].text == "Generated outage note"
