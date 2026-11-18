"""An eval subject preserves the deployment's configured workspace routing,
and isolation lives at the case-owned service boundary, not in a renamed
namespace (DEC-214).

With `[units]` a unit routes to the Honcho workspace its Nora workspace is
BOUND to. The eval subject keeps the deployment's `identity.toml`/`policy.toml`
verbatim — every binding reaches the wire exactly as production would route
it, against a server the case owns. These tests build a subject's config and
router from files shaped like a multi-workspace deployment and ask the
production router where each binding lands. Placeholder ids; the workspace
names are the operator's own mapping.
"""

from __future__ import annotations

import importlib

from nora.app.memory_routing import build_workspace_router
from nora.persons import load_persons
from nora.policy import load_policy
from nora.subject import SubjectSpec

obtain_module = importlib.import_module("nora.subject.obtain")

_ROOM = ("signal", "g-friends")

_POLICY = """
default = "deny"

[[conversations]]
platform = "signal"
id = "g-friends"
label = "Friends"
persons = "*"
unit = "friends"

[[conversations]]
platform = "signal"
id = "g-ridgeline"
label = "Ridgeline"
persons = "*"
unit = "ridgeline"

[units.friends]
workspace = "67"

[units.ridgeline]
workspace = "ridgeline"
"""

_IDENTITY = """
[persons.jordan]
name = "Jordan"
[[persons.jordan.signal]]
phone = "+85200000001"

[workspaces.67]
honcho_workspace = "nora"
"""


def test_the_files_as_written_would_reach_live_workspaces(tmp_path):
    """The positive control: composed as written, these files DO route to
    `nora` and `ridgeline` — so the next tests' assertions are about real
    routing, not an input that could never have escaped."""
    (tmp_path / "policy.toml").write_text(_POLICY)
    (tmp_path / "identity.toml").write_text(_IDENTITY)
    router = build_workspace_router(
        policy=load_policy(tmp_path / "policy.toml"),
        bindings=load_persons(tmp_path / "identity.toml").honcho_workspaces,
        default_workspace="nora-eval",
    )
    assert set(router.workspaces()) == {"nora", "ridgeline"}


def test_a_subject_preserves_the_deployed_routing_across_every_binding(
    tmp_path, monkeypatch,
):
    """The subject's workspace is the deployment's configured one — never
    renamed — and every `[units]` binding routes where the deployment's own
    files put it. What isolates a case is the case-owned server its
    `honcho.base_url` binds to (`IsolatedServices.bind_config`), not a
    rewritten namespace."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    deployment = obtain_module._load_config(obtain_module._TEMPLATE).honcho.workspace_id
    cfg = obtain_module.build_config(SubjectSpec(
        database_url="postgresql+asyncpg://x/x", workdir=tmp_path,
        identity=_IDENTITY, policy=_POLICY, room=_ROOM,
    ))
    assert cfg.honcho.workspace_id == deployment == "nora"
    router = build_workspace_router(
        policy=load_policy(tmp_path / "policy.toml"),
        bindings=load_persons(tmp_path / "identity.toml").honcho_workspaces,
        default_workspace=cfg.honcho.workspace_id,
    )
    assert set(router.workspaces()) == {"nora", "ridgeline"}, (
        "every deployment binding is routed as declared — pinning all of "
        "them into one namespace would lose the deployment's memory split"
    )
    assert router.strict is True
    assert router.for_conversation("g-friends").workspace == "nora"
    assert router.for_conversation("g-ridgeline").workspace == "ridgeline"
    assert router.for_conversation("g-ridgeline").scope == "ridgeline"
    assert (tmp_path / "policy.toml").read_text() == _POLICY
    assert (tmp_path / "identity.toml").read_text() == _IDENTITY


def test_a_workspace_override_changes_only_the_default_route(tmp_path, monkeypatch):
    """An explicit `honcho.workspace_id` override is honored as declared
    config — but it changes the DEFAULT route only: units bound through
    `[workspaces]` keep their bound workspace. Nothing flattens every
    binding into one name."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    deployment = obtain_module._load_config(obtain_module._TEMPLATE).honcho.workspace_id
    draw = f"{deployment}-eval-run-case-1-abcd"
    cfg = obtain_module.build_config(SubjectSpec(
        database_url="postgresql+asyncpg://x/x", workdir=tmp_path,
        identity=_IDENTITY, policy=_POLICY, room=_ROOM,
        overrides={"honcho.workspace_id": draw},
    ))
    assert cfg.honcho.workspace_id == draw
    router = build_workspace_router(
        policy=load_policy(tmp_path / "policy.toml"),
        bindings=load_persons(tmp_path / "identity.toml").honcho_workspaces,
        default_workspace=cfg.honcho.workspace_id,
    )
    assert set(router.workspaces()) == {"nora", "ridgeline"}, (
        "bound units keep their declared workspaces even under an override"
    )
    assert router.for_conversation("g-friends").workspace == "nora"
    assert router.for_conversation("g-ridgeline").workspace == "ridgeline"
    assert router.default == draw, (
        "the override becomes the router's default — what an unscoped call "
        "and the ownership check name — as production resolves it"
    )
    assert router.unrouted() is None, (
        "under declared [units] an unrouted call fails closed, never widened"
    )
