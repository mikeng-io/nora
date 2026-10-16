"""`nora.platform.instagram.ids` — the pure half of the wire contract.

Everything here is about a value NOT being normalised into something it is
not. The Instagram id spaces are three sets of bare digits and the whole
hazard is that any of them will pass any check you invent for another, so what
is asserted is mostly what these helpers REFUSE to do.
"""

from __future__ import annotations

from nora.platform.instagram.ids import (
    conversation_id_for,
    fbid_form,
    igid_form,
    is_person_mention,
)

# Two real shapes from ONE three-person room. They are 15 and 17
# digits, and the 17-digit one shares the `1784…` prefix with Nora's own fbid —
# which is exactly why no length or prefix check may exist anywhere.
SHORT_FBID = "186217604552756"
LONG_FBID = "18457536310970624"


def test_an_fbid_keeps_its_digits_and_gains_no_plus():
    """A `+` would make an Instagram id string-equal to a Signal or WhatsApp
    phone, and `AdapterRegistry.self_aliases` is a flat union of bare values."""
    assert fbid_form(SHORT_FBID) == SHORT_FBID
    assert not fbid_form(SHORT_FBID).startswith("+")


def test_fbids_of_DIFFERENT_LENGTHS_both_survive():
    """The negative measurement. Any length or prefix validation would reject
    a real member, silently — a sender Nora cannot name is a sender she
    records under an unmapped id forever."""
    assert fbid_form(SHORT_FBID) == SHORT_FBID
    assert fbid_form(LONG_FBID) == LONG_FBID


def test_surrounding_whitespace_is_stripped():
    assert fbid_form(f"  {SHORT_FBID}\n") == SHORT_FBID
    assert igid_form(" 246967358 ") == "246967358"


def test_a_missing_or_non_string_id_becomes_empty_never_a_guess():
    """`""` matches nothing, which is the correct answer for a sender Nora
    cannot name. Every caller drops on empty rather than forwarding it."""
    for value in (None, 12345, [], {}, ""):
        assert fbid_form(value) == ""
        assert igid_form(value) == ""


def test_the_conversation_id_is_the_short_thread_id_VERBATIM():
    """`policy.toml` matches exact-string, and the bridge has already chosen
    the id space. A second spelling invented here is a second thing the
    operator's policy line can fail to equal."""
    assert conversation_id_for("1183780976338188") == "1183780976338188"


def test_the_conversation_id_does_not_reject_a_LONG_thread_id():
    """Not a gap — a stated non-goal. The long id is all digits too
    (a long id in `room_id` is the bridge's 400), so
    a shape check here would buy nothing and would be a second place to keep
    a rule the bridge already owns."""
    long_id = "340282366841710301281152937372201004392"
    assert conversation_id_for(long_id) == long_id


def test_a_PROFILE_mention_names_a_person():
    assert is_person_mention("PROFILE") is True


def test_a_mention_with_no_range_type_names_a_person():
    """`range_type` is `omitempty` on the wire. Refusing an absent one would
    drop every plain profile tag — including Nora's own."""
    assert is_person_mention(None) is True
    assert is_person_mention("") is True


def test_a_THREAD_mention_does_NOT_name_a_person():
    """Instagram's @everyone. Its `fbid` carries the CHAT's id, not anybody's,
    so admitting it would put a ROOM id into the set that resolves PEOPLE."""
    assert is_person_mention("THREAD") is False


def test_an_UNKNOWN_range_type_does_not_name_a_person():
    """Fail-closed: a range type this adapter has never heard of is not
    evidence that the number beside it is a person."""
    assert is_person_mention("SOMETHING_NEW") is False
