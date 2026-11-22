"""Which Nora workspaces and (unit, workspace) scopes a person belongs to, read
from `policy.toml` (`nora.app.memory_routing`, DEC-214). Placeholder names
throughout, except the shape that mirrors the operator's live mapping by its
own names (`67`, `ridgeline`).
"""

from __future__ import annotations

import pytest

from nora.app.memory_routing import (
    profile_scopes_of_person,
    workspaces_of_person,
)
from nora.persons import PersonsError, load_persons
from nora.policy import load_policy


def _room(platform: str, cid: str, unit: str, *, also: str = "") -> str:
    also_ids = f'also_ids = ["{also}"]\n' if also else ""
    return (
        f'[[conversations]]\nplatform = "{platform}"\nid = "{cid}"\n{also_ids}'
        f'label = "{cid}"\npersons = "*"\nunit = "{unit}"\n'
    )


_LIVE_SHAPE = (
    'default = "deny"\n'
    + _room("signal", "sig-friends", "friends")
    + _room("discord", "100000000000000001/100000000000000002", "friends")
    + _room("signal", "sig-wedding", "wedding")
    + _room("signal", "sig-ridgeline", "ridgeline", also="sig-ridgeline-old")
    + '[units.friends]\nworkspace = "67"\n'
    + '[units.wedding]\nworkspace = "67"\n'
    + '[units.ridgeline]\nworkspace = "ridgeline"\n'
)


def _policy(tmp_path, body: str):
    path = tmp_path / "policy.toml"
    path.write_text(body)
    return load_policy(path)


def _bindings(tmp_path, body: str) -> dict[str, str]:
    path = tmp_path / "identity.toml"
    path.write_text(body)
    return load_persons(path).honcho_workspaces


def test_identity_binds_a_nora_workspace_to_a_honcho_workspace(tmp_path):
    assert _bindings(tmp_path, '[workspaces.67]\nhoncho_workspace = "nora"\n') == {
        "67": "nora",
    }


def test_a_honcho_id_honcho_would_refuse_is_refused_at_startup(tmp_path):
    with pytest.raises(PersonsError, match="honcho_workspace"):
        _bindings(tmp_path, '[workspaces.67]\nhoncho_workspace = "no spaces"\n')


def test_a_person_belongs_to_the_workspaces_whose_rooms_name_them(tmp_path):
    """The portal's membership rule: a room whose `persons` list names you
    puts you in its unit's workspace; a `persons = "*"` room names nobody, so
    it places nobody."""
    def room(cid, unit, persons):
        return (
            f'[[conversations]]\nplatform = "signal"\nid = "{cid}"\nlabel = "{cid}"\n'
            f"persons = {persons}\nunit = \"{unit}\"\n"
        )
    body = (
        'default = "deny"\n'
        + room("a", "friends", '["jordan", "blair"]')
        + room("b", "ridgeline", '["jordan"]')
        + room("c", "open", '"*"')
        + '[units.friends]\nworkspace = "67"\n[units.ridgeline]\nworkspace = "ridgeline"\n'
        + '[units.open]\nworkspace = "public"\n'
    )
    policy = _policy(tmp_path, body)
    assert workspaces_of_person(policy, "jordan") == ("67", "ridgeline")
    assert workspaces_of_person(policy, "blair") == ("67",)
    assert workspaces_of_person(policy, "nobody") == ()


def test_without_units_everyone_is_in_the_one_implicit_workspace(tmp_path):
    """The portal's back-compat: no `[units]`, so every person — including one
    who is only in `persons = "*"` rooms — reads the one workspace (DEC-214)."""
    body = 'default = "deny"\n' + _room("signal", "a", "one")
    assert workspaces_of_person(_policy(tmp_path, body), "anyone") == ("default",)


def test_profile_scopes_pair_each_listing_unit_with_its_workspace(tmp_path):
    """Where a person's Profile accounts live (45 §1): one (unit, workspace)
    per unit whose rooms list them BY NAME — a `*` room lists nobody, and an
    unlisted person has no scope at all (no account exists for one)."""
    def room(cid, unit, persons):
        return (
            f'[[conversations]]\nplatform = "signal"\nid = "{cid}"\nlabel = "{cid}"\n'
            f"persons = {persons}\nunit = \"{unit}\"\n"
        )
    body = (
        'default = "deny"\n'
        + room("a", "friends", '["jordan", "blair"]')
        + room("b", "ridgeline", '["jordan"]')
        + room("c", "open", '"*"')
        + '[units.friends]\nworkspace = "67"\n[units.ridgeline]\nworkspace = "ridgeline"\n'
        + '[units.open]\nworkspace = "public"\n'
    )
    policy = _policy(tmp_path, body)
    assert profile_scopes_of_person(policy, "jordan") == (
        ("friends", "67"), ("ridgeline", "ridgeline"),
    )
    assert profile_scopes_of_person(policy, "blair") == (("friends", "67"),)
    assert profile_scopes_of_person(policy, "nobody") == ()


def test_profile_scopes_without_units_cover_only_listed_people(tmp_path):
    """Without `[units]` the one workspace is `default`, but a Profile account
    exists only for a person a room lists — so unlike
    `workspaces_of_person`'s everyone, an unlisted person scopes to nothing."""
    def room(platform, cid, persons):
        return (
            f'[[conversations]]\nplatform = "{platform}"\nid = "{cid}"\nlabel = "{cid}"\n'
            f"persons = {persons}\n"
        )
    body = 'default = "deny"\n' + room("signal", "a", '["jordan"]') + room("discord", "b", '"*"')
    policy = _policy(tmp_path, body)
    assert profile_scopes_of_person(policy, "jordan") == (("nora", "default"),)
    assert profile_scopes_of_person(policy, "nobody") == ()
