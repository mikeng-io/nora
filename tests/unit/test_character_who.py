"""Whose `<character>` account a turn renders (DEC-278/DEC-290).

The block carries the accounts of the people in the turn, not every listed
person, and each account and the whole block are capped.
"""

from __future__ import annotations

from datetime import UTC, datetime

from nora.domain.culture import CultureRevision, character_key
from nora.domain.models import NormalizedMessage, PersonRef, Quote, TranscriptRow
from nora.engagement.character_who import fire_character_people, tag_character_people
from nora.language.culture_render import (
    CHARACTER_BLOCK_MAX_CHARS,
    PERSON_ACCOUNT_MAX_CHARS,
    read_character,
    render_character,
)
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.testing import InMemoryCultureStore

NOW = datetime(2026, 10, 7, tzinfo=UTC)
IDS = {"ada": "+85200000001", "bo": "+85200000002", "cy": "+85200000003",
       "di": "+85200000004", "ed": "+85200000005"}
REFS = {p: PersonRef(person_id=p, name=p.title(), honcho_peer_id=p) for p in IDS}
PERSONS = Persons(
    by_person=REFS, index={("signal", v): p for p, v in IDS.items()},
)
ROSTER = tuple(REFS.values())  # ada bo cy di ed


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


def _who(msg, transcript=()) -> list[str]:
    return [r.person_id for r in tag_character_people(
        ROSTER, persons=PERSONS, message=msg,
        tagger=PERSONS.resolve("signal", msg.sender_id), transcript=transcript,
    )]


def test_a_listed_person_who_neither_tagged_nor_was_named_nor_spoke_gets_no_account():
    assert _who(_msg("ada"), [_row("bo", 1)]) == ["ada", "bo"]


def test_the_tagger_is_always_included_even_in_an_empty_window():
    assert _who(_msg("cy")) == ["cy"]


def test_a_person_the_tag_message_mentions_is_included():
    assert _who(_msg("ada", mentions=["di"])) == ["ada", "di"]


def test_the_author_of_the_replied_to_message_is_included_from_the_window_row():
    quote = Quote(author_id="", text="", quoted_ts=5)
    assert _who(_msg("ada", quote=quote), [_row("ed", 5, is_nora=False)]) == ["ada", "ed"]


def test_a_quote_whose_parent_left_the_window_still_names_its_author():
    quote = Quote(author_id=IDS["bo"], text="", quoted_ts=5)
    assert _who(_msg("ada", quote=quote)) == ["ada", "bo"]


def test_roster_order_is_kept_not_the_order_they_were_found_in():
    assert _who(_msg("ed", mentions=["ada"]), [_row("cy", 1)]) == ["ada", "cy", "ed"]


def test_an_unmapped_sender_and_noras_own_rows_add_nobody():
    rows = [_row("+19999999999", 1), _row("bo", 2, is_nora=True)]
    assert _who(_msg("ada"), rows) == ["ada"]


def test_a_person_the_room_does_not_list_is_never_added():
    narrow = (REFS["ada"],)
    got = tag_character_people(
        narrow, persons=PERSONS, message=_msg("bo", mentions=["ada"]),
        tagger=REFS["bo"], transcript=[_row("cy", 1)],
    )
    assert [r.person_id for r in got] == ["ada"]


def test_a_fire_renders_its_window_speakers_and_its_own_person_only():
    got = fire_character_people(
        ROSTER, persons=PERSONS, platform="signal", person=REFS["di"],
        transcript=[_row("bo", 1), _row("ada", 2, is_nora=True)],
    )
    assert [r.person_id for r in got] == ["bo", "di"]
    quiet = fire_character_people(
        ROSTER, persons=PERSONS, platform="signal", person=None, transcript=[],
    )
    assert quiet == ()


async def test_only_the_people_in_the_turn_are_read_from_the_store():
    store = InMemoryCultureStore()
    for p in IDS:
        await store.append(key=character_key("friends", p), body=f"{p} account",
                           written_at=1, established_by="x")
    msg = _msg("ada")
    got = await read_character(
        store, unit="friends",
        members=tag_character_people(
            ROSTER, persons=PERSONS, message=msg, tagger=REFS["ada"],
            transcript=[_row("bo", 1)],
        ),
        stage="tag_character", platform="signal", metrics=Metrics(),
    )
    assert [n for n, _ in got] == ["Ada", "Bo"]
    assert store.reads == [character_key("friends", "ada"), character_key("friends", "bo")]


def _rev(body: str) -> CultureRevision:
    return CultureRevision(
        id="r", culture_key="k", body=body, written_at=0, established_by="x")


def test_one_account_is_capped_and_says_how_much_it_dropped():
    long = "a" * (PERSON_ACCOUNT_MAX_CHARS + 123)
    out = render_character([("Ada", _rev(long))], now=NOW).render()  # type: ignore[union-attr]
    assert "[123 more characters not shown]" in out
    assert "a" * (PERSON_ACCOUNT_MAX_CHARS + 1) not in out


def test_an_account_under_the_cap_is_untouched():
    out = render_character([("Ada", _rev("b" * 1_739))], now=NOW).render()  # type: ignore[union-attr]
    assert "more characters" not in out and "b" * 1_739 in out


def test_the_block_is_capped_and_an_account_that_no_longer_fits_is_counted():
    """The cap bounds the RENDERED block: wrappers, ages, markers included. The
    first account the budget cannot hold is cut; later ones are left out whole
    and counted in one closing marker, which is inside the cap."""
    accounts = [(f"P{i}", _rev("c" * PERSON_ACCOUNT_MAX_CHARS)) for i in range(6)]
    out = render_character(accounts, now=NOW).render()  # type: ignore[union-attr]
    assert len(out) <= CHARACTER_BLOCK_MAX_CHARS
    assert out.count("<person_account") == 5  # four whole, the fifth cut
    assert out.count("more characters not shown]") == 2  # the cut one and the closing line
    assert "P5" not in out  # the sixth is left out whole
    closing = f"[{PERSON_ACCOUNT_MAX_CHARS} more characters not shown]\n</character>"
    assert out.rstrip().endswith(closing)


def test_an_escape_heavy_block_is_still_bounded_as_rendered():
    """`escape()` multiplies `<`, so a cap on raw characters would not bound it."""
    accounts = [(f"P{i}", _rev("<" * PERSON_ACCOUNT_MAX_CHARS)) for i in range(6)]
    out = render_character(accounts, now=NOW).render()  # type: ignore[union-attr]
    assert len(out) <= CHARACTER_BLOCK_MAX_CHARS
    assert "<<<" not in out
    assert "more characters not shown]" in out


def test_the_whole_live_friends_roster_is_not_cut():
    sizes = (1739, 1241, 994, 1116, 1088, 1717, 1170)  # sizes of real account heads
    accounts = [(f"P{i}", _rev("d" * n)) for i, n in enumerate(sizes)]
    out = render_character(accounts, now=NOW).render()  # type: ignore[union-attr]
    assert "more characters" not in out and len(out) <= CHARACTER_BLOCK_MAX_CHARS


def test_an_unmapped_tagger_does_not_fall_back_to_the_whole_roster():
    got = tag_character_people(
        ROSTER, persons=PERSONS, message=_msg("ada"), tagger=None, transcript=[],
    )
    assert got == ()


def test_a_mention_added_by_an_edit_counts():
    import json
    from dataclasses import replace

    from nora.domain.models import RowEdit

    def env(ids):
        return json.dumps({"envelope": {"dataMessage": {"message": "\ufffc", "mentions": [
            {"start": 0, "length": 1, "number": i} for i in ids]}}})

    row = replace(_row("bo", 5), raw_json=env([]), edit=RowEdit(
        ts=6, raw_json=env([IDS["di"]]), was="x"))  # type: ignore[call-arg]
    quote = Quote(author_id="", text="", quoted_ts=5)
    assert _who(_msg("ada", quote=quote), [row]) == ["ada", "bo", "di"]


class _UnitPolicy:
    def conversation(self, platform, conversation_id):
        return type("_Convo", (), {
            "primary_id": "g", "unit": "friends",
            "persons": frozenset(IDS),
        })()


async def _seeded() -> InMemoryCultureStore:
    store = InMemoryCultureStore()
    for p in IDS:
        await store.append(key=character_key("friends", p), body=f"{p} account",
                           written_at=1, established_by="x")
    return store


async def test_the_tag_assembler_reads_only_the_people_in_the_turn():
    from nora.engagement.context import TranscriptInjection

    store = await _seeded()
    assembler = object.__new__(TranscriptInjection)
    assembler._culture_reader = store
    assembler._policy = _UnitPolicy()
    assembler._metrics = Metrics()
    assembler._persons = PERSONS
    assembler._room_members = lambda msg: ROSTER  # type: ignore[method-assign]
    got = await assembler._character(_msg("ada"), REFS["ada"], [_row("bo", 1)])
    assert [n for n, _ in got] == ["Ada", "Bo"]
    assert store.reads == [character_key("friends", "ada"), character_key("friends", "bo")]


async def test_the_fire_path_reads_only_the_people_in_the_window():
    from nora.app.fire import FirePath

    store = await _seeded()
    path = object.__new__(FirePath)
    path._culture = store
    path._policy = _UnitPolicy()
    path._metrics = Metrics()
    path._persons = PERSONS
    path._standing_store_timeout_seconds = 5.0
    path._room_members = lambda occasion: ROSTER  # type: ignore[method-assign]
    occasion = type("_Occ", (), {"platform": "signal", "conversation_id": "g"})()
    got = await path._read_character(occasion, None, [_row("cy", 1)])  # type: ignore[arg-type]
    assert [n for n, _ in got] == ["Cy"]
    assert store.reads == [character_key("friends", "cy")]
