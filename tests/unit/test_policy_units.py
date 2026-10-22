"""`policy.toml`'s `[units]` — workspace > unit > room (DEC-214).

The structure half of the hierarchy: which Nora workspace each unit belongs
to, and DEC-105's one-room-per-platform-per-unit rule, which the loader
enforces. Every id below is a placeholder.
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
    with pytest.raises(PolicyError, match="unit_workspace_missing"):
        _load(tmp_path, _ROOMS + partial)


def test_a_unit_entry_no_room_uses_refuses_to_start(tmp_path):
    with pytest.raises(PolicyError, match="unit_workspace_unused"):
        _load(tmp_path, _ROOMS + _UNITS + '\n[units.delta]\nworkspace = "two"\n')


def test_a_unit_entry_without_a_workspace_is_refused(tmp_path):
    with pytest.raises(PolicyError, match=r"units\[2\].workspace: missing: Field required"):
        _load(tmp_path, _ROOMS + _UNITS.replace('workspace = "two"', ""))


def test_a_workspace_name_honcho_would_refuse_is_refused_at_startup(tmp_path):
    with pytest.raises(PolicyError, match=r"units\[2\].workspace.*must match") as error:
        _load(tmp_path, _ROOMS + _UNITS.replace('"two"', '"two words"'))
    assert "two words" not in str(error.value)


@pytest.mark.parametrize("units", ["", _UNITS], ids=["no-units", "with-units"])
def test_two_rooms_on_one_platform_in_one_unit_refuse_to_start(tmp_path, units):
    """DEC-105's invariant, enforced in both forms of the file: the rule is
    the unit's, not the workspace's."""
    second = """
[[conversations]]
platform = "signal"
id = "sig-alpha-2"
label = "Alpha's second Signal room"
persons = "*"
unit = "alpha"
"""
    with pytest.raises(PolicyError, match="unit_platform_unique"):
        _load(tmp_path, _ROOMS + second + units)


def test_one_room_per_platform_across_units_is_allowed(tmp_path):
    """The positive control: two Signal rooms are fine in two units — the
    refusal above is about the unit, not the platform."""
    policy = _load(tmp_path, _ROOMS + _UNITS)
    signal_units = sorted(c.unit for c in policy.conversations if c.platform == "signal")
    assert signal_units == ["alpha", "beta", "gamma"]
