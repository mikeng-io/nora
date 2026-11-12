"""The frozen roster answers as the live reader would."""

from scripts.cognition_loop.rosters import FrozenRoster, frozen_rosters


async def test_a_frozen_room_answers_its_members_and_an_unread_room_is_unavailable():
    rosters = frozen_rosters({"rosters": {"g/1": [["u-a", "+000"], ["u-b"]]}})
    source = FrozenRoster("discord", rosters)
    read = await source.roster(conversation_id="g/1")
    assert read.available and set(read.members) == {frozenset({"u-a", "+000"}), frozenset({"u-b"})}
    assert not (await source.roster(conversation_id="g/2")).available
