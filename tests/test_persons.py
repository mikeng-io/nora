import pytest

from nora.domain.models import NormalizedMessage
from nora.persons import PersonsError, load_persons, sanitize_unmapped

TOML = """
[persons.miles]
name = "Miles"
[[persons.miles.signal]]
phone = "+85212345678"
uuid = "8185b8fa-359a-4c0a-c6ed-24814dc655bb"
[[persons.miles.life360]]
member_id = "life360-miles-uuid"

[persons.ada]
name = "Ada"
[[persons.ada.signal]]
phone = "+85287654321"
"""


@pytest.fixture
def persons(tmp_path):
    p = tmp_path / "identity.toml"
    p.write_text(TOML)
    return load_persons(p)


def _msg(sender_id, sender_name=None) -> NormalizedMessage:
    return NormalizedMessage("signal", "g1", "group", sender_id, sender_name, 0, "hi", {})


def test_resolve_maps_a_known_sender(persons):
    ref = persons.resolve("signal", "+85212345678")
    assert ref is not None
    assert (ref.person_id, ref.name, ref.honcho_peer_id) == ("miles", "Miles", "miles")


def test_resolve_returns_none_for_an_unmapped_sender(persons):
    assert persons.resolve("signal", "+85200000000") is None


def test_resolve_is_platform_scoped(persons):
    assert persons.resolve("discord", "+85212345678") is None


def test_a_second_id_kind_for_the_same_person_resolves_to_them_too(persons):
    """§3.2: any listed id resolves to the person — a hidden phone number
    still resolves via the uuid Nora saw and the operator confirmed."""
    ref = persons.resolve("signal", "8185b8fa-359a-4c0a-c6ed-24814dc655bb")
    assert ref is not None
    assert ref.person_id == "miles"


def test_a_life360_member_id_resolves_to_the_same_person(persons):
    """design/19 §3: a person's Life360 circle-member UUID binds to the SAME
    person_id as their Signal number, so the presence collector can name a live
    circle member as one of Nora's people and join presence to memory on one
    key. Both ids land on `miles`."""
    ref = persons.resolve("life360", "life360-miles-uuid")
    assert ref is not None
    assert ref.person_id == "miles"
    assert persons.resolve("signal", "+85212345678").person_id == ref.person_id


def test_a_life360_circle_id_is_captured_metadata_not_a_matchable_id(persons):
    """circle_id names WHICH circle a member is in (presence reads it) but is NOT
    matchable — you never see a circle_id on an inbound message. It is captured as
    a membership, and resolving it as an id returns nobody."""
    # (the shared fixture's miles block has member_id but no circle_id)
    members = {(m.person_id, m.member_id) for m in persons.life360}
    assert ("miles", "life360-miles-uuid") in members


def test_a_life360_circle_id_does_not_resolve_a_sender(tmp_path):
    p = tmp_path / "identity.toml"
    p.write_text(
        '[persons.miles]\nname = "Miles"\n'
        '[[persons.miles.life360]]\nmember_id = "m-1"\ncircle_id = "cir-1"\n'
    )
    persons = load_persons(p)
    assert persons.resolve("life360", "m-1").person_id == "miles"   # member_id matches
    assert persons.resolve("life360", "cir-1") is None             # circle_id does NOT
    (m,) = persons.life360
    assert (m.member_id, m.circle_id) == ("m-1", "cir-1")


def test_a_bad_life360_id_kind_is_a_startup_failure(tmp_path):
    """life360's only matchable kind is `member_id`; a typo'd kind must be a
    startup error, not a key that silently resolves no one (R-5)."""
    p = tmp_path / "identity.toml"
    p.write_text(
        '[persons.miles]\nname = "Miles"\n'
        '[[persons.miles.life360]]\nmemberid = "x"\n'   # typo: no underscore
    )
    with pytest.raises(PersonsError, match="member_id"):
        load_persons(p)


def test_display_name_prefers_the_mapped_name(persons):
    assert persons.display_name(_msg("+85212345678", "mikey_2")) == "Miles"


def test_display_name_falls_back_to_the_platform_name(persons):
    assert persons.display_name(_msg("+85200000000", "Stranger")) == "Stranger"


def test_display_name_falls_back_to_a_sanitized_id(persons):
    assert persons.display_name(_msg("+85200000000", None)) == sanitize_unmapped("+85200000000")


def test_sanitize_unmapped_hides_the_middle_of_a_phone_number():
    assert sanitize_unmapped("+85212345678") == "852…678"
    assert "12345" not in sanitize_unmapped("+85212345678")


def test_sanitize_unmapped_handles_short_ids():
    assert sanitize_unmapped("abc") == "abc"


def test_duplicate_sender_id_across_persons_is_a_startup_failure(tmp_path):
    p = tmp_path / "identity.toml"
    p.write_text(
        "[persons.miles]\n"
        "name = \"Miles\"\n"
        "[[persons.miles.signal]]\n"
        "phone = \"+852111\"\n"
        "[persons.clone]\n"
        "name = \"Clone\"\n"
        "[[persons.clone.signal]]\n"
        "phone = \"+852111\"\n"
    )
    with pytest.raises(PersonsError, match="duplicate"):
        load_persons(p)


def test_the_same_id_twice_under_the_same_person_is_not_a_duplicate_failure(tmp_path):
    """A person listing one literal value under two different kinds by
    mistake is harmless — it's the same person on both sides — unlike the
    same value mapped to two DIFFERENT people, which stays a hard error."""
    p = tmp_path / "identity.toml"
    p.write_text(
        "[persons.miles]\n"
        "name = \"Miles\"\n"
        "[[persons.miles.signal]]\n"
        "phone = \"+852111\"\n"
        "username = \"+852111\"\n"
    )
    persons = load_persons(p)
    assert persons.resolve("signal", "+852111") is not None


def test_person_id_must_satisfy_the_honcho_peer_id_charset(tmp_path):
    p = tmp_path / "identity.toml"
    p.write_text(
        "[persons.\"miles smith\"]\n"
        "name = \"Miles\"\n"
        "[persons.\"miles smith\".signal]\n"
        "phone = \"+1\"\n"
    )
    with pytest.raises(PersonsError, match="person_id"):
        load_persons(p)


def test_missing_file_is_a_startup_failure(tmp_path):
    with pytest.raises(PersonsError, match="not found"):
        load_persons(tmp_path / "nope.toml")


def test_by_person_exposes_every_mapped_person(persons):
    assert set(persons.by_person) == {"miles", "ada"}


def test_unknown_top_level_key_is_a_startup_failure(tmp_path):
    """extra="forbid" on _PersonsFile: the old YAML loader silently ignored unrecognised
    top-level keys; the pydantic model must fail loudly instead."""
    p = tmp_path / "identity.toml"
    p.write_text(
        "unexpected_top_level_key = \"oops\"\n"
        "\n"
        "[persons.miles]\n"
        "name = \"Miles\"\n"
        "[[persons.miles.signal]]\n"
        "phone = \"+85212345678\"\n"
    )
    with pytest.raises(PersonsError, match="unexpected_top_level_key"):
        load_persons(p)


def test_unknown_field_in_a_person_entry_is_a_startup_failure(tmp_path):
    """extra="forbid" on _PersonEntry: an unrecognised field inside a [persons.*] table
    must fail startup, not be silently dropped as the old YAML loader did."""
    p = tmp_path / "identity.toml"
    p.write_text(
        "[persons.miles]\n"
        "name = \"Miles\"\n"
        "unexpected_field = \"oops\"\n"
        "[[persons.miles.signal]]\n"
        "phone = \"+85212345678\"\n"
    )
    with pytest.raises(PersonsError, match="unexpected_field"):
        load_persons(p)


def test_loads_identity_toml(tmp_path):
    path = tmp_path / "identity.toml"
    path.write_text(
        '[persons.miles]\n'
        'name = "Miles"\n'
        '[[persons.miles.signal]]\n'
        'phone = "+85212345678"\n'
        '[[persons.miles.telegram]]\n'
        'user_id = 123456789\n'          # TOML integers must stringify
    )
    persons = load_persons(path)
    ref = persons.resolve("signal", "+85212345678")
    assert ref is not None
    assert ref.name == "Miles"
    assert ref.person_id == "miles"
    assert ref.honcho_peer_id == "miles"
    assert persons.resolve("telegram", "123456789") is not None


# ---------------------------------------------------------------------------
# design/10 §7.3 — hard break on the old shorthand
# ---------------------------------------------------------------------------


def test_the_old_platforms_shorthand_is_a_readable_startup_failure(tmp_path):
    p = tmp_path / "identity.toml"
    p.write_text(
        "[persons.miles]\n"
        "name = \"Miles\"\n"
        "[persons.miles.platforms]\n"
        "signal = \"+85212345678\"\n"
    )
    with pytest.raises(PersonsError, match=r"platforms.*no longer supported"):
        load_persons(p)


def test_display_name_as_an_id_kind_is_a_startup_failure(tmp_path):
    p = tmp_path / "identity.toml"
    p.write_text(
        "[persons.miles]\n"
        "name = \"Miles\"\n"
        "[[persons.miles.signal]]\n"
        "display_name = \"Miles\"\n"
    )
    with pytest.raises(PersonsError, match="display_name"):
        load_persons(p)


def test_an_unknown_id_kind_for_a_platform_is_a_startup_failure(tmp_path):
    p = tmp_path / "identity.toml"
    p.write_text(
        "[persons.miles]\n"
        "name = \"Miles\"\n"
        "[[persons.miles.signal]]\n"
        "user_id = \"123\"\n"          # that's telegram's kind, not signal's
    )
    with pytest.raises(PersonsError, match="not a valid id kind"):
        load_persons(p)


def test_every_platform_in_the_preview_toml_is_accepted(tmp_path):
    """design/10-preview-identity.toml's shape, matched exactly."""
    p = tmp_path / "identity.toml"
    p.write_text(
        "[persons.miles]\n"
        "name = \"Miles\"\n"
        "\n"
        "[[persons.miles.signal]]\n"
        "phone    = \"+85212345678\"\n"
        "uuid     = \"8185b8fa-359a-4c0a-c6ed-24814dc655bb\"\n"
        "username = \"miles.42\"\n"
        "\n"
        "[[persons.miles.telegram]]\n"
        "user_id  = \"123456789\"\n"
        "username = \"mikeng\"\n"
        "\n"
        "[[persons.miles.discord]]\n"
        "user_id = \"100000000000000003\"\n"
        "\n"
        "[[persons.miles.whatsapp]]\n"
        "phone = \"+85212345678\"\n"
    )
    persons = load_persons(p)
    assert persons.resolve("signal", "+85212345678") is not None
    assert persons.resolve("signal", "8185b8fa-359a-4c0a-c6ed-24814dc655bb") is not None
    assert persons.resolve("signal", "miles.42") is not None
    assert persons.resolve("telegram", "123456789") is not None
    assert persons.resolve("telegram", "pinegrove") is not None
    assert persons.resolve("discord", "100000000000000003") is not None
    assert persons.resolve("whatsapp", "+85212345678") is not None


# ── many accounts per platform (design/10 §3, the multi-account baseline) ────
# A person holding several accounts on ONE platform is the ordinary case, not
# an edge case: a dual-SIM personal/work pair, a second Discord, a burner
# Telegram. This is a different axis from many ID FORMS of one account (phone /
# uuid / username), which the tests above already cover — the original schema
# conflated the two and had nowhere to put the second account.


def _write(tmp_path, body: str):
    p = tmp_path / "identity.toml"
    p.write_text(body)
    return p


TWO_ACCOUNTS = """
[persons.miles]
name = "Miles"

[[persons.miles.signal]]
label = "personal"
phone = "+85290000101"
uuid = "a1000001-0000-4000-8000-000000000001"

[[persons.miles.signal]]
label = "business"
phone = "+85290000103"
uuid = "a1000002-0000-4000-8000-000000000002"
"""


def test_two_accounts_on_one_platform_resolve_to_the_same_person(tmp_path):
    """A2: one human is one Honcho peer. Both registrations must land on the
    same person_id, or his memory splits across two peer cards."""
    persons = load_persons(_write(tmp_path, TWO_ACCOUNTS))
    ids = [
        "+85290000101", "a1000001-0000-4000-8000-000000000001",
        "+85290000103", "a1000002-0000-4000-8000-000000000002",
    ]
    resolved = [persons.resolve("signal", i) for i in ids]
    assert all(r is not None for r in resolved), "every listed id must resolve"
    assert {r.person_id for r in resolved} == {"miles"}
    assert {r.honcho_peer_id for r in resolved} == {"miles"}


def test_a_label_is_never_matchable(tmp_path):
    """`label` is operator-authored free text. If it reached the reverse index
    a spoofable string would become a resolvable identity (design/10 §1.3)."""
    persons = load_persons(_write(tmp_path, TWO_ACCOUNTS))
    assert persons.resolve("signal", "personal") is None
    assert persons.resolve("signal", "business") is None
    assert ("signal", "personal") not in persons.index


def test_the_retired_single_table_form_names_its_replacement(tmp_path):
    """pydantic's own "Input should be a valid list" names neither the new
    shape nor why it changed; §7.3 set the precedent that this file's breaks
    get a readable error."""
    body = '[persons.miles]\nname = "Miles"\n[persons.miles.signal]\nphone = "+852111"\n'
    with pytest.raises(PersonsError, match=r"array of tables"):
        load_persons(_write(tmp_path, body))


def test_the_same_id_in_two_account_blocks_is_a_startup_failure(tmp_path):
    """The copy-paste-and-forget-to-edit mistake, which is otherwise SILENT:
    both blocks map to this person so the cross-person check never fires, and
    the id that was never pasted resolves to nobody. Under sealed sender that
    unpasted ACI is often the only id Nora ever sees."""
    body = (
        '[persons.miles]\nname = "Miles"\n'
        '[[persons.miles.signal]]\nlabel = "personal"\n'
        'phone = "+85290000101"\nuuid = "a1000001-0000-4000-8000-000000000001"\n'
        '[[persons.miles.signal]]\nlabel = "business"\n'
        'phone = "+85290000103"\nuuid = "a1000001-0000-4000-8000-000000000001"\n'
    )
    with pytest.raises(PersonsError, match=r"two different account blocks"):
        load_persons(_write(tmp_path, body))


def test_a_list_at_the_leaf_is_refused(tmp_path):
    """Multiplicity lives at the TABLE level, never at the leaf. If the leaf
    type were widened to accept a list, `str(["+1","+2"])` would reach the
    reverse index as a key no sender can ever present — a permanently mute
    Nora with no error, invisible under R-5."""
    body = '[persons.miles]\nname = "Miles"\n[[persons.miles.signal]]\nphone = ["+1", "+2"]\n'
    with pytest.raises(PersonsError):
        load_persons(_write(tmp_path, body))


def test_two_accounts_must_each_be_labelled(tmp_path):
    """A second, independent tripwire on the copy-paste failure above: it
    forces the operator to look at the block he just duplicated."""
    body = (
        '[persons.miles]\nname = "Miles"\n'
        '[[persons.miles.signal]]\nphone = "+1"\n'
        '[[persons.miles.signal]]\nphone = "+2"\n'
    )
    with pytest.raises(PersonsError, match=r"needs a `label"):
        load_persons(_write(tmp_path, body))


def test_two_accounts_may_not_share_a_label(tmp_path):
    body = (
        '[persons.miles]\nname = "Miles"\n'
        '[[persons.miles.signal]]\nlabel = "mine"\nphone = "+1"\n'
        '[[persons.miles.signal]]\nlabel = "mine"\nphone = "+2"\n'
    )
    with pytest.raises(PersonsError, match=r"duplicate label"):
        load_persons(_write(tmp_path, body))


def test_an_account_with_no_matchable_id_is_refused(tmp_path):
    """A block holding only a label contributes nothing to the index — it
    would sit in the file looking like a mapping while resolving no one."""
    body = '[persons.miles]\nname = "Miles"\n[[persons.miles.signal]]\nlabel = "empty"\n'
    with pytest.raises(PersonsError, match=r"no matchable id"):
        load_persons(_write(tmp_path, body))


def test_a_single_account_still_needs_no_label(tmp_path):
    """The cost of the change for a one-account person is two brackets."""
    body = '[persons.ada]\nname = "Ada"\n[[persons.ada.signal]]\nuuid = "b68741f9"\n'
    persons = load_persons(_write(tmp_path, body))
    assert persons.resolve("signal", "b68741f9").person_id == "ada"


def test_an_alias_never_resolves_a_sender(tmp_path):
    """Aliases are for COMPREHENSION, never for identity.

    A nickname is hand-written by the operator and a display name is settable
    by anyone, so letting either resolve a sender would hand identity to
    whoever can type it (design/10 §1.3). The property holds structurally —
    `aliases` sits on the person, not inside a platform account block, so the
    index loop cannot reach it — but structure is not a test, and this was
    previously unpinned despite being claimed.
    """
    p = _write(tmp_path, (
        '[persons.miles_novak]\nname = "Miles"\naliases = ["阿Miles", "俊爺", "norty"]\n'
        '[[persons.miles_novak.signal]]\nphone = "+85290000101"\n'
    ))
    persons = load_persons(p)
    assert persons.resolve("signal", "+85290000101").person_id == "miles_novak"
    for alias in ("阿Miles", "俊爺", "norty", "Miles"):
        assert persons.resolve("signal", alias) is None, alias
