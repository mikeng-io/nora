"""`default_policy()`/`build_config()`/`_conversation_for()` — the room a
subject composes with is RESOLVED from the policy it wrote, never grepped.

Two properties:

- the policy must name the subject's room, on its own platform, as its OWN
  entry (`primary_id == conversation_id`) — or culture/notes/the Dreamer's
  scope would key under some other room's identifiers, and ingest would
  refuse every message with nothing raising;
- the room's `unit` is whatever the policy says. It is NOT required to equal
  `honcho.workspace_id`: that is Honcho's per-deployment workspace, and the
  deployment's own `policy.toml` fails that equality on every room it has.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from nora.policy import load_policy
from nora.subject.obtain import SubjectSpec, _conversation_for, build_config, default_policy

_DEPLOYMENT_SHAPED = (
    'default = "deny"\n'
    '[[conversations]]\nplatform = "signal"\nid = "ridgeline-sig"\n'
    'label = "Ridgeline"\npersons = ["miles_novak"]\nunit = "ridgeline"\nrelevance_gate = true\n'
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


def test_judges_keep_the_bound_config_instead_of_reloading_deployment_routes(
    example_config, monkeypatch, tmp_path,
):
    import importlib

    from nora.subject.obtain import _with
    from scripts.memory_graph_spike import Judges

    module = importlib.import_module("nora.subject.obtain")
    endpoint = "http://127.0.0.1:13421/v1"
    cfg = _with(example_config, "llm.base_url", endpoint)
    for name in cfg.providers:
        cfg = _with(cfg, f"providers.{name}.base_url", endpoint)

    def forbidden(*args, **kwargs):
        raise AssertionError("bound model routing must not reload files or resolve DNS")

    monkeypatch.setattr(module, "_load_config", forbidden)
    monkeypatch.setattr(module, "_resolves", forbidden)
    judges = Judges(cfg, ["generated-judge"], tmp_path / "unbound-config.toml")
    assert judges._endpoint("generated-judge")[0] == endpoint
    assert judges._endpoint("generated-judge")[2] == "generated-judge"
    from evals.rebuilt.grading import Judge

    original = Judge("unused", model="generated-judge", config_path=tmp_path / "missing.toml",
                     samples=1, max_tokens=99, timeout_seconds=7)
    bound = original.bind(cfg)
    assert bound._endpoint()[0] == endpoint
    assert bound._endpoint()[2] == "generated-judge"
    assert (bound.samples, bound.max_tokens, bound.timeout_seconds) == (1, 99, 7)
    assert original.config is None


def test_default_policy_defaults_to_the_shipped_unit_default() -> None:
    policy = default_policy(("signal", "g1"))
    assert 'unit = "nora"' in policy


def test_the_generated_policy_still_parses() -> None:
    parsed = tomllib.loads(default_policy(("signal", "g1"), unit="friends"))
    assert parsed["conversations"][0]["unit"] == "friends"


def test_subject_consumers_read_the_pytest_owned_config(
    evaluation_config, tmp_path_factory, tmp_path, monkeypatch,
) -> None:
    import importlib

    module = importlib.import_module("nora.subject.obtain")
    assert evaluation_config.is_relative_to(tmp_path_factory.getbasetemp())
    assert evaluation_config == module._TEMPLATE
    stated = tomllib.loads(evaluation_config.read_text(encoding="utf-8"))
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    cfg = build_config(_spec(tmp_path))
    copied = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))
    assert copied == stated
    assert cfg.llm.model == stated["llm"]["model"] == "nora"
    assert cfg.relevance_gate.enabled is True


def test_each_spec_composes_its_own_declared_config(
    evaluation_config, tmp_path, monkeypatch,
) -> None:
    """`spec.config` binds THAT file, per spec: two distinguishable recorded
    configs compose two distinguishable subjects while the module template
    and a caller that declares nothing are untouched."""
    import importlib

    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    module = importlib.import_module("nora.subject.obtain")
    template_text = evaluation_config.read_text(encoding="utf-8")
    first_path = tmp_path / "in_force_first" / "config.toml"
    second_path = tmp_path / "in_force_second" / "config.toml"
    first_path.parent.mkdir()
    second_path.parent.mkdir()
    first_path.write_text(template_text.replace(
        'model = "nora"', 'model = "recorded-first"', 1), encoding="utf-8")
    second_path.write_text(template_text.replace(
        'model = "nora"', 'model = "recorded-second"', 1), encoding="utf-8")

    first = build_config(_spec(tmp_path / "first", config=first_path))
    second = build_config(_spec(tmp_path / "second", config=second_path))
    assert first.llm.model == "recorded-first"
    assert second.llm.model == "recorded-second"
    # Each subject owns its copy in ITS workdir.
    assert tomllib.loads((tmp_path / "first" / "config.toml").read_text(
        encoding="utf-8"))["llm"]["model"] == "recorded-first"
    # The module template never moved, and a spec that declares nothing
    # still composes it.
    assert evaluation_config == module._TEMPLATE
    assert build_config(_spec(tmp_path / "third")).llm.model == "nora"
    # Credential custody stays with the deployment's secret file wherever
    # the declared config lives — a recorded `in_force/` holds none.
    assert first._secret_path == module._TEMPLATE.parent / "secret.toml"


@pytest.mark.parametrize("ambient", ["absent", "home", "deployment_env"])
def test_evaluation_config_ignores_ambient_deployments(
    ambient, evaluation_config, tmp_path_factory, tmp_path, monkeypatch,
) -> None:
    from tests.conftest import evaluation_config as fixture

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("NORA_DEPLOYMENT_DIR", raising=False)
    if ambient != "absent":
        deployment = home / "workspace" / "nora"
        deployment.mkdir(parents=True)
        (deployment / "config.toml").write_text("[synthetic broken\n", encoding="utf-8")
        if ambient == "deployment_env":
            monkeypatch.setenv("NORA_DEPLOYMENT_DIR", str(deployment))
    # Exercise the real fixture again after setting the synthetic ambient input;
    # the outer session fixture necessarily ran before this test's monkeypatch.
    generated = fixture.__wrapped__(tmp_path_factory)
    try:
        target = next(generated)
        assert target != evaluation_config
        assert target.is_relative_to(tmp_path_factory.getbasetemp())
        assert target.read_bytes() == evaluation_config.read_bytes()
    finally:
        generated.close()


def test_build_config_writes_a_policy_naming_the_room(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    build_config(_spec(tmp_path))
    convo = _conversation_for(_spec(tmp_path), load_policy(tmp_path / "policy.toml"))
    assert (convo.primary_id, convo.unit) == ("g1", "nora")


def test_subjects_have_distinct_computer_journal_ownership(
    tmp_path, monkeypatch, evaluation_config,
) -> None:
    import importlib
    import json

    from nora.app.computer_queue import SegmentRegistry

    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    module = importlib.import_module("nora.subject.obtain")
    template = tmp_path / "template.toml"
    template.write_text(evaluation_config.read_text().replace(
        "[computer]\n",
        "[computer]\nstate_dir = " + json.dumps(str(tmp_path / "shared-state")) + "\n",
        1))
    monkeypatch.setattr(module, "_TEMPLATE", template)
    first = build_config(_spec(tmp_path / "first"))
    second = build_config(_spec(tmp_path / "second"))
    owners = []
    try:
        for cfg in (first, second):
            owners.append(SegmentRegistry(pending_dir=Path(cfg.computer.state_dir) / "computer"))
        assert first.computer.state_dir != second.computer.state_dir
    finally:
        for owner in owners:
            owner.close()


def test_an_explicit_policy_is_used_verbatim(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    build_config(_spec(tmp_path, room=("signal", "friends-sig"), policy=_DEPLOYMENT_SHAPED))
    assert (tmp_path / "policy.toml").read_text(encoding="utf-8") == _DEPLOYMENT_SHAPED


def test_a_deployment_shaped_policy_whose_unit_is_not_the_workspace_is_accepted(
    tmp_path, monkeypatch,
) -> None:
    """Production's own shape: `honcho.workspace_id = "nora"` and rooms in
    units `friends`/`ridgeline`. Refusing it would refuse the deployment."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    spec = _spec(tmp_path, room=("signal", "friends-sig"), policy=_DEPLOYMENT_SHAPED)
    cfg = build_config(spec)
    convo = _conversation_for(spec, load_policy(tmp_path / "policy.toml"))
    assert convo.unit == "friends"
    assert convo.primary_id == "friends-sig"
    assert cfg.honcho.workspace_id != "friends", "the unit is the room's, not Honcho's"


def test_conversation_for_resolves_the_rooms_own_entry_among_several(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    spec = _spec(tmp_path, room=("signal", "ridgeline-sig"), policy=_DEPLOYMENT_SHAPED)
    build_config(spec)
    convo = _conversation_for(spec, load_policy(tmp_path / "policy.toml"))
    assert (convo.primary_id, convo.unit) == ("ridgeline-sig", "ridgeline")


def test_the_honcho_workspace_name_is_preserved(evaluation_config, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    cfg = build_config(_spec(tmp_path))
    assert cfg.honcho.workspace_id == tomllib.loads(evaluation_config.read_text())["honcho"][
        "workspace_id"]


def test_subject_identity_workspace_bindings_are_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    identity = ('[workspaces.home]\nhoncho_workspace="generated-home"\n'
                '[workspaces.work]\nhoncho_workspace="generated-work"\n')
    build_config(_spec(tmp_path, identity=identity))
    assert (tmp_path / "identity.toml").read_text() == identity


def test_a_draw_names_its_own_workspace(tmp_path, monkeypatch) -> None:
    """An eval draw composes against a workspace of its own."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    cfg = build_config(_spec(
        tmp_path, overrides={"honcho.workspace_id": "nora-eval-run-case-1-abc123"},
    ))
    assert cfg.honcho.workspace_id == "nora-eval-run-case-1-abc123"


@pytest.mark.parametrize("workspace", ["nora", "probe", "nora-eval-", "nora-48b00d52c0ff"])
def test_workspace_names_are_values_not_the_external_isolation_boundary(
    tmp_path, monkeypatch, workspace,
) -> None:
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    cfg = build_config(_spec(tmp_path, overrides={"honcho.workspace_id": workspace}))
    assert cfg.honcho.workspace_id == workspace


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
        'also_ids = ["g-case3"]\nlabel = "Friends"\npersons = "*"\nunit = "friends"\n'
        '[[conversations]]\nplatform = "discord"\nid = "g-case3"\n'
        'label = "g-case3"\npersons = "*"\nunit = "friends"\n'
    )
    spec = _spec(tmp_path, room=("signal", "g-case3"), policy=stated)
    build_config(spec)
    with pytest.raises(ValueError, match="resolves to primary_id"):
        _conversation_for(spec, load_policy(tmp_path / "policy.toml"))


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
        'id = "g-case4"\nlabel = "g-case4"\npersons = "*"\nunit = "nora"\n'
    )
    spec = _spec(tmp_path, room=("signal", "g-case4"), policy=stated)
    build_config(spec)
    with pytest.raises(ValueError, match="names no conversation"):
        _conversation_for(spec, load_policy(tmp_path / "policy.toml"))


def test_a_named_provider_is_dialled_on_loopback_from_the_host(
    tmp_path, monkeypatch, example_config,
) -> None:
    """On the host, `host.docker.internal` does not resolve; every endpoint a
    model call can take, the named providers too, is rewritten to loopback."""
    import importlib

    from nora.config import ProviderConfig

    obtain = importlib.import_module("nora.subject.obtain")

    monkeypatch.setattr(obtain, "_resolves", lambda host: False)
    built = build_config(_spec(tmp_path, overrides={"providers": {"primary": ProviderConfig(
        base_url="http://host.docker.internal:20128/v1", api_key_env="OMNIROUTE_API_KEY")}}))
    assert built.providers["primary"].base_url == "http://127.0.0.1:20128/v1"
    assert built.providers["primary"].api_key_env == "OMNIROUTE_API_KEY"


def test_only_the_docker_host_name_is_rewritten_to_loopback() -> None:
    """The host cannot resolve `host.docker.internal`; every other name, one
    merely containing it included, is left for the caller to dial."""
    import importlib

    obtain = importlib.import_module("nora.subject.obtain")
    assert obtain._from_host("http://host.docker.internal:8000") == "http://127.0.0.1:8000"
    assert obtain._from_host("http://host.docker.internal/v1") == "http://127.0.0.1/v1"
    assert (obtain._from_host("http://host.docker.internal.example.org:8000")
            == "http://host.docker.internal.example.org:8000")
    assert obtain._from_host("http://omniroute:20128/v1") == "http://omniroute:20128/v1"


def test_the_judge_endpoint_is_dialled_on_loopback_from_the_host(
    tmp_path, monkeypatch, example_config,
) -> None:
    """The measurer resolves its route outside the subject; it gets the same
    host rewrite, or a judged run on the host never reaches its model."""
    import importlib

    obtain = importlib.import_module("nora.subject.obtain")
    cfg = example_config.model_copy(update={"llm": example_config.llm.model_copy(
        update={"base_url": "http://host.docker.internal:20128/v1"})})
    monkeypatch.setattr(obtain, "_load_config", lambda path, require_secrets: cfg)
    monkeypatch.setattr(obtain, "_resolves", lambda host: False)
    url, _key, _model = obtain.model_endpoint(tmp_path / "config.toml", "tag")
    assert url == "http://127.0.0.1:20128/v1"


def test_dumped_spike_overrides_remain_typed_and_sandboxed(
    tmp_path, monkeypatch, example_config,
):
    import importlib

    from nora.config import CognitionConfig, LLMConfig, ModelRoute

    module = importlib.import_module("nora.subject.obtain")
    monkeypatch.setattr(module, "_resolves", lambda host: False)
    llm = {**example_config.llm.model_dump(), "model": "generated-host-model",
           "base_url": "http://host.docker.internal:20128/v1", "provider": "generated"}
    spec = _spec(tmp_path, overrides={
        "llm": llm,
        "model_routes": {"cognition": {"model": "generated-cognition-model",
                                       "provider": "generated"}},
        "providers": {"generated": {"base_url": "http://host.docker.internal:20128/v1",
                                    "api_key_env": "OMNIROUTE_API_KEY"}},
        "cognition": {**example_config.cognition.model_dump(), "enabled": True},
    })
    cfg = build_config(spec)
    assert isinstance(cfg.llm, LLMConfig)
    assert isinstance(cfg.cognition, CognitionConfig)
    assert isinstance(cfg.model_routes["cognition"], ModelRoute)
    assert cfg.endpoint("cognition")[2] == "generated-cognition-model"
    assert cfg.endpoint("cognition")[0] == "http://127.0.0.1:20128/v1"
    assert cfg.llm.model == "generated-host-model"
    assert cfg.llm.base_url == "http://127.0.0.1:20128/v1"
    assert cfg.cognition.enabled
    assert cfg.store.data_dir == tmp_path
    assert spec.database_url == "postgresql+asyncpg://x/x"
