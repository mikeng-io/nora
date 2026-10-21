"""The templates are contracts: config.py's parser is written against config.example.toml."""

import re
import shutil
import tomllib
from pathlib import Path

import pytest

from nora.config import Config, LLMConfig, load, validate_schema

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
        "analytics", "memory_index", "compute", "computer", "otp",
        "search", "online", "weather", "preview", "heartbeat", "reminders",
        "speech", "social",
        "notes", "culture", "persona", "lessons", "standing", "music", "image_gen", "video_gen",
        "engagement", "participation", "proactive", "relevance_gate", "reaction",
        "doctrine", "dreamer", "cognition", "subagent", "assess", "lineage", "record_query",
        "documents", "external_files", "google",
        "identity_file", "policy_file",
        "obs", "evals", "tools",
    }


# --- Drift between the template and the real deployment ---------------------
#
# `config.toml` is the REAL deployment's config. It holds no secret values (every
# credential is a `*_env` name), so drift would be checkable statically, without reaching
# into the running container, WHEN the file is present. It is gitignored (collision rather
# than secrecy; see `_load_live_or_skip` below), so every check in this section runs
# against a real `config.toml` only when one happens to be present in the checkout, which
# is never true in a fresh clone or in CI.
#
# The failure this guards is not cosmetic. `config.example.toml` is what anyone reasoning
# about Nora from the repo reads, and when it disagrees with the deployment it gives the
# WRONG ANSWER about her model, sampling and timeouts.
#
# --- Relationship to evals/subject_config.py ----------------------------------
#
# `evals/subject_config.materialise()`/`divergences()` compare the GENERATED
# eval-subject config (`build()`'s output, `config.example.toml` rewritten
# with the deployment's own values) against the deployment, and REFUSE to
# start an eval run on disagreement — a stronger mechanism in the sense that a
# skip cannot happen, because the run does not start. It does NOT make the
# three checks below redundant, because it answers a different question with
# a different allowlist:
#
#   - `materialise()` protects the COMPUTED eval subject config, not the
#     checked-in `config.example.toml` text. `build()` self-heals around a
#     template that omits or misstates a deployment-set key — it rewrites (or
#     inserts) the deployment's own value into the generated file before
#     `divergences()` ever compares anything — so a stale or incomplete
#     template value that `test_config_example_agrees_with_the_deployment_
#     except_where_argued` or `test_config_example_declares_every_key_the_
#     deployment_sets` would catch produces no refusal at all: the generated
#     eval config is correct regardless of what the committed template says.
#     `divergences()`'s only two real signals are (1) `build()`'s line-rewrite
#     failing structurally, and (2) an EXAMPLE-only key (the deployment never
#     sets it) drifting from `nora/config.py`'s code default — the opposite
#     direction from either check below.
#   - The allowlists do not mean the same thing. `_MAY_DIFFER` below encodes
#     the operator's ON/OFF posture decisions (DEC-43: a fresh template ships
#     inert, the live deployment has opted features on) — `subject_config.py`'s
#     `SKIP_PREFIX` (read by both `adopted()` and `divergences()`'s `exempt()`)
#     and `ALLOWED_TO_DIFFER` (read only by `divergences()`'s `exempt()`)
#     encode the opposite: which settings must NOT follow the deployment into
#     an eval sandbox (credentials, internal endpoints, telemetry sinks). A key
#     like `standing.enabled` is *in* `_MAY_DIFFER` (template off, deployment
#     on, by design) and *taken straight from the deployment* by `adopted()` —
#     the eval subject runs with it on, which is correct for measuring the
#     real Nora and has nothing to say about whether the template's own text
#     is accurate.
#
# So `test_config_example_declares_every_key_the_deployment_sets`,
# `test_config_example_agrees_with_the_deployment_except_where_argued` and
# `test_both_config_files_agree_on_the_dreamer_runtime_shape` stay — each
# checks a property of the CHECKED-IN template that nothing else checks, ever,
# and the honest, named gap is that none of them run under the current
# workflow: `config.toml` is gitignored everywhere a worktree is created, and
# the deploy checkout is off-limits for verification (never bind-mounted for
# test runs). They remain here, SKIP not silently green, for the operator to
# run by hand against a real `config.toml` — a fixture would compare the
# template to itself, which is the exact "green forever, measuring nothing"
# failure `_load_live_or_skip`'s docstring already refuses.


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
    # `config.example.toml` is TRACKED and its absence is a real failure — it
    # is the only committed record of the config's shape, so a gate that
    # skipped on it would be measuring nothing while looking green.
    assert path.is_file(), f"{name} is tracked in git and must exist"
    return _flatten(tomllib.loads(path.read_text()))


def _load_live_or_skip() -> dict[str, object]:
    """`config.toml`, or a SKIP when this checkout has none.

    The file is the deployment's own and a fresh clone legitimately has none, so a hard
    assert would fail for everyone who is not the operator, and a gate that is red on
    every machine gets deleted, which loses the check entirely.

    A SKIP rather than a silent pass: pytest prints it, so «this ran and agreed» and
    «there was nothing here to compare against» stay different readings. It does NOT run
    somewhere else instead: `config.toml` is gitignored in every dev worktree and in CI,
    and the deploy checkout is off-limits for verification (never bind-mounted for a test
    run), so this check is not proven to run anywhere under the current workflow. It is
    here, SKIP not silently green, for the operator to run by hand against a real
    `config.toml` when they need it.
    """
    path = ROOT / "config.toml"
    if not path.is_file():
        pytest.skip(
            "no config.toml in this checkout — untracked since 2026-09-07, so "
            "the live-vs-example cross-check has nothing to compare against "
            "here. It does not run anywhere in the current workflow either "
            "(#321): config.toml is gitignored in every worktree and CI, and "
            "the deploy checkout is off-limits for verification."
        )
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
    "presence.refresh_url",
    # image_gen.enabled is the LIVE operator decision; the template stays the ships-OFF
    # default (DEC-43 posture) so a fresh deployment is inert by default.
    "image_gen.enabled",
    # The LIVE operator's ON/OFF decisions: the cognition loop, obs error reporting,
    # online/participation/relevance-gate/whatsapp/tts are all enabled in the deployment
    # while the template ships every one OFF (DEC-43 posture: a copied config must never
    # start speaking, reporting or participating on its own). Same class as
    # image_gen.enabled above.
    "cognition.enabled", "obs.errors.enabled",
    "obs.errors.environment", "online.enabled", "participation.enabled",
    "platforms.whatsapp.enabled", "relevance_gate.enabled",
    "relevance_gate.shadow", "speech.tts.enabled",
    # Voice tuning is the LIVE operator's ear; the template ships vendor defaults so a
    # copied config sounds neutral, the same DEC-43 posture as speech.tts.enabled above.
    "speech.tts.speed", "speech.tts.pitch", "speech.tts.vol",
    "speech.tts.emotion",
    # The voice sidecar's own topology and the operator's vendor choice. `provider` names
    # a vendor account, `sidecar_endpoint` is a compose service on this host, and
    # `voice_guild_ids` are Discord snowflakes for THIS deployment's servers: a copied
    # config has none of the three, and the template shipping them would be an identifier
    # in the tree (`tests/architecture/test_no_real_identifiers_in_the_tree.py`).
    # `sidecars` is a LIST, so it flattens to ONE leaf however many guilds are deployed,
    # which is why it is a list and not a table keyed by guild id (a keyed table would
    # need a real snowflake declared in the example).
    #
    # Keep this list free of dead rows: a row for a key that no longer exists is a latent
    # hole, because a key re-added under that name would bypass this gate silently.
    "speech.tts.provider", "speech.tts.sidecars",
    # The LIVE operator's ON decision for the culture fold; the template ships the DEC-43
    # posture, OFF, so a copied config does not start folding a room it has never read.
    # Same class as `cognition.enabled` above.
    "culture.enabled",
    # `[dreamer] max_tokens` — the Dreamer's own per-call ceiling, `0` meaning «inherit
    # `[llm] max_tokens`». The TEMPLATE must keep the sentinel: the field's whole posture
    # is that its arrival is a runtime no-op for a deployment that does not set it, the
    # same as `model = ""`. The live number is the operator's, and a fresh install must
    # not inherit a ceiling sized for a roster it does not have.
    "dreamer.max_tokens",
    # Env-var NAMES are the operator's choice of what to call a secret.
    "media.encryption_key_env",
    # The five model lanes route through NAMED OMNIROUTE COMBOS (`nora`, `nora-dreamer`,
    # `nora-image`). A combo is not a model id; it is a name defined in the operator's own
    # OmniRoute deployment, resolving to a primary plus a fallback that live entirely
    # outside this repo. It is the same class of value as `llm.base_url` two blocks up:
    # the deployment's own topology, which a fresh install cannot possibly have.
    #
    # WHAT THIS GIVES UP, stated because it is the cost: the template cannot be checked
    # against the live lane automatically. The convention is that the template names the
    # combo's FIRST MEMBER (`ollamacloud/deepseek-v4-flash:0731` is `nora`'s primary), so
    # a reader of the template still sees the real upstream. Nothing here can verify that,
    # because combo membership lives in the router, so it is a convention, not a proof,
    # and a lane retargeted in OmniRoute without touching the template will not be caught.
    "llm.model", "dreamer.model",
    "media.vision_model", "media.describe.image.model", "evals.judge_model",
    # Three live opt-ins. Each is the deployment spending something the template must not
    # spend on a fresh operator's behalf, which is the same rule `image_gen.enabled` sits
    # under above — NOT drift.
    "image_gen.allow_reference_images",
    # `provider`: which subscription lane draws her pictures is the operator's
    # (`openai_codex` live); the template keeps the default, `xai`. Both lanes' logins are
    # kept alive whichever is selected (DEC-308), so the choice is a switch, not a
    # migration.
    "image_gen.provider",
    # `autoplay_prewarm_seconds`: prewarming fetches a track nobody asked for
    # yet. 0.0 is inert; the deployment opted in at 12s (R-64).
    "music.autoplay_prewarm_seconds",
    # RustFS's own address (DEC-136): topology, the same bucket `llm.base_url` is in
    # above, not an opt-in flag (that one's a few lines down).
    "media.rustfs.endpoint",
    # The template ships every optional feature INERT — reaching a service,
    # holding a key, or speaking on a timer is an operator decision, never
    # something a copied file makes. The live deployment has opted in.
    "analytics.enabled", "compute.enabled", "evals.langfuse.enabled",
    "heartbeat.enabled",
    "platforms.discord.enabled", "presence.enabled", "preview.enabled",
    "route.enabled", "search.enabled",
    "tools.mcp.pulse.enabled", "weather.enabled", "speech.stt.enabled",
    # `notes.enabled` joins them: the template ships the tier inert so a copied config
    # never silently starts writing a notebook, and this deployment has opted in. NOT
    # `max_rendered_notes`: there is no such key, and there must not be, because a capped
    # index can hide a note and an invisible note is indistinguishable from one that does
    # not exist.
    "notes.enabled",
    # `standing.enabled` joins them, and it is the sharpest case in this list: the debrief
    # spends a model call INSIDE the reply slot, so a copied config that switched itself
    # on would lengthen a hold real people wait behind. Template inert, deployment opted
    # in. NOT `max_chars` / `window` / `timeout_seconds`: those are the same known-good
    # numbers in both files, and a template disagreeing with them is exactly the drift
    # this test exists for.
    "standing.enabled",
    # The one flag that runs the OTHER direction: the template ships the trigger phrase ON
    # (the code default, `TriggerConfig.enabled = True`); this deployment has opted OUT.
    # `@mention` and reply-to-her still engage her either way.
    "engagement.trigger.enabled",
    # The Dreamer runtime, live here and INERT in the template for the same reason as its
    # neighbours above: it spends model calls on a background clock. A copied file must
    # not do that without somebody deciding to.
    "dreamer.enabled",
    # RustFS media custody (DEC-136) and the Discord music bot, both live here for the
    # same reason as their neighbours above -- opted in by the operator, INERT in the
    # template until someone decides to.
    "media.rustfs.enabled", "music.enabled",
    # `social.enabled` joins them: the template ships `x_read` and `x_search` (DEC-201)
    # INERT (DEC-151 "explicitly on" rule: the tools read third-party social content
    # through an authenticated browser session), and this deployment has opted in. NOT
    # `timeout_seconds`/`content_max_chars`: those are the same known-good values in both
    # files when the deployment does not override them, and a template disagreeing with
    # them is exactly the drift this test exists for.
    "social.enabled",
    # `social.bridge_url` is a HOST address like `llm.base_url` above: the
    # deployment's container sits on the private compose `nora` network and
    # reaches the bridge at host.docker.internal:19826; a fresh copy has no
    # reason to inherit that, so the template ships the loopback default.
    "social.bridge_url",
    # A tuned value, not a universal default -- 0.0 in the template is deliberately "off
    # until you choose a fade length", not a measurement this deployment drifted from.
    "music.crossfade_seconds",
    # ON in this deployment (operator's own instruction) -- OFF in the template like every
    # other opt-in above.
    "music.autoplay_enabled",
    # `search.provider` is a live operator decision, and unlike its neighbours above it is
    # a CHOICE rather than an opt-in: neither value is the inert one. The template stays
    # on "tavily" because that is the backend whose relevance floor is measured (DEC-157
    # (4)): a fresh operator copying the file gets the more filtered of the two, and picks
    # the other deliberately.
    "search.provider",
    # `[memory_index] enabled`: which memory `ask_memory` answers from is the operator's
    # switch, measured on the deployment (`conversational_recall`) before it is turned on;
    # the template ships the Honcho path, OFF.
    "memory_index.enabled",
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

# The shadow-review keys carry REAL Discord coordinates (the guild/channel
# pair the operator watches) and the online probe's timeout is deployment
# tuning — neither belongs in a template a fresh install copies; the
# existence of the knobs is documented in the template prose instead.
_MAY_BE_ABSENT |= {
    "online.timeout_seconds",
    "participation.shadow_channel", "participation.shadow_platform",
    "relevance_gate.shadow_channel", "relevance_gate.shadow_platform",
}


def test_config_example_declares_every_key_the_deployment_sets():
    """A key set in config.toml but absent from the template is invisible.

    This is the half that catches a whole section (such as `[compute]`) or a knob (such as
    `memory.self_card_conclusions`) missing from the template: an operator copying the
    template gets the code default with no hint the knob exists.

    The reverse is NOT an error: the template may state a value the deployment leaves at
    its default, which is how it documents defaults.
    """
    live = _load_live_or_skip()
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

    Catches template drift: `llm.model`, `llm.reasoning_effort` and `llm.timeout_seconds`
    are not site-specific, they are the current known-good configuration, and a template
    disagreeing with them misleads every future investigation.
    """
    live = _load_live_or_skip()
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
    from tests.builders import PRIVATE_CONFIG_ENV as ENV
    validate_schema(dst)
    cfg = load(dst, env={**ENV, "OMNIROUTE_API_KEY": "test-key"})
    # Spot-check the drifted values actually landed in the typed model, rather
    # than trusting that "it loaded" means "it loaded what we think".
    # Literals pinned to the template values; update them together with the template.
    assert cfg.llm.model == "ollamacloud/deepseek-v4-flash:0731"
    assert cfg.llm.reasoning_effort == "low"
    assert cfg.llm.timeout_seconds == 360


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
    if not (ROOT / "config.toml").is_file():
        pytest.skip(
            "no config.toml in this checkout — untracked since 2026-09-07; "
            "this test compares the template's Dreamer shape against the live "
            "one and has nothing to compare against here."
        )
    loaded = {}
    for name in ("config.example.toml", "config.toml"):
        dst = tmp_path / name.replace(".example", "") / "config.toml"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, dst)
        loaded[name] = load(dst).dreamer

    example, live = loaded["config.example.toml"], loaded["config.toml"]
    assert example.enabled is False, (
        "the Dreamer runtime must ship OFF in the TEMPLATE — a copied file does "
        "not start spending model calls on a background clock. The live "
        "deployment's own value is an operator decision and is listed in "
        "_MAY_DIFFER."
    )
    # The TEMPLATE's Dreamer model is pinned to a real upstream: a fresh install must get
    # something it can actually reach. The LIVE side is not compared: it names the
    # `nora-dreamer` OmniRoute combo, which resolves outside this repo (see
    # `_MAY_DIFFER`).
    assert example.model == "ollamacloud/deepseek-v4-flash:0731", (
        "the template must name a REACHABLE upstream for the Dreamer, not a "
        "combo — a copied config has no OmniRoute combos defined"
    )
    assert live.model, "the deployment must name a Dreamer lane at all"
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
    """`top_p` stays UNSET until somebody measures it.

    `nora/agent/factory.py` sends `max_tokens`, `temperature`, `tool_choice` and
    `reasoning_effort` and nothing else, so nucleus truncation is the provider's default.
    `top_p = 1.0` would be a no-op; anything lower truncates the distribution on a guess,
    and the `reasoning_effort` series in `[llm]` shows sampling changes move whether she
    calls `send_reply` at all. Deleting this test is the deliberate act that admits a
    measured value.
    """
    # Key-level, not text-level: both files DISCUSS `top_p` at length in their
    # comments, and that prose is the point of A6 — what must not appear is a
    # SET key, in either file or in the model that would accept one.
    assert "top_p" not in LLMConfig.model_fields
    # The TEMPLATE is checked unconditionally — it is tracked, and it is what a new
    # deployment copies. The live file is checked when this checkout has one; it is
    # untracked and a fresh clone has none.
    assert "llm.top_p" not in _load_toml("config.example.toml")
    live = ROOT / "config.toml"
    if live.is_file():
        assert "llm.top_p" not in _flatten(tomllib.loads(live.read_text())), (
            "config.toml sets top_p"
        )


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


def test_every_secret_name_the_config_defaults_reaches_the_container_that_reads_it(
    tmp_path, monkeypatch,
):
    """A `*_env` field names the variable a secret arrives in, and compose passes an explicit
    list, not the whole `.env`. A name present in `.env` but missing from `nora`'s list
    reaches the container as unset, so enabling the feature that reads it crash-loops
    nora.

    The names come from TWO walks, because each misses what the other sees: the example
    config LOADED through the model (so dict and list sections such as `[tools.mcp.*]` and
    required fields such as `llm.api_key_env` are present), and each optional top-level
    section's own default, factories included (so a section the example leaves out is
    present too). Every non-empty name must reach the service that reads it."""
    import yaml
    from pydantic import BaseModel

    def names(value, prefix=""):
        if isinstance(value, BaseModel):
            for field in type(value).model_fields:
                yield from names(getattr(value, field), f"{prefix}{field}.")
        elif isinstance(value, dict):
            for key, item in value.items():
                yield from names(item, f"{prefix}{key}.")
        elif isinstance(value, (list, tuple)):
            for n, item in enumerate(value):
                yield from names(item, f"{prefix}{n}.")
        elif prefix.rstrip(".").endswith("_env") and isinstance(value, str) and value:
            yield prefix.rstrip("."), value

    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    dst = tmp_path / "config.toml"
    shutil.copy(ROOT / "config.example.toml", dst)
    from tests.builders import PRIVATE_CONFIG_ENV as ENV
    found = dict(names(load(dst, env={**ENV, "OMNIROUTE_API_KEY": "test-key"})))
    # Each top-level section's own default too, factories included: a section
    # the example leaves out still ships its defaults. (A bare `Config()`
    # does not construct; ten fields are required.)
    for field, info in Config.model_fields.items():
        if not info.is_required():
            found.update(names(info.get_default(call_default_factory=True), f"{field}."))
    assert "computer.token_env" in found and "llm.api_key_env" in found, (
        "the walk no longer reaches sections it must: " + ", ".join(sorted(found))
    )

    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())

    def passed(service):
        return {e.split("=", 1)[0] for e in compose["services"][service]["environment"]}

    # Who reads each secret. Default: nora. The presence collector reads only
    # the Life360 login; geocoding (Google or HK-gov) runs in nora
    # (`where_is`), so the collector deliberately has no Maps key.
    def readers(key):
        if key.startswith("presence.") and key.split(".")[-1] in (
            "client_credential_env", "username_env", "password_env", "auth_token_env",
        ):
            return ("presence",)
        return ("nora",)

    missing = [
        f"{key} -> {name} (service {service})"
        for key, name in sorted(found.items())
        for service in readers(key)
        if name not in passed(service)
    ]
    assert not missing, (
        "secret names not passed to the container that reads them: " + "; ".join(missing)
    )


def test_public_example_represents_every_serialisable_setting(tmp_path):
    """Check the schema's settings coverage without reading private deployment data.

    Null optional fields have no TOML representation. Cognition/worker budgets
    deliberately forbid explicitly setting the shared class's call ceilings.
    """
    from tests.builders import PRIVATE_CONFIG_ENV
    source = tomllib.loads((ROOT / "config.example.toml").read_text())
    path = tmp_path / "config.toml"
    shutil.copy(ROOT / "config.example.toml", path)
    cfg = load(path, require_secrets=False, env=PRIVATE_CONFIG_ENV)
    effective = cfg.model_dump(mode="json")
    missing = []

    def walk(defaults, declared, prefix=""):
        for key, value in defaults.items():
            field = f"{prefix}.{key}" if prefix else key
            if value is None:
                continue
            if prefix in ("dreamer.budgets.cognition", "dreamer.budgets.subagent") and key in (
                "max_memory_calls", "max_tool_calls", "tool_calls",
            ):
                continue
            if field == "agent.handles":
                assert set(value) == set(source["agent"]["handles_env"])
                continue
            if key not in declared and key + "_env" not in declared:
                missing.append(field)
            elif isinstance(value, dict):
                walk(value, declared.get(key, {}), field)

    walk(effective, source)
    assert missing == []
