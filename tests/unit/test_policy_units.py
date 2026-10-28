"""`policy.toml`'s `[units]` — workspace > unit > room (DEC-214).

The structure half of the hierarchy: which Nora workspace each unit belongs
to, and unique room names within a unit. Every id below is a placeholder.
"""

from __future__ import annotations

import pytest

from nora.policy import DEFAULT_WORKSPACE, PolicyError, load_policy

_ROOMS = """
default = "deny"

[[conversations]]
platform = "signal"
id = "sig-alpha"
label = "Alpha on Signal"
persons = "*"
unit = "alpha"

[[conversations]]
platform = "discord"
id = "100000000000000001/100000000000000002"
label = "Alpha on Discord"
persons = "*"
unit = "alpha"

[[conversations]]
platform = "signal"
id = "sig-beta"
label = "Beta on Signal"
persons = "*"
unit = "beta"

[[conversations]]
platform = "signal"
id = "sig-gamma"
also_ids = ["sig-gamma-old"]
label = "Gamma on Signal"
persons = "*"
unit = "gamma"
"""

_UNITS = """
[units.alpha]
workspace = "one"

[units.beta]
workspace = "one"

[units.gamma]
workspace = "two"
"""


def _load(tmp_path, body: str):
    path = tmp_path / "policy.toml"
    path.write_text(body)
    return load_policy(path)


def test_units_parse_into_the_workspace_hierarchy(tmp_path):
    policy = _load(tmp_path, _ROOMS + _UNITS)
    assert policy.units_declared is True
    assert policy.workspace_of("alpha") == "one"
    assert policy.workspace_of("beta") == "one"
    assert policy.workspace_of("gamma") == "two"
    assert policy.workspace_of("nobody") is None
    assert dict(policy.unit_workspaces()) == {"alpha": "one", "beta": "one", "gamma": "two"}


def test_without_units_every_unit_is_in_the_one_implicit_workspace(tmp_path):
    """A policy written without the hierarchy loads unchanged, every unit —
    listed or not — in `DEFAULT_WORKSPACE`."""
    policy = _load(tmp_path, _ROOMS)
    assert policy.units_declared is False
    assert policy.workspace_of("alpha") == DEFAULT_WORKSPACE
    assert policy.workspace_of("never-listed") == DEFAULT_WORKSPACE
    assert set(policy.unit_workspaces().values()) == {DEFAULT_WORKSPACE}


def test_a_unit_with_no_workspace_refuses_to_start_once_units_exist(tmp_path):
    partial = '\n[units.alpha]\nworkspace = "one"\n[units.beta]\nworkspace = "one"\n'
    with pytest.raises(PolicyError, match=r"declares \[units\] but not \['gamma'\]"):
        _load(tmp_path, _ROOMS + partial)


def test_a_declared_unit_can_keep_its_workspace_while_it_has_no_rooms(tmp_path):
    policy = _load(tmp_path, _ROOMS + _UNITS + '\n[units.delta]\nworkspace = "two"\n')
    assert policy.workspace_of("delta") == "two"
    assert not any(c.unit == "delta" for c in policy.conversations)


def test_a_unit_entry_without_a_workspace_is_refused(tmp_path):
    with pytest.raises(PolicyError, match="workspace"):
        _load(tmp_path, _ROOMS + _UNITS.replace('workspace = "two"', ""))


def test_a_workspace_name_honcho_would_refuse_is_refused_at_startup(tmp_path):
    with pytest.raises(PolicyError, match=r"\^\[a-zA-Z0-9_-\]\+\$"):
        _load(tmp_path, _ROOMS + _UNITS.replace('"two"', '"two words"'))


@pytest.mark.parametrize("units", ["", _UNITS], ids=["no-units", "with-units"])
def test_two_labelled_signal_rooms_in_one_unit_load(tmp_path, units):
    """Distinct room names address two Signal rooms in either policy form."""
    second = """
[[conversations]]
platform = "signal"
id = "sig-alpha-2"
label = "Alpha's second Signal room"
persons = "*"
unit = "alpha"
"""
    policy = _load(tmp_path, _ROOMS + second + units)
    rooms = [c for c in policy.conversations if c.unit == "alpha" and c.platform == "signal"]
    assert [(c.primary_id, c.label) for c in rooms] == [
        ("sig-alpha", "Alpha on Signal"),
        ("sig-alpha-2", "Alpha's second Signal room"),
    ]
    assert all(policy.conversation("signal", c.primary_id) is c for c in rooms)
    assert policy.workspace_of("alpha") == ("one" if units else DEFAULT_WORKSPACE)


def test_rooms_on_one_platform_across_units_are_allowed(tmp_path):
    """Rooms on one network may also belong to different units."""
    policy = _load(tmp_path, _ROOMS + _UNITS)
    signal_units = sorted(c.unit for c in policy.conversations if c.platform == "signal")
    assert signal_units == ["alpha", "beta", "gamma"]


@pytest.mark.parametrize("units", ["", _UNITS], ids=["no-units", "with-units"])
def test_duplicate_room_names_are_refused_after_case_and_spacing_fold(tmp_path, units):
    body = _ROOMS.replace('label = "Alpha on Discord"', 'label = " alpha  ON Signal "')
    with pytest.raises(PolicyError, match="duplicate room names") as exc:
        _load(tmp_path, body + units)
    assert "Alpha on Signal" in str(exc.value)
    assert " alpha  ON Signal " in str(exc.value)
    assert "signal:" in str(exc.value) and "discord:" in str(exc.value)


@pytest.mark.parametrize("units", ["", _UNITS], ids=["no-units", "with-units"])
def test_a_network_name_cannot_label_another_networks_room(tmp_path, units):
    body = _ROOMS.replace('label = "Alpha on Discord"', 'label = " sIgNaL "')
    with pytest.raises(PolicyError, match="reserved network name") as exc:
        _load(tmp_path, body + units)
    assert " sIgNaL " in str(exc.value)
    assert "Alpha on Signal" in str(exc.value)


@pytest.mark.parametrize("units", ["", _UNITS], ids=["no-units", "with-units"])
def test_a_network_name_cannot_hide_a_second_room_on_that_network(tmp_path, units):
    body = _ROOMS.replace('label = "Alpha on Signal"', 'label = "Signal"')
    body = body.replace('platform = "discord"', 'platform = "signal"')
    with pytest.raises(PolicyError, match="reserved network name") as exc:
        _load(tmp_path, body + units)
    assert "Signal" in str(exc.value)
    assert "Alpha on Discord" in str(exc.value)


@pytest.mark.parametrize("units", ["", _UNITS], ids=["no-units", "with-units"])
def test_a_network_name_can_label_its_own_networks_only_room(tmp_path, units):
    body = _ROOMS.replace('label = "Alpha on Signal"', 'label = "Signal"')
    assert len(_load(tmp_path, body + units).conversations) == 4


@pytest.mark.parametrize("units", ["", _UNITS], ids=["no-units", "with-units"])
def test_room_names_may_repeat_in_different_units(tmp_path, units):
    body = _ROOMS.replace('label = "Beta on Signal"', 'label = "Alpha on Signal"')
    assert len(_load(tmp_path, body + units).conversations) == 4


@pytest.mark.parametrize("units", ["", _UNITS], ids=["no-units", "with-units"])
def test_a_missing_room_label_still_refuses_to_start(tmp_path, units):
    body = _ROOMS.replace('label = "Alpha on Signal"\n', "")
    with pytest.raises(PolicyError, match="has no label"):
        _load(tmp_path, body + units)
