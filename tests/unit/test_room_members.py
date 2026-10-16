"""`<room_members>`: who is who in THIS room, with the names the group calls
them. Only the room's listed persons, never the whole identity file: a roster
fed the address book would list people who had never been members, and that
leak is what the first test pins."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from nora.app.fire import FirePath
from nora.domain.models import NormalizedMessage
from nora.engagement.context import TranscriptInjection
from nora.persons import load_persons
from nora.policy import load_policy

IDENTITY = """
[persons.miles_novak]
name = "Miles Novak"
aliases = ["俊爺", "Miles"]
[[persons.miles_novak.signal]]
phone = "+852111"

[persons.grant_carver]
name = "Grant Carver"
aliases = ["Chi Ming"]
[[persons.grant_carver.signal]]
phone = "+852222"

[persons.outsider]
name = "Sam Outsider"
aliases = ["Sammy"]
[[persons.outsider.signal]]
phone = "+852333"
"""

POLICY = """
default = "deny"
[[conversations]]
platform = "signal"
id = "g1"
label = "listed"
persons = ["miles_novak", "grant_carver", "nobody_mapped"]
unit = "u"

[[conversations]]
platform = "signal"
id = "g2"
label = "everyone"
persons = "*"
unit = "v"
"""


def _load(tmp_path: Path):
    (tmp_path / "identity.toml").write_text(IDENTITY)
    (tmp_path / "policy.toml").write_text(POLICY)
    return load_persons(tmp_path / "identity.toml"), load_policy(tmp_path / "policy.toml")


def test_only_the_rooms_listed_persons_and_never_the_address_book(tmp_path):
    persons, policy = _load(tmp_path)
    members = persons.room_members(policy.conversation("signal", "g1").persons)
    assert [(m.name, m.aliases) for m in members] == [
        ("Grant Carver", ("Chi Ming",)), ("Miles Novak", ("俊爺", "Miles")),
    ]
    # In identity.toml, not in the room: never listed (the leak).
    assert all(m.name != "Sam Outsider" for m in members)


def test_a_room_open_to_everyone_lists_nobody(tmp_path):
    persons, policy = _load(tmp_path)
    assert persons.room_members(policy.conversation("signal", "g2").persons) == ()


def _msg(conversation_id: str) -> NormalizedMessage:
    return NormalizedMessage("signal", conversation_id, "group", "+852222", "Grant",
                             1, "@nora tell me when 俊爺 joins", {})


def test_the_tag_path_and_the_fire_path_give_the_same_members(tmp_path):
    persons, policy = _load(tmp_path)
    holder = SimpleNamespace(_policy=policy, _persons=persons)
    tag = TranscriptInjection._room_members(holder, _msg("g1"))  # type: ignore[arg-type]
    fire = FirePath._room_members(  # type: ignore[arg-type]
        holder, SimpleNamespace(platform="signal", conversation_id="g1"),
    )
    assert tag == fire and [m.name for m in tag] == ["Grant Carver", "Miles Novak"]
    # A room outside the policy, or no policy wired: nothing.
    assert TranscriptInjection._room_members(holder, _msg("elsewhere")) == ()  # type: ignore[arg-type]
    assert TranscriptInjection._room_members(  # type: ignore[arg-type]
        SimpleNamespace(_policy=None, _persons=persons), _msg("g1"),
    ) == ()


def test_two_members_of_one_name_keep_one_order_in_every_process():
    """A frozenset walks in hash order, which changes per process
    (PYTHONHASHSEED); the block is a cached, snapshot-compared prefix, so a
    tie must break the same way in every process."""
    import os
    import subprocess
    import sys

    code = (
        "from nora.domain.models import PersonRef\n"
        "from nora.persons import Persons\n"
        "ids = ['p_c', 'p_a', 'p_b']\n"
        "refs = {i: PersonRef(i, 'Chris', i) for i in ids}\n"
        "p = Persons(by_person=refs, index={})\n"
        "print(','.join(m.person_id for m in p.room_members(frozenset(ids))))\n"
    )
    seen = set()
    for seed in range(6):
        out = subprocess.run(  # noqa: S603 - sys.executable with a fixed script
            [sys.executable, "-c", code], capture_output=True, text=True, check=True,
            env={**os.environ, "PYTHONHASHSEED": str(seed)},
        ).stdout.strip()
        seen.add(out)
    assert seen == {"p_a,p_b,p_c"}


async def test_the_fire_path_fills_room_members_through_build_context(tmp_path):
    """The wiring, not only the helper: a reminder fired into a listed room
    carries that room's members (dropping the fire wiring must fail)."""
    from datetime import UTC, datetime

    from nora.app.fire import FirePath, reminder_occasion
    from nora.obs.metrics import Metrics
    from nora.testing import FakeMemoryStore, FakeRecordStore
    from tests.unit.test_fire_path import _reminder

    persons, _ = _load(tmp_path)
    (tmp_path / "p2.toml").write_text(
        'default = "deny"\n[[conversations]]\nplatform = "signal"\nid = "conv-1"\n'
        'label = "c"\npersons = ["miles_novak"]\nunit = "u"\n')
    path = FirePath(
        persons=persons, records=FakeRecordStore(), memory=FakeMemoryStore(),
        doctrine_store=None, metrics=Metrics(),  # type: ignore[arg-type]
        runner=None, adapters={}, registry=None,  # type: ignore[arg-type]
        canary="c", display_name="Nora", ai_peer="nora", context_window=40,
        limits={}, timeout_seconds=120.0, clock=lambda: datetime(2026, 9, 28, tzinfo=UTC),
        policy=load_policy(tmp_path / "p2.toml"),
    )
    ctx = await path.build_context(reminder_occasion([_reminder(
        requester_person_id="miles_novak"
    )]))
    assert [m.name for m in ctx.room_members] == ["Miles Novak"]
