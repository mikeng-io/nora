"""`sandbox_from_snapshot()` copies the Character blocks' accounts.

The unit's own account (`<unit_culture>`) and the per-unit account of each
person the room LISTS (`<character>`) are copied, each as of the instant and at
the age it had.

A snapshot that copied only the room's account would give an eval turn neither
block: the case would grade a prompt missing the very thing a Character change
is about. The source also holds what must NOT come across: an account written
after the instant, a person the room does not list, and the same person's
account in another unit. """

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest

from evals.backbone import _close
from nora.domain.culture import character_key, nora_character_key, unit_culture_key
from nora.domain.models import NormalizedMessage
from nora.infra.sql import create_sql_engine
from nora.infra.sql.culture import SqlCultureStore
from nora.subject import (
    Knowledge,
    Message,
    Subject,
    SubjectSpec,
    obtain,
    sandbox_database,
    sandbox_from_snapshot,
)
from nora.subject.as_of import render_tag_blocks
from tests.sandbox_fixtures import ROOM, Source, seed_source

BEFORE_MS = 1_752_000_000_000
NOW = datetime.fromtimestamp(BEFORE_MS / 1000, tz=UTC)
MINUTE = 60_000
HOUR = 3_600_000
UNIT = "nora"
POLICY = (
    'default = "deny"\n[[conversations]]\nplatform = "signal"\n'
    f'id = "g1"\nlabel = "g1"\npersons = ["miles_novak"]\nunit = "{UNIT}"\n'
)
IDENTITY = (
    '[persons.miles_novak]\nname = "Miles"\n'
    '[[persons.miles_novak.signal]]\nphone = "+852111"\n'
    '[persons.bo]\nname = "Bo"\n'
    '[[persons.bo.signal]]\nphone = "+852222"\n'
)
OVERRIDES = {"culture.enabled": True}


@pytest.fixture
async def source(pg_url: str, pg_template: str, tmp_path: Any, monkeypatch: Any) -> Source:
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    src = await seed_source(
        pg_url, tmp_path, Knowledge(transcript=(
            Message(body="anyone up for friday", at_ms=BEFORE_MS - 10 * MINUTE),
            Message(body="yes, if it stays dry", at_ms=BEFORE_MS - 5 * MINUTE),
        )),
        template=pg_template, before_ms=BEFORE_MS, policy=POLICY, identity=IDENTITY,
    )
    engine = create_sql_engine(pg_url)
    try:
        store = SqlCultureStore(engine)

        async def write(key: str, body: str, at: int) -> None:
            await store.append(key=key, body=body, written_at=at, established_by="test")

        await write(unit_culture_key(UNIT), "a close group of cyclists", BEFORE_MS - 5 * HOUR)
        await write(unit_culture_key(UNIT), "AFTER: the unit changed", BEFORE_MS + HOUR)
        await write(nora_character_key(UNIT), "she answers short and dry", BEFORE_MS - 3 * HOUR)
        await write(nora_character_key(UNIT), "AFTER: she changed", BEFORE_MS + HOUR)
        await write(nora_character_key("elsewhere"), "OTHER UNIT: her", BEFORE_MS - HOUR)
        await write(
            character_key(UNIT, "miles_novak"), "Miles plans, then waits", BEFORE_MS - 2 * HOUR,
        )
        await write(character_key(UNIT, "miles_novak"), "AFTER: Miles stopped", BEFORE_MS + HOUR)
        # The room does not list Bo, and Miles's account in another unit is not
        # this unit's: neither may reach the sandbox.
        await write(character_key(UNIT, "bo"), "UNLISTED: Bo is loud", BEFORE_MS - HOUR)
        await write(character_key(
            "elsewhere", "miles_novak",
        ), "OTHER UNIT: Miles", BEFORE_MS - HOUR)
    finally:
        await engine.dispose()
    return src


@pytest.fixture
async def sandbox(source: Source, pg_template: str, tmp_path: Any) -> AsyncIterator[Subject]:
    async with sandbox_database(source.url, template=pg_template) as url:
        subject = await obtain(SubjectSpec(
            database_url=url, workdir=tmp_path / "sandbox", room=source.room,
            policy=source.policy, identity=source.identity, now=NOW,
            overrides=dict(OVERRIDES),
        ))
        try:
            yield subject
        finally:
            await _close(subject)


async def test_an_eval_turn_carries_the_unit_and_person_accounts_as_of_then(
    sandbox: Subject, source: Source,
) -> None:
    await sandbox_from_snapshot(sandbox, source_url=source.url, before_ms=BEFORE_MS)
    tag = NormalizedMessage(
        platform=ROOM[0], conversation_id=ROOM[1], conversation_type="group",
        sender_id="+85200000001", sender_name="Jordan", timestamp=BEFORE_MS,
        body="@nora what is the plan", raw={},
    )
    await sandbox.deps.audit.append(message=tag, person_id="")

    blocks = await render_tag_blocks(sandbox, message=tag, now=NOW)

    assert "a close group of cyclists" in blocks["unit_culture"]
    assert 'written="5h ago"' in blocks["unit_culture"], blocks["unit_culture"]
    assert "she answers short and dry" in blocks["self"]
    assert 'written="3h ago"' in blocks["self"], blocks["self"]
    assert '<person_account name="Miles" written="2h ago"' in blocks["character"]
    assert "Miles plans, then waits" in blocks["character"]
    rendered = "\n".join(blocks.values())
    for absent in ("AFTER:", "UNLISTED:", "OTHER UNIT:"):
        assert absent not in rendered, absent


async def test_only_the_listed_persons_accounts_of_this_unit_are_copied(
    sandbox: Subject, source: Source,
) -> None:
    """The rendering hides an unlisted person's account anyway, so the negative
    is read off the sandbox's own store: the copy takes what the room lists and
    nothing else, in this unit's keys alone."""
    await sandbox_from_snapshot(sandbox, source_url=source.url, before_ms=BEFORE_MS)
    engine = create_sql_engine(sandbox.database_url)
    try:
        store = SqlCultureStore(engine)
        miles = await store.head(key=character_key(UNIT, "miles_novak"))
        assert miles is not None and miles.body == "Miles plans, then waits"
        assert miles.written_at == BEFORE_MS - 2 * HOUR
        assert await store.head(key=character_key(UNIT, "bo")) is None
        assert await store.head(key=character_key("elsewhere", "miles_novak")) is None
    finally:
        await engine.dispose()
