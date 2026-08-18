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
