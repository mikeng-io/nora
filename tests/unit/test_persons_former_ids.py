from __future__ import annotations

import pytest

from nora.persons import PersonsError, load_persons


def test_a_person_with_no_former_ids_resolves_to_itself(tmp_path):
    p = tmp_path / "identity.toml"
    p.write_text(
        '[persons.miles_novak]\nname = "Miles"\n'
    )
    persons = load_persons(p)
    assert persons.subject_id_for("miles_novak") == "miles_novak"


def test_a_former_id_resolves_to_the_current_person_id(tmp_path):
    p = tmp_path / "identity.toml"
    p.write_text(
        '[persons.clara_lawson]\n'
        'name = "Clara"\n'
        'former_person_ids = ["clara_yates"]\n'
    )
    persons = load_persons(p)
    assert persons.subject_id_for("clara_yates") == "clara_lawson"
    assert persons.subject_id_for("clara_lawson") == "clara_lawson"


def test_an_unknown_id_resolves_to_itself_rather_than_raising(tmp_path):
    # subject_id_for is called from the analytics rollup over IDs already
    # observed in presence_fixes — a person removed from identity.toml
    # entirely must not crash the rollup for every other subject.
    p = tmp_path / "identity.toml"
    p.write_text('[persons.miles_novak]\nname = "Miles"\n')
    persons = load_persons(p)
    assert persons.subject_id_for("someone_gone") == "someone_gone"


def test_a_former_id_claimed_by_two_people_refuses_to_load(tmp_path):
    # Fail-closed at load time (R-5's shape): an ambiguous merge declaration
    # must stop the process, not silently pick one winner.
    p = tmp_path / "identity.toml"
    p.write_text(
        '[persons.a]\nname = "A"\nformer_person_ids = ["x"]\n'
        '[persons.b]\nname = "B"\nformer_person_ids = ["x"]\n'
    )
    with pytest.raises(PersonsError, match="x"):
        load_persons(p)


def test_a_former_id_that_collides_with_a_live_person_id_refuses_to_load(tmp_path):
    p = tmp_path / "identity.toml"
    p.write_text(
        '[persons.miles_novak]\nname = "Miles"\n'
        '[persons.delia_lindberg]\nname = "Delia"\nformer_person_ids = ["miles_novak"]\n'
    )
    with pytest.raises(PersonsError, match="miles_novak"):
        load_persons(p)


def test_former_ids_for_returns_every_retired_id_for_the_current_owner(tmp_path):
    # The reverse of subject_id_for — nora.analytics.composition
    # ._subjects_from_identity needs this because life360 memberships can
    # only ever carry LIVE person_ids (load_persons refuses a
    # former_person_ids entry that collides with a live block), so
    # subject_id_for alone can never surface a former id.
    p = tmp_path / "identity.toml"
    p.write_text(
        '[persons.clara_lawson]\n'
        'name = "Clara"\n'
        'former_person_ids = ["clara_yates", "clara_walsh"]\n'
    )
    persons = load_persons(p)
    assert set(persons.former_ids_for("clara_lawson")) == {
        "clara_yates", "clara_walsh",
    }


def test_former_ids_for_a_never_renamed_person_is_empty(tmp_path):
    p = tmp_path / "identity.toml"
    p.write_text('[persons.miles_novak]\nname = "Miles"\n')
    persons = load_persons(p)
    assert persons.former_ids_for("miles_novak") == ()


def test_former_ids_for_an_unknown_person_is_empty_rather_than_raising(tmp_path):
    p = tmp_path / "identity.toml"
    p.write_text('[persons.miles_novak]\nname = "Miles"\n')
    persons = load_persons(p)
    assert persons.former_ids_for("someone_gone") == ()


def test_an_opted_out_person_is_off_under_every_id_they_were_recorded_under(tmp_path):
    # Social items carry the person id of the poll that recorded them; one
    # recorded before a rename carries the retired id and must stay out.
    p = tmp_path / "identity.toml"
    p.write_text(
        '[persons.clara_lawson]\n'
        'name = "Clara"\n'
        'social = false\n'
        'former_person_ids = ["clara_yates"]\n'
        '[persons.miles_novak]\nname = "Miles"\n'
    )
    persons = load_persons(p)
    assert persons.social_off("clara_yates")
    assert persons.social_off_ids() == frozenset({"clara_lawson", "clara_yates"})
    assert persons.social_off_ids(["clara_lawson", "miles_novak"]) == frozenset(
        {"clara_lawson", "clara_yates"},
    )
    assert persons.social_off_ids(["miles_novak"]) == frozenset()
