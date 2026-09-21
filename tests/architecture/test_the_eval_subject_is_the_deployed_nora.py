"""The subject an eval composes IS the deployment's Nora, not a fresh install.

Until 2026-09-21 `nora/subject/obtain.py` built every eval subject from
`config.example.toml`, whose job is to be a SAFE TEMPLATE for a new deployment
— so it ships with almost everything off. 65 values differed from the
deployment, including `llm.model`, `llm.reasoning_effort`, and
`relevance_gate`, `participation`, `standing`, `culture`, `notes`, `dreamer`,
`search`, `online`, `weather`, `route` and `preview` all DISABLED. Every case
ever run measured a different model, at a different reasoning effort, with most
of Nora switched off.

**No case could have caught it, and that is the point of pinning it here.**
`design/evaluation/01-boundaries.md` §5's rule 4 — "nothing reaches the model
that the deployment would not send" — constrains the PROMPT, and the prompt was
fine. What differed was the reader. Rule 5 and these tests are what hold the
other half.
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
#: Every value here is one the deployment really sets (verified 2026-09-21),
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
[culture]
enabled = true
[notes]
enabled = true
candidates_enabled = true
curator_enabled = false
discharge_enabled = false
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
[media.describe.audio]
enabled = false
[platforms.signal]
enabled = true
[engagement.trigger]
enabled = false
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
    assert "store.prune_enabled" not in dict(_flat(
        tomllib.loads((_REPO_ROOT / "config.example.toml").read_text(encoding="utf-8"))
    )), "the example now states it; this test needs a different absent key"
    assert _eval_config()["store.prune_enabled"] is False


def test_an_enabled_adapter_is_adopted_but_its_credentials_are_not():
    """Corrected 2026-09-21 after measuring, not reasoning.

    An earlier cut forced every `platforms.*.enabled` OFF, on the argument
    that an eval drives entry points directly and never listens. That produced
    `StartupError: no platform adapter is enabled` in 22 integration tests —
    and the argument was wrong anyway: the registry's `self_aliases()` feeds
    `self_ids` (`nora/app/composition.py:2428`), and `self_ids` is what
    `MentionPolicy`/`ReplyToNoraPolicy` match on. With the deployment's text
    trigger off since 2026-09-01, mention-and-reply IS the engagement path, so
    a disabled adapter deletes it.

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
    would write into beside real traffic."""
    from evals.subject_config import adopted

    taken = adopted(_DEPLOYMENT_FIXTURE)
    assert not [k for k in taken if k.startswith("obs.") and taken[k] is not False]


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
    `CHANGE-ME-PER-DEPLOYMENT`, so every leak assertion in the suite was
    hunting a placeholder that the deployed doctrine never contains.
    """
    canary = _eval_config().get("doctrine.canary", "")
    assert canary and "CHANGE-ME" not in canary, (
        f"doctrine.canary is {canary!r} — a placeholder. A leak probe looking "
        "for it cannot fire on the deployed doctrine's real canary."
    )
