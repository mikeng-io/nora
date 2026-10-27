"""Where each unit's memory lives: policy structure + identity binding +
`honcho.workspace_id`, resolved once (`nora.app.memory_routing`, DEC-214).

A Nora workspace and a Honcho workspace are different names for different
things; these tests hold the line between them. Placeholder names throughout,
except the one test that mirrors the operator's live mapping by its own
names (`67`, `ridgeline`, `nora`).
"""

from __future__ import annotations

import pytest

from nora.app.memory_routing import (
    MemoryRoutingError,
    build_workspace_router,
    honcho_workspaces,
    profile_scopes_of_person,
    workspaces_of_person,
)
from nora.infra.honcho import HonchoRoute
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


def test_the_live_mapping_routes_two_workspaces_apart(tmp_path):
    """The operator's decision, by its own names: `67` = friends + wedding on
    the EXISTING Honcho workspace `nora`; `ridgeline` alone, on `nora-ridgeline`
    (the Honcho workspace named `ridgeline` belongs to another application).
    Units of one workspace share its Honcho workspace; each is its own
    scope; every id a room answers to routes."""
    router = build_workspace_router(
        policy=_policy(tmp_path, _LIVE_SHAPE),
        bindings={"67": "nora", "ridgeline": "nora-ridgeline"},
        default_workspace="nora",
    )
    assert router.strict is True
    assert router.for_conversation("sig-friends") == HonchoRoute("nora", "friends")
    assert router.for_conversation(
        "100000000000000001/100000000000000002",
    ) == HonchoRoute("nora", "friends")
    assert router.for_conversation("sig-wedding") == HonchoRoute("nora", "wedding")
    assert router.for_conversation("sig-ridgeline") == HonchoRoute("nora-ridgeline", "ridgeline")
    assert router.for_conversation("sig-ridgeline-old") == HonchoRoute(
        "nora-ridgeline", "ridgeline",
    )
    assert router.for_conversation("sig-unlisted") is None
    assert router.workspaces() == ("nora", "nora-ridgeline")
    assert router.for_workspace("67") == HonchoRoute("nora")


def test_without_units_everything_is_the_default_workspace_unscoped(tmp_path):
    """BACK-COMPAT: deploying the image with no `[units]` changes no read.
    Scoped routes here would read empty scopes (nothing has joined them) and
    silently empty every recall on the day of the deploy."""
    body = 'default = "deny"\n' + _room("signal", "a", "one") + _room("signal", "b", "two")
    router = build_workspace_router(
        policy=_policy(tmp_path, body), bindings={}, default_workspace="nora",
    )
    assert router.strict is False
    assert router.for_conversation("a") == HonchoRoute("nora")
    assert router.for_conversation("b") == HonchoRoute("nora")
    assert router.for_unit("one") == HonchoRoute("nora")
    # A harness's synthetic room: the default, UNSCOPED path.
    assert router.for_conversation("synthetic") == HonchoRoute("nora")
    assert router.workspaces() == ("nora",)


def test_a_binding_without_units_is_refused(tmp_path):
    body = 'default = "deny"\n' + _room("signal", "a", "one")
    with pytest.raises(MemoryRoutingError, match=r"declares no \[units\]"):
        honcho_workspaces(
            policy=_policy(tmp_path, body), bindings={"x": "y"}, default_workspace="nora",
        )


def test_a_binding_for_a_workspace_no_unit_uses_is_refused(tmp_path):
    with pytest.raises(MemoryRoutingError, match=r"workspaces\.typo"):
        honcho_workspaces(
            policy=_policy(tmp_path, _LIVE_SHAPE), bindings={"typo": "nora"},
            default_workspace="nora",
        )


def test_two_nora_workspaces_on_one_honcho_workspace_are_refused(tmp_path):
    """Peers are per Honcho workspace, so this would merge two workspaces'
    people into one set of models — what separate workspaces exist to stop."""
    with pytest.raises(MemoryRoutingError, match="both bound to the Honcho workspace"):
        honcho_workspaces(
            policy=_policy(tmp_path, _LIVE_SHAPE),
            bindings={"67": "ridgeline"}, default_workspace="nora",
        )


def test_one_conversation_id_in_two_units_is_refused(tmp_path):
    """Possible only across platforms, and memory routes by the id alone."""
    body = (
        'default = "deny"\n' + _room("signal", "same", "one")
        + _room("telegram", "same", "two")
    )
    with pytest.raises(MemoryRoutingError, match="two units"):
        build_workspace_router(
            policy=_policy(tmp_path, body), bindings={}, default_workspace="nora",
        )


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
