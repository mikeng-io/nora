"""Typed Identity and recoverable oldest-first evidence through real Postgres."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from nora.domain.profile import (
    AddOp,
    ProfileView,
    ReviseOp,
    identity_text,
)
from nora.infra.sql.schema import PROFILE_LINE
from nora.language.profile_render import account_text
from tests.infra.test_profile_store import ACCOUNT, CITABLE, KIT, NOW, _write
from tests.infra.test_profile_store import env as env


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
                part="facts",
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
        ProfileView(entity="person", of="Kit", lines=(line,)),
        name_of=lambda _: "Kit",
        now=datetime.fromtimestamp(NOW / 1000, UTC),
    )
    from nora.domain.character_card import format_line
    assert body and format_line(line, name_of=lambda _: "Kit") in body[0]
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
        part="facts",
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
        part="facts",
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
            await conn.execute(sa.select(PROFILE_LINE).order_by(PROFILE_LINE.c.written_at))
        ).all()
    assert [(r.fact_value, r.valid_until) for r in rows] == [("London", 20), ("Hong Kong", None)]


async def test_legacy_prose_is_retained_but_not_promoted(env):
    # A migration leaves all typed fields null on this pre-existing row.
    async with env.engine.begin() as conn:
        await conn.execute(
            sa.insert(PROFILE_LINE).values(
                id="00000000-0000-0000-0000-000000000001",
                profile_key=ACCOUNT.key,
                part="facts",
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
            ProfileView(entity="person", of="Kit", lines=(line,)),
            name_of=lambda _: "Kit",
            now=datetime.fromtimestamp(NOW / 1000, UTC),
        )
        is None
    )


async def test_a_current_singleton_requires_explicit_correction_and_old_claims_stay_history(env):
    first = AddOp(
        part="facts",
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


@pytest.mark.parametrize("key", ["gender", "sex"])
async def test_gender_and_sex_require_explicit_statements_not_deductions(env, key):
    result = await _write(env, [AddOp(part="facts", kind="attribute", text="claim",
        mode="deduced", citations=("101", "102"), fact_key=key, fact_value="female")])
    assert result.status == "refused"
    assert result.refusals[0].reason == "invalid_fact"
    assert await env.store.account_heads(key=ACCOUNT.key) == ()


async def test_fallback_revision_preserves_same_fact_and_dates_changed_fact(env):
    value = {"name": "height", "value": 178, "unit": "cm"}
    op = AddOp(part="facts", kind="attribute", text="height", mode="told",
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
        rows = (await conn.execute(sa.select(PROFILE_LINE.c.fact_value)
                                   .order_by(PROFILE_LINE.c.written_at))).scalars().all()
    assert rows == [value, value, changed]


@pytest.mark.parametrize("field", ["name", "value", "unit"])
@pytest.mark.parametrize("unsafe", ["+85291234567", "22.31234, 114.12345"])
async def test_fallback_all_components_pass_real_egress_screen(env, field, unsafe):
    from nora.agent.dream_screen import text_offence
    from nora.infra.sql.profile import SqlProfileStore

    env.store = SqlProfileStore(env.engine, screen=text_offence)
    value = {"name": "height", "value": 178, "unit": "cm", field: unsafe}
    op = AddOp(part="facts", kind="attribute", text="safe prose", mode="told",
               citations=("101",), told_by=KIT, fact_key="attribute", fact_value=value)
    result = await _write(env, [op])
    assert result.status == "refused"
    assert result.refusals[0].reason in {"identifier", "coordinate", "invalid_fact"}
    assert await env.store.account_heads(key=ACCOUNT.key) == ()


async def test_boolean_and_number_are_distinct_fallback_revisions(env):
    first = AddOp(part="facts", kind="attribute", text="explicit value", mode="told",
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
    from nora.infra.sql.profile import SqlProfileStore

    env.store = SqlProfileStore(env.engine, screen=text_offence)
    op = AddOp(part="facts", kind="attribute", text="safe prose", mode="told",
               citations=("101",), told_by=KIT, fact_key="attribute",
               fact_value={"name": "location", "value": 22.31234, "unit": ", 114.12345"})
    result = await _write(env, [op])
    assert result.status == "refused" and result.refusals[0].reason == "coordinate"
    assert await env.store.account_heads(key=ACCOUNT.key) == ()
