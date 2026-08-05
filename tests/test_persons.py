import pytest

from nora.domain.models import NormalizedMessage
from nora.persons import PersonsError, load_persons, sanitize_unmapped

TOML = """
[persons.miles]
name = "Miles"
[persons.miles.signal]
phone = "+85212345678"
uuid = "8185b8fa-359a-4c0a-c6ed-24814dc655bb"

[persons.ada]
name = "Ada"
[persons.ada.signal]
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
        "[persons.miles.signal]\n"
        "phone = \"+852111\"\n"
        "[persons.clone]\n"
        "name = \"Clone\"\n"
        "[persons.clone.signal]\n"
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
        "[persons.miles.signal]\n"
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
        "[persons.miles.signal]\n"
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
        "[persons.miles.signal]\n"
        "phone = \"+85212345678\"\n"
    )
    with pytest.raises(PersonsError, match="unexpected_field"):
        load_persons(p)


def test_loads_identity_toml(tmp_path):
    path = tmp_path / "identity.toml"
    path.write_text(
        '[persons.miles]\n'
        'name = "Miles"\n'
        '[persons.miles.signal]\n'
        'phone = "+85212345678"\n'
        '[persons.miles.telegram]\n'
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
        "[persons.miles.signal]\n"
        "display_name = \"Miles\"\n"
    )
    with pytest.raises(PersonsError, match="display_name"):
        load_persons(p)


def test_an_unknown_id_kind_for_a_platform_is_a_startup_failure(tmp_path):
    p = tmp_path / "identity.toml"
    p.write_text(
        "[persons.miles]\n"
        "name = \"Miles\"\n"
        "[persons.miles.signal]\n"
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
        "[persons.miles.signal]\n"
        "phone    = \"+85212345678\"\n"
        "uuid     = \"8185b8fa-359a-4c0a-c6ed-24814dc655bb\"\n"
        "username = \"miles.42\"\n"
        "\n"
        "[persons.miles.telegram]\n"
        "user_id  = \"123456789\"\n"
        "username = \"mikeng\"\n"
        "\n"
        "[persons.miles.discord]\n"
        "user_id = \"100000000000000003\"\n"
        "\n"
        "[persons.miles.whatsapp]\n"
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
