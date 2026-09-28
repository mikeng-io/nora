"""`<room_members>` (design/cognition-loop/15-map.md §3): who is who in THIS
room, with the names the group calls them. Only the room's listed persons,
never the whole identity file: a roster fed the address book listed people
who had never been members (design/12 §3), and that leak is what the first
test pins."""

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
    # In identity.toml, not in the room: never listed (the design/12 §3 leak).
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
