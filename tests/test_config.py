"""Frozen config tree (spec D.1), pydantic loader over config.toml."""

import re
import shutil
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from nora.config import (
    AnalyticsConfig,
    Config,
    ConfigError,
    DreamBudgetConfig,
    DreamerConfig,
    LimitsConfig,
    LLMConfig,
    MediaConfig,
    SpeechConfig,
    load,
)
from nora.domain.facts import (
    FACT_PREDICATE_CEILING_CHARS,
    FACT_TERM_CEILING_CHARS,
)

ROOT = Path(__file__).resolve().parents[1]


def _set_key(text: str, key: str, value: str) -> str:
    """Rewrite `key = ...` and ASSERT the edit landed.

    These tests used `str.replace("max_slot_hold_seconds = 150.0", ...)`. The
    default changed to 160.0 and the replace became a SILENT NO-OP — the config
    loaded unmodified and two refuse-to-start tests reported DID NOT RAISE.
    A replace that does not match is the failure mode this repo has been bitten
    by three times today, so this one refuses rather than returning the input
    unchanged.
    """
    import re as _re

    new, n = _re.subn(rf"(?m)^{_re.escape(key)} = .*$", f"{key} = {value}", text)
    assert n == 1, f"{key} not found exactly once in the config ({n} matches)"
    return new


@pytest.fixture
def toml_path(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    dst = tmp_path / "config.toml"
    shutil.copy(ROOT / "config.example.toml", dst)
    return dst


def test_unknown_key_is_a_startup_failure(toml_path):
    toml_path.write_text(toml_path.read_text() + '\n[tagg]\ntrigger = "x"\n')
    with pytest.raises(ConfigError, match="tagg"):
        load(toml_path)


def _trigger_line(toml_path) -> str:
    """The `text = "..."` line as the config under test ACTUALLY spells it.

    Read, never hardcoded. These tests do string surgery on the real config to
    build malformed variants, so a literal trigger couples them to a value the
    operator owns: when the trigger changed from `nora.000` to `nora` on
    2026-08-26 every `str.replace` below silently became a no-op, the "malformed"
    config stayed valid, and the startup-failure tests failed for a reason that
    had nothing to do with what they measure. A no-op replace is the dangerous
    half — it leaves a test asserting against an input it never actually built.
    """
    for line in toml_path.read_text().splitlines():
        if line.startswith("text = "):
            return line
    raise AssertionError("no `text = ` trigger line in the config under test")


def test_unknown_nested_key_is_a_startup_failure(toml_path):
    line = _trigger_line(toml_path)
    toml_path.write_text(toml_path.read_text().replace(line, line + '\ntriger = "oops"'))
    with pytest.raises(ConfigError, match="triger"):
        load(toml_path)


def test_missing_required_key_is_a_startup_failure(toml_path):
    toml_path.write_text(toml_path.read_text().replace(_trigger_line(toml_path) + "\n", ""))
    with pytest.raises(ConfigError, match="text"):
        load(toml_path)


def test_tilde_expands_and_paths_are_absolute(toml_path):
    cfg = load(toml_path)
    assert cfg.store.data_dir.is_absolute()
    assert "~" not in str(cfg.store.data_dir)
    assert cfg.obs.logging.path.is_absolute()
    assert cfg.doctrine.dir.is_absolute()


def test_relative_paths_resolve_against_the_config_file_directory(toml_path):
    cfg = load(toml_path)
    assert cfg.doctrine.dir == (toml_path.parent / "nora" / "doctrine").resolve()


def test_missing_env_secret_is_a_startup_failure(toml_path, monkeypatch):
    monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="OMNIROUTE_API_KEY"):
        load(toml_path)


def test_config_never_holds_the_secret_value(toml_path):
    cfg = load(toml_path)
    assert cfg.llm.api_key_env == "OMNIROUTE_API_KEY"
    assert "test-key" not in repr(cfg)


def test_invalid_doctrine_reload_mode_is_a_startup_failure(toml_path):
    toml_path.write_text(toml_path.read_text().replace(
        'reload = "mtime"', 'reload = "inotify"'))
    with pytest.raises(ConfigError, match="reload"):
        load(toml_path)


def test_invalid_tracing_exporter_is_a_startup_failure(toml_path):
    toml_path.write_text(toml_path.read_text().replace(
        'exporter = "file"', 'exporter = "jaeger"'))
    with pytest.raises(ConfigError, match="exporter"):
        load(toml_path)


def test_otlp_exporter_without_an_endpoint_is_a_startup_failure(toml_path):
    toml_path.write_text(toml_path.read_text().replace(
        'exporter = "file"', 'exporter = "otlp_http"'))
    with pytest.raises(ConfigError, match="endpoint"):
        load(toml_path)


def test_headers_env_names_the_env_var_not_the_value(toml_path):
    """DEC-8: `*_env` fields hold an environment variable NAME, never the
    secret value, and there is no `${VAR}` interpolation."""
    cfg = load(toml_path)
    assert cfg.obs.tracing.headers_env == ""


def test_headers_env_defaults_to_empty_when_absent_from_the_toml():
    """`config.example.toml` already declares `headers_env`, but any
    pre-existing `config.toml` predating this field must still load —
    `TracingConfig`'s `extra=\"forbid\"` only forbids UNKNOWN keys, an absent
    optional one is fine."""
    from tests.builders import make_tracing_config

    assert make_tracing_config().headers_env == ""


def test_trigger_enabled_defaults_to_true_when_absent_from_the_toml(toml_path):
    """A `config.toml` written before `TriggerConfig.enabled` existed must keep
    matching its trigger phrase exactly as it always did — the trigger was
    already live for every deployment, so defaulting this field OFF would be
    the silent behaviour change, not defaulting it on (unlike `[speech]`/
    `[weather]`'s INERT-by-default shape, where the capability never existed
    before)."""
    # Drop the key from ITS OWN SECTION rather than by adjacency to another line.
    # An earlier version matched "enabled = true\ntext = ...", which assumed the two
    # sat next to each other; a comment was added between them and the replace
    # silently became a no-op, so the test loaded a config that still HAD `enabled`
    # and proved nothing about the default. Section-scoped surgery cannot rot that way.
    head, rest = toml_path.read_text().split("[engagement.trigger]", 1)
    section, tail = rest.split("[store]", 1)
    stripped = "\n".join(ln for ln in section.splitlines()
                         if not ln.strip().startswith("enabled"))
    text = f"{head}[engagement.trigger]{stripped}\n[store]{tail}"
    assert "enabled" not in text.split("[engagement.trigger]")[1].split("[store]")[0]
    toml_path.write_text(text)

    cfg = load(toml_path)

    assert cfg.engagement.trigger.enabled is True


def test_config_is_immutable(toml_path):
    cfg = load(toml_path)
    with pytest.raises(ValidationError):
        cfg.identity_file = Path("/other")


def test_missing_config_file_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load(tmp_path / "does-not-exist.toml")


def test_malformed_toml_is_a_config_error(toml_path):
    toml_path.write_text("[engagement.trigger\ntext = 'unclosed table header'\n")
    with pytest.raises(ConfigError):
        load(toml_path)


def test_config_uses_the_target_shape(toml_path):
    cfg = load(toml_path)
    # platforms replaces signal_cli, keyed by adapter name
    assert cfg.platforms.signal.base_url.startswith("http")
    assert cfg.platforms.signal.enabled is True
    # `nora_sender_id` is GONE (DEC-102): Nora's id in her own rows now comes
    # from the platform (`resolve_identity().primary`), never from a string an
    # operator typed — with two platforms wired one configured name is
    # necessarily wrong for one of them.
    assert not hasattr(cfg.platforms.signal, "nora_sender_id")
    # memory replaces honcho
    assert cfg.memory.workspace_id == "nora"
    # store replaces buffer
    assert cfg.store.max_per_conversation == 500
    assert cfg.store.context_window == 40
    # tiers replace default/deep, and carry their own timeout. These two track
    # the DEPLOYMENT (DEC-84's opencode-zen → ollamacloud move, and 30 → 60 →
    # 120 → 240; the model gained its `:0731` catalog tag 2026-08-30);
    # `tests/test_env_template.py` is what keeps the template and
    # config.toml from drifting apart again.
    assert cfg.llm.model == "ollamacloud/deepseek-v4-flash:0731"
    assert cfg.llm.timeout_seconds == 240
    assert cfg.llm.limits.turns == 20
    assert cfg.llm.max_tokens == 2000
    # engagement owns the trigger; policy is declared even though only one exists
    assert cfg.engagement.policy == "trigger_text"
    assert cfg.engagement.trigger.enabled is True
    # The trigger STRING is the operator's, not this test's — pinning it here is
    # what kept the suite red from 2026-08-26, when `nora.000` was replaced by
    # `nora` and this assertion started measuring the operator's choice rather
    # than the config shape. Assert the shape.
    assert cfg.engagement.trigger.text and not cfg.engagement.trigger.text.isspace()
    assert cfg.engagement.trigger.min_interval_seconds == 5
    # obs groups the three signals
    assert cfg.obs.metrics.port == 9100
    assert cfg.obs.tracing.exporter == "file"
    assert cfg.obs.tracing.enabled is True
    assert cfg.obs.logging.level == "INFO"
    # identity_file replaces persons_file
    assert cfg.identity_file.name == "identity.toml"
    # values dropped from the deleted test_loads_the_example_config, restored here
    assert cfg.llm.limits.total_tokens == 256000
    assert cfg.llm.limits.output_tokens == 12000
    assert cfg.doctrine.strict is True
    assert cfg.doctrine.reload == "mtime"


def test_constructing_config_without_base_context_is_an_error():
    """Paths can only be resolved relative to the config file's directory, so
    constructing a Config without that context must fail loudly rather than
    silently yielding unresolved relative paths."""
    data = tomllib.loads((ROOT / "config.example.toml").read_text())
    with pytest.raises(ValidationError, match="base directory context is required"):
        Config.model_validate(data)


def test_the_media_section_is_optional_and_defaults_to_custody_on(toml_path, monkeypatch):
    """DEC-43. `extra="forbid"` makes "old config keeps loading" a real
    constraint, not a courtesy: a REQUIRED [media] section would turn an
    upgrade into a refusal to start on a file the operator never edited.

    Deleting the section from the example is the probe — the values asserted
    are the class defaults, so this fails if a default silently changes.
    """
    text = toml_path.read_text()
    head, _, tail = text.partition("[media]\n")
    assert tail, "config.example.toml no longer declares [media]"
    # Everything after [media] up to the next section header goes too.
    remainder = tail.split("\n[", 1)
    toml_path.write_text(head + ("[" + remainder[1] if len(remainder) > 1 else ""))

    cfg = load(toml_path)

    assert cfg.media.enabled is True
    assert cfg.media.max_bytes == 100 * 1024 * 1024
    assert cfg.media.timeout_seconds == 8.0
    # DEC-136: ships OFF — a config.toml written before RustFS support
    # existed keeps writing to the filesystem exactly as before.
    assert cfg.media.rustfs.enabled is False


def test_an_unknown_rustfs_key_is_still_a_startup_failure(toml_path):
    """`extra="forbid"` on `RustfsBlobConfig` too — a typo here must not
    silently mean "rustfs off", the same posture `[media]` itself takes."""
    toml_path.write_text(
        toml_path.read_text() + '\n[media.rustfs]\nendpointt = "http://rustfs:9000"\n'
    )
    with pytest.raises(ConfigError):
        load(toml_path)


def test_media_custody_can_be_switched_off(toml_path):
    """The honest off switch while retention is undecided — the most PII-heavy
    data in the system must have one."""
    # Targeted at THIS section: `platforms.signal.enabled = true` appears
    # earlier in the file, and a blind first-match replace silently edited that
    # instead — which is how a test asserts a default back to itself.
    toml_path.write_text(
        toml_path.read_text().replace("[media]\nenabled = true", "[media]\nenabled = false")
    )
    assert load(toml_path).media.enabled is False


def test_an_unknown_media_key_is_still_a_startup_failure(toml_path):
    """Defaulted does not mean lenient: a typo in this section must not
    silently mean "custody off"."""
    toml_path.write_text(toml_path.read_text() + '\n[media]\nenabledd = true\n')
    with pytest.raises(ConfigError):
        load(toml_path)


def test_discord_platform_section_is_optional_and_defaults_to_disabled(toml_path):
    """A config.toml written before Discord existed must still load — extra="forbid"
    only forbids UNKNOWN keys, an absent optional section is fine (same probe
    shape as test_the_media_section_is_optional_and_defaults_to_custody_on)."""
    text = toml_path.read_text()
    head, _, tail = text.partition("[platforms.discord]\n")
    assert tail, "config.example.toml no longer declares [platforms.discord]"
    remainder = tail.split("\n[", 1)
    toml_path.write_text(head + ("[" + remainder[1] if len(remainder) > 1 else ""))

    cfg = load(toml_path)

    assert cfg.platforms.discord.enabled is False
    assert cfg.platforms.discord.token_env == "DISCORD_BOT_TOKEN"  # noqa: S105 — an env-var NAME


def test_discord_platform_can_be_enabled(toml_path):
    toml_path.write_text(
        toml_path.read_text().replace(
            "[platforms.discord]\nenabled = false", "[platforms.discord]\nenabled = true"
        )
    )
    assert load(toml_path).platforms.discord.enabled is True


def test_an_unknown_discord_key_is_a_startup_failure(toml_path):
    """A typo'd key must be refused by pydantic's `extra="forbid"`, not merely
    ignored — a silently-dropped line is an operator certain they configured
    something that never took effect.

    The key is edited INTO the existing section. Appending a second
    `[platforms.discord]` table would make TOML itself reject the file for
    declaring the table twice, and `load` wraps `TOMLDecodeError` in
    `ConfigError` too — so the assertion passed without pydantic ever running,
    and `extra="allow"` left it green.
    """
    toml_path.write_text(
        toml_path.read_text().replace(
            'token_env = "DISCORD_BOT_TOKEN"', 'bot_tokenn = "x"',
        )
    )
    with pytest.raises(ConfigError, match="bot_tokenn"):
        load(toml_path)


def test_the_presence_section_is_optional_and_ships_inert(toml_path):
    """DEC-54/55. `[presence]` deleted from the example must keep loading, and
    the default is INERT (enabled = false): presence must never be on by a
    missing section. The values asserted are the class defaults, so this fails
    if a default silently changes."""
    text = toml_path.read_text()
    head, _, tail = text.partition("[presence]\n")
    assert tail, "config.example.toml no longer declares [presence]"
    remainder = tail.split("\n[", 1)
    toml_path.write_text(head + ("[" + remainder[1] if len(remainder) > 1 else ""))

    cfg = load(toml_path)
    assert cfg.presence.enabled is False
    assert cfg.presence.cadence_seconds == 90.0
    assert cfg.presence.retention_days == 720


def test_the_analytics_section_is_optional_and_ships_inert(toml_path):
    """DEC-115. `[analytics]` deleted from the example must keep loading, and
    the default is INERT (enabled = false): the presence analytics substrate
    must never be on by a missing section. The values asserted are the class
    defaults, so this fails if a default silently changes."""
    text = toml_path.read_text()
    head, _, tail = text.partition("[analytics]\n")
    assert tail, "config.example.toml no longer declares [analytics]"
    remainder = tail.split("\n[", 1)
    toml_path.write_text(head + ("[" + remainder[1] if len(remainder) > 1 else ""))

    cfg = load(toml_path)
    assert cfg.analytics.enabled is False
    assert cfg.analytics.tick_seconds == 120.0
    assert cfg.analytics.safety_lag == 600
    assert cfg.analytics.settled_seconds == 900.0
    assert cfg.analytics.max_age_seconds == 86_400.0
    assert cfg.analytics.batch_limit == 400
    assert cfg.analytics.backfill_batch_limit == 20_000
    assert cfg.analytics.backfill_threshold == 4_000
    assert cfg.analytics.metrics_port == 9103


def test_the_heartbeat_and_reminders_sections_are_optional_and_ship_inert(toml_path):
    """Both sections deleted from the example must keep loading, and the
    heartbeat default is INERT (enabled = false): Nora's first
    unprompted-speech path must never turn on because a section is MISSING
    from a config.toml written before it existed. `extra="forbid"` is what
    makes that a real constraint rather than a courtesy.

    The values asserted are the class defaults, so this fails if one silently
    changes — including the `min_lead_seconds > tick_seconds` relation the two
    sections are chosen together for."""
    text = toml_path.read_text()
    head, _, tail = text.partition("[heartbeat]\n")
    assert tail, "config.example.toml no longer declares [heartbeat]"
    # [reminders] is the very next section, so cutting from [heartbeat] to the
    # section after it removes both.
    remainder = tail.split("\n[", 2)
    assert len(remainder) > 2, "config.example.toml no longer declares [reminders]"
    toml_path.write_text(head + "[" + remainder[2])

    cfg = load(toml_path)

    assert cfg.heartbeat.enabled is False
    assert cfg.heartbeat.tick_seconds == 60.0
    assert cfg.heartbeat.timeout_seconds == 120.0
    assert cfg.reminders.max_pending_per_requester == 20
    assert cfg.reminders.min_lead_seconds == 120
    # 365 since 2026-08-10: 30 refused the real asks (a wedding 55 days out, a
    # birthday 6 months out) and the code default now MATCHES the shipped config
    # — they had drifted, which made the eval harness measure a horizon
    # production does not have (`nora.app.offline` builds `ToolRegistry()` with
    # no caps). This assertion is what keeps the two from drifting again.
    assert cfg.reminders.max_horizon_days == 365
    assert cfg.reminders.min_lead_seconds > cfg.heartbeat.tick_seconds


def test_the_language_section_is_optional_and_ships_inert(toml_path):
    """Task 11's `[language]` — a config.toml written before the Dreamer
    existed must keep loading, and `enabled` must default to False: a new
    subsystem does not switch itself on. The values asserted are the class
    defaults, so this fails if one silently changes — including the
    store > render relation the render cap depends on
    (`nora.engagement.context.DEFAULT_ROOM_LANGUAGE_STORE_LIMIT`, 15, is not
    itself configurable, so it is checked here as a fixed constant rather
    than a second config field)."""
    from nora.engagement.context import DEFAULT_ROOM_LANGUAGE_STORE_LIMIT

    text = toml_path.read_text()
    head, _, tail = text.partition("[language]\n")
    assert tail, "config.example.toml no longer declares [language]"
    remainder = tail.split("\n[", 1)
    toml_path.write_text(head + ("[" + remainder[1] if len(remainder) > 1 else ""))

    cfg = load(toml_path)

    assert cfg.language.enabled is False
    assert cfg.language.tick_seconds == 300.0
    assert cfg.language.safety_lag == 300
    assert cfg.language.quiet_seconds == 1800.0
    assert cfg.language.max_age_seconds == 86_400.0
    assert cfg.language.batch_limit == 400
    assert cfg.language.max_rendered_lexemes == 6
    # The CODE default — deliberately NOT the deployment's `:0731` tag (the
    # deployment carries the tag in config.toml because the bare id 400s
    # against the re-synced OmniRoute catalog; the code default is unchanged).
    assert cfg.language.model == "ollamacloud/deepseek-v4-flash"
    assert cfg.language.timeout_seconds == 120.0
    assert cfg.language.max_rendered_lexemes < DEFAULT_ROOM_LANGUAGE_STORE_LIMIT, (
        "the render cap must stay below the store limit or the block "
        "silently under-fills"
    )


def test_the_facts_section_is_optional_and_ships_inert(toml_path):
    """The fact tier's `[facts]` — a config.toml written before this section
    existed must keep loading, and `enabled` must default to False: a new
    subsystem does not switch itself on, and nothing writes a fact yet. The
    values asserted are the class defaults, so this fails if one silently
    changes.

    The workspace startup check the design doc's §7 argued for was cut
    (operator, 2026-08-20): the store's read (`standing_facts`, Task 3, not
    yet built) is specified to scope by workspace in SQL, so once it exists
    there will be no second refusal-shaped property to test here — see
    `docs/superpowers/plans/2026-08-20-fact-tier.md`'s Task 6 note."""
    text = toml_path.read_text()
    head, _, tail = text.partition("[facts]\n")
    assert tail, "config.example.toml no longer declares [facts]"
    remainder = tail.split("\n[", 1)
    toml_path.write_text(head + ("[" + remainder[1] if len(remainder) > 1 else ""))

    cfg = load(toml_path)

    assert cfg.facts.enabled is False
    assert cfg.facts.max_adjudications_per_tick == 10
    assert cfg.facts.max_room_facts == 8
    assert cfg.facts.max_sender_facts == 4
    assert cfg.facts.retention_days == 180


def test_the_notes_section_is_optional_and_ships_inert(toml_path):
    """The note tier's `[notes]`, its `[facts]` sibling's shape and reasoning:
    a config.toml written before this section existed must keep loading under
    `extra="forbid"`, and `enabled` must default to False — the tier ships
    dormant and an operator turns it on deliberately, which is what keeps a
    `<notes>` block out of every tag until somebody decides otherwise.

    One field only, and the ABSENCE is the assertion worth making: there is no
    render cap here, unlike `[facts]`' two slice caps. §5.1 makes the index
    uncapped on purpose — a note missing from it is invisible to her, and
    invisible is indistinguishable from absent — so a top-N knob would be a
    supported way to reintroduce that failure. The per-entry caps in
    `nora.domain.notes` are where the prompt budget is held instead."""
    text = toml_path.read_text()
    head, _, tail = text.partition("[notes]\n")
    assert tail, "config.example.toml no longer declares [notes]"
    remainder = tail.split("\n[", 1)
    toml_path.write_text(head + ("[" + remainder[1] if len(remainder) > 1 else ""))

    cfg = load(toml_path)

    assert cfg.notes.enabled is False
    assert not hasattr(cfg.notes, "max_rendered_notes"), (
        "the note index is uncapped by design (§5.1) — a render cap here "
        "would make a note silently invisible"
    )


def test_a_render_cap_at_or_above_the_store_limit_is_refused(toml_path):
    """`[language].max_rendered_lexemes` has no upper bound of its own
    (`Field(ge=1)`) — this cross-field relation was a comment in
    `LanguageConfig`'s own docstring and a rule in none, the same gap
    `_lead_clears_the_tick` closed for `[reminders]`/`[heartbeat]`. Raising
    the cap to or past the STORE limit (15) must refuse to start rather than
    silently under-fill the rendered block."""
    text = toml_path.read_text()
    toml_path.write_text(
        text.replace("max_rendered_lexemes = 6", "max_rendered_lexemes = 15")
    )

    with pytest.raises(ConfigError, match="max_rendered_lexemes"):
        load(toml_path)


def test_a_render_cap_below_the_store_limit_is_accepted(toml_path):
    """The control for the test above — without it, a bug that refused
    EVERY value (not just `>= 15`) would leave that one green."""
    text = toml_path.read_text()
    toml_path.write_text(
        text.replace("max_rendered_lexemes = 6", "max_rendered_lexemes = 14")
    )

    cfg = load(toml_path)
    assert cfg.language.max_rendered_lexemes == 14


def test_a_minimum_lead_inside_the_tick_is_refused(toml_path):
    """The relation was a COMMENT in three files and a rule in none.

    An operator who lengthens the tick past the minimum lead gets a reminder
    that becomes due inside the tick already running — so Nora answers 「等陣提
    你」 to something she will not say until after the moment has gone. The
    promise made at create time is what is at stake, so this refuses to start
    rather than warning; `policy.toml`'s deny-by-default refusal (R-5) is the
    precedent for a config that stops the process rather than degrading.
    """
    text = toml_path.read_text()
    toml_path.write_text(text.replace("tick_seconds = 60", "tick_seconds = 600"))

    with pytest.raises(ConfigError, match="min_lead_seconds"):
        load(toml_path)


def test_a_lead_equal_to_the_tick_is_refused_too(toml_path):
    """`>`, not `>=`. Equal is the boundary the relation exists to exclude: a
    row due exactly one tick out races the tick that is already running, and
    which side it lands on is decided by scheduling rather than by config."""
    text = toml_path.read_text()
    toml_path.write_text(text.replace("min_lead_seconds = 120", "min_lead_seconds = 60"))

    with pytest.raises(ConfigError, match="min_lead_seconds"):
        load(toml_path)


def test_presence_config_carries_no_ids(toml_path):
    """The [presence] section holds only operational knobs — no circle or member
    ids. Circle membership lives in identity.toml (design/19 §3), so config.toml
    is id-free and safe to track. An unknown key like `circles` is rejected."""
    cfg = load(toml_path)
    assert not hasattr(cfg.presence, "circles")
    with pytest.raises(ConfigError):
        toml_path.write_text(
            toml_path.read_text().replace(
                "[presence]\nenabled = false", "[presence]\nenabled = false"
            ) + '\n[[presence.circles]]\ncircle_id = "x"\nmembers = ["m"]\n'
        )
        load(toml_path)


def test_presence_credentials_are_named_not_inlined(toml_path):
    """DEC-8: the config names the env vars, never the secret values."""
    cfg = load(toml_path)
    assert cfg.presence.client_credential_env == "LIFE360_CLIENT_CREDENTIAL"
    assert cfg.presence.username_env == "LIFE360_USERNAME"
    assert cfg.presence.password_env == "LIFE360_PASSWORD"  # noqa: S105 — an env-var NAME


def test_an_unknown_presence_key_is_still_a_startup_failure(toml_path):
    toml_path.write_text(toml_path.read_text() + '\n[presence]\nenabledd = true\n')
    with pytest.raises(ConfigError):
        load(toml_path)


def test_an_unknown_analytics_key_is_still_a_startup_failure(toml_path):
    """`extra="forbid"` — a typo'd key is a startup crash-loop, not silently
    ignored (the same posture every other INERT-by-default section takes)."""
    toml_path.write_text(toml_path.read_text() + '\n[analytics]\nenabledd = true\n')
    with pytest.raises(ConfigError):
        load(toml_path)


def test_analytics_config_rejects_unknown_keys_directly():
    """Constructing the model directly (not via `load()`) still refuses an
    unknown key — belt-and-braces with the startup-failure test above."""
    with pytest.raises(ValidationError):
        AnalyticsConfig(enabled=True, nonexistent_key=1)


def test_min_trip_fixes_defaults_to_three_and_refuses_a_one_fix_trip():
    """Tier 5. `extract_trips`'s own noise gate: a run shorter than this is
    dropped rather than written as a trip. `ge=2`, not `ge=1` — a one-fix
    "trip" has no pair to run `derive_speed_kmh`/`haversine_distance_m`
    over, so its `distance_m` and `avg_speed_kmh` are both structurally
    0.0; admitting one would put a fabricated zero-speed sample into
    `presence_corridor_speed`'s percentiles, which is precisely the
    "never assert what you cannot ground" rule."""
    assert AnalyticsConfig().min_trip_fixes == 3
    assert AnalyticsConfig(min_trip_fixes=2).min_trip_fixes == 2
    with pytest.raises(ValidationError):
        AnalyticsConfig(min_trip_fixes=1)


def test_corridor_cluster_batch_limit_defaults_to_the_measured_bound():
    """Tier 5. How many not-yet-clustered trips one corridor-clustering
    pass may re-cluster. MEASURED, unlike the two knobs beside it:
    `cluster_trips` is O(n^2) over an edit-distance metric, n=500 costs
    1.87s on this machine and n=1000 costs 7.59s, so 500 is under 2% of a
    `tick_seconds=120` tick for one subject."""
    assert AnalyticsConfig().corridor_cluster_batch_limit == 500
    assert AnalyticsConfig(corridor_cluster_batch_limit=50).corridor_cluster_batch_limit == 50
    with pytest.raises(ValidationError):
        AnalyticsConfig(corridor_cluster_batch_limit=0)


def test_max_trip_duration_seconds_defaults_to_three_hours():
    """Tier 5. Trip settlement (design/27 §4, 2026-08-20 trip-settlement design
    §2.4): the safety valve against a stuck `is_driving` signal — if Life360
    ever reports `True` indefinitely (a stale flag, a phone that stopped
    updating mid-drive), a trip could otherwise never settle and hold the
    watermark back forever. This default is a JUDGMENT CALL, not a measured
    value — no genuine multi-hour trip data exists yet to derive a percentile
    from (the trips in production today are exactly the fragments this fix
    corrects). 3 hours is generous enough to cover a real long drive without
    leaving a stuck signal unbounded."""
    assert AnalyticsConfig().max_trip_duration_seconds == 10800.0
    assert AnalyticsConfig(max_trip_duration_seconds=7200.0).max_trip_duration_seconds == 7200.0


def test_a_cluster_batch_limit_below_corridor_min_samples_is_refused():
    """Fail-closed on a config that could never mint a corridor. A pool
    smaller than `corridor_min_samples` cannot arithmetically hold enough
    similar trips for `cluster_trips` to call any of them a cluster — the
    exact shape of the defect this knob exists to fix (Tier 5's final
    whole-branch review, Critical #1), and one with no log line and no
    metric of its own, only a counter that never moves."""
    with pytest.raises(ValidationError):
        AnalyticsConfig(corridor_cluster_batch_limit=2, corridor_min_samples=3)
    # ...and the boundary itself is allowed: exactly enough IS enough.
    cfg = AnalyticsConfig(corridor_cluster_batch_limit=3, corridor_min_samples=3)
    assert cfg.corridor_cluster_batch_limit == 3


def test_tools_mcp_is_optional_and_defaults_to_empty(toml_path):
    text = toml_path.read_text()
    head, _, tail = text.partition("[tools.mcp.pulse]\n")
    assert tail, "config.example.toml no longer declares [tools.mcp.pulse]"
    remainder = tail.split("\n[", 1)
    toml_path.write_text(head + ("[" + remainder[1] if len(remainder) > 1 else ""))

    cfg = load(toml_path)

    assert cfg.tools.mcp == {}


def test_tools_mcp_parses_a_configured_server(toml_path, monkeypatch):
    monkeypatch.setenv("PULSE_MCP_TOKEN", "test-token")
    toml_path.write_text(toml_path.read_text().replace(
        "[tools.mcp.pulse]\nenabled = false", "[tools.mcp.pulse]\nenabled = true"))

    cfg = load(toml_path)

    assert cfg.tools.mcp["pulse"].enabled is True
    assert cfg.tools.mcp["pulse"].base_url == "https://orion.tail0a1b2c3d.ts.net/pulse/mcp"
    assert cfg.tools.mcp["pulse"].token_env == "PULSE_MCP_TOKEN"  # noqa: S105 — an env-var NAME
    assert cfg.tools.mcp["pulse"].budget == 5


def test_tools_mcp_rejects_unknown_keys(toml_path):
    toml_path.write_text(toml_path.read_text().replace(
        "[tools.mcp.pulse]\nenabled = false",
        "[tools.mcp.pulse]\nenabled = false\ntypo_field = true"))
    with pytest.raises(ConfigError, match="typo_field"):
        load(toml_path)


def test_a_zero_cadence_is_rejected(toml_path):
    """`ge=1` guards a busy-loop cadence nobody chose."""
    toml_path.write_text(
        toml_path.read_text().replace("cadence_seconds = 90", "cadence_seconds = 0")
    )
    with pytest.raises(ConfigError):
        load(toml_path)


def _llm_config(**kw) -> LLMConfig:
    """A minimal valid `LLMConfig`, so the budget assertions below are about
    `[llm.budgets]` and nothing else."""
    return LLMConfig(
        base_url="http://x/v1", api_key_env="K", model="m", max_tokens=2000,
        temperature=0.7, timeout_seconds=120.0,
        limits=LimitsConfig(turns=20, total_tokens=256000, output_tokens=12000),
        **kw,
    )


def test_a_budget_override_naming_an_unknown_tool_refuses_to_start():
    """A typo here is an operator certain they raised a cap that never moved —
    the same class as the `peer_card` endpoint that answered `[]` for the life
    of a deployment. Validated against the LIVE registry, so it cannot go stale
    when a tool is added or removed."""
    with pytest.raises(ValidationError, match="unknown tool 'send_replies'"):
        _llm_config(budgets={"send_replies": 8})


def test_a_budget_below_one_refuses_to_start():
    """0 does not mean "unlimited" and does not mean "off" — R-7 would veto
    every call, so the tool is present, advertised in the schemas, and
    unreachable. Removing the tool is the honest way to do that."""
    with pytest.raises(ValidationError, match="would make the tool unreachable"):
        _llm_config(budgets={"send_reply": 0})


def test_a_valid_budget_override_is_accepted():
    assert _llm_config(budgets={"send_reply": 9}).budgets == {"send_reply": 9}


def test_audio_and_video_describers_ship_inert():
    """The split's whole premise: image describes, audio and video are wired but
    OFF, so enabling either later is a flag plus a model name and not a code
    change (DEC-114). Ships inert like [presence]/[search]/[language].

    Asserted against the CODE DEFAULTS, not against a loaded config.example.toml
    — that file literally writes `enabled = true/false/false`, so reading it
    back proved only that TOML parses. Inverting the defaults in
    `DescribersConfig` left the original version of this test green.
    """
    describe = MediaConfig().describe
    assert describe.image.enabled is True
    assert describe.audio.enabled is False
    assert describe.video.enabled is False
    # The model, not just the flag, is what `_describe_models` gates on: an
    # enabled modality with no model named is still no describer.
    assert describe.audio.model == ""
    assert describe.video.model == ""


def test_speech_ships_inert():
    """`[speech]` is off by default — a config.toml written before speech
    existed keeps loading and Nora never calls out. TTS is deferred and
    present-but-inert so the section's shape is stable.

    Asserted against the CODE DEFAULTS, not against a loaded
    config.example.toml — inverting the defaults in `SpeechConfig` must leave
    this test red, exactly like `test_audio_and_video_describers_ship_inert`.
    """
    speech = SpeechConfig()
    assert speech.stt.enabled is False
    assert speech.stt.endpoint == "http://stt:8000"
    assert speech.stt.language == "yue"
    assert speech.tts.enabled is False
    assert speech.tts.provider == ""


def test_a_config_written_before_the_split_still_describes_images(toml_path):
    """Backward compatibility here is a LIVENESS property, not a courtesy.

    `MediaConfig` is `extra="forbid"`, and on 2026-08-17 a `[language]` block
    against an image whose `Config` lacked the field put Nora into a startup
    crash-loop. A live config.toml naming only `vision_model` must therefore keep
    loading AND keep describing images — so `vision_model` stays as the image
    describer's deprecated alias.
    """
    # A genuinely pre-split file: the `[media.describe.*]` tables do not exist at
    # all, and `vision_model` is the only place a model is named.
    # Anchored on the TABLE HEADER at the start of a line, not on the bare
    # string: the `[media]` comment block now names `[media.describe.image]`
    # prose to point the operator forward, and partitioning on that mention
    # truncated the file above `[doctrine]`.
    head, sep, _ = toml_path.read_text().partition("\n[media.describe.image]\n")
    assert sep, "config.example.toml no longer declares [media.describe.image]"
    toml_path.write_text(re.sub(
        r'^vision_model = ".*"$', 'vision_model = "legacy/only-model"',
        head, count=1, flags=re.MULTILINE))
    media = load(toml_path).media
    assert media.describer_for("image").model == "legacy/only-model"
    assert media.describer_for("image").enabled is True


def test_an_explicit_image_model_wins_over_the_legacy_alias(toml_path):
    """Otherwise the alias would pin the new key open and the split be a no-op."""
    toml_path.write_text(re.sub(
        r'^vision_model = ".*"$', 'vision_model = "legacy/only-model"',
        toml_path.read_text(), count=1, flags=re.MULTILINE,
    ).replace('model = "ollamacloud/glm-5.3-flash"', 'model = "new/image-model"'))
    assert load(toml_path).media.describer_for("image").model == "new/image-model"


# --- [dreamer] (instruction-substrate spec §4.1) ---------------------------


def test_the_dreamer_directory_resolves_against_the_config_file(toml_path):
    """The same rule `[doctrine] dir` follows, and for the same reason: the
    process's working directory is not the deployment's, so a path resolved
    against `cwd` finds the corpus in a dev shell and nothing in the
    container."""
    cfg = load(toml_path)
    assert cfg.dreamer.dir.is_absolute()
    assert cfg.dreamer.dir == (toml_path.parent / "nora" / "dreamer").resolve()


def test_a_config_written_before_the_dreamer_section_existed_still_loads(toml_path):
    """`extra="forbid"` cuts both ways: a NEW required section breaks every
    config file that predates it. The whole section is optional and every field
    defaulted for exactly that reason — the `[route]`/`[presence]`/`[language]`
    shape.

    Deleting from `[dreamer]` to `[obs.logging]` also removes the
    `[dreamer.budgets.*]` tables, which is the point: a config written before
    the runtime existed declares no budget for any adaptor, and every adaptor
    must still come back BOUNDED rather than raising. The values asserted are
    the class defaults, so this fails if one silently changes."""
    text = toml_path.read_text()
    start = text.index("\n[dreamer]")
    end = text.index("\n[obs.logging]")
    toml_path.write_text(text[:start] + text[end:])
    cfg = load(toml_path)
    assert cfg.dreamer.reload == "mtime"
    assert cfg.dreamer.strict is True
    assert cfg.dreamer.dir == (toml_path.parent / "nora" / "dreamer").resolve()
    # The runtime half, absent from the file entirely: OFF, no model pinned,
    # no budget declared — and still bounded.
    assert cfg.dreamer.enabled is False
    # The CLASS default, not the shipped file's value: this test deletes the
    # section, so what it measures is what a config predating it gets. Empty
    # still means "fall back to [language].model", which is why that fallback
    # stays wired even though config.toml now states the model outright.
    assert cfg.dreamer.model == ""
    assert cfg.dreamer_model() == cfg.language.model
    assert cfg.dreamer.budgets == {}
    assert cfg.dreamer.budget_for("room_language") == DreamBudgetConfig()


def test_two_configs_do_not_share_one_defaulted_dreamer_section(tmp_path, monkeypatch):
    """A defaulted sub-model is constructed ONCE, at class-definition time, and
    `Config._resolve_paths` mutates it in place through `object.__setattr__`. If
    pydantic handed every `Config` the same instance, the second deployment
    loaded in one process would silently inherit the first one's absolute path
    — and in the eval harness, which loads several configs, that is a corpus
    read from the wrong tree with nothing to notice."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    first = tmp_path / "a" / "config.toml"
    second = tmp_path / "b" / "config.toml"
    for dst in (first, second):
        dst.parent.mkdir(parents=True)
        shutil.copy(ROOT / "config.example.toml", dst)
    a, b = load(first), load(second)
    assert a.dreamer.dir != b.dreamer.dir
    assert a.dreamer.dir == (first.parent / "nora" / "dreamer").resolve()


def test_an_invalid_dreamer_reload_mode_is_a_startup_failure(toml_path):
    toml_path.write_text(toml_path.read_text().replace(
        'dir = "nora/dreamer"\nreload = "mtime"',
        'dir = "nora/dreamer"\nreload = "inotify"'))
    with pytest.raises(ConfigError, match="reload"):
        load(toml_path)


def test_an_unknown_dreamer_key_is_a_startup_failure(toml_path):
    """`extra="forbid"`: a typo'd key is a silent no-op everywhere it is
    tolerated, and this section's whole job is to be edited by hand.

    The probe used to be `enabled = true`, which stopped being an unknown key
    the moment the runtime's own switch landed — and would then have passed for
    the wrong reason (a DUPLICATE `enabled` in one table is a TOML decode
    error, not an `extra="forbid"` refusal). A misspelling of a real key is the
    probe that stays honest as the section grows."""
    toml_path.write_text(toml_path.read_text().replace(
        '[dreamer]\n', '[dreamer]\nenbaled = true\n'))
    with pytest.raises(ConfigError, match="enbaled"):
        load(toml_path)


def test_the_dreamer_runtime_ships_off(toml_path):
    """`enabled` arrives WITH the runtime it gates, shipping False.

    Phase 1 shipped `[dreamer]` with no `enabled` field at all, and said why
    (DEC-128): the section described a CORPUS two already-running adaptors read
    regardless, and a flag that gates nothing is a flag an operator will
    eventually set expecting it to gate something. That argument named its own
    expiry — "it arrives with the Dreamer RUNTIME, shipping false" — and this is
    that arrival.

    Asserted BOTH ways on purpose: the CODE default (what a config.toml written
    before this field existed gets) and the SHIPPED value in the template (what
    someone copying it gets). Either one being true alone would leave the other
    free to turn a background loop on by accident."""
    assert DreamerConfig().enabled is False
    assert load(toml_path).dreamer.enabled is False


def test_the_shipped_dreamer_model_is_the_one_nora_runs(toml_path):
    """`deepseek-v4-flash`, stated explicitly — an OPERATOR DECISION
    (2026-08-21), not a measurement.

    Plan gate D2 asked which model the Dreamer should run and answered "measure
    it". That is closed as unanswerable in its own terms for now: nothing is
    deployed, the fact tier holds zero rows, and a research loop's quality
    cannot be graded against an empty store. A number produced there would look
    like evidence and not be one.

    Running Nora's own model is also what keeps the architecture's claim
    falsifiable — the Dreamer is meant to do better because it is DIFFERENTLY
    SITUATED, not smarter — so this equality is asserted rather than left to
    coincidence."""
    cfg = load(toml_path)
    # Literal carries the `:0731` catalog tag the deployment moved to 2026-08-30.
    assert cfg.dreamer.model == "ollamacloud/deepseek-v4-flash:0731"
    assert cfg.dreamer_model() == cfg.language.model


def test_an_empty_dreamer_model_still_falls_back_to_the_language_model(toml_path):
    """The fallback stays wired even though the shipped file no longer uses it:
    a deployment whose `config.toml` predates this field must keep loading, and
    `extra="forbid"` makes that a real constraint rather than a courtesy."""
    toml_path.write_text(toml_path.read_text().replace(
        'model = "ollamacloud/deepseek-v4-flash:0731"\n\n# One budget block',
        'model = ""\n\n# One budget block',
    ))
    cfg = load(toml_path)
    assert cfg.dreamer.model == ""
    assert cfg.dreamer_model() == cfg.language.model


def test_an_explicit_dreamer_model_overrides_the_language_model(toml_path):
    """Otherwise the fallback would pin the knob open and D2's answer, when it
    arrives, would be a no-op — the same failure
    `test_an_explicit_image_model_wins_over_the_legacy_alias` guards for
    `[media]`'s own alias."""
    toml_path.write_text(toml_path.read_text().replace(
        'model = "ollamacloud/deepseek-v4-flash:0731"\n\n# One budget block',
        'model = "measured/dreamer-tier"\n\n# One budget block',
    ))
    cfg = load(toml_path)
    assert cfg.language.model == "ollamacloud/deepseek-v4-flash:0731"
    assert cfg.dreamer_model() == "measured/dreamer-tier"


def test_an_adaptor_with_no_budget_block_gets_the_default_budget(toml_path):
    """Never a `KeyError` at tick time, and never an unbounded run.

    An adaptor added later — or renamed, or misspelled in the TOML — must still
    be bounded. The failure this replaces has no metric of its own: a `KeyError`
    inside `_dream_once` is caught per scope, counted, and backed off, so it
    would look exactly like a provider outage."""
    cfg = load(toml_path)
    assert "room_portrait" not in cfg.dreamer.budgets
    budget = cfg.dreamer.budget_for("room_portrait")
    assert budget == DreamBudgetConfig()
    assert (budget.turns, budget.total_tokens, budget.output_tokens) == (6, 40_000, 4_000)
    assert budget.timeout_seconds == 120.0


def test_a_partial_budget_block_fills_the_rest_from_the_default(toml_path):
    """A block naming one field must not zero the other three.

    `output_tokens = 0` is not "no limit", it is a subsystem that silently never
    produces anything — and `total_tokens = 0` is a run that ends before it
    starts. Both would be a Dreamer that appears to be on, ticks, and writes
    nothing, with no error anywhere."""
    toml_path.write_text(
        toml_path.read_text() + '\n[dreamer.budgets.room_portrait]\nturns = 3\n')
    budget = load(toml_path).dreamer.budget_for("room_portrait")
    assert budget.turns == 3
    # Against the class defaults *and* against the literals: comparing only to
    # `DreamBudgetConfig()` would stay green if those defaults were themselves
    # zeroed, which is precisely the failure being guarded.
    assert budget.total_tokens == DreamBudgetConfig().total_tokens == 40_000
    assert budget.output_tokens == DreamBudgetConfig().output_tokens == 4_000
    assert budget.timeout_seconds == DreamBudgetConfig().timeout_seconds == 120.0


def test_the_declared_budgets_parse_into_bounded_budgets(toml_path):
    """The template's own blocks, read back through the typed model.

    "It loaded" is not the same as "it loaded what we think": a
    `[dreamer.budgets.x]` block with a typo'd table name would parse as TOML,
    refuse under `extra="forbid"`... but a block that parses into an EMPTY map
    would not, and the section would silently be doing nothing."""
    budgets = load(toml_path).dreamer.budgets
    # Pinned, so ADDING an adaptor is a decision rather than an omission —
    # `budget_for` falls back to the defaults for an unknown key silently, so
    # an adaptor with no block runs on numbers nobody chose. The three `fact-*`
    # are the adaptors that exist today; the two `room_*` are Phase 3's
    # migration targets and name nothing yet.
    assert set(budgets) == {
        "room_language", "room_exchange",
        "fact-candidates", "fact-adjudication", "fact-entity-resolution",
    }
    for name, budget in budgets.items():
        assert budget.turns > 0, name
        assert budget.total_tokens > 0, name
        assert budget.output_tokens > 0, name
        assert budget.timeout_seconds > 0, name


def test_a_zero_budget_field_is_a_startup_failure(toml_path):
    """`ge=1`/`gt=0` on every field, for `LimitsConfig`'s own reason: a cap that
    can be zeroed is a cap an operator can turn into a silent outage by hand,
    and this section's whole job is to be edited by hand."""
    toml_path.write_text(toml_path.read_text().replace(
        '[dreamer.budgets.room_language]\nturns = 6',
        '[dreamer.budgets.room_language]\nturns = 0'))
    with pytest.raises(ConfigError, match="turns"):
        load(toml_path)


def test_an_unknown_budget_key_is_a_startup_failure(toml_path):
    toml_path.write_text(toml_path.read_text().replace(
        '[dreamer.budgets.room_language]\nturns = 6',
        '[dreamer.budgets.room_language]\nturn = 6'))
    with pytest.raises(ConfigError, match="turn"):
        load(toml_path)


def test_every_adaptor_that_exists_has_a_budget_of_its_own(toml_path):
    """`budget_for` falls back to defaults for an unknown key, SILENTLY — its
    own docstring concedes it. So a `[dreamer.budgets.*]` section naming only
    adaptors that do not exist yet leaves the ones that DO on numbers nobody
    chose, and nothing says so (2026-08-21 review, which found exactly that
    shipped).

    Derived from the adaptor classes rather than hand-listed, so an adaptor
    added tomorrow turns this red instead of quietly inheriting a default."""
    from nora.language.fact_adaptor import FactAdaptor, FactCandidateAdaptor

    cfg = load(toml_path)
    for adaptor in (FactCandidateAdaptor, FactAdaptor):
        assert adaptor.name in cfg.dreamer.budgets, (
            f"adaptor {adaptor.name!r} has no [dreamer.budgets.{adaptor.name}] "
            "block and would silently run on the shared defaults"
        )


def test_the_adjudicator_is_budgeted_more_generously_than_the_extractor(toml_path):
    """The reason budgets are PER ADAPTOR rather than one global ceiling
    (keystone §6): proving a claim against the open web is search, read, notice
    the page does not cover the question, search differently — while the
    extractor makes one tool-free call over a message batch. A single number
    would starve the first or overpay for the second, and this asserts the
    config actually expresses that rather than merely allowing it."""
    cfg = load(toml_path)
    extract = cfg.dreamer.budget_for("fact-candidates")
    adjudicate = cfg.dreamer.budget_for("fact-adjudication")

    assert adjudicate.turns > extract.turns
    assert adjudicate.total_tokens > extract.total_tokens
    assert adjudicate.timeout_seconds > extract.timeout_seconds


# --- [standing] (design/dreamer-agent-loop/26-standing §4.0) ----------------

def test_the_standing_section_is_optional_and_ships_inert(toml_path):
    """A config.toml written before this section existed must keep loading
    under `extra="forbid"`, and must load with the tier OFF. This tier has the
    strongest version of that argument in the repo: it is the only optional one
    whose work runs INSIDE the live reply slot, so switching it on lengthens a
    hold real people wait behind."""
    text = toml_path.read_text()
    start = text.index("\n[standing]")
    toml_path.write_text(text[:start] + text[text.index("\n[music]"):])
    cfg = load(toml_path)
    assert cfg.standing.enabled is False
    assert cfg.standing.max_chars == 1200
    assert cfg.standing.window == 40
    assert cfg.standing.model == ""


def test_a_standing_cap_above_the_database_ceiling_refuses_to_start(toml_path):
    """**The startup refusal, not a runtime one.** `max_chars` is a knob and
    `standing_body_fits_the_ceiling_check` is DDL that cannot be one, so a
    config above the ceiling means every fold the sink accepted is refused by
    Postgres — a config error discovered by a production turn, on the live
    reply path, rather than at boot. `policy.toml`'s own refuse-to-start shape,
    applied to a number.
    """
    toml_path.write_text(
        toml_path.read_text().replace("max_chars = 1200", "max_chars = 5000"))
    with pytest.raises(ConfigError, match="ceiling"):
        load(toml_path)


def test_a_standing_cap_exactly_at_the_ceiling_is_allowed(toml_path):
    """The boundary from the accepting side: the ceiling is the largest value a
    writer may pass, not the first one it may not."""
    toml_path.write_text(
        toml_path.read_text().replace("max_chars = 1200", "max_chars = 4000"))
    assert load(toml_path).standing.max_chars == 4000


def test_an_unknown_standing_key_is_a_startup_failure(toml_path):
    """`extra="forbid"`, so `windwo = 40` is a boot failure rather than a
    silently ignored line that leaves the debrief reading forty rows."""
    toml_path.write_text(
        toml_path.read_text().replace("window = 40", "windwo = 40"))
    with pytest.raises(ConfigError):
        load(toml_path)


def test_the_standing_slot_hold_refuses_a_budget_the_timeouts_do_not_fit(toml_path):
    """**The gate that catches an arity error in the one number deciding how
    long a live conversation can stop answering.** It has been wrong four
    times: 2x store calls when there were two, then four, then five, and then
    an `[llm] timeout_seconds` term that bounds nothing here at all.

    44 is under the real hold (20 + 8 + 5 x 5 = 53).
    """
    # `count=0` on `re.sub` means replace ALL, not none — so the line that used
    # to sit here flipped every `enabled = false` in the file (~18 sections:
    # heartbeat, dreamer, facts, notes, presence...) while the comment beneath
    # it claimed the opposite, and made this test depend on every unrelated
    # subsystem tolerating being switched on. Removed; the targeted replace
    # below is the whole mechanism (review finding, 2026-08-27).
    text = _set_key(toml_path.read_text(), "max_slot_hold_seconds", "44.0")
    i = text.index("[standing]")
    text = text[:i] + text[i:].replace("enabled = false", "enabled = true", 1)
    toml_path.write_text(text)
    with pytest.raises(ConfigError, match="hold a conversation's slot"):
        load(toml_path)


def test_the_slot_hold_does_not_count_the_llm_timeout(toml_path):
    """**`[llm] timeout_seconds` bounds NOTHING in the debrief**, and counting
    it made an operator-facing budget claim a 165-second hold where the real
    one is 45.

    The fold's model call is built with `[standing] timeout_seconds` in
    `_DebriefModelClient` AND wrapped in an `asyncio.wait_for` on the same
    value; the `[llm]` figure is never consulted on this path. So an enormous
    reply timeout must not move this gate at all.

    Falsification: put `self.llm.timeout_seconds` back in the sum and this
    refuses to start.

    53, not 45: the real hold gained recognition's classify timeout when that
    stage stopped inheriting `[language]`'s 120s Dreamer ceiling. The number
    here is the WHOLE hold, deliberately — set to exactly what the sum is, so
    a term added to it or dropped from it reddens this test as well as the
    one below.
    """
    text = _set_key(toml_path.read_text(), "max_slot_hold_seconds", "53.0")
    # `timeout_seconds` appears in ten sections, so this one is scoped to
    # `[llm]` by hand — `_set_key` deliberately refuses an ambiguous key rather
    # than silently editing the first or all of them.
    start = text.index("[llm]")
    end = text.index("\n[", start + 1)
    section = _set_key(text[start:end], "timeout_seconds", "600.0")
    text = text[:start] + section + text[end:]
    i = text.index("[standing]")
    text = text[:i] + text[i:].replace("enabled = false", "enabled = true", 1)
    toml_path.write_text(text)

    cfg = load(toml_path)
    assert cfg.llm.timeout_seconds == 600.0, "the edit did not land"
    assert cfg.standing.max_slot_hold_seconds == 53.0


def test_the_slot_hold_check_fires_even_while_standing_is_off(toml_path):
    """**Reversed after review, 2026-08-27.** This used to assert the check was
    INERT while the tier was off, which set a trap: an operator could write an
    impossible budget, boot fine, and only discover it on the restart that
    switched Standing on — the exact moment the sibling window validator's
    docstring names as the worst one to find out.

    The window validator was already ungated on that reasoning. This one's
    failure is worse (a live conversation that stops answering, versus a fold
    reading fewer rows), so gating it was the inconsistency, not ungating it.
    """
    toml_path.write_text(_set_key(toml_path.read_text(), "max_slot_hold_seconds", "1.0"))
    with pytest.raises(ConfigError, match="hold a conversation's slot"):
        load(toml_path)


def test_a_standing_window_larger_than_the_reply_window_refuses_to_start(toml_path):
    """A knob that cannot do what it says is refused, not silently clamped.

    The debrief folds `ctx.transcript` — the rows the REPLY path already
    assembled under `[store] context_window` — so `[standing] window` above
    that number selects exactly the same rows. Accepting it would let the
    config claim a 60-row fold while producing a 40-row one, which is the
    silent-no-op shape this repo refuses everywhere else (an uncapped index, a
    cap mechanism with no cap). Operator asked for 60-80 on 2026-08-27; this is
    what makes the real ceiling visible instead of letting the number lie.
    """
    text = toml_path.read_text()
    i = text.index("[standing]")
    toml_path.write_text(
        text[:i] + text[i:].replace("window = 40", "window = 80", 1))
    with pytest.raises(ConfigError, match="context_window"):
        load(toml_path)


def test_the_standing_window_may_equal_the_reply_window(toml_path):
    """The boundary from the accepting side — the fold seeing everything she
    saw must not be off-by-one refused.

    **Rewritten after review:** the first version asserted only that two config
    defaults were equal, which passes with the validator deleted and would even
    pass with `window = 12` if `context_window` were also 12. It pinned
    nothing. This drives the boundary explicitly: `window == context_window`
    must LOAD, and one more must not, so a `>=`-instead-of-`>` slip goes red.
    """
    text = toml_path.read_text()
    i = text.index("[standing]")
    at_ceiling = text[:i] + text[i:].replace("window = 40", "window = 40", 1)
    toml_path.write_text(at_ceiling)
    cfg = load(toml_path)
    assert cfg.standing.window == cfg.store.context_window == 40

    over = text[:i] + text[i:].replace("window = 40", "window = 41", 1)
    toml_path.write_text(over)
    with pytest.raises(ConfigError, match="context_window"):
        load(toml_path)


def test_a_fact_term_cap_above_the_database_ceiling_refuses_to_start(toml_path):
    """`[standing] max_chars`' refusal, one tier over and for the same reason:
    `fact_term_fits_the_ceiling_check` is DDL and cannot be a knob, so a config
    above it means every fact the adjudicator writes is refused by Postgres —
    in a background tick nobody is watching, rather than at boot."""
    text = toml_path.read_text()
    i = text.index("[facts]")
    toml_path.write_text(
        text[:i] + text[i:].replace(
            "enabled = false",
            f"enabled = false\nmax_term_chars = {FACT_TERM_CEILING_CHARS + 1}",
            1))
    with pytest.raises(ConfigError, match="ceiling"):
        load(toml_path)


def test_a_fact_predicate_cap_above_the_database_ceiling_refuses_to_start(toml_path):
    """The second cap, asserted separately: one validator covering two fields
    passes with either half deleted."""
    text = toml_path.read_text()
    i = text.index("[facts]")
    toml_path.write_text(
        text[:i] + text[i:].replace(
            "enabled = false",
            f"enabled = false\n"
            f"max_predicate_chars = {FACT_PREDICATE_CEILING_CHARS + 1}",
            1))
    with pytest.raises(ConfigError, match="ceiling"):
        load(toml_path)


def test_fact_caps_exactly_at_their_ceilings_are_allowed(toml_path):
    """The boundary from the accepting side: the ceiling is the largest value a
    writer may pass, not the first one it may not.

    **The numbers are IMPORTED, not written here.** Hard-coding 800 and 200 was
    a fourth copy of a constant migration 0041's own docstring says must never
    be repeated — and it made the test green through a raised ceiling while no
    longer testing a boundary at all."""
    text = toml_path.read_text()
    i = text.index("[facts]")
    toml_path.write_text(
        text[:i] + text[i:].replace(
            "enabled = false",
            f"enabled = false\n"
            f"max_term_chars = {FACT_TERM_CEILING_CHARS}\n"
            f"max_predicate_chars = {FACT_PREDICATE_CEILING_CHARS}",
            1))
    cfg = load(toml_path)
    assert cfg.facts.max_term_chars == FACT_TERM_CEILING_CHARS
    assert cfg.facts.max_predicate_chars == FACT_PREDICATE_CEILING_CHARS


def test_the_dispute_classifier_has_its_own_timeout_and_is_in_the_slot_hold(
    toml_path,
):
    """**A 120-second model call sat ahead of every reply.**

    `Recogniser`'s classifier was built with `[language] timeout_seconds`,
    which is 120 because it bounds a DREAMER call in a background tick where a
    slow model costs a tick. Recognition runs on the REPLY PATH — awaited in
    `_run_turn` before context assembly, holding the conversation's slot — so
    inherited there it was two minutes of silence before she answered.
    `[standing]`, the other stage on this slot, bounds itself at 20 and made
    the total a startup gate; this walked past that gate because it had no key
    of its own to put in the sum.

    Asserted BOTH ways round, because the default alone would be satisfied by
    a key nothing reads: the value is in the sum the gate enforces, so raising
    it past the budget refuses to start.
    """
    cfg = load(toml_path)
    assert cfg.facts.dispute_classify_timeout_seconds == 8.0
    assert cfg.language.timeout_seconds != 8.0, (
        "the two must be different values or this proves nothing about which "
        "one the classifier is built with"
    )

    text = _set_key(toml_path.read_text(), "max_slot_hold_seconds", "50.0")
    toml_path.write_text(text)
    with pytest.raises(ConfigError, match="dispute_classify_timeout_seconds"):
        load(toml_path)


def test_an_empty_veto_file_setting_is_no_veto_file(toml_path):
    """**`config.toml` documented this exact form**, and once a
    configured-but-missing veto path started refusing to start, it became a
    boot failure naming a path the operator never wrote.

    Pydantic coerces `""` to `PosixPath(".")` — neither `None` nor a file — so
    an operator uncommenting the repo's own example got "`[facts] veto_file`
    points at `.`, which is not a readable file". The empty string is a
    pydantic artifact rather than an operator intent, so it is normalised
    HERE, in the layer that can tell the difference, rather than at the reader
    which would then have to treat "." as special forever.

    Through a real TOML load, not through `FactsConfig(...)`: the coercion is
    what the file's own value goes through, and constructing the model with a
    Python value takes a different path.
    """
    text = toml_path.read_text()
    i = text.index("[facts]")
    j = text.index("\n[", i + 1)
    section = text[i:j] + '\nveto_file = ""\n'
    toml_path.write_text(text[:i] + section + text[j:])

    cfg = load(toml_path)

    assert cfg.facts.veto_file is None


def test_a_real_veto_path_survives_the_empty_string_normalisation(toml_path):
    """The positive control: a normalisation that returned `None` for every
    value would satisfy the test above while making the veto list
    unconfigurable."""
    text = toml_path.read_text()
    i = text.index("[facts]")
    j = text.index("\n[", i + 1)
    section = text[i:j] + '\nveto_file = "/app/config/veto.txt"\n'
    toml_path.write_text(text[:i] + section + text[j:])

    cfg = load(toml_path)

    assert cfg.facts.veto_file is not None
    assert cfg.facts.veto_file.name == "veto.txt"


def test_the_dreamer_carries_its_own_reasoning_effort(toml_path):
    """**The knowledge tier's first production failure**, and the reason the
    key is the Dreamer's rather than one level up.

    Reasoning is billed as COMPLETION on this model, so `reasoning_effort` is
    a budget spent before any answer text is written. At the tier's "low" the
    extractor reasons its way to `{"candidates": []}` on a transcript that
    plainly contains claims, and to `MaxTokensReachedException` on a real
    room's. At "none" it returns three candidates in 133 tokens.

    Asserted as a SEPARATION, not just a value: `[llm] reasoning_effort` must
    keep its own, because that one is shared with her replies and changing it
    to fix a background pass would change what every turn can say.
    """
    cfg = load(toml_path)

    assert cfg.dreamer.reasoning_effort == "none"

    # The separation asserted STRUCTURALLY, not by pinning `[llm]`'s content.
    # The first version asserted `cfg.llm.reasoning_effort != "none"`, which
    # forbids a value DEC-84 records as the operator's decision with an ADR
    # behind it: restoring it would turn this red claiming the separation was
    # broken when nothing was. What the tier owes is a field of its own that
    # is READ, not a particular value in the other one.
    assert "reasoning_effort" in type(cfg.dreamer).model_fields, (
        "the Dreamer must carry its own field rather than reading [llm]'s"
    )
    assert (
        type(cfg.dreamer).model_fields["reasoning_effort"]
        is not type(cfg.llm).model_fields["reasoning_effort"]
    ), "two fields, so one can move without the other"


def test_an_unset_dreamer_effort_inherits_the_tier(toml_path):
    """`""` is the seventh legal value and means «inherit `[llm]`» —
    `StandingConfig`'s shape, so that switching a tier on changes one thing at
    a time and a config written before this key existed keeps loading."""
    # `\n[dreamer]\n`, not `[dreamer]`: the `[notes]` section's prose contains
    # the literal string `[dreamer] (the runtime exists)`, so an unanchored
    # search finds a COMMENT and the replacement below silently does nothing —
    # which the test then reads as the code failing.
    text = toml_path.read_text()
    i = text.index("\n[dreamer]\n") + 1
    j = text.index("\n[", i + 1)
    section = text[i:j].replace('reasoning_effort = "none"', 'reasoning_effort = ""')
    toml_path.write_text(text[:i] + section + text[j:])

    cfg = load(toml_path)

    assert cfg.dreamer.reasoning_effort == ""


def test_a_misspelt_dreamer_effort_refuses_to_start(toml_path):
    """TYPED for `StandingConfig`'s reason: a typo in a loose `str` reaches
    the provider and comes back a 400 — here inside a background tick, where
    it is one more `unavailable` among many and nobody looks. A boot failure
    is the refuse-to-start posture the rest of the config uses."""
    text = toml_path.read_text()
    i = text.index("\n[dreamer]\n") + 1
    j = text.index("\n[", i + 1)
    section = text[i:j].replace('reasoning_effort = "none"', 'reasoning_effort = "hgh"')
    toml_path.write_text(text[:i] + section + text[j:])

    with pytest.raises(ConfigError, match="reasoning_effort"):
        load(toml_path)


def test_the_adjudicator_has_room_to_converge(toml_path):
    """**MEASURED, and it is the fix for the tier's second production
    failure.** Five runs of the shipped `fact-adjudication` objective against
    the shipped prompt, with a `web_search` schema attached and a plausible
    result returned each turn, converged at turns 9, 15, 14, 10 and 13.

    At `turns = 10`, four of those five would have exhausted the budget — and
    an exhausted loop hands `parse_envelope` whatever text accumulated, which
    the runtime reports as `unavailable` while HOLDING the watermark. A batch
    that was read, re-read forever, under a counter saying the model could not
    be reached.

    Pinned against the OBSERVED MAXIMUM rather than as a round number: if a
    later change makes the loop converge faster, lowering this should be a
    decision with its own measurement rather than a tidy-up, and if the model
    starts needing more this is the number that has to move.
    """
    cfg = load(toml_path)
    budget = cfg.dreamer.budgets["fact-adjudication"]

    assert budget.turns >= 16, (
        "the adjudicator converged at turn 15 in one of five measured runs; "
        "a budget at or below that exhausts the loop and reports an outage "
        "for a batch it had read"
    )
    # The other three are what still bound it, and they are unchanged: a
    # turn budget with nothing beside it is an unbounded research loop.
    assert budget.total_tokens == 120_000
    assert budget.timeout_seconds == 300.0
