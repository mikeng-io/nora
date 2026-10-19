"""Typed Identity and recoverable oldest-first evidence through real Postgres."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from nora.domain.persona import (
    AddOp,
    PersonaView,
    ReviseOp,
    identity_text,
)
from nora.infra.sql.character import SqlCharacterEvidence
from nora.infra.sql.schema import MESSAGES, PERSONA_LINE
from nora.language.persona_render import account_text
from tests.infra.test_persona_store import ACCOUNT, CITABLE, KIT, NOW, _write
from tests.infra.test_persona_store import env as env


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("birthday", {"month": 2, "day": 29}),
        ("attribute", {"name": "height", "value": 178, "unit": "cm"}),
        ("attribute", {"name": "age", "value": 34, "unit": "years"}),
        ("attribute", {"name": "hobbies", "value": ["cycling", "reading"]}),
        ("attribute", {"name": "owns_car", "value": False}),
        ("gender", "woman"),
        ("sex", "female"),
        ("residence", "Hong Kong"),
        ("languages", ["Cantonese", "English"]),
        ("dietary_restrictions", []),
        ("food_preferences", ["noodles"]),
        ("relationship", {"relation": "sister", "unresolved_name": "Jo"}),
    ],
)
async def test_typed_values_round_trip_and_override_conflicting_model_text(env, key, value):
    kind = "relationship" if key == "relationship" else "attribute"
    result = await _write(
        env,
        [
            AddOp(
                part="identity",
                kind=kind,
                mode="told",
                text="conflicting model prose",
                citations=("101",),
                told_by=KIT,
                fact_key=key,
                fact_value=value,
            )
        ],
    )
    assert result.status == "ok"
    [line] = await env.store.account_heads(key=ACCOUNT.key)
    assert line.fact_value == value and line.fact_key == key
    assert line.as_of == CITABLE.messages[101].ts and line.written_at == NOW
    rendered = identity_text(line)
    assert "conflicting model prose" not in rendered
    body = account_text(
        PersonaView(entity="person", of="Kit", lines=(line,)),
        name_of=lambda _: "Kit",
        now=datetime.fromtimestamp(NOW / 1000, UTC),
    )
    assert body and rendered in body[0]
    if key == "birthday":
        assert "birthday: 02-29" in rendered and "2000-02-29" not in rendered
    if key == "dietary_restrictions":
        assert "none (explicit)" in rendered


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("birthday", {"month": 2, "day": 30}),
        ("birthday", {"month": 4}),
        ("birthday", {"month": 2, "day": 29, "year": 2025}),
        ("languages", "English"),
        ("gender", []),
        ("dietary_restrictions", None),
        ("relationship", {"relation": "friend", "person_id": "p", "unresolved_name": "X"}),
        ("other", "dinner tomorrow"),
        ("attribute", {"name": "height", "value": None}),
        ("attribute", {"name": "height", "value": {"amount": 178}}),
        ("attribute", {"name": "height", "value": float("nan")}),
        ("attribute", {"name": "birthday", "value": "June 8"}),
    ],
)
async def test_invalid_shapes_are_refused_without_a_row(env, key, value):
    op = AddOp(
        part="identity",
        kind="relationship" if key == "relationship" else "attribute",
        text="claim",
        mode="told",
        citations=("101",),
        told_by=KIT,
        fact_key=key,
        fact_value=value,
    )
    result = await _write(env, [op])
    assert result.status == "refused"
    assert result.refusals[0].reason == "invalid_fact"
    assert await env.store.account_heads(key=ACCOUNT.key) == ()


async def test_residence_correction_keeps_source_effective_and_write_history(env):
    first = AddOp(
        part="identity",
        kind="attribute",
        text="lives here",
        mode="told",
        citations=("101",),
        told_by=KIT,
        fact_key="residence",
        fact_value="London",
        valid_from=10,
        valid_until=20,
    )
    assert (await _write(env, [first])).status == "ok"
    result = await _write(
        env,
        [
            ReviseOp(
                line="P1",
                citations=("102",),
                fact_key="residence",
                fact_value="Hong Kong",
                valid_from=20,
            )
        ],
        now=NOW + 1,
    )
    assert result.status == "ok"
    [head] = await env.store.account_heads(key=ACCOUNT.key)
    assert head.fact_value == "Hong Kong" and head.valid_from == 20
    assert head.valid_until is None and head.as_of == CITABLE.messages[102].ts
    async with env.engine.connect() as conn:
        rows = (
            await conn.execute(sa.select(PERSONA_LINE).order_by(PERSONA_LINE.c.written_at))
        ).all()
    assert [(r.fact_value, r.valid_until) for r in rows] == [("London", 20), ("Hong Kong", None)]


async def test_legacy_prose_is_retained_but_not_promoted(env):
    # A migration leaves all typed fields null on this pre-existing row.
    async with env.engine.begin() as conn:
        await conn.execute(
            sa.insert(PERSONA_LINE).values(
                id="00000000-0000-0000-0000-000000000001",
                persona_key=ACCOUNT.key,
                part="identity",
                kind="attribute",
                text="birthday meal on Saturday",
                mode="told",
                citations=["101"],
                told_by=KIT,
                state="current",
                unit=ACCOUNT.unit,
                written_at=NOW,
                written_by_session="old-shadow",
                support_count=1,
            )
        )
    [line] = await env.store.account_heads(key=ACCOUNT.key)
    assert identity_text(line) == "" and line.fact_value is None
    assert (
        account_text(
            PersonaView(entity="person", of="Kit", lines=(line,)),
            name_of=lambda _: "Kit",
            now=datetime.fromtimestamp(NOW / 1000, UTC),
        )
        is None
    )


async def _message(engine, n, *, person="kit", mentions=None, hidden=False, room="room-a"):
    import json

    async with engine.begin() as conn:
        await conn.execute(
            sa.insert(MESSAGES).values(
                id=n,
                platform="signal",
                conversation_id=room,
                conversation_type="group",
                sender_id="sender",
                person_id=person,
                ts=1000 * n,
                body=f"evidence {n}",
                is_nora=0,
                mention_persons_json=json.dumps(mentions) if mentions is not None else None,
                hidden_at=99 if hidden else None,
            )
        )


async def test_historical_union_freezes_boundary_and_resumes_without_consuming_unread(env):
    for n in range(1, 9):
        await _message(
            env.engine, n, person="kit" if n % 2 else "other", mentions=["kit"] if not n % 2 else []
        )
    reads = SqlCharacterEvidence(env.engine)
    assert await reads.history_state(key=ACCOUNT.key) == (8, 0)
    page = await reads.historical_page(
        rooms=[("signal", "room-a")], person_id="kit", after=0, through=8, limit=3
    )
    assert [m.row.id for m in page] == [1, 2, 3]
    # Failure/conflict has not advanced progress. A fresh reader is a restart.
    assert await SqlCharacterEvidence(env.engine).history_state(key=ACCOUNT.key) == (8, 0)
    await _message(env.engine, 9)
    await reads.advance_history(key=ACCOUNT.key, cursor=3)
    reads = SqlCharacterEvidence(env.engine)
    boundary, cursor = await reads.history_state(key=ACCOUNT.key)
    seen = [1, 2, 3]
    while cursor < boundary:
        page = await reads.historical_page(
            rooms=[("signal", "room-a")], person_id="kit", after=cursor, through=boundary, limit=3
        )
        seen.extend(m.row.id for m in page)
        cursor = page[-1].row.id if page else boundary
        await reads.advance_history(key=ACCOUNT.key, cursor=cursor)
    assert seen == list(range(1, 9))
    assert await reads.history_state(key=ACCOUNT.key) == (8, 8)
    assert [
        m.row.id
        for m in (
            await reads.person_since(
                rooms=[("signal", "room-a")], person_id="kit", after=8, limit=3
            )
        )[0]
    ] == [9]


async def test_a_current_singleton_requires_explicit_correction_and_old_claims_stay_history(env):
    first = AddOp(
        part="identity",
        kind="attribute",
        text="current home",
        mode="told",
        citations=("102",),
        told_by=KIT,
        fact_key="residence",
        fact_value="Hong Kong",
    )
    assert (await _write(env, [first])).status == "ok"
    older = replace(first, citations=("101",), fact_value="London")
    assert (await _write(env, [older], now=NOW + 1)).status == "ok"
    heads = await env.store.account_heads(key=ACCOUNT.key)
    assert [(h.fact_value, h.state) for h in heads] == [
        ("Hong Kong", "current"),
        ("London", "retired"),
    ]
    newer = replace(first, citations=("103",), fact_value="Paris")
    assert (await _write(env, [newer], now=NOW + 2)).status == "refused"


async def test_historical_unsettled_and_conflicted_sessions_hold_the_same_page(env):
    from nora.domain.errors import AccountNotRecordableError
    from nora.domain.models import PersonRef
    from nora.language.character import CharacterRoom, PersonKey
    from nora.language.persona import PersonaEvidence, PersonaPersonAdaptor
    from nora.testing import InMemoryCultureStore

    await _message(env.engine, 1, person=KIT)
    reads = SqlCharacterEvidence(env.engine)
    pk = PersonKey(
        unit=ACCOUNT.unit,
        person=PersonRef(person_id=KIT, name="Kit", honcho_peer_id="kit"),
        rooms=(CharacterRoom(key="room-a", platform="signal", label="Friends"),),
        listed=("room-a",),
        home="room-a",
    )

    def build():
        evidence = PersonaEvidence(
            store=reads, persona=env.store, keys={ACCOUNT.key: pk}, wake_batch=5
        )
        return PersonaPersonAdaptor(
            persona=env.store,
            culture=InMemoryCultureStore(),
            evidence=evidence,
            reads=reads,
            keys={ACCOUNT.key: pk},
            name_of=lambda _: "Kit",
            clock_ms=lambda: NOW,
        )

    adaptor = build()
    # More than the normal abandonment bound must still retain a historical page.
    for _ in range(4):
        page, _ = await adaptor.evidence.newest(key=ACCOUNT.key, after=99, limit=1)
        await adaptor.inputs(key=ACCOUNT.key, batch=page)
        with pytest.raises(AccountNotRecordableError):
            await adaptor.accept(key=ACCOUNT.key, items=[])
    assert await reads.history_state(key=ACCOUNT.key) == (1, 0)
    page, _ = await adaptor.evidence.newest(key=ACCOUNT.key, after=99, limit=1)
    await adaptor.inputs(key=ACCOUNT.key, batch=page)
    _, unchanged = adaptor.verbs(key=ACCOUNT.key)
    await unchanged(why="nothing new")
    await _write(
        env,
        [
            AddOp(
                part="identity",
                kind="role",
                text="organiser",
                mode="told",
                citations=("101",),
                told_by=KIT,
                fact_key="role",
                fact_value="organiser",
            )
        ],
    )
    from nora.domain.ports import Proposal

    staged = await adaptor.accept(key=ACCOUNT.key, items=[])
    with pytest.raises(AccountNotRecordableError):
        await adaptor.commit(
            key=ACCOUNT.key,
            now_ms=NOW,
            proposals=[
                Proposal(
                    payload=s, instruction_sha256="a" * 64, model="test", established_by="test"
                )
                for s in staged
            ],
        )
    assert await reads.history_state(key=ACCOUNT.key) == (1, 0)
    adaptor = build()  # process restart, same durable page
    page, _ = await adaptor.evidence.newest(key=ACCOUNT.key, after=99, limit=1)
    assert [m.row.id for m in page] == [1]
    await adaptor.inputs(key=ACCOUNT.key, batch=page)
    _, unchanged = adaptor.verbs(key=ACCOUNT.key)
    await unchanged(why="no stable facts")
    staged = await adaptor.accept(key=ACCOUNT.key, items=[])
    await adaptor.commit(
        key=ACCOUNT.key,
        now_ms=NOW,
        proposals=[
            Proposal(payload=s, instruction_sha256="a" * 64, model="test", established_by="test")
            for s in staged
        ],
    )
    await adaptor.evidence.settle(key=ACCOUNT.key)
    assert await reads.history_state(key=ACCOUNT.key) == (1, 1)


async def test_render_on_person_and_unit_inputs_use_persona_rooms_without_legacy_culture(env):
    from nora.domain.culture import unit_culture_key
    from nora.domain.models import PersonRef
    from nora.domain.persona import Account
    from nora.language.character import CharacterRoom, PersonKey
    from nora.language.persona import PersonaEvidence, PersonaPersonAdaptor
    from nora.language.persona_collective import (
        CollectiveEvidence,
        CollectiveKey,
        PersonaCollectiveAdaptor,
    )
    from nora.testing import InMemoryCultureStore

    await _message(env.engine, 101, person=KIT)
    room = CharacterRoom(key="room-a", platform="signal", label="Friends")
    result = await env.store.persona_write(
        account=Account(key="room-a", entity_kind="room", unit=ACCOUNT.unit),
        ops=[
            AddOp(
                part="identity",
                kind="norm",
                text="conflicting prose",
                mode="told",
                citations=("101",),
                told_by=KIT,
                fact_key="norm",
                fact_value="plan in the open",
            )
        ],
        citable=CITABLE,
        now_ms=NOW,
        written_by_session="test",
        apply_decay=True,
    )
    assert result.status == "ok"
    reads = SqlCharacterEvidence(env.engine)
    key = unit_culture_key(ACCOUNT.unit)
    keys = {key: CollectiveKey(unit=ACCOUNT.unit, rooms=(room,))}
    evidence = CollectiveEvidence(store=reads, keys=keys, kind="unit", render=True)
    assert await evidence.count(key=key, after=0) == 1
    batch, cursor = await evidence.newest(key=key, after=0, limit=10)
    unit = PersonaCollectiveAdaptor(
        kind="unit",
        persona=env.store,
        culture=InMemoryCultureStore(),
        evidence=evidence,
        reads=reads,
        keys=keys,
        name_of=lambda _: "Kit",
        clock_ms=lambda: NOW,
        render=True,
    )
    assert "norm: plan in the open" in await unit.inputs(key=key, batch=batch)
    assert await evidence.count(key=key, after=cursor) == 0
    pk = PersonKey(
        unit=ACCOUNT.unit,
        person=PersonRef(person_id=KIT, name="Kit", honcho_peer_id="kit"),
        rooms=(room,),
        listed=("room-a",),
        home="room-a",
    )
    person = PersonaPersonAdaptor(
        persona=env.store,
        culture=InMemoryCultureStore(),
        evidence=PersonaEvidence(
            store=reads, persona=env.store, keys={ACCOUNT.key: pk}, wake_batch=5
        ),
        reads=reads,
        keys={ACCOUNT.key: pk},
        name_of=lambda _: "Kit",
        clock_ms=lambda: NOW,
        render=True,
    )
    page = await reads.rows_by_id(ids=[101])
    rendered = await person.inputs(key=ACCOUNT.key, batch=page)
    assert "norm: plan in the open" in rendered and 'source_at="1970-' in rendered


async def test_history_cli_receipts_and_explicit_rerun_preserve_claims(
    env, pg_url, monkeypatch, capsys
):
    import json
    from argparse import Namespace
    from types import SimpleNamespace

    from nora.app import cli
    from nora.persons import Persons

    await _message(env.engine, 1, person=KIT)
    reads = SqlCharacterEvidence(env.engine)
    assert await reads.history_state(key=ACCOUNT.key) == (1, 0)
    await reads.advance_history(key=ACCOUNT.key, cursor=1)
    await _write(
        env,
        [
            AddOp(
                part="identity",
                kind="role",
                text="organiser",
                mode="told",
                citations=("101",),
                told_by=KIT,
                fact_key="role",
                fact_value="organiser",
            )
        ],
    )
    monkeypatch.setattr(cli, "_config", lambda _: SimpleNamespace())
    monkeypatch.setattr(cli, "_database_url", lambda _: pg_url)
    monkeypatch.setattr(cli, "_policy", lambda _: SimpleNamespace(conversations=()))
    monkeypatch.setattr(cli, "_persons", lambda _: Persons(by_person={}, index={}))
    args = Namespace(key=ACCOUNT.key, restart=True, apply=False)
    assert await cli.cmd_persona_history(args) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["complete"] and not receipt["restart_applied"]
    assert receipt["typed_current"] == 1 and "gender" in receipt["missing_fields"]
    assert await reads.history_state(key=ACCOUNT.key) == (1, 1)
    await _message(env.engine, 2, person=KIT)
    args.apply = True
    assert await cli.cmd_persona_history(args) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["restart_applied"] and not receipt["complete"]
    assert await reads.history_state(key=ACCOUNT.key) == (2, 0)
    [line] = await env.store.account_heads(key=ACCOUNT.key)
    assert line.fact_value == "organiser"


@pytest.mark.parametrize("key", ["gender", "sex"])
async def test_gender_and_sex_require_explicit_statements_not_deductions(env, key):
    result = await _write(env, [AddOp(part="identity", kind="attribute", text="claim",
        mode="deduced", citations=("101", "102"), fact_key=key, fact_value="female")])
    assert result.status == "refused"
    assert result.refusals[0].reason == "invalid_fact"
    assert await env.store.account_heads(key=ACCOUNT.key) == ()


async def test_fallback_revision_preserves_same_fact_and_dates_changed_fact(env):
    value = {"name": "height", "value": 178, "unit": "cm"}
    op = AddOp(part="identity", kind="attribute", text="height", mode="told",
               citations=("101",), told_by=KIT, fact_key="attribute", fact_value=value)
    assert (await _write(env, [op])).status == "ok"
    [original] = await env.store.account_heads(key=ACCOUNT.key)
    assert (await _write(env, [ReviseOp(line="P1", citations=("102",),
                                      text="reworded height")], now=NOW + 1)).status == "ok"
    [same] = await env.store.account_heads(key=ACCOUNT.key)
    assert (same.as_of, same.support_count, same.last_supported_at) == (
        original.as_of, original.support_count, original.last_supported_at)
    assert same.citations == ("101", "102")
    changed = {**value, "value": 179}
    assert (await _write(env, [ReviseOp(line="P1", citations=("103",),
        fact_key="attribute", fact_value=changed)], now=NOW + 2)).status == "ok"
    [head] = await env.store.account_heads(key=ACCOUNT.key)
    assert head.fact_value == changed and head.as_of == CITABLE.messages[103].ts
    async with env.engine.connect() as conn:
        rows = (await conn.execute(sa.select(PERSONA_LINE.c.fact_value)
                                   .order_by(PERSONA_LINE.c.written_at))).scalars().all()
    assert rows == [value, value, changed]


@pytest.mark.parametrize("field", ["name", "value", "unit"])
@pytest.mark.parametrize("unsafe", ["+85291234567", "22.31234, 114.12345"])
async def test_fallback_all_components_pass_real_egress_screen(env, field, unsafe):
    from nora.agent.dream_screen import text_offence
    from nora.infra.sql.persona import SqlPersonaStore

    env.store = SqlPersonaStore(env.engine, screen=text_offence)
    value = {"name": "height", "value": 178, "unit": "cm", field: unsafe}
    op = AddOp(part="identity", kind="attribute", text="safe prose", mode="told",
               citations=("101",), told_by=KIT, fact_key="attribute", fact_value=value)
    result = await _write(env, [op])
    assert result.status == "refused"
    assert result.refusals[0].reason in {"identifier", "coordinate", "invalid_fact"}
    assert await env.store.account_heads(key=ACCOUNT.key) == ()


async def test_boolean_and_number_are_distinct_fallback_revisions(env):
    first = AddOp(part="identity", kind="attribute", text="explicit value", mode="told",
                  citations=("101",), told_by=KIT, fact_key="attribute",
                  fact_value={"name": "test_result", "value": True})
    assert (await _write(env, [first])).status == "ok"
    assert (await _write(env, [ReviseOp(line="P1", citations=("102",),
        fact_key="attribute", fact_value={"name": "test_result", "value": 1})],
        now=NOW + 1)).status == "ok"
    [line] = await env.store.account_heads(key=ACCOUNT.key)
    assert type(line.fact_value["value"]) is int
    assert line.as_of == CITABLE.messages[102].ts


async def test_numeric_value_and_unit_cannot_assemble_unscreened_coordinates(env):
    from nora.agent.dream_screen import text_offence
    from nora.infra.sql.persona import SqlPersonaStore

    env.store = SqlPersonaStore(env.engine, screen=text_offence)
    op = AddOp(part="identity", kind="attribute", text="safe prose", mode="told",
               citations=("101",), told_by=KIT, fact_key="attribute",
               fact_value={"name": "location", "value": 22.31234, "unit": ", 114.12345"})
    result = await _write(env, [op])
    assert result.status == "refused" and result.refusals[0].reason == "coordinate"
    assert await env.store.account_heads(key=ACCOUNT.key) == ()
