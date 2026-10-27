"""An eval subject can reach no Honcho workspace outside the eval namespace,
whatever the policy and identity it is composed with bind (DEC-214).

With `[units]` a unit routes to the Honcho workspace its Nora workspace is
BOUND to, and the eval suite composes against the deployment's own files by
default, whose binding names production's live workspace; rewriting
`honcho.workspace_id` alone is not the guarantee. This builds a subject's
config from files shaped like that and asks the production router where it can
reach. Placeholder ids; the workspace names are the operator's own mapping.
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
    `nora` and `ridgeline` — so the next test's result is the pinning's doing,
    not an input that could never have escaped."""
    (tmp_path / "policy.toml").write_text(_POLICY)
    (tmp_path / "identity.toml").write_text(_IDENTITY)
    router = build_workspace_router(
        policy=load_policy(tmp_path / "policy.toml"),
        bindings=load_persons(tmp_path / "identity.toml").honcho_workspaces,
        default_workspace="nora-eval",
    )
    assert set(router.workspaces()) == {"nora", "ridgeline"}


def test_a_subject_reaches_only_the_eval_workspace_across_every_binding(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    cfg = obtain_module.build_config(SubjectSpec(
        database_url="postgresql+asyncpg://x/x", workdir=tmp_path,
        identity=_IDENTITY, policy=_POLICY, room=_ROOM,
    ))
    eval_workspace = cfg.honcho.workspace_id
    assert eval_workspace.endswith("-eval")
    router = build_workspace_router(
        policy=load_policy(tmp_path / "policy.toml"),
        bindings=load_persons(tmp_path / "identity.toml").honcho_workspaces,
        default_workspace=eval_workspace,
    )
    assert router.workspaces() == (eval_workspace,)
    # Still STRICT, as production reads with `[units]`: every room routes to
    # its own unit's scope, inside the one eval workspace.
    assert router.strict is True
    assert router.for_conversation("g-friends").workspace == eval_workspace
    assert router.for_conversation("g-ridgeline").scope == "ridgeline"


def test_a_draw_workspace_override_is_what_every_unit_reaches(tmp_path, monkeypatch):
    """A draw names its workspace through an override; the pinning follows it."""
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
        default_workspace=draw,
    )
    assert router.workspaces() == (draw,)
