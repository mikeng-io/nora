"""Whose `<character>` account a turn renders (DEC-278/DEC-290).

The block carries the accounts of the people in the turn, not every listed
person, and each account and the whole block are capped.
"""

from __future__ import annotations

from datetime import UTC, datetime

from nora.access import IdentityAccess
from nora.domain.models import NormalizedMessage, PersonRef, Quote, TranscriptRow
from nora.engagement.character_who import fire_character_people, tag_character_people
from nora.persons import Persons
from nora.policy import permissive_policy

NOW = datetime(2026, 10, 7, tzinfo=UTC)
IDS = {"ada": "+85200000001", "bo": "+85200000002", "cy": "+85200000003",
       "di": "+85200000004", "ed": "+85200000005"}
REFS = {p: PersonRef(person_id=p, name=p.title()) for p in IDS}
PERSONS = Persons(
    by_person=REFS, index={("signal", v): p for p, v in IDS.items()},
)
ROSTER = tuple(REFS.values())  # ada bo cy di ed
ACCESS = IdentityAccess.for_files(persons=PERSONS, policy=permissive_policy())


def _row(sender: str, ts: int, *, is_nora: bool = False) -> TranscriptRow:
    return TranscriptRow(
        id=ts, sender_id=IDS.get(sender, sender), sender_name=None, ts=ts,
        body="x", is_nora=is_nora, delivery_status=None, raw_json=None,  # type: ignore[arg-type]
    )


def _msg(sender: str, *, mentions=(), quote=None) -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id="g", conversation_type="group",
        sender_id=IDS[sender], sender_name=None, timestamp=9_000, body="hi", raw={},
        mentions=frozenset(IDS[m] for m in mentions), quote=quote,
    )


async def _who(msg, transcript=()) -> list[str]:
    return [r.person_id for r in await tag_character_people(
        ROSTER, access=ACCESS, message=msg,
        tagger=PERSONS.resolve("signal", msg.sender_id), transcript=transcript,
    )]


async def test_a_listed_person_who_neither_tagged_nor_was_named_nor_spoke_gets_no_account():
    assert await _who(_msg("ada"), [_row("bo", 1)]) == ["ada", "bo"]


async def test_the_tagger_is_always_included_even_in_an_empty_window():
    assert await _who(_msg("cy")) == ["cy"]


async def test_a_person_the_tag_message_mentions_is_included():
    assert await _who(_msg("ada", mentions=["di"])) == ["ada", "di"]


async def test_the_author_of_the_replied_to_message_is_included_from_the_window_row():
    quote = Quote(author_id="", text="", quoted_ts=5)
    assert await _who(_msg("ada", quote=quote), [_row("ed", 5, is_nora=False)]) == ["ada", "ed"]


async def test_a_quote_whose_parent_left_the_window_still_names_its_author():
    quote = Quote(author_id=IDS["bo"], text="", quoted_ts=5)
    assert await _who(_msg("ada", quote=quote)) == ["ada", "bo"]


async def test_roster_order_is_kept_not_the_order_they_were_found_in():
    assert await _who(_msg("ed", mentions=["ada"]), [_row("cy", 1)]) == ["ada", "cy", "ed"]


async def test_an_unmapped_sender_and_noras_own_rows_add_nobody():
    rows = [_row("+19999999999", 1), _row("bo", 2, is_nora=True)]
    assert await _who(_msg("ada"), rows) == ["ada"]


async def test_a_person_the_room_does_not_list_is_never_added():
    narrow = (REFS["ada"],)
    got = await tag_character_people(
        narrow, access=ACCESS, message=_msg("bo", mentions=["ada"]),
        tagger=REFS["bo"], transcript=[_row("cy", 1)],
    )
    assert [r.person_id for r in got] == ["ada"]


async def test_a_fire_renders_its_window_speakers_and_its_own_person_only():
    got = await fire_character_people(
        ROSTER, access=ACCESS, platform="signal", person=REFS["di"],
        transcript=[_row("bo", 1), _row("ada", 2, is_nora=True)],
    )
    assert [r.person_id for r in got] == ["bo", "di"]
    quiet = await fire_character_people(
        ROSTER, access=ACCESS, platform="signal", person=None, transcript=[],
    )
    assert quiet == ()


async def test_an_unmapped_tagger_does_not_fall_back_to_the_whole_roster():
    got = await tag_character_people(
        ROSTER, access=ACCESS, message=_msg("ada"), tagger=None, transcript=[],
    )
    assert got == ()


async def test_a_mention_added_by_an_edit_counts():
    import json
    from dataclasses import replace

    from nora.domain.models import RowEdit

    def env(ids):
        return json.dumps({"envelope": {"dataMessage": {"message": "\ufffc", "mentions": [
            {"start": 0, "length": 1, "number": i} for i in ids]}}})

    row = replace(_row("bo", 5), raw_json=env([]), edit=RowEdit(
        ts=6, raw_json=env([IDS["di"]]), was="x"))  # type: ignore[call-arg]
    quote = Quote(author_id="", text="", quoted_ts=5)
    assert await _who(_msg("ada", quote=quote), [row]) == ["ada", "bo", "di"]
