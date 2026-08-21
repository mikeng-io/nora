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
    LimitsConfig,
    LLMConfig,
    MediaConfig,
    SpeechConfig,
    load,
)

ROOT = Path(__file__).resolve().parents[1]


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


def test_unknown_nested_key_is_a_startup_failure(toml_path):
    toml_path.write_text(toml_path.read_text().replace(
        'text = "nora.000"', 'text = "nora.000"\ntriger = "oops"'))
    with pytest.raises(ConfigError, match="triger"):
        load(toml_path)


def test_missing_required_key_is_a_startup_failure(toml_path):
    toml_path.write_text(toml_path.read_text().replace('text = "nora.000"\n', ""))
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
    # 120); `tests/test_env_template.py` is what keeps the template and
    # config.toml from drifting apart again.
    assert cfg.llm.model == "ollamacloud/deepseek-v4-flash"
    assert cfg.llm.timeout_seconds == 120
    assert cfg.llm.limits.turns == 20
    assert cfg.llm.max_tokens == 2000
    # engagement owns the trigger; policy is declared even though only one exists
    assert cfg.engagement.policy == "trigger_text"
    assert cfg.engagement.trigger.text == "nora.000"
    assert cfg.engagement.trigger.min_interval_seconds == 5
    # obs groups the three signals
    assert cfg.obs.metrics.port == 9100
    assert cfg.obs.tracing.exporter == "file"
    assert cfg.obs.tracing.enabled is True
    assert cfg.obs.logging.level == "INFO"
    # identity_file replaces persons_file
    assert cfg.identity_file.name == "identity.toml"
    # values dropped from the deleted test_loads_the_example_config, restored here
    assert cfg.llm.limits.total_tokens == 128000
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
    assert cfg.facts.max_rendered_facts == 8
    assert cfg.facts.retention_days == 180


def test_the_notes_section_is_optional_and_ships_inert(toml_path):
    """The note tier's `[notes]`, its `[facts]` sibling's shape and reasoning:
    a config.toml written before this section existed must keep loading under
    `extra="forbid"`, and `enabled` must default to False — the tier ships
    dormant and an operator turns it on deliberately, which is what keeps a
    `<notes>` block out of every tag until somebody decides otherwise.

    One field only, and the ABSENCE is the assertion worth making: there is no
    render cap here, unlike `[facts].max_rendered_facts`. §5.1 makes the index
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
        limits=LimitsConfig(turns=20, total_tokens=128000, output_tokens=12000),
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
    every call, so the tool is present, advertised in the capability block, and
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
    ).replace('model = "ollamacloud/minimax-m3"', 'model = "new/image-model"'))
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
    shape."""
    text = toml_path.read_text()
    start = text.index("\n[dreamer]")
    end = text.index("\n[obs.logging]")
    toml_path.write_text(text[:start] + text[end:])
    cfg = load(toml_path)
    assert cfg.dreamer.reload == "mtime"
    assert cfg.dreamer.strict is True
    assert cfg.dreamer.dir == (toml_path.parent / "nora" / "dreamer").resolve()


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
    tolerated, and this section's whole job is to be edited by hand."""
    toml_path.write_text(toml_path.read_text().replace(
        '[dreamer]\n', '[dreamer]\nenabled = true\n'))
    with pytest.raises(ConfigError):
        load(toml_path)
