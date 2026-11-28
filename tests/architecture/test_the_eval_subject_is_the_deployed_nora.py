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
identity_file = "identity.toml"
policy_file = "policy.toml"
[llm]
model = "nora"
base_url = "http://llm.generated:11434/v1"
reasoning_effort = "none"
timeout_seconds = 360
temperature = 0.7
max_tokens = 2000
[llm.limits]
turns = 20
total_tokens = 1024000
output_tokens = 12000
[engagement]
policy = "trigger_text"
[engagement.trigger]
enabled = false
text = "nora"
min_interval_seconds = 5
[dreamer]
model = "nora-dreamer"
reasoning_effort = "low"
max_tokens = 6000
[evals]
judge_model = "nora-judge"
[doctrine]
dir = "nora/doctrine"
language_hint = "yue-Hant-HK"
reload = "mtime"
strict = true
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
data_dir = "/generated/data"
max_per_conversation = 100
context_window = 50
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
mode = "bridge"
account = "generated-signal-account"
base_url = "http://signal-bridge.generated:18090"
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
    import ast
    import importlib

    # Ordinary pytest redirects the template to worker-owned generated input.
    # Read the live CLI's default from source, unaffected by that fixture.
    module = importlib.import_module("nora.subject.obtain")
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    templates = [
        node.value for node in tree.body if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_TEMPLATE" for t in node.targets)
    ]
    expected = ast.parse('_REPO_ROOT / "config.evaluation.toml"', mode="eval").body
    assert len(templates) == 1 and ast.dump(templates[0]) == ast.dump(expected), (
        "the eval subject must compose "
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
    assert "store.prune_enabled" not in dict(_flat(
        tomllib.loads((_REPO_ROOT / "config.example.toml").read_text(encoding="utf-8"))
    )), "the example now states it; this test needs a different absent key"
    assert _eval_config()["store.prune_enabled"] is False


async def test_an_enabled_adapter_stays_enabled_and_its_endpoints_bind_to_the_case(tmp_path):
    """Platform adapters stay ENABLED as the deployment has them, and declared
    identity values (the account) are kept — what would dial out is the
    ENDPOINT, which the case-owned environment rebinds at composition.

    Forcing every `platforms.*.enabled` OFF would give
    `StartupError: no platform adapter is enabled`, and it is wrong anyway: the
    registry's `self_aliases()` feeds `self_ids` (`nora/app/composition.py`),
    and `self_ids` is what `MentionPolicy`/`ReplyToNoraPolicy` match on. With
    the deployment's text trigger off, mention-and-reply IS the engagement path,
    so a disabled adapter deletes it.
    """
    from aiohttp import web

    from evals.subject_config import build
    from nora.config import load
    from nora.subject.environment import IsolatedServices

    deployment = _DEPLOYMENT_FIXTURE.replace(
        'account = "generated-signal-account"', 'account = "generated-signal-account-2"')
    generated = build(
        (_REPO_ROOT / "config.example.toml").read_text(encoding="utf-8"), deployment)
    target = tmp_path / "config.toml"
    target.write_text(generated, encoding="utf-8")
    cfg = load(target, require_secrets=False)
    assert cfg.platforms.signal.enabled is True
    assert cfg.platforms.signal.account == "generated-signal-account-2", (
        "the account is declared deployment identity — parity keeps it; "
        "it is not a dial path, the adapter's base_url is"
    )

    def at(root: object, path: str) -> object:
        node = root
        for part in path.split("."):
            node = node[part] if isinstance(node, dict) else getattr(node, part)
        return node

    applications = {
        key: web.Application()
        for key in ("llm.base_url", "platforms.signal.base_url", "social.bridge_url")
    }
    for name in cfg.providers:
        applications[f"providers.{name}.base_url"] = web.Application()
    async with IsolatedServices(
        database_url="sqlite:///generated", applications=applications,
    ) as environment:
        bound = environment.bind_config(cfg)
        for key in ("llm.base_url", "platforms.signal.base_url", "social.bridge_url"):
            assert environment.owns(at(bound, key)), (
                f"{key} must reach the case-owned listener, not the declared "
                "deploy-network address"
            )
        assert not environment.owns("http://signal-bridge:18090"), (
            "the declared deploy-network address is refused outside the case's "
            "own services"
        )
        assert bound.platforms.signal.enabled is True
        assert bound.platforms.signal.account == "generated-signal-account-2"


def test_a_skipped_prefix_is_not_taken_from_the_deployment():
    """Measurement sinks (`obs.`, `evals.langfuse.`, `store.data_dir`) are not
    adopted — a run writes its own private sinks, never beside real traffic.

    A declared deploy-network ENDPOINT is different: it is functional config,
    adopted verbatim, and `bind_config` is what keeps it undialed (proved in
    the binding test above). The fixture uses NON-BOOLEAN `obs.` keys, which
    only `SKIP_PREFIX` can exclude: a boolean `obs.` key would be forced False
    by `FORCE_OFF` independently, so removing `"obs."` from `SKIP_PREFIX` —
    the exact thing the test is named for — would still pass.
    """
    from evals.subject_config import adopted

    taken = adopted(_DEPLOYMENT_FIXTURE)
    for key in ("obs.tracing.endpoint", "obs.tracing.exporter"):
        assert key not in taken, (
            f"{key} is a measurement sink; adopting it writes a run beside "
            "the deployment's real telemetry"
        )
    assert taken["social.bridge_url"] == "http://bridge.internal:9000", (
        "a declared endpoint is deployment config: parity keeps it, and the "
        "environment rebinds it to the case-owned service at composition"
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

    An example-stated, deployment-silent key is OMITTED, so the subject
    loads the same schema default production resolves — a tidied or
    drifted EXAMPLE line cannot move it — and `divergences()` still
    reports a generated config carrying an undeclared value, which is how
    a rewrite that silently stops applying is caught.
    """
    from evals.subject_config import build, divergences

    example = (_REPO_ROOT / "config.example.toml").read_text(encoding="utf-8")
    # `reasoning_effort` is example-stated in several sections; drift the first
    # — the generated config must not carry it at all.
    drifted = example.replace('reasoning_effort = "none"', 'reasoning_effort = "high"', 1)
    generated = build(drifted, _DEPLOYMENT_FIXTURE)
    assert "participation.reasoning_effort" not in dict(
        _flat(tomllib.loads(generated))), (
        "a deployment-silent key is omitted: the subject takes the schema "
        "default production resolves, never the example's line"
    )
    assert not divergences(generated, _DEPLOYMENT_FIXTURE), (
        "an example that drifts in an undeclared key is a clean build, not drift"
    )
    # The falsification: a generated config that DOES carry a wrong undeclared
    # value — the rewrite having silently stopped — must be named.
    tampered = _build_generated() + '\n[presence]\ncadence_moving_seconds = 5\n'
    assert "presence.cadence_moving_seconds" in divergences(tampered, _DEPLOYMENT_FIXTURE), (
        "an undeclared key disagreeing with nora/config.py's default is a "
        "subject running settings production does not — the gate must see it"
    )


def test_a_key_whose_default_cannot_be_resolved_is_not_silently_passed():
    """"Cannot check" is not "agrees" — and is not the value `None`.

    `config_default` returns `UNRESOLVED` for a key it cannot resolve,
    distinct from a real nullable default. Treating either as agreement
    would reopen the hole for exactly the keys hardest to reason about.
    """
    from nora.subject import UNRESOLVED, config_default

    assert config_default("no.such.setting.anywhere") is UNRESOLVED
    assert config_default("dreamer.budgets.culture.turns") is UNRESOLVED, (
        "a runtime-keyed container has no static default to compare against"
    )
    assert config_default("participation.reasoning_effort") == "none"
    assert config_default("store.prompt_window") is None, (
        "a nullable default is a resolved answer, not an unresolvable path"
    )


def test_an_unresolvable_generated_key_is_a_refusal_not_an_agreement():
    """A runtime-keyed member the deployment never stated cannot be checked
    against production, so `divergences()` names it `UNRESOLVED` — the run
    refuses rather than composing a config whose parity is unknown."""
    from evals.subject_config import divergences
    from nora.subject import UNRESOLVED

    tampered = _build_generated() + '\n[dreamer.budgets.culture]\nturns = 9\n'
    drift = divergences(tampered, _DEPLOYMENT_FIXTURE)
    assert drift.get("dreamer.budgets.culture.turns") == (9, UNRESOLVED)


def test_an_omitted_optional_key_keeps_the_deployment_effective_value():
    """The `store.prompt_window = 20` defect, closed by omission.

    The deployment omits `prompt_window`; the example ships `20` and a
    first cut of the rewrite materialised `7` — both wrong under the real
    semantics, where `None` derives from `context_window`. Omitting the
    key leaves `None`, and `effective_prompt_window` lands on the same
    number the deployment computes — checked on a real `StoreConfig` load.
    """
    from evals.subject_config import build
    from nora.config import StoreConfig

    example = (_REPO_ROOT / "config.example.toml").read_text(encoding="utf-8")
    generated = build(example, _DEPLOYMENT_FIXTURE)
    generated_store = tomllib.loads(generated)["store"]
    assert "prompt_window" not in generated_store
    ctx = {"base": _REPO_ROOT}
    subject = StoreConfig.model_validate(generated_store, context=ctx)
    deployment = StoreConfig.model_validate(
        tomllib.loads(_DEPLOYMENT_FIXTURE)["store"], context=ctx)
    assert subject.effective_prompt_window == deployment.effective_prompt_window
    # An explicit value stays explicit: parity is the deployment's, not
    # the schema's, when the deployment states one.
    stated = _DEPLOYMENT_FIXTURE.replace(
        "prune_enabled = false", "prompt_window = 7\nprune_enabled = false")
    stated_store = tomllib.loads(build(example, stated))["store"]
    assert StoreConfig.model_validate(
        stated_store, context=ctx).prompt_window == 7



def test_a_deployment_with_compute_on_keeps_the_subjects_compute_on():
    """`[compute] enabled` is functional configuration, not telemetry: the
    subject IS the deployment's Nora, so a declared `true` is kept verbatim.
    What the computer can REACH is bounded by the case's services, not by
    rewriting the deployment's declaration — `compute.enabled` is not and
    must not be a silent `FORCE_OFF` rewrite."""
    from evals.subject_config import ALLOWED_TO_DIFFER, adopted, build

    deployment = _DEPLOYMENT_FIXTURE + "\n[compute]\nenabled = true\n"
    assert adopted(deployment)["compute.enabled"] is True
    assert "compute.enabled" not in ALLOWED_TO_DIFFER, (
        "a preserved declared value is parity, not a difference to excuse"
    )
    generated = build(
        (_REPO_ROOT / "config.example.toml").read_text(encoding="utf-8"), deployment)
    assert dict(_flat(tomllib.loads(generated)))["compute.enabled"] is True


_SUBAGENT_DEPLOYMENT = (
    _DEPLOYMENT_FIXTURE
    + "\n[compute]\nenabled = true\n[subagent]\nenabled = true\n"
    + "[dreamer.budgets.subagent]\nturns = 8\n"
)


def test_a_deployment_with_the_subagent_on_keeps_the_preconditions_it_needs():
    """`[subagent] enabled` refuses to load without `[compute]`
    on, so FORCE_OFF writing `compute.enabled = false` over it would leave a
    generated config that cannot be loaded."""
    from evals.subject_config import adopted

    taken = adopted(_SUBAGENT_DEPLOYMENT)
    assert taken["subagent.enabled"] is True
    assert taken["compute.enabled"] is True


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
    assert cfg.subagent.enabled and cfg.compute.enabled


def test_the_subagent_rewrite_changes_nothing_when_the_subagent_is_off():
    """`forced_off` is FORCE_OFF verbatim without `[subagent] enabled`, and a
    declared `compute.enabled` — the field the old rewrite used to force —
    passes through as the deployment stated it."""
    from evals.subject_config import FORCE_OFF, adopted, forced_off

    assert forced_off({}) == FORCE_OFF
    assert forced_off({"subagent.enabled": False}) == FORCE_OFF
    deployment = _DEPLOYMENT_FIXTURE + "\n[compute]\nenabled = true\n"
    taken = adopted(deployment)
    assert taken["compute.enabled"] is True
    assert all(taken[k] is False for k in FORCE_OFF)
