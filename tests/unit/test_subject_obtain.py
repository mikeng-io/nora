"""`default_policy()`/`build_config()` — the generated policy's `unit` must
agree with the composed `memory.workspace_id`, not merely share its default.

**Found live, third-party review of `nora/subject/sandbox.py`.** A subject
built with a `memory.workspace_id` OVERRIDE and no explicit policy got a
`Conversation.unit` of `"nora"` regardless -- `default_policy()`'s own
hardcoded default -- while `memory.workspace_id` held the override. Every
unit-keyed production read (`nora.app.composition._note_unit_of`, the
`<notes>` prompt block) resolves through the POLICY's `unit`, never through
`memory.workspace_id`, so the two silently disagreeing means notes seeded
under the isolated workspace are invisible to a turn driven against the same
subject, or a turn's own write lands wherever the policy's default unit
happens to point instead of the isolated one.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from nora.subject.obtain import SubjectSpec, build_config, default_policy


def test_default_policy_names_the_given_unit() -> None:
    policy = default_policy(("signal", "g1"), unit="friends-abc123")
    assert 'unit = "friends-abc123"' in policy


def test_default_policy_defaults_to_the_shipped_workspace_default() -> None:
    """No `unit=` argument -- the caller stated no override, so this must
    match `memory.workspace_id`'s own shipped default (`config.example.toml`)
    and `Conversation.unit`'s own shipped default (`nora/policy.py`), which
    already agree with EACH OTHER today; this only has to not disagree with
    both."""
    policy = default_policy(("signal", "g1"))
    assert 'unit = "nora"' in policy


def test_build_config_ties_the_generated_policys_unit_to_the_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The end-to-end property: a caller that overrides `memory.workspace_id`
    and states no policy gets a policy whose `unit` agrees, by construction
    -- not by every caller remembering to say so twice."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    spec = SubjectSpec(
        database_url="postgresql+asyncpg://x/x",
        workdir=tmp_path,
        room=("signal", "g1"),
        overrides={"memory.workspace_id": "friends-abc123"},
    )
    build_config(spec)
    policy_text = (tmp_path / "policy.toml").read_text(encoding="utf-8")
    assert 'unit = "friends-abc123"' in policy_text


def test_build_config_with_no_override_still_agrees_with_the_shipped_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    spec = SubjectSpec(
        database_url="postgresql+asyncpg://x/x",
        workdir=tmp_path,
        room=("signal", "g1"),
        overrides={},
    )
    build_config(spec)
    policy_text = (tmp_path / "policy.toml").read_text(encoding="utf-8")
    assert 'unit = "nora"' in policy_text


def test_an_explicit_policy_is_never_overwritten_when_it_already_agrees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`spec.policy`, when a caller states one that ALREADY agrees with the
    resolved unit, is used verbatim -- the unit resolution only fills in for
    a caller that stated NO policy at all. A case that already states its
    own `unit = "..."` matching its own `memory.workspace_id` (every ported
    corpus case does) must not have it silently replaced."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    stated = (
        'default = "deny"\n[[conversations]]\nplatform = "signal"\n'
        'id = "g1"\npersons = "*"\nunit = "friends-abc123"\n'
    )
    spec = SubjectSpec(
        database_url="postgresql+asyncpg://x/x",
        workdir=tmp_path,
        room=("signal", "g1"),
        overrides={"memory.workspace_id": "friends-abc123"},
        policy=stated,
    )
    build_config(spec)
    policy_text = (tmp_path / "policy.toml").read_text(encoding="utf-8")
    assert policy_text == stated


def test_the_no_override_case_is_grounded_on_the_composed_config_not_a_guess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**Found live, FIFTH review pass.** With no `memory.workspace_id`
    override, the unit check compared the policy against a value RE-DERIVED
    from `spec.overrides` with its own hardcoded `"nora"` fallback -- not
    against `cfg.memory.workspace_id`, the value `know()` actually writes
    notes under. Agreement rested on three literals in three files
    (`config.example.toml`, `nora/policy.py`, `obtain.py`) happening to
    coincide, which is exactly the "convention, not construction" this whole
    fix exists to close, one layer further down than the override case.

    Reproduced by pointing `_TEMPLATE` at a copy whose shipped
    `memory.workspace_id` differs from `"nora"` -- a config the operator
    could ship, and the exact shape a hardcoded-default comparison cannot
    see coming."""
    # `nora.subject`'s own `__init__.py` re-exports the `obtain()` FUNCTION
    # under the name `obtain`, which shadows `import nora.subject.obtain as
    # x` (a real Python import-machinery gotcha, not a typo) -- the string
    # form below resolves the SUBMODULE unambiguously.
    import importlib

    obtain_module = importlib.import_module("nora.subject.obtain")
    diverged = tmp_path / "diverged-template.toml"
    template_text = obtain_module._TEMPLATE.read_text(encoding="utf-8")
    diverged.write_text(
        template_text.replace('workspace_id = "nora"', 'workspace_id = "household"'),
        encoding="utf-8",
    )
    monkeypatch.setattr(obtain_module, "_TEMPLATE", diverged)
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    spec = SubjectSpec(
        database_url="postgresql+asyncpg://x/x",
        workdir=tmp_path / "subject",
        room=("signal", "g1"),
        overrides={},
    )
    cfg = build_config(spec)
    policy_text = (tmp_path / "subject" / "policy.toml").read_text(encoding="utf-8")
    assert cfg.memory.workspace_id == "household"
    assert 'unit = "household"' in policy_text


def test_an_explicit_policy_whose_unit_disagrees_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**Found live, FOURTH review pass.** The original fix only checked the
    `default_policy()` branch; a caller-stated `spec.policy` that names the
    room correctly but disagrees on `unit` reproduced the exact same defect
    through the untouched branch -- accepted silently, composing a subject
    whose `Conversation.unit` disagrees with its own `memory.workspace_id`.
    Resolution replaces the old substring test entirely: `build_config` now
    reads the policy it just wrote back through the real parser and refuses
    on disagreement, whichever branch produced it."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    stated = (
        'default = "deny"\n[[conversations]]\nplatform = "signal"\n'
        'id = "g1"\npersons = "*"\nunit = "ridgeline"\n'
    )
    spec = SubjectSpec(
        database_url="postgresql+asyncpg://x/x",
        workdir=tmp_path,
        room=("signal", "g1"),
        overrides={"memory.workspace_id": "friends-abc123"},
        policy=stated,
    )
    with pytest.raises(ValueError, match="resolves to unit"):
        build_config(spec)


def test_a_policy_naming_the_rooms_id_on_a_different_entry_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**Found live, FOURTH review pass.** The old check was a substring test
    over the raw file: `f'id = "{spec.room[1]}"' in policy` is satisfied by
    ANY entry carrying that literal, not necessarily the one the subject's
    own `(platform, conversation_id)` resolves to. A two-entry policy where
    the room's id string sits on a DIFFERENT platform's entry composed
    cleanly under the old check, with `Conversation.primary_id` resolving to
    a real OTHER room -- so culture/notes/the Dreamer's scope would key
    under a real room's identifiers, not the subject's own."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    stated = (
        'default = "deny"\n'
        '[[conversations]]\nplatform = "signal"\nid = "real-friends-sig"\n'
        'also_ids = ["g-case3"]\npersons = "*"\nunit = "friends"\n'
        '[[conversations]]\nplatform = "discord"\nid = "g-case3"\n'
        'persons = "*"\nunit = "friends"\n'
    )
    spec = SubjectSpec(
        database_url="postgresql+asyncpg://x/x",
        workdir=tmp_path,
        room=("signal", "g-case3"),
        overrides={"memory.workspace_id": "friends"},
        policy=stated,
    )
    with pytest.raises(ValueError, match="resolves to primary_id"):
        build_config(spec)


def test_a_policy_naming_the_id_on_the_wrong_platform_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**Found live, FOURTH review pass.** The old substring check could not
    see PLATFORM at all -- `id = "g-case4"` satisfies it regardless of which
    `[[conversations]]` entry's platform the subject's own room actually
    names. A policy whose only entry is on a different platform than the
    subject's room composed cleanly under the old check while
    `policy.conversation(*subject.room)` was `None` -- ingest refuses every
    message and no turn ever runs, the exact quiet failure this check exists
    to prevent."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    stated = (
        'default = "deny"\n[[conversations]]\nplatform = "discord"\n'
        'id = "g-case4"\npersons = "*"\nunit = "nora"\n'
    )
    spec = SubjectSpec(
        database_url="postgresql+asyncpg://x/x",
        workdir=tmp_path,
        room=("signal", "g-case4"),
        overrides={},
        policy=stated,
    )
    with pytest.raises(ValueError, match="names no conversation"):
        build_config(spec)


def test_the_generated_policy_still_parses() -> None:
    """The new `unit = "..."` line must be valid TOML the real parser
    accepts, not just a string containing the right substring."""
    policy = default_policy(("signal", "g1"), unit="friends-abc123")
    parsed = tomllib.loads(policy)
    assert parsed["conversations"][0]["unit"] == "friends-abc123"
