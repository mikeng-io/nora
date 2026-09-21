"""`default_policy()`/`build_config()`/`_conversation_for()` — the room a
subject composes with is RESOLVED from the policy it wrote, never grepped.

Two properties, and the second replaced a third that was wrong:

- the policy must name the subject's room, on its own platform, as its OWN
  entry (`primary_id == conversation_id`) — or culture/notes/the Dreamer's
  scope would key under some other room's identifiers, and ingest would
  refuse every message with nothing raising;
- the room's `unit` is whatever the policy says. It is NOT required to equal
  `memory.workspace_id`: that is Honcho's per-deployment workspace, and the
  deployment's own `policy.toml` fails that equality on every room it has.
  An earlier version of this package refused exactly the shape production
  runs.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from nora.subject.obtain import SubjectSpec, _conversation_for, build_config, default_policy

_DEPLOYMENT_SHAPED = (
    'default = "deny"\n'
    '[[conversations]]\nplatform = "signal"\nid = "ridgeline-sig"\n'
    'persons = ["miles_novak"]\nunit = "ridgeline"\nrelevance_gate = true\n'
    '[[conversations]]\nplatform = "signal"\nid = "friends-sig"\n'
    'label = "Friends"\ndescription = "the friends"\npersons = "*"\n'
    'unit = "friends"\nhome_room = true\n'
)


def _spec(tmp_path: Path, **over) -> SubjectSpec:
    return SubjectSpec(
        database_url="postgresql+asyncpg://x/x", workdir=tmp_path,
        **{"room": ("signal", "g1"), **over},
    )


def test_default_policy_names_the_given_unit() -> None:
    policy = default_policy(("signal", "g1"), unit="friends")
    assert 'unit = "friends"' in policy


def test_default_policy_defaults_to_the_shipped_unit_default() -> None:
    policy = default_policy(("signal", "g1"))
    assert 'unit = "nora"' in policy


def test_the_generated_policy_still_parses() -> None:
    parsed = tomllib.loads(default_policy(("signal", "g1"), unit="friends"))
    assert parsed["conversations"][0]["unit"] == "friends"


def test_build_config_writes_a_policy_naming_the_room(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    build_config(_spec(tmp_path))
    convo = _conversation_for(_spec(tmp_path))
    assert (convo.primary_id, convo.unit) == ("g1", "nora")


def test_an_explicit_policy_is_used_verbatim(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    build_config(_spec(tmp_path, room=("signal", "friends-sig"), policy=_DEPLOYMENT_SHAPED))
    assert (tmp_path / "policy.toml").read_text(encoding="utf-8") == _DEPLOYMENT_SHAPED


def test_a_deployment_shaped_policy_whose_unit_is_not_the_workspace_is_accepted(
    tmp_path, monkeypatch,
) -> None:
    """Production's own shape: `memory.workspace_id = "nora"` and rooms in
    units `friends`/`ridgeline`. Refusing it would refuse the deployment."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    spec = _spec(tmp_path, room=("signal", "friends-sig"), policy=_DEPLOYMENT_SHAPED)
    cfg = build_config(spec)
    convo = _conversation_for(spec)
    assert convo.unit == "friends"
    assert convo.primary_id == "friends-sig"
    assert cfg.memory.workspace_id != "friends", "the unit is the room's, not Honcho's"


def test_conversation_for_resolves_the_rooms_own_entry_among_several(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    spec = _spec(tmp_path, room=("signal", "ridgeline-sig"), policy=_DEPLOYMENT_SHAPED)
    build_config(spec)
    convo = _conversation_for(spec)
    assert (convo.primary_id, convo.unit) == ("ridgeline-sig", "ridgeline")


def test_the_honcho_workspace_is_a_fixed_sibling_of_the_deployments(tmp_path, monkeypatch) -> None:
    """Honcho is the one store the sandbox database does not isolate. The
    subject's workspace is the deployment's with `-eval` — stable, so a
    replay into it can be shared; never per case, never the real one."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    cfg = build_config(_spec(tmp_path))
    assert cfg.memory.workspace_id.endswith("-eval")
    assert cfg.memory.workspace_id != "-eval"


def test_a_caller_may_still_override_the_workspace(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    cfg = build_config(_spec(tmp_path, overrides={"memory.workspace_id": "probe"}))
    assert cfg.memory.workspace_id == "probe"


def test_a_policy_naming_the_rooms_id_on_a_different_entry_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A substring test over the raw file is satisfied by ANY entry carrying
    the literal. A two-entry policy where the room's id sits on a DIFFERENT
    platform's entry composes cleanly under such a check, with
    `Conversation.primary_id` resolving to a real OTHER room — so
    culture/notes/the Dreamer's scope would key under that room's
    identifiers."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    stated = (
        'default = "deny"\n'
        '[[conversations]]\nplatform = "signal"\nid = "real-friends-sig"\n'
        'also_ids = ["g-case3"]\npersons = "*"\nunit = "friends"\n'
        '[[conversations]]\nplatform = "discord"\nid = "g-case3"\n'
        'persons = "*"\nunit = "friends"\n'
    )
    with pytest.raises(ValueError, match="resolves to primary_id"):
        build_config(_spec(tmp_path, room=("signal", "g-case3"), policy=stated))


def test_a_policy_naming_the_id_on_the_wrong_platform_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A policy whose only entry is on a different platform than the
    subject's room: `policy.conversation(*subject.room)` is `None`, ingest
    refuses every message and no turn ever runs — the quiet failure this
    check exists to prevent."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    stated = (
        'default = "deny"\n[[conversations]]\nplatform = "discord"\n'
        'id = "g-case4"\npersons = "*"\nunit = "nora"\n'
    )
    with pytest.raises(ValueError, match="names no conversation"):
        build_config(_spec(tmp_path, room=("signal", "g-case4"), policy=stated))
