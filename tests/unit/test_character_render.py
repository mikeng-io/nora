"""The `<character>` block (DEC-278): one account per person, each with its own age."""

from __future__ import annotations

from datetime import UTC, datetime

from nora.domain.culture import CultureRevision
from nora.language.culture_render import render_character

NOW = datetime(2026, 10, 3, tzinfo=UTC)


def _rev(body: str) -> CultureRevision:
    return CultureRevision(
        id="r", culture_key="k", body=body,
        written_at=int(NOW.timestamp() * 1000) - 3 * 86_400_000, established_by="culture",
    )


def test_character_block_renders_each_person_with_an_age():
    accounts = [("Miles", _rev("dry and loyal")), ("Ada", _rev("<b>quiet</b>"))]
    block = render_character(accounts, now=NOW)
    assert block is not None and block.name == "character"
    out = block.render()
    assert 'name="Miles"' in out and "dry and loyal" in out and "written=" in out
    assert "<b>" not in out  # escaped: an account is model-authored prose
    assert render_character([], now=NOW) is None


async def test_the_character_read_is_keyed_by_the_rooms_unit_for_its_listed_persons():
    """One account per (unit, person): the read keys the rendering room's UNIT and
    asks only for the persons that room lists. An account of someone the room
    does not list is never read into it, and a per-room key is never read."""
    from nora.domain.culture import character_key
    from nora.domain.models import PersonRef
    from nora.language.culture_render import read_character
    from nora.testing import InMemoryCultureStore

    store = InMemoryCultureStore()
    await store.append(key=character_key("friends", "ada"), body="steady",
                       written_at=1, established_by="x")
    await store.append(key=character_key("friends", "bo"), body="not listed here",
                       written_at=1, established_by="x")
    await store.append(key=character_key("room-a", "ada"), body="per-room, old",
                       written_at=2, established_by="x")
    ada = PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada")
    got = await read_character(
        store, unit="friends", members=(ada,), stage="tag_character",
        platform="signal", metrics=None,
    )
    assert [(name, rev.body) for name, rev in got] == [("Ada", "steady")]
    assert store.reads == [character_key("friends", "ada")]
