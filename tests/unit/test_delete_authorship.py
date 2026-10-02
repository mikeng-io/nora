"""The authorship rule and the held-delete key rule, directly (`nora.domain.delete_authorship`,
`nora.platform.whatsapp.parser.revoker_ids`). They are used at ingest, when a held delete
is applied, by the start-up sweep and by `unforget`; a mistake in one is a wrong message
hidden or a forged delete honoured, so each branch is pinned here rather than only through
the pipelines that happen to reach it.
"""

from __future__ import annotations

import pytest

from nora.domain.delete_authorship import (
    ORPHAN_CAP_GLOBAL,
    ORPHAN_CAP_PER_DELETER,
    ORPHAN_CAP_PER_ROOM,
    ORPHAN_CAP_UNNAMED_PER_ROOM,
    ORPHAN_TTL_MS,
    deleter_is_author,
    orphan_names_message,
)
from nora.domain.models import OrphanDelete
from nora.platform.whatsapp.parser import revoker_ids


def _author(**over):
    base = {
        "author_sender_id": "+85211112222", "author_person_id": None,
        "deleter_sender_id": "+85211112222", "deleter_person_id": None, "deleter_ids": (),
    }
    return deleter_is_author(**(base | over))


def test_the_same_platform_id_is_the_author():
    assert _author() is True


def test_a_different_id_with_nothing_else_is_not():
    assert _author(deleter_sender_id="+85299999999") is False


def test_a_deleter_that_names_nobody_is_never_the_author():
    assert _author(deleter_sender_id="") is False
    assert _author(deleter_sender_id="", deleter_ids=("",)) is False
    assert _author(deleter_sender_id="", deleter_ids=[""]) is False


def test_an_author_with_no_id_is_never_matched_by_a_deleter_with_none():
    assert _author(author_sender_id="", deleter_sender_id="") is False
    assert _author(author_sender_id="", deleter_sender_id="", deleter_ids=("",)) is False
    assert _author(author_sender_id="", deleter_sender_id="+1", deleter_ids=("",)) is False


def test_the_same_resolved_person_is_the_author_but_two_unresolved_ones_are_not():
    assert _author(author_sender_id="a-phone", deleter_sender_id="a-lid",
                   author_person_id="ada", deleter_person_id="ada") is True
    assert _author(author_sender_id="a-phone", deleter_sender_id="a-lid",
                   author_person_id="ada", deleter_person_id="ben") is False
    assert _author(author_sender_id="a", deleter_sender_id="b",
                   author_person_id=None, deleter_person_id=None) is False
    # Two EMPTY person ids are not the same person.
    assert _author(author_sender_id="a", deleter_sender_id="b",
                   author_person_id="", deleter_person_id="") is False
    assert _author(author_sender_id="a", deleter_sender_id="b",
                   author_person_id="ada", deleter_person_id=None) is False


def test_any_id_form_the_event_carried_is_the_authors():
    assert _author(author_sender_id="999", deleter_sender_id="+85211112222",
                   deleter_ids=("+85211112222", "999", "999@lid")) is True
    assert _author(author_sender_id="999", deleter_sender_id="+85211112222",
                   deleter_ids=("+85211112222", "555")) is False


def test_a_jid_with_its_domain_does_not_collide_with_a_bare_id():
    """The raw JID keeps its `@domain`: a phone JID's digits would otherwise equal a
    different person's LID."""
    assert _author(author_sender_id="85211112222", deleter_sender_id="+85299999999",
                   deleter_ids=("85211112222@s.whatsapp.net",)) is False
    assert _author(author_sender_id="85211112222@s.whatsapp.net",
                   deleter_sender_id="+85299999999",
                   deleter_ids=("85211112222@s.whatsapp.net",)) is True


# --- revoker_ids ------------------------------------------------------------


def test_revoker_ids_are_the_phone_the_lid_and_the_raw_jid_in_the_forms_sender_id_takes():
    event = {"sender_phone": "85211112222", "sender_lid": "999", "sender_jid": "999@lid"}
    assert revoker_ids(event) == ("+85211112222", "999", "999@lid")


def test_the_phone_form_has_a_plus_and_the_lid_form_never_does():
    assert revoker_ids({"sender_phone": "+85211112222"}) == ("+85211112222",)
    assert revoker_ids({"sender_phone": "85211112222"}) == ("+85211112222",)
    assert revoker_ids({"sender_lid": "+999"}) == ("999",)
    assert revoker_ids({"sender_lid": "999"}) == ("999",)


def test_the_raw_jid_keeps_its_domain():
    assert revoker_ids({"sender_jid": "85211112222@s.whatsapp.net"}) == (
        "85211112222@s.whatsapp.net",
    )


@pytest.mark.parametrize("event", [
    {}, {"sender_phone": None, "sender_lid": None, "sender_jid": None},
    {"sender_phone": "", "sender_lid": "", "sender_jid": ""},
    {"sender_phone": 5, "sender_lid": ["x"], "sender_jid": 7},
    {"sender_phone": "  ", "sender_lid": " + ", "sender_jid": "\t"},
])
def test_an_event_with_no_usable_id_gives_none(event):
    assert revoker_ids(event) == ()


def test_the_ids_are_not_repeated():
    event = {"sender_phone": "+85211112222", "sender_lid": "85211112222",
             "sender_jid": "85211112222@lid"}
    ids = revoker_ids(event)
    assert len(ids) == len(set(ids))


# --- orphan_names_message: the ONE key rule ----------------------------------


def _orphan(**over):
    base = {
        "id": 1, "platform": "whatsapp", "conversation_id": "g1", "handle": "H1",
        "sent_ts": None, "deleter_id": "+1", "deleter_ids": ("+1",),
        "deleter_person_id": None, "event_ts": 5, "recorded_at": 1,
    }
    return OrphanDelete(**(base | over))


def _names(orphan, **over):
    base = {"platform": "whatsapp", "conversation_id": "g1", "handle": "H1",
            "timestamps": (7,), "sender_id": "+1", "person_id": None}
    return orphan_names_message(orphan, **(base | over))


def test_a_held_delete_names_the_message_with_its_handle_in_its_room():
    assert _names(_orphan()) is True


def test_it_is_per_platform_and_per_room():
    assert _names(_orphan(), platform="discord") is False
    assert _names(_orphan(platform="discord", deleter_id=""), platform="instagram") is False
    assert _names(_orphan(), conversation_id="g2") is False


def test_a_handle_must_be_non_empty_and_equal():
    assert _names(_orphan(handle="H2")) is False
    assert _names(_orphan(handle=""), handle="") is False
    assert _names(_orphan(platform="discord", handle="", deleter_id=""),
                  platform="discord", handle="") is False


def test_signals_timestamp_matches_the_message_or_any_of_its_edits():
    orphan = _orphan(platform="signal", handle="", sent_ts=777, deleter_id="+1")
    assert _names(orphan, platform="signal", handle="", timestamps=(777,)) is True
    assert _names(orphan, platform="signal", handle="", timestamps=(5, 777)) is True
    assert _names(orphan, platform="signal", handle="", timestamps=(5, 6)) is False


def test_only_the_author_can_have_deleted_on_signal_and_whatsapp_not_elsewhere():
    assert _names(_orphan(deleter_id="+9", deleter_ids=("+9",))) is False
    assert _names(_orphan(platform="signal", handle="", sent_ts=7, deleter_id="+9",
                          deleter_ids=("+9",)),
                  platform="signal", handle="", timestamps=(7,)) is False
    assert _names(_orphan(platform="discord", deleter_id="", deleter_ids=()),
                  platform="discord", sender_id="d1") is True


def test_an_orphans_other_id_forms_and_person_make_it_the_authors():
    orphan = _orphan(deleter_id="+85211112222", deleter_ids=("+85211112222", "999"),
                     deleter_person_id="ada")
    assert _names(orphan, sender_id="999") is True
    assert _names(orphan, sender_id="zzz", person_id="ada") is True
    assert _names(orphan, sender_id="zzz", person_id="ben") is False


def test_a_held_delete_waits_three_days_at_most_and_the_caps_are_ordered_by_blast_radius():
    """The race lasts seconds to minutes (a bridge backlog after an outage, hours): a held
    delete older than three days is purged. The caps bound the table by EVICTION: one named
    deleter is held well below the room, an unnamed one (Discord, Instagram) is held per
    ROOM, and the room well below the global ceiling, so one party cannot starve another."""
    assert ORPHAN_TTL_MS == 3 * 24 * 3600 * 1000
    assert ORPHAN_CAP_PER_DELETER < ORPHAN_CAP_UNNAMED_PER_ROOM < ORPHAN_CAP_PER_ROOM
    assert ORPHAN_CAP_PER_ROOM * 10 <= ORPHAN_CAP_GLOBAL
    assert (ORPHAN_CAP_PER_DELETER, ORPHAN_CAP_UNNAMED_PER_ROOM, ORPHAN_CAP_PER_ROOM,
            ORPHAN_CAP_GLOBAL) == (40, 100, 200, 5000)


def test_whitespace_is_no_id_in_the_rule_itself():
    assert _author(author_sender_id=" ", deleter_sender_id=" ") is False
    assert _author(author_sender_id=" ", deleter_sender_id="x", deleter_ids=[" "]) is False
    assert _author(author_sender_id="a", deleter_sender_id=" a ") is True
    assert _author(author_sender_id=" a", deleter_sender_id="b", deleter_ids=["a "]) is True
    assert _author(author_sender_id="x", deleter_sender_id="y",
                   author_person_id=" ", deleter_person_id=" ") is False
