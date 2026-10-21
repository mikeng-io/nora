"""The subject an eval composes IS the deployment's Nora, not a fresh install.

`nora/subject/obtain.py` must not build an eval subject from
`config.example.toml`, whose job is to be a SAFE TEMPLATE for a new deployment
— it ships with almost everything off, and differs from the deployment in
`llm.model`, `llm.reasoning_effort`, and `relevance_gate`, `participation`,
`standing`, `culture`, `notes`, `dreamer`, `search`, `online`, `weather`,
`route` and `preview` all DISABLED. Cases would measure a different model, at a
different reasoning effort, with most of Nora switched off.

**No case could catch that, and that is the point of pinning it here.** The
rule "nothing reaches the model that the deployment would not send" constrains
the PROMPT, and the prompt would be fine. What differs is the reader. These
tests hold the other half.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EVAL_CONFIG = _REPO_ROOT / "config.evaluation.toml"

#: The exemption list lives in `evals.subject_config.ALLOWED_TO_DIFFER`, beside
#: the check that reads it — NOT copied here. A second copy is two lists that
#: agree until one is edited, which is the failure this file exists to catch one
#: layer up.


def _flat(d: dict, prefix: str = ""):
    for key, value in d.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            yield from _flat(value, name)
        else:
            yield name, value


#: A DEPLOYMENT-SHAPED fixture, not the real `config.toml`.
#:
#: These guards must hold without reaching the deployment: the generated config
#: is per-run and gitignored, so a guard that read it could only SKIP when it
#: was absent — and a skipping guard is the vacuous pass this layer exists to
#: refuse. `build()` is pure, so the rules are checked against its OUTPUT on a
#: stated input instead.
#:
#: Every value here is one the deployment really sets,
#: with the room id and canary replaced: the fixture states the SHAPE, and the
#: repo stays clear of deployment values for the same reason `config.toml` is
#: untracked.
_DEPLOYMENT_FIXTURE = """\
[llm]
model = "nora"
reasoning_effort = "none"
[dreamer]
enabled = true
model = "nora-dreamer"
reasoning_effort = "low"
max_tokens = 6000
[evals]
judge_model = "nora-judge"
[doctrine]
canary = "A-REAL-CANARY-VALUE"
[relevance_gate]
enabled = true
shadow = false
[participation]
enabled = true
[standing]
enabled = true
fold_batch = 40
max_slot_hold_seconds = 150.0
reasoning_effort = "none"
[culture]
enabled = true
[notes]
enabled = true
[search]
enabled = true
[online]
enabled = true
[weather]
enabled = true
[route]
enabled = true
[preview]
enabled = true
[store]
prune_enabled = false
[speech.wake]
enabled = false
[media]
encryption_key_env = "MEDIA_ENCRYPTION_KEY"
vision_model = "a-vision-model"
vision_timeout_seconds = 120.0
vision_max_tokens = 16384
sticker_descriptions = true
[media.describe.audio]
enabled = true
model = "an-audio-model"
provider = "opencode-go"
[media.describe.image]
enabled = true
model = "a-describe-model"
[media.describe.video]
enabled = true
model = "a-video-model"
provider = "opencode-go"
max_bytes = 104857600
[image_gen.xai]
model = "an-image-model"
[platforms.signal]
enabled = true
[engagement.trigger]
enabled = false
[obs.tracing]
endpoint = "http://collector.internal:4317"
exporter = "otlp_http"
[analytics]
enabled = true
[social]
bridge_url = "http://bridge.internal:9000"
"""


def _build_generated() -> str:
    from evals.subject_config import build

    return build(
        (_REPO_ROOT / "config.example.toml").read_text(encoding="utf-8"),
        _DEPLOYMENT_FIXTURE,
    )


def _eval_config() -> dict:
    """What `build()` produces for a deployment shaped like the fixture."""
    return dict(_flat(tomllib.loads(_build_generated())))


def test_the_subject_composes_the_evaluation_config_not_the_example():
    """The one-line defect, pinned.

    `config.example.toml` is a template for a fresh deployment and is CORRECT
    to ship with features off. It is simply not what Nora runs as.
    """
    # `importlib`, because `from nora.subject import obtain` resolves to the
    # FUNCTION the package re-exports, not the module that holds `_TEMPLATE`.
    import importlib

    module = importlib.import_module("nora.subject.obtain")

    assert module._TEMPLATE == _EVAL_CONFIG, (
        f"the eval subject composes {module._TEMPLATE.name}. It must compose "
        "config.evaluation.toml — an example file is by definition not the "
        "deployment and drifts from it by design."
    )


def test_a_deployment_value_overrides_the_examples():
    """The defect itself: the example ships features off, the deployment runs
    them on, and the subject took the example's answer."""
    cfg = _eval_config()
    assert cfg["relevance_gate.enabled"] is True
    assert cfg["standing.enabled"] is True
    assert cfg["llm.reasoning_effort"] == "none"


def test_a_deployment_key_the_example_never_mentions_is_written_not_lost():
    """The class the first cut of the generator missed entirely.

    A key with no line in the example has nothing to rewrite, so it silently
    falls back to `nora/config.py`'s default — which is not the deployment's
    value. `store.prune_enabled` defaults to True in code, the deployment sets
    it False, and the example omits it: a sandbox would have pruned its own
    seeded rows mid-run.
    """
    from evals.subject_config import build
    # The public example now states this setting. Remove it explicitly so the
    # regression still measures a missing source key rather than template drift.
    example = (_REPO_ROOT / "config.example.toml").read_text(encoding="utf-8")
    example = "\n".join(line for line in example.splitlines()
                        if not line.startswith("prune_enabled ="))
    assert "store.prune_enabled" not in dict(_flat(tomllib.loads(example)))
    generated = dict(_flat(tomllib.loads(build(example, _DEPLOYMENT_FIXTURE))))
    assert generated["store.prune_enabled"] is False



def test_an_enabled_adapter_is_adopted_but_its_credentials_are_not():
    """Platform adapters stay ENABLED as the deployment has them.

    Forcing every `platforms.*.enabled` OFF would give
    `StartupError: no platform adapter is enabled`, and it is wrong anyway: the
    registry's `self_aliases()` feeds `self_ids` (`nora/app/composition.py`),
    and `self_ids` is what `MentionPolicy`/`ReplyToNoraPolicy` match on. With
    the deployment's text trigger off, mention-and-reply IS the engagement path,
    so a disabled adapter deletes it.

    So the adapter is ENABLED as the deployment has it, and the NETWORK is
    neutralised instead — `_baseline()` points the account at a placeholder.
    """
    from evals.subject_config import SKIP_PREFIX, adopted

    taken = adopted(_DEPLOYMENT_FIXTURE)
    assert taken["platforms.signal.enabled"] is True, (
        "the deployment runs the adapter; disabling it removes the live "
        "mention/reply engagement path"
    )
    assert "platforms.signal.account" in SKIP_PREFIX, (
        "the ACCOUNT is what would dial out, and `_baseline()` overrides it "
        "with a placeholder — adopting the real one would reach signal-cli"
    )
    assert _eval_config()["platforms.signal.enabled"] is True


def test_a_skipped_prefix_is_not_taken_from_the_deployment():
    """Endpoints that resolve only inside the deploy network, and sinks a run
    would write into beside real traffic.

    The fixture uses NON-BOOLEAN `obs.` keys, which only `SKIP_PREFIX` can
    exclude: a boolean `obs.` key would be forced False by `FORCE_OFF`
    independently, so removing `"obs."` from `SKIP_PREFIX` — the exact thing the
    test is named for — would still pass.
    """
    from evals.subject_config import adopted

    taken = adopted(_DEPLOYMENT_FIXTURE)
    for key in ("obs.tracing.endpoint", "obs.tracing.exporter",
                "social.bridge_url"):
        assert key not in taken, (
            f"{key} is a deploy-network endpoint; adopting it points the "
            "sandbox at the deployment's own infrastructure"
        )
    # And the generated config must not carry the deployment's value either.
    assert _eval_config().get("obs.tracing.endpoint") != "http://collector.internal:4317"


def test_the_generated_config_matches_the_deployment_or_is_refused():
    """`materialise()`'s own gate, falsified.

    A rewrite rule that stops applying must REFUSE the run, not emit a config
    that quietly disagrees with the deployment.
    """
    from evals.subject_config import divergences

    generated = _eval_config()
    assert not divergences(
        _build_generated(), _DEPLOYMENT_FIXTURE,
    ), "the fixture should generate cleanly"

    # Falsification: a deployment stating something the generator does not
    # carry must be REPORTED, or the gate is decoration.
    drifted = divergences(_build_generated(), _DEPLOYMENT_FIXTURE + '\n[music]\nenabled = true\n')
    assert "music.enabled" in drifted, (
        "a setting the deployment runs and the generated config does not must "
        "be named; a gate that cannot fail is not a gate"
    )
    assert generated["relevance_gate.enabled"] is True


@pytest.mark.parametrize("key,expected", [
    ("llm.model", "nora"),
    ("dreamer.model", "nora-dreamer"),
    ("evals.judge_model", "nora-judge"),
])
def test_the_models_are_the_deployments_aliases_not_a_raw_upstream_id(
    key: str, expected: str,
) -> None:
    """`config.example.toml` names raw upstream ids
    (`ollamacloud/deepseek-v4-flash:0731`). The deployment routes through
    OmniRoute ALIASES, and an alias is what carries its fallback chain — so a
    raw id is both a different model and a different failure behaviour.
    """
    assert _eval_config().get(key) == expected, (
        f"{key} must be the deployment's alias {expected!r}, not a raw "
        "upstream id: the alias is what carries the routing chain."
    )


def test_the_canary_is_the_deployments_own():
    """`doctrine.canary` is the string a leak test looks for. The example ships
    `CHANGE-ME-PER-DEPLOYMENT`, so a leak assertion built on it would hunt
    a placeholder that the deployed doctrine never contains.
    """
    canary = _eval_config().get("doctrine.canary", "")
    assert canary and "CHANGE-ME" not in canary, (
        f"doctrine.canary is {canary!r} — a placeholder. A leak probe looking "
        "for it cannot fire on the deployed doctrine's real canary."
    )


# --- no room swap: isolation is by database, the room is the real one --------


def test_the_room_swap_is_gone():
    """A case composes against a sandbox database of its own
    (`nora.subject.sandbox_database`) under the REAL room's id, with the
    deployment's `policy.toml` and `identity.toml` verbatim — so there is
    nothing to swap, and a helper that reappeared here would be a second copy of
    the room drifting from the first."""
    import evals.subject_config as subject_config

    reappeared = [
        name for name in ("sandbox_room_for", "policy_for", "identity_for", "_ROOM_FIELDS")
        if hasattr(subject_config, name)
    ]
    assert not reappeared, f"the room swap is back: {reappeared}"


def test_materialise_refuses_to_write_when_the_result_disagrees(tmp_path, monkeypatch):
    """`materialise()` is the one place that reads the real deployment and fails
    closed, so it is tested directly (not through `divergences()`).

    The trigger is a GENERATOR THAT STOPPED WORKING, not a config that
    disagrees: every setting in `FORCE_OFF` is also in `ALLOWED_TO_DIFFER`, by
    design, so no input file can make the two legitimately differ. What must
    be caught is a rewrite rule silently ceasing to apply — after which a run
    would compose a Nora nobody deployed.

    Falsified both ways: it must RAISE, and it must NOT write the target, or a
    refused run leaves a stale config for the next one to compose unchecked.
    """
    import evals.subject_config as sc
    from evals.subject_config import materialise

    example = tmp_path / "config.example.toml"
    example.write_text('[llm]\nmodel = "example-model"\n', encoding="utf-8")
    deployment = tmp_path / "config.toml"
    deployment.write_text('[llm]\nmodel = "deployed-model"\n', encoding="utf-8")
    target = tmp_path / "config.evaluation.toml"

    # The rewriter stops applying: the example comes back verbatim.
    monkeypatch.setattr(sc, "build", lambda example_toml, deployment_toml: example_toml)

    with pytest.raises(ValueError, match="differs from the deployment"):
        materialise(example=example, deployment=deployment, target=target)

    assert not target.exists(), (
        "a refused run must not leave a config behind; the next run would "
        "compose it with no check at all"
    )


def test_materialise_writes_when_the_result_agrees(tmp_path):
    """The positive control, so the test above cannot pass by always raising."""
    from evals.subject_config import materialise

    example = tmp_path / "config.example.toml"
    example.write_text('[llm]\nmodel = "example-model"\n', encoding="utf-8")
    deployment = tmp_path / "config.toml"
    deployment.write_text('[llm]\nmodel = "deployed-model"\n', encoding="utf-8")
    target = tmp_path / "config.evaluation.toml"

    materialise(example=example, deployment=deployment, target=target)

    assert target.exists()
    assert 'model = "deployed-model"' in target.read_text(encoding="utf-8")


def test_the_gate_looks_at_keys_the_deployment_never_states():
    """The half of the key space a deployment-keys-only gate is blind to.

    `divergences()` must not iterate only DEPLOYMENT-stated keys on the
    reasoning that a key the example carries and the deployment does not "is a
    code default for both". False: it is the EXAMPLE's value for the subject and
    `nora/config.py`'s default for production. Such keys agree only by
    curation, so one tidied line in the example, or one new default in
    `config.py`, would move production and leave the subject behind with the
    gate green.
    """
    from evals.subject_config import build, divergences

    example = (_REPO_ROOT / "config.example.toml").read_text(encoding="utf-8")
    # `participation.reasoning_effort` is example-stated, deployment-silent,
    # and its code default is "none".
    drifted = example.replace('reasoning_effort = "none"', 'reasoning_effort = "high"', 1)

    assert not divergences(build(example, _DEPLOYMENT_FIXTURE), _DEPLOYMENT_FIXTURE), (
        "the undrifted example must be clean, or this test proves nothing"
    )
    found = divergences(build(drifted, _DEPLOYMENT_FIXTURE), _DEPLOYMENT_FIXTURE)
    assert found, (
        "an example-only key drifting from nora/config.py's default is a "
        "subject running settings production does not — the gate must see it"
    )


def test_a_key_whose_default_cannot_be_resolved_is_not_silently_passed():
    """"Cannot check" is not "agrees".

    `_code_default` returns None for a key it cannot resolve. Treating that as
    agreement would reopen the hole for exactly the keys hardest to reason
    about.
    """
    from nora.subject import config_default

    assert config_default("no.such.setting.anywhere") is None
    assert config_default("dreamer.budgets.culture.turns") is None, (
        "a runtime-keyed container has no static default to compare against"
    )
    assert config_default("participation.reasoning_effort") == "none"



def test_a_deployment_with_compute_on_yields_a_subject_with_it_off():
    """Without the sub-agent no backend is registered, so a deployment's
    `[compute] enabled = true` would only make the subject log a boot warning
    and run a poller with nothing behind it. The deployment's `true` must come
    out of the rebuild as `false`, and the difference must be a declared one."""
    from evals.subject_config import ALLOWED_TO_DIFFER, adopted

    deployment = _DEPLOYMENT_FIXTURE + "\n[compute]\nenabled = true\n"
    assert adopted(deployment)["compute.enabled"] is False
    assert "compute.enabled" in ALLOWED_TO_DIFFER


_SUBAGENT_DEPLOYMENT = (
    _DEPLOYMENT_FIXTURE
    + "\n[compute]\nenabled = true\n[subagent]\nenabled = true\n"
    + "[dreamer.budgets.subagent]\nturns = 8\n"
)


def test_a_deployment_with_the_subagent_on_keeps_the_preconditions_it_needs():
    """`[subagent] enabled` refuses to load without `[dreamer]` and `[compute]`
    on, so FORCE_OFF writing `compute.enabled = false` over it would leave a
    generated config that cannot be loaded."""
    from evals.subject_config import adopted

    taken = adopted(_SUBAGENT_DEPLOYMENT)
    assert taken["subagent.enabled"] is True
    assert taken["compute.enabled"] is True
    assert taken["dreamer.enabled"] is True


def test_the_generated_subagent_config_loads_under_the_production_validators(tmp_path):
    from evals.subject_config import build
    from nora.config import load

    generated = build(
        (_REPO_ROOT / "config.example.toml").read_text(encoding="utf-8"),
        _SUBAGENT_DEPLOYMENT,
    )
    target = tmp_path / "config.toml"
    target.write_text(generated, encoding="utf-8")
    cfg = load(target, require_secrets=False)
    assert cfg.subagent.enabled and cfg.compute.enabled and cfg.dreamer.enabled


def test_without_the_subagent_the_generated_config_is_unchanged_by_it():
    """Byte-identical to the build that has no sub-agent code path at all."""
    from evals.subject_config import FORCE_OFF, adopted, forced_off

    assert forced_off({}) == FORCE_OFF
    assert forced_off({"subagent.enabled": False}) == FORCE_OFF
    deployment = _DEPLOYMENT_FIXTURE + "\n[compute]\nenabled = true\n"
    taken = adopted(deployment)
    assert taken["compute.enabled"] is False
    assert all(taken[k] is False for k in FORCE_OFF)


def test_eval_generator_accepts_literal_private_inputs_without_template_refs(tmp_path):
    from evals.subject_config import build
    from nora.config import load
    example = (_REPO_ROOT / "config.example.toml").read_text()
    generated = build(example, '''
[agent.handles]
signal = "@synthetic"
[platforms.signal]
account = "+12025550123"
[doctrine]
canary = "SYNTHETIC-CANARY"
[tools.mcp.pulse]
base_url = "https://example.invalid/pulse/mcp"
''')
    target = tmp_path / "config.toml"
    target.write_text(generated)
    cfg = load(target, require_secrets=False, env={})
    assert cfg.agent.handles == {"signal": "@synthetic"}
    assert cfg.platforms.signal.account == ""  # sandbox replaces this before any startup lookup
    assert cfg.doctrine.canary == "SYNTHETIC-CANARY"
    assert cfg.tools.mcp["pulse"].base_url == "https://example.invalid/pulse/mcp"
    assert cfg.documents.public_url == ""
