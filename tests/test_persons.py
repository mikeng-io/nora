import pytest

from nora.domain.models import DeliveryStatus, NormalizedMessage, TranscriptRow
from nora.persons import PersonsError, load_persons, sanitize_unmapped

TOML = """
[persons.miles]
name = "Miles"
[[persons.miles.signal]]
phone = "+85212345678"
uuid = "8185b8fa-359a-4c0a-c6ed-24814dc655bb"
[[persons.miles.life360]]
member_id = "life360-miles-uuid"
[[persons.miles.pulse]]
steam_id = "76561190000000001"

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
    """Any listed id resolves to the person — a hidden phone number
    still resolves via the uuid Nora saw and the operator confirmed."""
    ref = persons.resolve("signal", "8185b8fa-359a-4c0a-c6ed-24814dc655bb")
    assert ref is not None
    assert ref.person_id == "miles"


def test_a_life360_member_id_resolves_to_the_same_person(persons):
    """A person's Life360 circle-member UUID binds to the SAME
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


def test_a_pulse_steam_id_resolves_to_the_same_person(persons):
    """A Pulse (game-server-monitor) SteamID64 binds to the SAME person_id as
    their Signal number — the web portal's Games Hub can name a player as one
    of Nora's people the same way presence names a live circle member."""
    ref = persons.resolve("pulse", "76561190000000001")
    assert ref is not None
    assert ref.person_id == "miles"
    assert persons.resolve("signal", "+85212345678").person_id == ref.person_id


def test_a_bad_pulse_id_kind_is_a_startup_failure(tmp_path):
    """pulse's only matchable kind is `steam_id` — never `player_key`, which is
    per-game and does not carry over to a different title (R-5: a typo or the
    wrong id kind must be a startup error, not a silent non-match)."""
    p = tmp_path / "identity.toml"
    p.write_text(
        '[persons.miles]\nname = "Miles"\n'
        '[[persons.miles.pulse]]\nplayer_key = "id:eos:x"\n'
    )
    with pytest.raises(PersonsError, match="steam_id"):
        load_persons(p)


# ------------------------------------------------------- instagram `username`

def test_an_instagram_username_is_readable_as_the_handle_for_its_fbid(tmp_path):
    """`username` is an accepted, never-matchable `instagram` metadata key
    (`nora.domain.identity.metadata_keys("instagram")`). Proves the two
    id-vocabulary declarations this repo splits across `nora.domain.identity`
    (the allowed keys) and `nora.persons_file` (the pydantic shape that must
    accept them) are in sync: `_check_account` raises `PersonsError`
    (via pydantic) the moment either one drops `username`, so this test goes
    red on that mismatch before it reaches a live identity.toml — the class of
    startup crash caused by adding an id kind to one file and not the other."""
    p = tmp_path / "identity.toml"
    p.write_text(
        '[persons.miles]\nname = "Miles Novak"\n'
        '[[persons.miles.instagram]]\n'
        'fbid = "186217604552756"\n'
        'igid = "61652861337"\n'
        'username = "nka_bpumvg"\n'
    )
    persons = load_persons(p)
    assert persons.instagram_handle("186217604552756") == "nka_bpumvg"


def test_an_instagram_fbid_with_no_username_on_file_has_no_handle(tmp_path):
    """The ordinary, EXPECTED state until an operator opts a person in:
    `identity.example.toml` shows `username` as optional metadata,
    and the caller (`nora.agent.outbound._mentions`) must degrade to today's
    plain-text mention rather than raise or guess one."""
    p = tmp_path / "identity.toml"
    p.write_text(
        '[persons.miles]\nname = "Miles Novak"\n'
        '[[persons.miles.instagram]]\nfbid = "186217604552756"\nigid = "61652861337"\n'
    )
    persons = load_persons(p)
    assert persons.instagram_handle("186217604552756") is None


def test_instagram_handle_answers_empty_for_an_fbid_it_has_never_heard_of(persons):
    assert persons.instagram_handle("000000000000000") is None


def test_an_instagram_handle_is_scoped_to_the_account_it_was_recorded_beside(tmp_path):
    """A person may hold two Instagram accounts (personal/business) with
    DIFFERENT handles. Keying on the fbid, not the person_id, is what keeps
    `nka_bpumvg`'s handle from leaking onto `miles_biz`'s fbid or vice
    versa — `nora.agent.outbound._mentions` already knows which fbid THIS
    room's roster resolved to, and this must read off that exact account."""
    p = tmp_path / "identity.toml"
    p.write_text(
        '[persons.miles]\nname = "Miles Novak"\n'
        '[[persons.miles.instagram]]\n'
        'label = "personal"\nfbid = "186217604552756"\nusername = "nka_bpumvg"\n'
        '[[persons.miles.instagram]]\n'
        'label = "business"\nfbid = "200000000000001"\nusername = "miles_biz"\n'
    )
    persons = load_persons(p)
    assert persons.instagram_handle("186217604552756") == "nka_bpumvg"
    assert persons.instagram_handle("200000000000001") == "miles_biz"


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


def test_the_same_life360_member_id_across_two_circles_is_not_a_duplicate_failure(tmp_path):
    """One physical Life360 account can sit in several circles —
    `member_id` is per-ACCOUNT, not per-circle, so it legitimately repeats
    across blocks as long as `circle_id` differs. The generic copy-paste
    guard (`test_...same_id_twice...`, above) must not fire on this
    intentional pattern."""
    p = tmp_path / "identity.toml"
    p.write_text(
        "[persons.miles]\n"
        "name = \"Miles\"\n"
        "[[persons.miles.life360]]\n"
        "label = \"family circle\"\n"
        "member_id = \"m-1\"\n"
        "circle_id = \"family\"\n"
        "[[persons.miles.life360]]\n"
        "label = \"team42 circle\"\n"
        "member_id = \"m-1\"\n"
        "circle_id = \"team42\"\n"
    )
    persons = load_persons(p)
    assert persons.resolve("life360", "m-1").person_id == "miles"
    circles = {m.circle_id for m in persons.life360 if m.member_id == "m-1"}
    assert circles == {"family", "team42"}


def test_the_same_life360_member_id_and_circle_id_twice_is_still_a_copy_paste_failure(tmp_path):
    """The narrower key must not swallow the real mistake it exists to
    catch: two blocks with the SAME member_id AND the SAME circle_id is
    still a copy-paste-and-forget, not a second legitimate membership."""
    p = tmp_path / "identity.toml"
    p.write_text(
        "[persons.miles]\n"
        "name = \"Miles\"\n"
        "[[persons.miles.life360]]\n"
        "label = \"family circle\"\n"
        "member_id = \"m-1\"\n"
        "circle_id = \"family\"\n"
        "[[persons.miles.life360]]\n"
        "label = \"family circle again\"\n"
        "member_id = \"m-1\"\n"
        "circle_id = \"family\"\n"
    )
    with pytest.raises(PersonsError, match="two different account blocks"):
        load_persons(p)


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
    with pytest.raises(PersonsError, match="entry: extra_forbidden"):
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
    with pytest.raises(PersonsError, match=r"persons\[0\].entry: extra_forbidden"):
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
# hard break on the old shorthand
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
    """The preview identity TOML's shape is accepted, matched exactly."""
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
    assert persons.resolve("telegram", "123456789") is not None
    # A username is ACCEPTED in the file (so it stays readable) and never
    # INDEXED — a renameable handle must not resolve a person. Both platforms'
    # usernames are present in the TOML above, so these prove the exclusion
    # rather than an omission.
    assert persons.resolve("signal", "miles.42") is None
    assert persons.resolve("telegram", "pinegrove") is None
    assert persons.resolve("discord", "100000000000000003") is not None
    assert persons.resolve("whatsapp", "+85212345678") is not None


# ── many accounts per platform ───────────────────────────────────────────────
# A person holding several accounts on ONE platform is the ordinary case, not
# an edge case: a dual-SIM personal/work pair, a second Discord, a burner
# Telegram. This is a different axis from many ID FORMS of one account (phone /
# uuid / username), which the tests above already cover.


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
    a spoofable string would become a resolvable identity."""
    persons = load_persons(_write(tmp_path, TWO_ACCOUNTS))
    assert persons.resolve("signal", "personal") is None
    assert persons.resolve("signal", "business") is None
    assert ("signal", "personal") not in persons.index


def test_the_retired_single_table_form_names_its_replacement(tmp_path):
    """pydantic's own "Input should be a valid list" names neither the new
    shape nor why it changed; this file's breaks
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


def test_platform_ids_returns_every_id_form_that_person_presents(persons):
    """The reverse of `resolve`, and the only OUTBOUND consumer of this class.

    Every FORM, not one: `nora.agent.outbound` intersects this with the group's
    own membership list before choosing what to put on the wire, so handing
    back a single "best" id here would make that intersection able to miss —
    the platform reports one member as `{number, uuid}` and identity.toml may
    only have matched on the other one.
    """
    ids = persons.platform_ids("signal", "miles")
    assert ids == frozenset({"+85212345678", "8185b8fa-359a-4c0a-c6ed-24814dc655bb"})


def test_platform_ids_is_scoped_to_the_platform_asked_for(persons):
    """Miles's life360 `member_id` resolves to the same person (A2/DEC-14) and
    is emphatically NOT something to hand signal-cli as a mention recipient."""
    assert persons.platform_ids("life360", "miles") == frozenset({"life360-miles-uuid"})
    assert "life360-miles-uuid" not in persons.platform_ids("signal", "miles")


def test_platform_ids_answers_empty_for_someone_it_does_not_know(persons):
    """Empty, never a `KeyError`: the caller is mid-turn composing a reply, and
    a name it could not place must cost a mention, not the message."""
    assert persons.platform_ids("signal", "nobody") == frozenset()
    assert persons.platform_ids("telegram", "miles") == frozenset()


def test_platform_ids_round_trips_with_resolve(persons):
    """The two directions read one index, so they cannot disagree — which is
    why `platform_ids` derives from `index` instead of keeping an inverse."""
    for value in persons.platform_ids("signal", "ada"):
        assert persons.resolve("signal", value).person_id == "ada"


def test_an_alias_never_resolves_a_sender(tmp_path):
    """Aliases are for COMPREHENSION, never for identity.

    A nickname is hand-written by the operator and a display name is settable
    by anyone, so letting either resolve a sender would hand identity to
    whoever can type it. The property holds structurally — `aliases` sits on
    the person, not inside a platform account block, so the index loop cannot
    reach it — but structure is not a test, so this pins it.
    """
    p = _write(tmp_path, (
        '[persons.miles_novak]\nname = "Miles"\naliases = ["阿Miles", "俊爺", "norty"]\n'
        '[[persons.miles_novak.signal]]\nphone = "+85290000101"\n'
    ))
    persons = load_persons(p)
    assert persons.resolve("signal", "+85290000101").person_id == "miles_novak"
    for alias in ("阿Miles", "俊爺", "norty", "Miles"):
        assert persons.resolve("signal", alias) is None, alias


def _t_row(row_id, sender_id, sender_name, *, is_nora=False):
    return TranscriptRow(
        id=row_id, sender_id=sender_id, sender_name=sender_name, ts=row_id, body="hi",
        is_nora=is_nora, delivery_status=DeliveryStatus.PENDING, raw_json=None,
    )


def test_name_transcript_projects_every_mapped_speaker(tmp_path):
    """DEC-80. `display_name` for a whole transcript, so the prefill renders the
    SAME name space as `tag_message from=` / `group_members` / `lookup_person`.

    Both names survive: `sender_name` is the self-set platform profile name and
    stays as provenance, `person_name` is the operator-authored one the renderer
    prefers. Overwriting `sender_name` in place would make the projection
    unauditable — you could no longer see what the wire actually said."""
    persons = load_persons(_write(tmp_path, TOML))
    rows = [_t_row(1, "+85212345678", "陳志明 Chi Ming"), _t_row(2, "+85287654321", "峰鵰")]

    out = persons.name_transcript(rows, platform="signal")

    assert [r.person_name for r in out] == ["Miles", "Ada"]
    assert [r.sender_name for r in out] == ["陳志明 Chi Ming", "峰鵰"]   # provenance kept


def test_name_transcript_leaves_an_unmapped_speaker_alone(tmp_path):
    """No canonical name rather than a guessed one — `_label` then falls back to
    the profile name, which is what keeps a stranger visible as somebody."""
    persons = load_persons(_write(tmp_path, TOML))
    out = persons.name_transcript([_t_row(1, "+85299999999", "A Stranger")], platform="signal")
    assert out[0].person_name is None


def test_name_transcript_never_resolves_on_the_display_name(tmp_path):
    """The `test_an_alias_never_resolves_a_sender` rule, applied to the
    transcript: a row whose PROFILE NAME is «Miles» but whose sender_id belongs to
    nobody must not be projected onto Miles. A display name is settable by anyone,
    so matching on it would let a stranger wear a member's identity in the one
    block the model reads as fact."""
    persons = load_persons(_write(tmp_path, TOML))
    out = persons.name_transcript([_t_row(1, "+85299999999", "Miles")], platform="signal")
    assert out[0].person_name is None


def test_name_transcript_leaves_noras_own_rows_unprojected(tmp_path):
    """She is deliberately absent from identity.toml and `_label` renders her
    from the configured `display_name`, so projecting her could only ever write
    None over None — but it would also mean a person_id collision could rename
    her. Skipped explicitly."""
    persons = load_persons(_write(tmp_path, TOML))
    out = persons.name_transcript(
        [_t_row(1, "+85212345678", None, is_nora=True)], platform="signal",
    )
    assert out[0].person_name is None


def test_name_transcript_resolves_per_platform(tmp_path):
    """Keyed `(platform, sender_id)` like every other resolution: the same id
    string on a different platform is a different account."""
    persons = load_persons(_write(tmp_path, TOML))
    out = persons.name_transcript([_t_row(1, "+85212345678", "Miles")], platform="whatsapp")
    assert out[0].person_name is None


# ── direct messages: addressed from identity.toml alone ──

_DIRECT_TOML = """
[persons.miles]
name = "Miles"
[[persons.miles.signal]]
phone = "+85212345678"
uuid = "8185b8fa-359a-4c0a-c6ed-24814dc655bb"
[[persons.miles.whatsapp]]
phone = "+85212345678"
lid = "60000000000001"

[persons.ada]
name = "Ada"
[[persons.ada.signal]]
phone = "+85287654321"
[[persons.ada.whatsapp]]
lid = "14575433319544"
"""


@pytest.fixture
def direct(tmp_path):
    p = tmp_path / "identity.toml"
    p.write_text(_DIRECT_TOML)
    return load_persons(p)


def test_a_direct_target_prefers_the_aci_on_signal_and_the_phone_on_whatsapp(direct):
    signal = direct.direct_target("signal", "miles")
    assert (signal.conversation_id, signal.conversation_type) == (
        "8185b8fa-359a-4c0a-c6ed-24814dc655bb", "dm",
    )
    assert direct.direct_target("signal", "ada").conversation_id == "+85287654321"
    assert direct.direct_target("whatsapp", "miles").conversation_id == (
        "85212345678@s.whatsapp.net"
    )
    assert direct.direct_target("whatsapp", "ada").conversation_id == "14575433319544@lid"


def test_somebody_not_in_identity_toml_has_no_direct_target(direct):
    assert direct.direct_target("signal", "stranger") is None
    assert direct.direct_target("discord", "miles") is None


def test_the_direct_addresses_are_every_account_in_the_file_and_nothing_else(direct):
    assert direct.direct_addresses("signal") == {
        "+85212345678", "8185b8fa-359a-4c0a-c6ed-24814dc655bb", "+85287654321",
    }
    assert direct.direct_addresses("whatsapp") == {
        "85212345678@s.whatsapp.net", "60000000000001@lid", "14575433319544@lid",
    }
    # Every target `direct_target` builds is one the senders will accept.
    for platform in ("signal", "whatsapp"):
        for person in ("miles", "ada"):
            target = direct.direct_target(platform, person)
            assert target.conversation_id in direct.direct_addresses(platform)
