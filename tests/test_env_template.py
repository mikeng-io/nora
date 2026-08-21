"""The templates are contracts: config.py's parser is written against config.example.toml."""

import re
import shutil
import tomllib
from pathlib import Path

from nora.config import LLMConfig, load

ROOT = Path(__file__).resolve().parents[1]

# Matches `api_key` used as a bare key (any amount of whitespace, or none, before `=`),
# e.g. `api_key = "sk-..."` or the whitespace-hole `api_key="sk-..."`. Deliberately does
# NOT match `api_key_env`, which is the only form allowed in the committed template.
_BARE_API_KEY_RE = re.compile(r"\bapi_key\s*=")


def test_config_example_exists_and_parses():
    data = tomllib.loads((ROOT / "config.example.toml").read_text())
    assert set(data) == {
        "agent",
        "platforms", "llm", "memory", "store", "media", "route", "presence",
        "analytics", "shortener", "compute",
        "search", "weather", "preview", "heartbeat", "reminders", "language",
        "facts",
        "version", "engagement", "doctrine", "dreamer", "identity_file", "policy_file",
        "obs", "evals", "tools",
    }


# --- Drift between the template and the real deployment (A8 / audit F11) -----
#
# `config.toml` is the REAL deployment's config and is tracked in git (it holds
# no secret values — every credential is a `*_env` name), so this drift is
# checkable statically, without reaching into the running container.
#
# The failure this guards is not cosmetic. `config.example.toml` is what anyone
# reasoning about Nora from the repo reads, and when it disagreed with the
# deployment it gave the WRONG ANSWER about her sampling — the 2026-08-20
# prompt-architecture audit drew conclusions from `opencode-zen/…`,
# `reasoning_effort = "low"` and `timeout_seconds = 30` while the deployment had
# been running `ollamacloud/…`, `"high"` and `120` for over a week.


def _flatten(table: object, prefix: str = "") -> dict[str, object]:
    """`{"llm": {"model": "x"}}` -> `{"llm.model": "x"}`, leaves only."""
    flat: dict[str, object] = {}
    assert isinstance(table, dict)
    for key, value in table.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flat.update(_flatten(value, path))
        else:
            flat[path] = value
    return flat


def _load_toml(name: str) -> dict[str, object]:
    path = ROOT / name
    # Fail, never skip: a missing config.toml means this gate silently stopped
    # measuring, which is the class of bug the whole file exists to catch.
    assert path.is_file(), f"{name} is tracked in git and must exist"
    return _flatten(tomllib.loads(path.read_text()))


# Keys whose VALUES legitimately differ, with the reason. Everything else must
# agree. Deliberately an allowlist of DIFFERENCES rather than of things to
# check: a NEW key added to config.toml must either match the template or be
# argued for here, which is the fail-closed direction.
_MAY_DIFFER = {
    # Hosts, ports, accounts and paths — the deployment's own topology.
    "llm.base_url", "memory.base_url", "platforms.signal.base_url",
    "platforms.signal.account", "store.data_dir", "obs.logging.path",
    "obs.tracing.endpoint", "obs.tracing.exporter", "obs.tracing.headers_env",
    "evals.langfuse.host", "evals.langfuse.headers_env",
    "presence.refresh_url", "shortener.base_url",
    # Env-var NAMES are the operator's choice of what to call a secret.
    "media.encryption_key_env",
    # The template ships every optional feature INERT — reaching a service,
    # holding a key, or speaking on a timer is an operator decision, never
    # something a copied file makes. The live deployment has opted in.
    "analytics.enabled", "compute.enabled", "evals.langfuse.enabled",
    "heartbeat.enabled", "language.enabled", "language.verify_known_entities",
    "platforms.discord.enabled", "presence.enabled", "preview.enabled",
    "route.enabled", "search.enabled", "shortener.enabled",
    "tools.mcp.pulse.enabled", "weather.enabled",
    # Per-deployment policy/cost knobs.
    "doctrine.canary",            # deliberately CHANGE-ME in the template
    "presence.retention_days",    # disk and privacy, the operator's call
    "evals.journey_samples", "evals.judge_samples",  # pre-deploy spend
}


# The ONE key the deployment sets that the template must NOT declare.
# `tests/test_main.py::test_the_retention_sweep_is_spawned_when_the_flag_is_on`
# loads config.example.toml to pin the CODE default — absent means the sweep
# still spawns — so writing the key into the template would silently delete
# that assertion. The template argues the case for `false` in prose instead.
_MAY_BE_ABSENT = {"store.prune_enabled"}


def test_config_example_declares_every_key_the_deployment_sets():
    """A key set in config.toml but absent from the template is invisible.

    This is the half that caught `[shortener]` and `[compute]` missing from the
    template entirely, along with `memory.self_card_conclusions`,
    `media.sticker_descriptions` and the adaptive-cadence presence keys — an
    operator copying the template got the code default with no hint the knob
    existed.

    The reverse is NOT an error: the template may state a value the deployment
    leaves at its default, which is how it documents defaults.
    """
    live = _load_toml("config.toml")
    example = _load_toml("config.example.toml")
    missing = sorted(set(live) - set(example) - _MAY_BE_ABSENT)
    assert not missing, (
        "config.example.toml does not declare keys the deployment sets: "
        f"{missing}. Add them to the template (with the reasoning), or the "
        "next reader will not know they exist. If a key's ABSENCE from the "
        "template is load-bearing for another test, add it to _MAY_BE_ABSENT "
        "with that reason."
    )


def test_config_example_agrees_with_the_deployment_except_where_argued():
    """Values must match unless `_MAY_DIFFER` says why they legitimately don't.

    Catches exactly the F11 drift: `llm.model`, `llm.reasoning_effort` and
    `llm.timeout_seconds` are not site-specific, they are the current
    known-good configuration, and a template disagreeing with them misleads
    every future investigation.
    """
    live = _load_toml("config.toml")
    example = _load_toml("config.example.toml")
    drifted = {
        key: (example[key], live[key])
        for key in sorted(set(live) & set(example))
        if key not in _MAY_DIFFER and example[key] != live[key]
    }
    assert not drifted, (
        "config.example.toml disagrees with the deployment (example, live): "
        f"{drifted}. Either sync the template — keeping the superseded "
        "measurement comment and marking it superseded — or add the key to "
        "_MAY_DIFFER with the reason it is site-specific."
    )


def test_config_example_still_loads_through_the_config_model(tmp_path, monkeypatch):
    """Key-level parity is not enough: `extra="forbid"` must still accept it.

    A key present in both files and spelled wrong in both would pass the two
    tests above and fail at startup. This is what makes the parity checks a
    contract against `nora/config.py` rather than a diff of two text files.
    """
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    dst = tmp_path / "config.toml"
    shutil.copy(ROOT / "config.example.toml", dst)
    cfg = load(dst)
    # Spot-check the drifted values actually landed in the typed model, rather
    # than trusting that "it loaded" means "it loaded what we think".
    assert cfg.llm.model == "ollamacloud/deepseek-v4-flash"
    assert cfg.llm.reasoning_effort == "high"
    assert cfg.llm.timeout_seconds == 120


def test_both_config_files_agree_on_the_dreamer_runtime_shape(tmp_path, monkeypatch):
    """Key-level parity above compares two TOML documents; this compares the two
    TYPED sections they produce, which is the level `[dreamer.budgets.*]`
    actually has to agree on.

    A budget block is a nested table keyed by adaptor NAME, so the two files can
    drift in a way `_flatten` reports as a plain missing key without saying what
    it means: an adaptor budgeted in one file and not the other silently runs
    under a different ceiling in the deployment than the template documents.
    Comparing the model-level maps names the adaptor instead.

    Also the bound: EVERY declared budget, in either file, must be a real
    ceiling on all four axes. `timeout_seconds` above all — a hung Dreamer call
    is quiet (`dream_loop` marks the scope `running` and skips it every
    subsequent tick without ever raising, so the exponential backoff never
    engages), so an unbounded one is a subsystem that stops working with no
    error anywhere.
    """
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    loaded = {}
    for name in ("config.example.toml", "config.toml"):
        dst = tmp_path / name.replace(".example", "") / "config.toml"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, dst)
        loaded[name] = load(dst).dreamer

    example, live = loaded["config.example.toml"], loaded["config.toml"]
    assert example.enabled == live.enabled is False, (
        "the Dreamer runtime ships OFF in both files"
    )
    assert example.model == live.model == "", (
        "an empty model means [language].model — the open D2 gate fills this in"
    )
    assert set(example.budgets) == set(live.budgets), (
        "the two files budget different adaptors: "
        f"{sorted(set(example.budgets) ^ set(live.budgets))}"
    )
    for adaptor in example.budgets:
        assert example.budgets[adaptor] == live.budgets[adaptor], adaptor
    for section in (example, live):
        for adaptor, budget in section.budgets.items():
            assert budget.turns > 0, adaptor
            assert budget.total_tokens > 0, adaptor
            assert budget.output_tokens > 0, adaptor
            assert budget.timeout_seconds > 0, adaptor


def test_config_example_sets_no_top_p():
    """`top_p` stays UNSET until somebody measures it (audit F8 / A6).

    `nora/agent/factory.py` sends `max_tokens`, `temperature`, `tool_choice`
    and `reasoning_effort` and nothing else, so nucleus truncation is the
    provider's default. `top_p = 1.0` would be a no-op; anything lower
    truncates the distribution on a guess, and the `reasoning_effort` series in
    `[llm]` shows sampling changes move whether she calls `send_reply` at all.
    Deleting this test is the deliberate act that admits a measured value.
    """
    # Key-level, not text-level: both files DISCUSS `top_p` at length in their
    # comments, and that prose is the point of A6 — what must not appear is a
    # SET key, in either file or in the model that would accept one.
    assert "top_p" not in LLMConfig.model_fields
    for name in ("config.example.toml", "config.toml"):
        assert "llm.top_p" not in _load_toml(name), f"{name} sets top_p"


def test_config_example_has_no_secret_values():
    text = (ROOT / "config.example.toml").read_text()
    assert "api_key_env" in text
    # Only the env var NAME (api_key_env) may ever appear in the committed config — a
    # real key value (`api_key = "..."` or the no-space `api_key="..."`, both valid
    # TOML) must never appear on any line.
    for line in text.splitlines():
        assert not _BARE_API_KEY_RE.search(line), f"secret-shaped key found: {line!r}"


def test_env_example_names_the_secret():
    assert "OMNIROUTE_API_KEY" in (ROOT / ".env.example").read_text()
