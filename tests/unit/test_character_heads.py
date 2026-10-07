"""The C2b switch of person accounts from per-room to per-unit keys
(design/self-growth/10-character.md §3): nobody loses an account, nothing is
moved twice, and the keys come from the policy alone."""

from __future__ import annotations

from dataclasses import dataclass

from nora.app.character_heads import seed_unit_person_heads
from nora.app.composition import character_person_keys
from nora.domain.culture import character_key
from nora.domain.models import PersonRef
from nora.language.character import CharacterRoom, PersonKey
from nora.testing import InMemoryCultureStore

ADA = PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada")
ROOMS = (
    CharacterRoom(key="room-a", platform="signal", label="Friends"),
    CharacterRoom(key="room-b", platform="discord", label="Zone 42"),
)
UNIT_KEY = character_key("friends", "ada")
KEYS = {UNIT_KEY: PersonKey(unit="friends", person=ADA, rooms=ROOMS, home="room-a")}


async def test_the_newest_per_room_account_becomes_the_unit_keys_first_revision():
    store = InMemoryCultureStore()
    old = await store.append(key="room-a#character:ada", body="older, room a",
                             written_at=100, established_by="dream:culture:a",
                             instruction_sha256="a" * 64, model="flash")
    new = await store.append(key="room-b#character:ada", body="newer, room b",
                             written_at=200, established_by="dream:culture:b",
                             instruction_sha256="b" * 64, model="flash")
    assert await seed_unit_person_heads(culture=store, keys=KEYS) == 1
    head = await store.head(key=UNIT_KEY)
    assert head is not None
    assert (head.body, head.written_at, head.instruction_sha256, head.model) == (
        "newer, room b", 200, "b" * 64, "flash")
    assert head.established_by == f"moved:{new.id}"
    assert head.parent_revision_id is None
    # The per-room chains are left as they were.
    assert (await store.head(key="room-a#character:ada")) == old


async def test_the_move_is_idempotent_and_never_overwrites_a_unit_head():
    store = InMemoryCultureStore()
    await store.append(key="room-a#character:ada", body="per room", written_at=100,
                       established_by="x")
    assert await seed_unit_person_heads(culture=store, keys=KEYS) == 1
    assert await seed_unit_person_heads(culture=store, keys=KEYS) == 0
    assert len(await store.chain(key=UNIT_KEY)) == 1


async def test_a_person_with_no_account_anywhere_gets_none():
    store = InMemoryCultureStore()
    assert await seed_unit_person_heads(culture=store, keys=KEYS) == 0
    assert await store.head(key=UNIT_KEY) is None


@dataclass(frozen=True)
class _Conv:
    primary_id: str
    platform: str
    unit: str
    label: str
    persons: frozenset[str] | None


class _Allowlist:
    conversations = (
        _Conv("room-a", "signal", "friends", "Friends", frozenset({"ada"})),
        _Conv("room-b", "discord", "friends", "Zone 42", frozenset({"ada", "bo"})),
        _Conv("room-c", "signal", "wedding", "Wedding", frozenset({"ada"})),
        _Conv("room-d", "signal", "ridgeline", "Open", None),
    )


class _Persons:
    def __init__(self) -> None:
        self.by_person = {
            "ada": ADA, "bo": PersonRef(person_id="bo", name="Bo", honcho_peer_id="bo"),
        }

    def room_members(self, listed):
        return () if listed is None else tuple(self.by_person[p] for p in sorted(listed))


def test_one_key_per_unit_and_listed_person_with_the_units_rooms():
    """A person in two units has two accounts; a `*` room lists nobody; a
    person's reads stand in the first room of the unit that lists them."""
    keys = character_person_keys(_Allowlist(), _Persons())
    assert set(keys) == {
        "friends#character:ada", "friends#character:bo", "wedding#character:ada",
    }
    ada = keys["friends#character:ada"]
    assert [r.key for r in ada.rooms] == ["room-a", "room-b"]
    assert ada.home == "room-a"
    assert keys["friends#character:bo"].home == "room-b"
    assert [r.key for r in keys["wedding#character:ada"].rooms] == ["room-c"]
