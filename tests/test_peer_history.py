"""`Room.tagger_card_injected`: the room reports whether the tagger's block landed."""

from __future__ import annotations

from nora.agent.room import Room
from nora.domain.models import PersonRef
from nora.persons import Persons

BLAKE = PersonRef(
    person_id="blake_lindqvist", name="Blake Lindqvist", honcho_peer_id="blake_lindqvist",
    aliases=("Fung", "峰哥"),
)


def _persons() -> Persons:
    return Persons(
        by_person={"blake_lindqvist": BLAKE},
        index={("signal", "uuid-blake"): "blake_lindqvist"},
        life360=(),
    )


def _room(*, injected: bool) -> Room:
    return Room(
        platform="signal", conversation_id="room", roster_source=None,
        persons=_persons(), tagger=BLAKE, tagger_card_injected=injected,
    )


def test_the_room_reports_whether_the_block_landed():
    assert _room(injected=True).tagger_card_injected is True
    assert _room(injected=False).tagger_card_injected is False
