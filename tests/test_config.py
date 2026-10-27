"""Frozen config tree (spec D.1), pydantic loader over config.toml."""

import re
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from nora.config import (
    AnalyticsConfig,
    CognitionConfig,
    Config,
    ConfigError,
    DocumentsConfig,
    DreamBudgetConfig,
    DreamerConfig,
    LimitsConfig,
    LLMConfig,
    MediaConfig,
    SpeechConfig,
    load,
)
from nora.domain.standing import STANDING_STORE_CALLS_PER_FOLD
from tests.builders import copy_example_config

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
def toml_path(example_config_path, monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    return example_config_path


def test_unknown_key_is_a_startup_failure(toml_path):
    toml_path.write_text(toml_path.read_text() + '\n[tagg]\ntrigger = "x"\n')
    with pytest.raises(ConfigError, match="tagg"):
        load(toml_path)


def _trigger_line(toml_path) -> str:
    """The `text = "..."` line as the config under test ACTUALLY spells it.

    Read, never hardcoded. These tests do string surgery on the real config to
    build malformed variants, so a literal trigger couples them to a value the
    operator owns: if the trigger changes, every `str.replace` below silently
    becomes a no-op and the "malformed" config stays valid. A no-op replace is the
    dangerous half — it leaves a test asserting against an input it never built.
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
    # Drop the key from ITS OWN SECTION rather than by adjacency to another line:
    # matching "enabled = true\ntext = ..." assumes the two sit next to each other, and
    # a comment between them turns the replace into a silent no-op that leaves
    # `enabled` in place and proves nothing about the default.
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
    # `nora_sender_id` is absent (DEC-102): Nora's id in her own rows comes
    # from the platform (`resolve_identity().primary`), never from a string an
    # operator typed — with two platforms wired one configured name is
    # necessarily wrong for one of them.
    assert not hasattr(cfg.platforms.signal, "nora_sender_id")
    # Honcho client settings are separate from Nora memory settings
    assert cfg.honcho.workspace_id == "nora"
    # store replaces buffer
    assert cfg.store.max_per_conversation == 500
    assert cfg.store.context_window == 40
    # tiers replace default/deep, and carry their own timeout. These two track
    # the DEPLOYMENT (DEC-84); `tests/test_env_template.py` is what keeps the
    # template and config.toml from drifting apart.
    assert cfg.llm.model == "ollamacloud/deepseek-v4-flash:0731"
    assert cfg.llm.timeout_seconds == 360  # DEC-197
    assert cfg.llm.limits.turns == 20
    assert cfg.llm.max_tokens == 2000
    # engagement owns the trigger; policy is declared even though only one exists
    assert cfg.engagement.policy == "trigger_text"
    assert cfg.engagement.trigger.enabled is True
    # The trigger STRING is the operator's, not this test's — pinning it would
    # measure the operator's choice rather than the config shape. Assert the shape.
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
    assert cfg.llm.limits.total_tokens == 1024000
    assert cfg.llm.limits.output_tokens == 12000
    assert cfg.doctrine.strict is True
    assert cfg.doctrine.reload == "mtime"


def test_constructing_config_without_base_context_is_an_error(example_config_path):
    """Paths can only be resolved relative to the config file's directory, so
    constructing a Config without that context must fail loudly rather than
    silently yielding unresolved relative paths."""
    data = tomllib.loads(example_config_path.read_text())
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


def test_instagram_platform_section_is_optional_and_defaults_to_disabled(toml_path):
    """A config.toml written before the Instagram transport existed must still
    load — `extra="forbid"` only forbids UNKNOWN keys, an absent optional
    section is fine (same probe shape as Discord's and WhatsApp's above)."""
    text = toml_path.read_text()
    head, _, tail = text.partition("[platforms.instagram]\n")
    assert tail, "config.example.toml no longer declares [platforms.instagram]"
    remainder = tail.split("\n[", 1)
    toml_path.write_text(head + ("[" + remainder[1] if len(remainder) > 1 else ""))

    cfg = load(toml_path)

    assert cfg.platforms.instagram.enabled is False
    assert cfg.platforms.instagram.base_url == "http://instagram-bridge:18082"


def test_instagram_platform_can_be_enabled(toml_path):
    toml_path.write_text(
        toml_path.read_text().replace(
            "[platforms.instagram]\nenabled = false",
            "[platforms.instagram]\nenabled = true",
        )
    )
    assert load(toml_path).platforms.instagram.enabled is True


def test_an_unknown_instagram_platform_key_is_a_startup_failure(toml_path):
    """A typo'd key must be refused by pydantic's `extra="forbid"`, not merely
    ignored — a silently-dropped line is an operator certain they configured
    something that never took effect. Edited INTO the existing section, for
    the reason Discord's version of this test records."""
    toml_path.write_text(
        toml_path.read_text().replace(
            'base_url = "http://instagram-bridge:18082"', 'base_urll = "x"',
        )
    )
    with pytest.raises(ConfigError, match="base_urll"):
        load(toml_path)


def test_the_messaging_and_social_instagram_blocks_are_SEPARATE_knobs(toml_path):
    """They name the same container on the same port and they are still two
    flags (`nora.config.InstagramPlatformConfig`'s docstring). One
    authenticated session serves both axes, but sending a message and reading
    a story are provisioned and fail independently — a single flag would mean
    a messaging outage silently taking the social axis with it, or the
    reverse. Asserted because the duplication looks like a mistake to anyone
    who has not read the argument."""
    toml_path.write_text(
        toml_path.read_text().replace(
            "[platforms.instagram]\nenabled = false",
            "[platforms.instagram]\nenabled = true",
        )
    )

    cfg = load(toml_path)

    assert cfg.platforms.instagram.enabled is True
    assert cfg.social.instagram.enabled is False


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


def test_the_instagram_social_backend_is_declared_live_in_the_example(toml_path):
    """A wholly commented-out section is not "the default, shown".

    A commented-out `[social.instagram]` header would mean
    `InstagramSocialConfig` is never constructed from `config.example.toml`
    and `extra="forbid"` never runs against it — a typo'd key in that block
    would be found by an operator rather than here. `[social]` above ships a
    live header for exactly this reason, and the probe is the same one the
    other sections use: delete it and the config must still load, with the
    class defaults.
    """
    text = toml_path.read_text()
    head, _, tail = text.partition("\n[social.instagram]\n")
    assert tail, "config.example.toml no longer declares a live [social.instagram]"
    # Live, not commented: the section must actually reach the model.
    cfg = load(toml_path)
    assert cfg.social.instagram.enabled is False
    # And `extra="forbid"` is armed against that block.
    toml_path.write_text(head + "\n[social.instagram]\nnot_a_key = 1\n" + tail)
    with pytest.raises(ConfigError, match="not_a_key"):
        load(toml_path)


def test_the_instagram_summons_ships_off_in_dry_run_and_is_live_in_the_example(toml_path):
    """Off and `dry_run` are two separate brakes
    and both are the shipped state. The header is LIVE in the example, so
    `extra="forbid"` runs against that block (the `[social.instagram]`
    argument above), and deleting the block still loads with the same
    class defaults."""
    cfg = load(toml_path)
    summons = cfg.social.instagram.summons
    assert summons.enabled is False
    assert summons.mode == "dry_run"
    assert summons.poll_seconds is None  # deprecated, read by nothing
    assert summons.max_age_minutes == 60.0
    assert summons.handle == "noraexample"

    text = toml_path.read_text()
    head, _, tail = text.partition("\n[social.instagram.summons]\n")
    assert tail, "config.example.toml no longer declares a live [social.instagram.summons]"
    toml_path.write_text(head + "\n[social.instagram.summons]\nnot_a_key = 1\n" + tail)
    with pytest.raises(ConfigError, match="not_a_key"):
        load(toml_path)

    remainder = tail.split("\n[", 1)
    toml_path.write_text(head + ("\n[" + remainder[1] if len(remainder) > 1 else ""))
    assert load(toml_path).social.instagram.summons == summons


def _in_section(text: str, header: str, old: str, new: str) -> str:
    """`old` -> `new`, first occurrence AFTER `header`, asserting it landed
    (`_set_key`'s reason: a replace that matches nothing is a silent no-op)."""
    head, sep, tail = text.partition(f"\n{header}\n")
    assert sep, f"no {header} in the example"
    assert old in tail, f"{old!r} not under {header}"
    return head + sep + tail.replace(old, new, 1)


def test_the_deprecated_summons_poll_interval_is_still_accepted(toml_path):
    """The live config.toml sets `poll_seconds = 120` (the retired poller's
    interval) and the model forbids unknown keys: it must still load, and
    nothing reads it. Deleting the field would make that config fail to start."""
    text = _in_section(
        toml_path.read_text(), "[social.instagram.summons]",
        "enabled = false\n", "enabled = false\npoll_seconds = 120\n",
    )
    toml_path.write_text(text)
    assert load(toml_path).social.instagram.summons.poll_seconds == 120


def test_a_shortener_section_is_refused(toml_path):
    """DEC-296: the shortener has no key. A file that sets `[shortener]` is a
    mistake to report, not a setting to ignore."""
    toml_path.write_text(toml_path.read_text() + '\n[shortener]\nenabled = true\n')
    with pytest.raises(ConfigError, match="shortener"):
        load(toml_path)


def test_the_summons_mode_is_a_closed_set(toml_path):
    toml_path.write_text(_in_section(
        toml_path.read_text(), "[social.instagram.summons]",
        'mode = "dry_run"', 'mode = "on"',
    ))
    with pytest.raises(ConfigError, match="mode"):
        load(toml_path)


def test_a_summons_without_the_instagram_backend_is_refused_at_load(toml_path):
    """A summons is read and answered over [social.instagram]'s bridge; on
    alone it would be a poller with nothing behind it."""
    on = _in_section(
        toml_path.read_text(), "[social.instagram.summons]",
        "enabled = false", "enabled = true",
    )
    toml_path.write_text(on)
    with pytest.raises(ConfigError, match=r"social.instagram.summons"):
        load(toml_path)
    toml_path.write_text(_in_section(
        on, "[social.instagram]", "enabled = false", "enabled = true",
    ))
    cfg = load(toml_path)
    assert cfg.social.instagram.enabled is True
    assert cfg.social.instagram.summons.enabled is True


def test_the_social_poller_ships_off_and_is_live_in_the_example(toml_path):
    """It reads through the login her Instagram messaging uses, so off is the
    shipped state. The header is LIVE in the example, so `extra="forbid"` runs
    against that block."""
    cfg = load(toml_path)
    poll = cfg.social.poll
    assert poll.enabled is False
    assert (poll.tick_seconds, poll.floor_minutes, poll.ceiling_hours) == (600.0, 30.0, 6.0)
    assert (poll.hourly_budget, poll.back_off_start_minutes, poll.back_off_max_hours) == (
        60, 15.0, 6.0,
    )

    text = toml_path.read_text()
    head, _, tail = text.partition("\n[social.poll]\n")
    assert tail, "config.example.toml no longer declares a live [social.poll]"
    toml_path.write_text(head + "\n[social.poll]\nnot_a_key = 1\n" + tail)
    with pytest.raises(ConfigError, match="not_a_key"):
        load(toml_path)


def test_a_social_poller_without_the_instagram_backend_is_refused_at_load(toml_path):
    on = _in_section(toml_path.read_text(), "[social.poll]", "enabled = false", "enabled = true")
    toml_path.write_text(on)
    with pytest.raises(ConfigError, match=r"social.poll"):
        load(toml_path)
    toml_path.write_text(_in_section(on, "[social.instagram]", "enabled = false", "enabled = true"))
    assert load(toml_path).social.poll.enabled is True


def test_a_social_poll_floor_above_its_ceiling_is_refused(toml_path):
    toml_path.write_text(_in_section(
        toml_path.read_text(), "[social.poll]", "enabled = false",
        "enabled = false\nfloor_minutes = 400.0",
    ))
    with pytest.raises(ConfigError, match="floor_minutes"):
        load(toml_path)


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
    # The code default MATCHES the shipped config: a drift would make the eval
    # harness measure a horizon production does not have (`nora.app.offline`
    # builds `ToolRegistry()` with no caps). This assertion keeps the two equal.
    assert cfg.reminders.max_horizon_days == 365
    assert cfg.reminders.min_lead_seconds > cfg.heartbeat.tick_seconds


def test_the_notes_section_is_optional_and_ships_inert(toml_path):
    """The note tier's `[notes]`, its `[facts]` sibling's shape and reasoning:
    a config.toml written before this section existed must keep loading under
    `extra="forbid"`, and `enabled` must default to False — the tier ships
    dormant and an operator turns it on deliberately, which is what keeps a
    `<notes>` block out of every tag until somebody decides otherwise.

    One field only, and the ABSENCE is the assertion worth making: there is no
    render cap here, unlike `[facts]`' two slice caps. The index is uncapped on
    purpose — a note missing from it is invisible to her, and invisible is
    indistinguishable from absent — so a top-N knob would be a supported way to
    reintroduce that failure. The per-entry caps in `nora.domain.notes` are where
    the prompt budget is held instead."""
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


def test_a_minimum_lead_inside_the_tick_is_refused(toml_path):
    """The tick/lead relation is a rule, not a comment.

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
    ids. Circle membership lives in identity.toml, so config.toml
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


def test_min_trip_fixes_defaults_to_two_and_refuses_a_one_fix_trip():
    """Tier 5. `extract_trips`'s own noise gate: a run shorter than this is
    dropped rather than written as a trip. `ge=2`, not `ge=1` — a one-fix
    "trip" has no pair to run `derive_speed_kmh`/`haversine_distance_m`
    over, so its `distance_m` and `avg_speed_kmh` are both structurally
    0.0; admitting one would put a fabricated zero-speed sample into
    `presence_corridor_speed`'s percentiles, which is precisely the
    "never assert what you cannot ground" rule.

    Life360's 30-min cadence means a real 30km drive produces only 2 movement
    fixes between stays, so a 3-fix floor would drop those trips as noise. 2 is
    the minimum that still distinguishes movement from a stray fix."""
    assert AnalyticsConfig().min_trip_fixes == 2
    assert AnalyticsConfig(min_trip_fixes=2).min_trip_fixes == 2
    with pytest.raises(ValidationError):
        AnalyticsConfig(min_trip_fixes=1)


def test_corridor_cluster_batch_limit_defaults_to_the_measured_bound():
    """Tier 5. How many not-yet-clustered trips one corridor-clustering
    pass may re-cluster. `cluster_trips` is O(n^2) over an edit-distance
    metric, which is why the pool is capped; 500 keeps a pass under about 2%
    of a tick."""
    assert AnalyticsConfig().corridor_cluster_batch_limit == 500
    assert AnalyticsConfig(corridor_cluster_batch_limit=50).corridor_cluster_batch_limit == 50
    with pytest.raises(ValidationError):
        AnalyticsConfig(corridor_cluster_batch_limit=0)


def test_max_trip_duration_seconds_defaults_to_three_hours():
    """Tier 5. Trip settlement: the safety valve against a stuck `is_driving`
    signal — if Life360 ever reports `True` indefinitely (a stale flag, a phone
    that stopped updating mid-drive), a trip could otherwise never settle and
    hold the watermark back forever. The default is a judgment call, not a
    measured value: 3 hours is generous enough to cover a real long drive
    without leaving a stuck signal unbounded."""
    assert AnalyticsConfig().max_trip_duration_seconds == 10800.0
    assert AnalyticsConfig(max_trip_duration_seconds=7200.0).max_trip_duration_seconds == 7200.0


def test_a_cluster_batch_limit_below_corridor_min_samples_is_refused():
    """Fail-closed on a config that could never mint a corridor. A pool
    smaller than `corridor_min_samples` cannot arithmetically hold enough
    similar trips for `cluster_trips` to call any of them a cluster — a
    failure with no log line and no metric of its own, only a counter that
    never moves."""
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
    the same class as a `peer_card` endpoint that answers `[]` forever.
    Validated against the LIVE registry, so it cannot go stale when a tool is
    added or removed."""
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
    back would prove only that TOML parses.
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

    `MediaConfig` is `extra="forbid"`, so a config naming a key the image's
    `Config` lacks fails to start. A live config.toml naming only `vision_model`
    must therefore keep loading AND keep describing images — so `vision_model`
    stays as the image describer's deprecated alias.
    """
    # A genuinely pre-split file: the `[media.describe.*]` tables do not exist at
    # all, and `vision_model` is the only place a model is named.
    # Anchored on the TABLE HEADER at the start of a line, not on the bare
    # string: the `[media]` comment block names `[media.describe.image]` in
    # prose, and partitioning on that mention would truncate the file above
    # `[doctrine]`.
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


# --- [dreamer] -------------------------------------------------------------


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
    # still means "fall back to [llm].model", which is why that fallback stays
    # wired even though config.toml states the model outright.
    assert cfg.dreamer.model == ""
    assert cfg.dreamer_model() == cfg.llm.model
    # `0` is the inherit sentinel, so a config predating
    # this field still gets the tier's per-call ceiling rather than a
    # model asked to emit nothing.
    assert cfg.dreamer.max_tokens == 0
    assert cfg.dreamer_max_tokens() == cfg.llm.max_tokens
    assert cfg.dreamer.budgets == {}
    assert cfg.dreamer.budget_for("note-curation") == DreamBudgetConfig()
    # The clock's own five numbers, which moved here from the deleted
    # `[language]`: a config predating the move still gets a bounded loop.
    assert cfg.dreamer.tick_seconds == 300.0
    assert cfg.dreamer.safety_lag == 300
    assert cfg.dreamer.quiet_seconds == 1800.0
    assert cfg.dreamer.max_age_seconds == 86_400.0
    assert cfg.dreamer.batch_limit == 400
    # The wall clock around a whole unit: a config predating it
    # still gets a bounded `_run_adaptor`, not an unbounded one — `None`
    # (unset, the class default) DERIVES a real number rather than reading as
    # "no bound" (`Config.dreamer_max_unit_hold_seconds`).
    assert cfg.dreamer.max_unit_hold_seconds is None
    assert cfg.dreamer_max_unit_hold_seconds() > cfg._dream_unit_hold_known_minimum()


def test_two_configs_do_not_share_one_defaulted_dreamer_section(tmp_path, monkeypatch):
    """A defaulted sub-model is constructed ONCE, at class-definition time, and
    `Config._resolve_paths` mutates it in place through `object.__setattr__`. If
    pydantic handed every `Config` the same instance, the second deployment
    loaded in one process would silently inherit the first one's absolute path
    — and in the eval harness, which loads several configs, that is a corpus
    read from the wrong tree with nothing to notice."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    for sub in ("a", "b"):
        (tmp_path / sub).mkdir()
    first = copy_example_config(tmp_path / "a")
    second = copy_example_config(tmp_path / "b")
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

    The probe is a misspelling of a real key, not `enabled = true`: a DUPLICATE
    `enabled` in one table is a TOML decode error, not an `extra="forbid"`
    refusal, so it would pass for the wrong reason."""
    toml_path.write_text(toml_path.read_text().replace(
        '[dreamer]\n', '[dreamer]\nenbaled = true\n'))
    with pytest.raises(ConfigError, match="enbaled"):
        load(toml_path)


def test_the_dreamer_runtime_ships_off(toml_path):
    """`enabled` gates the Dreamer runtime and ships False (DEC-128).

    Asserted BOTH ways on purpose: the CODE default (what a config.toml written
    before this field existed gets) and the SHIPPED value in the template (what
    someone copying it gets). Either one being true alone would leave the other
    free to turn a background loop on by accident."""
    assert DreamerConfig().enabled is False
    assert load(toml_path).dreamer.enabled is False


def test_the_shipped_dreamer_model_is_the_one_nora_runs(toml_path):
    """`deepseek-v4-flash`, stated explicitly — an operator decision, not a
    measurement.

    Running Nora's own model keeps the architecture's claim falsifiable — the
    Dreamer is meant to do better because it is DIFFERENTLY SITUATED, not
    smarter — so this equality is asserted rather than left to coincidence."""
    cfg = load(toml_path)
    # The literal carries the deployment's `:0731` catalog tag.
    assert cfg.dreamer.model == "ollamacloud/deepseek-v4-flash:0731"
    assert cfg.dreamer_model() == cfg.llm.model


def test_an_adaptor_with_no_budget_block_gets_the_default_budget(toml_path):
    """Never a `KeyError` at tick time, and never an unbounded run.

    An adaptor added, renamed or misspelled in the TOML must still
    be bounded. The failure this replaces has no metric of its own: a `KeyError`
    inside the dream tick is caught per unit, counted, and backed off, so it
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




def test_a_zero_budget_field_is_a_startup_failure(toml_path):
    """`ge=1`/`gt=0` on every field, for `LimitsConfig`'s own reason: a cap that
    can be zeroed is a cap an operator can turn into a silent outage by hand,
    and this section's whole job is to be edited by hand.

    The block is APPENDED here rather than edited in place, so it does not have
    to know which blocks the template already declares (see the test above)."""
    toml_path.write_text(
        toml_path.read_text()
        + "\n[dreamer.budgets.note-curation]\nturns = 0\n")
    with pytest.raises(ConfigError, match="turns"):
        load(toml_path)


def test_an_unknown_budget_key_is_a_startup_failure(toml_path):
    toml_path.write_text(
        toml_path.read_text()
        + "\n[dreamer.budgets.note-curation]\nturn = 6\n")
    with pytest.raises(ConfigError, match="turn"):
        load(toml_path)


def test_a_declared_budget_block_parses_into_a_bounded_budget(toml_path):
    """The positive control for the two refusals above, and for the key-set
    assertion two tests up: a LEGAL block must actually reach `budget_for`.

    "It loaded" is not the same as "it loaded what we think": a
    `[dreamer.budgets.x]` block with a typo'd table name would parse as TOML,
    refuse under `extra="forbid"`... but a block that parses into an EMPTY map
    would not, and the section would silently be doing nothing."""
    toml_path.write_text(
        toml_path.read_text()
        + "\n[dreamer.budgets.note-curation]\n"
        "turns = 7\ntotal_tokens = 50000\n"
        "output_tokens = 5000\ntimeout_seconds = 90\n")
    budget = load(toml_path).dreamer.budget_for("note-curation")
    assert (budget.turns, budget.total_tokens, budget.output_tokens) == (
        7, 50_000, 5_000)
    assert budget.timeout_seconds == 90.0


# --- [standing] --------------------------------------------------------------



# --- [participation] --------------------------------------------------------

def test_the_participation_section_is_optional_and_ships_inert(toml_path):
    """A config.toml written before this section existed must keep loading
    under `extra="forbid"`, with the occasion OFF. Off, no quiescence window
    is ever evaluated; on, participation STILL engages nowhere until
    policy.toml opts a room in (`participation = true`) — the two grants are
    independent on purpose."""
    text = toml_path.read_text()
    start = text.index("\n[participation]")
    toml_path.write_text(text[:start] + text[text.index("\n[engagement]"):])
    cfg = load(toml_path)
    assert cfg.participation.enabled is False
    assert cfg.participation.settle_seconds == 10
    assert cfg.participation.cooldown_minutes == 10
    # The review surface ships OFF too, and with no destination.
    assert cfg.participation.shadow is False
    assert cfg.participation.shadow_channel == ""
    assert cfg.participation.shadow_platform == "discord"


def test_shadow_mode_requires_a_channel(toml_path):
    """`shadow = true` with no `shadow_channel` is a config error, not a
    runtime condition — a mirror with nowhere to post would fail soft on every
    determination, which is exactly the silent-instrumentation shape R-5
    forbids. Refused at load."""
    text = toml_path.read_text()
    start = text.index("\n[participation]")
    end = text.index("\n[engagement]")
    toml_path.write_text(
        text[:start]
        + '\n[participation]\nenabled = true\nshadow = true\n'
        + text[end:]
    )
    with pytest.raises(Exception, match="shadow_channel"):
        load(toml_path)


def test_a_discord_shadow_channel_needs_its_guild_half(toml_path):
    """A bare channel id such as `shadow_channel = "100000000000000207"` is
    refused: Discord addresses a room as `"<guild_id>/<channel_id>"`.

    `DiscordSender.send` partitions a bare snowflake into
    `guild_id="100000000000000207"`, `channel_id=""` and posts to
    `/channels//messages`, which Discord answers with 405.

    A set-but-unsendable channel fails soft on every determination exactly as
    an unset one does, so it is the SAME config error and is refused in the
    same place. It has to be caught here: the mirror is deliberately
    fail-soft — review traffic must never break the path it reviews — so the
    one loud place left is the load that reads the value.
    """
    text = toml_path.read_text()
    start = text.index("\n[participation]")
    end = text.index("\n[engagement]")

    def _with(channel: str) -> str:
        return (
            text[:start]
            + "\n[participation]\nenabled = true\nshadow = true\n"
            + f'shadow_channel = "{channel}"\nshadow_platform = "discord"\n'
            + text[end:]
        )

    toml_path.write_text(_with("100000000000000207"))
    with pytest.raises(Exception, match="guild_id"):
        load(toml_path)

    toml_path.write_text(_with("100000000000000203/100000000000000207"))
    cfg = load(toml_path)
    assert cfg.participation.shadow_channel == "100000000000000203/100000000000000207"


def test_a_shadow_channel_on_a_platform_without_guilds_is_not_shape_checked(toml_path):
    """Only Discord's ids have two halves. A Signal conversation id is one
    opaque base64 string in which a `/` means nothing, so asserting a shape
    there would refuse the very ids that platform issues."""
    text = toml_path.read_text()
    start = text.index("\n[participation]")
    end = text.index("\n[engagement]")
    toml_path.write_text(
        text[:start]
        + "\n[participation]\nenabled = true\nshadow = true\n"
        + 'shadow_channel = "Lf2L9zqeNUahRsOz6BA+sTB8aWfbuvqu9vOhpvmJi34="\n'
        + 'shadow_platform = "signal"\n'
        + text[end:]
    )
    cfg = load(toml_path)
    assert cfg.participation.shadow_platform == "signal"


def test_the_relevance_gate_ships_inert(toml_path):
    """The relevance gate is OFF, and in review mode when on."""
    cfg = load(toml_path)
    assert cfg.relevance_gate.enabled is False
    assert cfg.relevance_gate.model == "nora-gate"
    assert cfg.relevance_gate.reasoning_effort == "none"
    assert cfg.relevance_gate.max_tokens == 512
    assert cfg.relevance_gate.transcript_rows == 40
    # Shadow is the shipped POSTURE, unlike [participation]'s: a deployment
    # that turns the gate on without thinking about review gets review, not
    # live admission.
    assert cfg.relevance_gate.shadow is True
    assert cfg.relevance_gate.shadow_channel == ""


def test_the_gate_shadow_requires_a_channel_once_the_gate_is_on(toml_path):
    """A review surface with nowhere to post would fail soft on every GO —
    the silent-instrumentation shape R-5 forbids. Gated on `enabled` as well
    as `shadow`, unlike [participation]'s otherwise identical check, because
    `shadow` DEFAULTS true here and the unconfigured instance would otherwise
    refuse at import time."""
    text = toml_path.read_text()
    start = text.index("\n[relevance_gate]")
    end = text.index("\n[engagement]")
    toml_path.write_text(
        text[:start]
        + "\n[relevance_gate]\nenabled = true\nshadow = true\n"
        + text[end:]
    )
    with pytest.raises(Exception, match="shadow_channel"):
        load(toml_path)


def test_the_gate_review_channel_needs_its_guild_half_too(toml_path):
    """The gate's twin of the participation check, and the one that actually
    fired: on the deployment BOTH sections held the same bare snowflake, and
    the gate is the one in shadow mode, so it is the one whose GO verdicts
    went nowhere. Gated on `enabled` for the same reason the emptiness check
    is — a disabled gate posts nowhere, so its unshaped channel is not yet a
    contradiction."""
    text = toml_path.read_text()
    start = text.index("\n[relevance_gate]")
    end = text.index("\n[engagement]")
    head = text[:start] + "\n[relevance_gate]\nshadow = true\n"
    tail = 'shadow_channel = "100000000000000207"\nshadow_platform = "discord"\n'

    # Gate OFF: nothing is posted, so nothing is asserted about the channel.
    toml_path.write_text(head + tail + text[end:])
    assert load(toml_path).relevance_gate.enabled is False

    toml_path.write_text(head + "enabled = true\n" + tail + text[end:])
    with pytest.raises(Exception, match="guild_id"):
        load(toml_path)


def test_the_gate_refuses_to_start_with_no_turn_behind_it(toml_path):
    """The cross-section refusal, and the reason it must be a refusal rather
    than a log: the failure it prevents is INVISIBLE. With the gate on and
    `[participation] enabled = false`, every window is judged and every GO is
    counted while the room hears nothing — which reads exactly like a gate
    correctly declining."""
    text = toml_path.read_text()
    start = text.index("\n[relevance_gate]")
    end = text.index("\n[engagement]")
    toml_path.write_text(
        text[:start]
        + '\n[relevance_gate]\nenabled = true\nshadow = false\n'
        + text[end:]
    )
    with pytest.raises(Exception, match="participation"):
        load(toml_path)


def test_an_unknown_reasoning_effort_refuses_to_start():
    """Narrowed at composition, so a typo cannot reach the provider and come
    back a 400 on the first window that closes."""
    from nora.app.composition import _reasoning_effort
    assert _reasoning_effort("none", source="x") == "none"
    with pytest.raises(Exception, match="reasoning effort"):
        _reasoning_effort("nope", source="[relevance_gate] reasoning_effort")


def test_a_standing_cap_above_the_database_ceiling_refuses_to_start(toml_path):
    """**The startup refusal, not a runtime one.** `max_chars` is a knob and
    `standing_body_fits_the_ceiling_check` is DDL that cannot be one, so a
    config above the ceiling means every fold the sink accepted is refused by
    Postgres — a config error discovered on the live reply path rather than
    at boot. `policy.toml`'s own refuse-to-start shape, applied to a number.
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
    """`extra="forbid"`, so `fold_batc = 200` is a boot failure rather than a
    silently ignored line that leaves the fold batch meaning something else."""
    # `_set_key` first (asserts the edit landed) so the typo surgery below
    # cannot silently no-op when the operator changes the fold_batch value —
    # the exact failure this file's `_set_key` docstring exists to prevent.
    text = _set_key(toml_path.read_text(), "fold_batch", "200")
    toml_path.write_text(text.replace("fold_batch = 200", "fold_batc = 200"))
    with pytest.raises(ConfigError):
        load(toml_path)


def _real_slot_hold(toml_path) -> float:
    """The hold `Config._check_standing_slot_hold` actually computes, DERIVED
    from the same two values and the same constant it uses.

    A hand-computed literal cannot notice a term entering or leaving the sum;
    a derived value can.
    """
    cfg = load(toml_path)
    return (
        cfg.standing.timeout_seconds
        + STANDING_STORE_CALLS_PER_FOLD * cfg.standing.store_timeout_seconds
    )


def _uncomment_and_set(text: str, key: str, value: str) -> str:
    """Turn a commented-out `# key = ...` template line into an active
    `key = value` line, and ASSERT the edit landed — `_set_key`'s own reason,
    one step earlier. `[dreamer] max_unit_hold_seconds` ships COMMENTED OUT (a fixed
    literal would go stale against the deployment's own numbers), so a test that
    wants an EXPLICIT override has to uncomment it first — `_set_key`'s `^key = .*$`
    regex does not match a line starting with `# `."""
    new, n = re.subn(
        rf"(?m)^# ?{re.escape(key)} = .*$", f"{key} = {value}", text,
    )
    assert n == 1, f"commented {key} not found exactly once ({n} matches)"
    return new




def test_the_dream_unit_hold_refuses_an_explicit_budget_the_timeouts_do_not_fit(
    toml_path,
):
    """`[dreamer] max_unit_hold_seconds`, EXPLICITLY SET, must PROVABLY
    exceed the known-bounded minimum — the model budget, both store bounds, and
    the media join's own bound — the same shape `[standing]
    max_slot_hold_seconds` checks one tier over.

    Set one second UNDER the real minimum, DERIVED from `Config.
    _dream_unit_hold_known_minimum` rather than a literal, so this stays a
    real probe of the formula rather than of whatever number was typed the
    day it was written."""
    known_minimum = load(toml_path)._dream_unit_hold_known_minimum()
    text = _uncomment_and_set(
        toml_path.read_text(), "max_unit_hold_seconds", f"{known_minimum - 1:g}",
    )
    toml_path.write_text(text)
    with pytest.raises(ConfigError, match="max_unit_hold_seconds"):
        load(toml_path)


def test_the_dream_unit_hold_check_fires_even_while_the_dreamer_is_off(toml_path):
    """**Not gated on `[dreamer] enabled`** — `[standing]`'s own sibling check
    deliberately matches: a number that lies about the deployment is wrong on
    the page whether or not the loop is running, and the moment `enabled`
    flips to `true` is the worst one to discover an impossible budget."""
    toml_path.write_text(
        _uncomment_and_set(toml_path.read_text(), "max_unit_hold_seconds", "1.0"))
    with pytest.raises(ConfigError, match="max_unit_hold_seconds"):
        load(toml_path)


def test_an_explicit_dream_unit_hold_above_the_minimum_is_accepted(toml_path):
    """The POSITIVE control for an explicit override: a value comfortably
    above the known minimum still loads (the check is `<=`, not a refusal of
    every explicit value)."""
    known_minimum = load(toml_path)._dream_unit_hold_known_minimum()
    text = _uncomment_and_set(
        toml_path.read_text(), "max_unit_hold_seconds", f"{known_minimum + 1:g}",
    )
    toml_path.write_text(text)
    cfg = load(toml_path)
    assert cfg.dreamer.max_unit_hold_seconds == known_minimum + 1
    assert cfg.dreamer_max_unit_hold_seconds() == known_minimum + 1




def test_the_standing_slot_hold_refuses_a_budget_the_timeouts_do_not_fit(toml_path):
    """**The gate that catches an arity error in the one number deciding how
    long a live conversation can stop answering.**

    The budget is derived as one second UNDER the real hold, so it stays
    under whatever the sum currently is rather than under whatever it was the
    day someone typed a literal.
    """
    # Only the targeted replace below switches the tier on. A blanket `re.sub`
    # (`count=0` means ALL) would flip every `enabled = false` in the file and make
    # this test depend on every unrelated subsystem tolerating being switched on.
    text = _set_key(
        toml_path.read_text(), "max_slot_hold_seconds",
        f"{_real_slot_hold(toml_path) - 1:g}",
    )
    i = text.index("[standing]")
    text = text[:i] + text[i:].replace("enabled = false", "enabled = true", 1)
    toml_path.write_text(text)
    with pytest.raises(ConfigError, match="hold a conversation's slot"):
        load(toml_path)


def test_the_slot_hold_does_not_count_the_llm_timeout(toml_path):
    """**`[llm] timeout_seconds` bounds NOTHING in the debrief**, and counting
    it would make an operator-facing budget claim a hold longer than the real one.

    The fold's model call is built with `[standing] timeout_seconds` in
    `_DebriefModelClient` AND wrapped in an `asyncio.wait_for` on the same
    value; the `[llm]` figure is never consulted on this path. So an enormous
    reply timeout must not move this gate at all.

    Falsification: put `self.llm.timeout_seconds` back in the sum and this
    refuses to start.

    The budget is set to EXACTLY the real hold, deliberately, so a term added
    to the sum or dropped from it reddens this test as well as the one above.
    It is DERIVED rather than typed: a literal goes stale when the sum changes
    and then passes silently, and a gate that cannot notice the thing it exists
    to notice is worse than no gate, because it reads as coverage.
    """
    text = _set_key(
        toml_path.read_text(), "max_slot_hold_seconds",
        f"{_real_slot_hold(toml_path):g}",
    )
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
    # The POSITIVE CONTROL for the whole test: it must have loaded with the
    # budget set to exactly the real hold, not to some larger number that would
    # have let an uncounted term hide underneath it.
    assert cfg.standing.max_slot_hold_seconds == (
        cfg.standing.timeout_seconds
        + STANDING_STORE_CALLS_PER_FOLD * cfg.standing.store_timeout_seconds
    )


def test_the_slot_hold_check_fires_even_while_standing_is_off(toml_path):
    """The check is NOT gated on the tier being enabled: otherwise an operator
    could write an impossible budget, boot fine, and only discover it on the
    restart that switched Standing on.

    The window validator is ungated on the same reasoning, and this failure is
    worse (a live conversation that stops answering, versus a fold reading
    fewer rows).
    """
    toml_path.write_text(_set_key(toml_path.read_text(), "max_slot_hold_seconds", "1.0"))
    with pytest.raises(ConfigError, match="hold a conversation's slot"):
        load(toml_path)


def test_a_standing_fold_batch_bigger_than_the_reply_window_is_accepted(toml_path):
    """There is no `window` cap relationship: `fold_batch` is a BATCH SIZE, not a
    recency window — the fold reads the NEXT `fold_batch` rows since its row-id
    watermark, which may reach further back than `[store] context_window` (that is
    the whole point; the watermark replaces the reply path's window). A value larger
    than `context_window` must LOAD, or the read could not do its job."""
    # `_set_key` asserts the edit landed — a hardcoded `str.replace` on the
    # shipped value would silently no-op if the operator changes fold_batch.
    toml_path.write_text(_set_key(toml_path.read_text(), "fold_batch", "400"))
    cfg = load(toml_path)
    assert cfg.standing.fold_batch == 400
    assert cfg.standing.fold_batch > cfg.store.context_window


def test_a_standing_fold_batch_of_zero_refuses_to_start(toml_path):
    """`fold_batch` is a batch size, so the one hard bound it has is the
    domain one: a fold that reads zero rows is a fold that does nothing, and a
    `ge=1` constraint is what refuses it at startup rather than at the first
    fold."""
    toml_path.write_text(_set_key(toml_path.read_text(), "fold_batch", "0"))
    with pytest.raises(ConfigError):
        load(toml_path)


def test_the_dreamer_carries_its_own_reasoning_effort(toml_path):
    """The key is the Dreamer's rather than one level up.

    Reasoning is billed as COMPLETION on this model, so `reasoning_effort` is
    a budget spent before any answer text is written; at "low" the extractor
    can reason its way to an empty candidate list or to
    `MaxTokensReachedException`.

    Asserted as a SEPARATION, not just a value: `[llm] reasoning_effort` must
    keep its own, because that one is shared with her replies and changing it
    to fix a background pass would change what every turn can say.
    """
    cfg = load(toml_path)

    assert cfg.dreamer.reasoning_effort == "none"

    # The separation asserted STRUCTURALLY, not by pinning `[llm]`'s content.
    # Asserting `cfg.llm.reasoning_effort != "none"` would forbid a value DEC-84
    # records as the operator's decision: restoring it would turn this red claiming
    # the separation was broken when nothing was. What the tier owes is a field of
    # its own that is READ, not a particular value in the other one.
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



def _with_sidecars(text: str, entries: str) -> str:
    """Rewrite the template's `sidecars = []` to the given list literal."""
    return _set_key(text, "sidecars", entries)


def test_two_guilds_each_with_a_controller_load(toml_path):
    """Multi-guild: there is deliberately no cap. The single-guild bound belongs to
    a CONTROLLER and each enforces its own, so two entries here mean two
    controller containers."""
    toml_path.write_text(_with_sidecars(
        toml_path.read_text(),
        '[{ guild = "g1", endpoint = "http://voice-sidecar:4600" },'
        ' { guild = "g2", endpoint = "http://voice-sidecar-b:4600" }]',
    ))

    cfg = load(toml_path)

    assert cfg.speech.tts.sidecars_by_guild == {
        "g1": "http://voice-sidecar:4600",
        "g2": "http://voice-sidecar-b:4600",
    }


def test_a_guild_channel_pair_as_a_key_refuses_to_start(toml_path):
    """An operator carrying forward either earlier key must SEE that the shape
    changed — `voice_channel_ids` named channels, `voice_guild_ids` named
    guilds, and neither is a key. A pair silently truncated to its guild half
    would look like it kept working while meaning something else."""
    toml_path.write_text(_with_sidecars(
        toml_path.read_text(),
        '[{ guild = "g1/c1", endpoint = "http://voice-sidecar:4600" }]',
    ))

    with pytest.raises(ConfigError, match="bare Discord guild id"):
        load(toml_path)


def test_a_guild_with_an_empty_endpoint_refuses_to_start(toml_path):
    """An entry with no controller behind it would admit a join the hub then
    cannot make. Leaving the guild out is how you say "no voice here", and
    that fails closed."""
    toml_path.write_text(_with_sidecars(
        toml_path.read_text(), '[{ guild = "g1", endpoint = "   " }]',
    ))

    with pytest.raises(ConfigError, match="empty endpoint"):
        load(toml_path)


def test_one_guild_listed_twice_refuses_to_start(toml_path):
    """Two answers to "which controller owns this guild" would send joins to
    one and take events back from the other."""
    toml_path.write_text(_with_sidecars(
        toml_path.read_text(),
        '[{ guild = "g1", endpoint = "http://a:4600" },'
        ' { guild = "g1", endpoint = "http://b:4600" }]',
    ))

    with pytest.raises(ConfigError, match="twice"):
        load(toml_path)


def test_an_endpoint_is_stripped_not_merely_checked(toml_path):
    """The loaded endpoint is the stripped value: validating on `.strip()` while
    passing the raw value would load clean and fail at the first request with a
    URL error instead of at load."""
    toml_path.write_text(_with_sidecars(
        toml_path.read_text(),
        '[{ guild = "g1", endpoint = "  http://voice-sidecar:4600  " }]',
    ))

    cfg = load(toml_path)

    assert cfg.speech.tts.sidecars_by_guild == {"g1": "http://voice-sidecar:4600"}


def test_no_sidecars_loads_and_fails_closed(toml_path):
    """The shipped posture: no controller for any guild, so nothing joinable."""
    cfg = load(toml_path)

    assert cfg.speech.tts.sidecars == []
    assert cfg.speech.tts.sidecars_by_guild == {}


# ── [cognition] ───────────────────────────────────────────────────────────────


def test_cognition_ships_off_with_20s_starting_values(toml_path):
    """A new subsystem does not switch itself on; `quiet_seconds` (settled) is
    1800 by operator decision.
    """
    c = load(toml_path).cognition
    assert c.enabled is False
    assert (c.quiet_seconds, c.cue_quiet_seconds, c.rollup_delay_seconds,
            c.batch_max, c.max_age_seconds, c.cooldown_seconds,
            c.max_concurrent_sessions, c.reply_quiet_seconds) == (
        1800, 120, 60, 80, 3600, 300, 2, 600,
    )
    assert c.time_floors == ["07:00", "00:00"] and c.timezone == "Asia/Hong_Kong"


def test_cognition_config_bare_default_is_1800():
    """The class default, unmediated by the example file: the two are
    independent sources of truth for the same number, and a test reading
    only through `load(toml_path)` never re-checks THIS one — it would stay
    green if the field default alone changed."""
    assert CognitionConfig().quiet_seconds == 1800.0


def test_the_cognition_section_is_optional(toml_path):
    text = toml_path.read_text()
    start = text.index("\n[cognition]\n")
    end = text.index("\n[obs.logging]")
    toml_path.write_text(text[:start] + text[end:])
    assert load(toml_path).cognition.enabled is False


def test_the_retired_cognition_modes_refuse_to_load(toml_path):
    """`enabled` replaces `mode` and `session_mode`: a config still naming
    either refuses to start rather than load half-read."""
    for retired in ('mode = "shadow"', 'session_mode = "shadow"'):
        toml_path.write_text(_cognition_on(toml_path.read_text()).replace(
            "[cognition]\n", f"[cognition]\n{retired}\n", 1))
        with pytest.raises(ConfigError):
            load(toml_path)


def test_a_floor_that_is_not_hh_mm_or_an_unknown_zone_refuses_to_start(toml_path):
    text = toml_path.read_text()
    toml_path.write_text(_set_key(text, "time_floors", '["7am"]'))
    with pytest.raises(ConfigError):
        load(toml_path)
    toml_path.write_text(text.replace('timezone = "Asia/Hong_Kong"\nfloor_grace_seconds',
                                      'timezone = "Mars/Olympus"\nfloor_grace_seconds'))
    with pytest.raises(ConfigError):
        load(toml_path)


# ── [cognition] enabled: what it needs ──────────────────────────────────────


def _set_dreamer_enabled(text: str) -> str:
    """`[dreamer] enabled = true`, whichever way the template spells it."""
    head, sep, tail = text.partition("\n[dreamer]\n")
    assert sep, "the template has no [dreamer] section"
    import re as _re

    tail, n = _re.subn(r"(?m)^enabled = .*$", "enabled = true", tail, count=1)
    assert n == 1, "the [dreamer] section states no `enabled` key"
    return head + sep + tail




def _cognition_on(text: str) -> str:
    """`[cognition] enabled = true` with the Dreamer on, the template's way."""
    import re as _re

    head, sep, tail = text.partition("\n[cognition]\n")
    assert sep, "the template has no [cognition] section"
    tail, n = _re.subn(r"(?m)^enabled = false$", "enabled = true", tail, count=1)
    assert n == 1, "the [cognition] section states no `enabled = false`"
    return _set_dreamer_enabled(head + sep + tail)


def test_cognition_on_without_the_dreamer_is_refused_at_load(toml_path):
    import re as _re

    text = _cognition_on(toml_path.read_text())
    head, sep, tail = text.partition("\n[dreamer]\n")
    tail = _re.sub(r"(?m)^enabled = true$", "enabled = false", tail, count=1)
    toml_path.write_text(head + sep + tail)
    with pytest.raises(ConfigError, match=r"\[cognition\] enabled = true needs \[dreamer\]"):
        load(toml_path)


def test_cognition_on_with_the_dreamer_loads(toml_path):
    toml_path.write_text(_cognition_on(toml_path.read_text()))
    assert load(toml_path).cognition.enabled is True


def test_cognition_on_refuses_to_load_without_an_explicit_cognition_budget(toml_path):
    """`budget_for` answers an adaptor with no block from the class defaults
    (6 turns, 40k tokens, 120 s), a budget nobody chose for a session that reads
    and writes across up to `max_items` items."""
    import re as _re

    text = _cognition_on(toml_path.read_text())
    assert "[dreamer.budgets.cognition]" in text, "positive control: the template carries it"
    load(_write(toml_path, text))                              # with the block: loads
    stripped, n = _re.subn(
        r"(?ms)^\[dreamer\.budgets\.cognition\]\n.*?(?:\n\n|\Z)", "", text, count=1,
    )
    assert n == 1
    toml_path.write_text(stripped)
    with pytest.raises(ConfigError, match=r"needs an explicit \[dreamer\.budgets\.cognition\]"):
        load(toml_path)




def _write(path, text):
    path.write_text(text)
    return path


def test_the_failure_backoff_and_grounding_keys_load_with_their_documented_defaults(toml_path):
    for c in (load(toml_path).cognition, CognitionConfig()):     # the template AND the bare default
        assert (c.backoff_max_seconds, c.max_consecutive_failures) == (21600.0, 5)
        assert (c.record_chars, c.message_chars, c.register_lines) == (40000, 400, 40)
        # window unknown: no overflow is ever proven to be the agenda's
        assert (c.model_window_tokens, c.overflow_window_fraction) == (None, 0.8)




#: The tag turn's own loop bounds on the live deployment: `[llm.limits]` turns /
#: total_tokens / output_tokens and `[llm] timeout_seconds` (the tag turn's
#: whole-loop clock), and `[llm] max_tokens`. DEC-247: a Cognition session's are at
#: least 10x each.
_TAG_TURN = {"turns": 20, "total_tokens": 1_024_000, "output_tokens": 12_000,
             "timeout_seconds": 360.0}


def test_the_cognition_bounds_are_at_least_ten_times_the_tag_turns(toml_path):
    from nora.config import DreamBudgetConfig

    cfg = load(toml_path)
    template = cfg.dreamer.budgets["cognition"]
    # The template, the code defaults (a deployment with no block, which the
    # config refuses in shadow mode but `budget_for` still answers) and the
    # shipped tag-turn values the template's own `[llm.limits]` carries.
    default = cfg.dreamer.model_copy(update={"budgets": {}}).budget_for("cognition")
    assert isinstance(default, DreamBudgetConfig)
    for got in (template, default):
        for field, tag in _TAG_TURN.items():
            assert getattr(got, field) >= 10 * tag, (field, getattr(got, field), tag)
    for field in ("turns", "total_tokens", "output_tokens"):
        assert getattr(template, field) >= 10 * getattr(cfg.llm.limits, field), field
    assert template.timeout_seconds >= 10 * cfg.llm.timeout_seconds


def test_the_wakes_timeout_stays_above_the_sessions_by_the_margin(toml_path):
    from nora.app.cognition_session import SESSION_TIMEOUT_MARGIN_SECONDS

    cfg = load(toml_path)
    assert (
        cfg.dreamer.budgets["cognition"].timeout_seconds
        <= cfg.cognition.session_timeout_seconds - SESSION_TIMEOUT_MARGIN_SECONDS
    )


@pytest.mark.parametrize("key", ["max_memory_calls = 6\n", "max_tool_calls = 60\n", None])
def test_a_cognition_block_that_sets_a_call_ceiling_refuses_to_load(toml_path, key):
    """DEC-247: the retired keys are refused, not ignored: an ignored ceiling is one
    the operator believes binds."""
    text = toml_path.read_text()
    marker = "[dreamer.budgets.cognition]\n"
    assert marker in text
    if key is None:                       # the per-tool table
        toml_path.write_text(text + "\n[dreamer.budgets.cognition.tool_calls]\nabout = 4\n")
    else:
        toml_path.write_text(text.replace(marker, marker + key, 1))
    with pytest.raises(ConfigError, match="no call ceiling of any kind"):
        load(toml_path)


# --------------------------------------------------------- [documents] public_url
def test_documents_public_url_defaults_to_no_link_and_the_example_ships_it_empty(
    example_config_path,
) -> None:
    assert DocumentsConfig().public_url == ""
    example = tomllib.loads(example_config_path.read_text())
    assert example["documents"]["public_url"] == ""


@pytest.mark.parametrize(
    ("given", "kept"),
    [
        ("https://nora.67.zone", "https://nora.67.zone"),
        ("https://nora.67.zone/", "https://nora.67.zone"),
        ("http://localhost:3000", "http://localhost:3000"),
        ("https://a-b.example.com:8443", "https://a-b.example.com:8443"),
        ("https://xn--nora-8na.example", "https://xn--nora-8na.example"),
    ],
)
def test_documents_public_url_accepts_an_origin_and_drops_a_trailing_slash(given, kept) -> None:
    assert DocumentsConfig(public_url=given).public_url == kept


@pytest.mark.parametrize(
    "bad",
    [
        "nora.67.zone",  # no scheme
        "javascript:alert(1)",
        "ftp://nora.67.zone",
        "https://",
        "https://nora.67.zone/app",  # a path
        "https://nora.67.zone/?x=1",
        "https://nora.67.zone/#frag",
        "https://user:pw@nora.67.zone",  # credentials
        "https://nora.67.zone extra",
        "//nora.67.zone",
        "https://ho\x00st.com",  # a control character
        "https://host\x7f",
        "https://good.com\\evil.com",  # a bare backslash
        "https://nora.67.zone.",  # a trailing dot
        "https://host:99999",  # a port out of range
        "https://host:abc",
        "https://+85212345678.example.com",  # not a host (and a phone to egress)
        "https://under_score.example.com",
        "https://-lead.example.com",
    ],
)
def test_documents_public_url_refuses_anything_that_is_not_an_origin(bad) -> None:
    with pytest.raises(ValidationError):
        DocumentsConfig(public_url=bad)


def test_the_template_pins_the_budget_blocks_it_declares(toml_path):
    """`budget_for` falls back SILENTLY for an unknown key, so an adaptor with no
    block runs on numbers nobody chose. The template declares three: culture, the
    Cognition session and the sub-agent worker (DEC-247: rounds, tokens and
    time, no call ceiling)."""
    from nora.config import (
        COGNITION_OUTPUT_TOKENS,
        COGNITION_TIMEOUT_SECONDS,
        COGNITION_TOTAL_TOKENS,
        COGNITION_TURNS,
    )

    budgets = load(toml_path).dreamer.budgets
    assert set(budgets) == {"culture", "cognition", "subagent"}
    culture = budgets["culture"]
    assert (culture.turns, culture.total_tokens) == (10, 120_000)
    assert (culture.output_tokens, culture.max_memory_calls) == (6_000, 12)
    assert culture.tool_calls == {"lookup_person": 12}
    cognition = budgets["cognition"]
    assert (cognition.turns, cognition.total_tokens) == (COGNITION_TURNS, COGNITION_TOTAL_TOKENS)
    assert (cognition.output_tokens, cognition.timeout_seconds) == (
        COGNITION_OUTPUT_TOKENS, COGNITION_TIMEOUT_SECONDS,
    )
    subagent = budgets["subagent"]
    assert (subagent.turns, subagent.total_tokens) == (COGNITION_TURNS, COGNITION_TOTAL_TOKENS)
    assert (subagent.output_tokens, subagent.timeout_seconds) == (
        COGNITION_OUTPUT_TOKENS, COGNITION_TIMEOUT_SECONDS,
    )


def test_the_standing_section_is_optional_and_ships_inert(toml_path):
    """A config.toml written before this section existed keeps loading under
    `extra="forbid"`, with the tier OFF."""
    text = toml_path.read_text()
    start = text.index("\n[standing]")
    toml_path.write_text(text[:start] + text[text.index("\n[music]"):])
    cfg = load(toml_path)
    assert cfg.standing.enabled is False
    assert (cfg.standing.max_chars, cfg.standing.fold_batch) == (1200, 200)
    assert cfg.standing.model == ""


def test_the_dream_unit_hold_ships_unset_and_derives_a_default(toml_path):
    """`[dreamer] max_unit_hold_seconds` ships commented out: the value
    is derived from this config's own numbers, always above the known minimum."""
    cfg = load(toml_path)
    assert cfg.dreamer.max_unit_hold_seconds is None
    assert cfg.dreamer_max_unit_hold_seconds() > cfg._dream_unit_hold_known_minimum()


# --------------------------------------------------------- [subagent]
def _subagent_on(text: str) -> str:
    """`[subagent] enabled = true` with the Dreamer and the compute poller on."""
    import re as _re

    head, sep, tail = text.partition("\n[subagent]\n")
    assert sep, "the template has no [subagent] section"
    tail, n = _re.subn(r"(?m)^enabled = false$", "enabled = true", tail, count=1)
    assert n == 1, "the [subagent] section states no `enabled = false`"
    text = _set_dreamer_enabled(head + sep + tail)
    head, sep, tail = text.partition("\n[compute]\n")
    assert sep, "the template has no [compute] section"
    tail, n = _re.subn(r"(?m)^enabled = false$", "enabled = true", tail, count=1)
    assert n == 1
    return head + sep + tail


def test_the_subagent_ships_off_and_the_example_carries_its_block(toml_path):
    config = load(toml_path)
    assert config.subagent.enabled is False
    block = config.dreamer.budgets["subagent"]
    assert (block.turns, block.total_tokens, block.output_tokens, block.timeout_seconds) == (
        200, 10_240_000, 120_000, 3600.0)


def test_subagent_on_with_the_dreamer_and_the_poller_loads(toml_path):
    toml_path.write_text(_subagent_on(toml_path.read_text()))
    assert load(toml_path).subagent.enabled is True


@pytest.mark.parametrize("key", ["max_memory_calls = 6\n", "max_tool_calls = 60\n", None])
def test_a_subagent_block_that_sets_a_call_ceiling_refuses_to_load(toml_path, key):
    """DEC-247: the keys are refused, not ignored (an ignored ceiling is one the
    operator believes binds), exactly as for cognition."""
    text = toml_path.read_text()
    marker = "[dreamer.budgets.subagent]\n"
    assert marker in text
    if key is None:                       # the per-tool table
        toml_path.write_text(text + "\n[dreamer.budgets.subagent.tool_calls]\nabout = 4\n")
    else:
        toml_path.write_text(text.replace(marker, marker + key, 1))
    with pytest.raises(ConfigError, match="no call ceiling of any kind"):
        load(toml_path)


def test_subagent_on_refuses_to_load_without_its_budget_block(toml_path):
    import re as _re

    text = _subagent_on(toml_path.read_text())
    load(_write(toml_path, text))                              # with the block: loads
    stripped, n = _re.subn(
        r"(?ms)^\[dreamer\.budgets\.subagent\]\n.*?(?:\n\n|\Z)", "", text, count=1,
    )
    assert n == 1
    toml_path.write_text(stripped)
    with pytest.raises(ConfigError, match=r"needs an explicit \[dreamer\.budgets\.subagent\]"):
        load(toml_path)


@pytest.mark.parametrize("section", ["[compute]", "[dreamer]"])
def test_subagent_on_refuses_to_load_without_the_poller_or_the_dreamer(toml_path, section):
    import re as _re

    text = _subagent_on(toml_path.read_text())
    head, sep, tail = text.partition(f"\n{section}\n")
    assert sep
    tail, n = _re.subn(r"(?m)^enabled = true$", "enabled = false", tail, count=1)
    assert n == 1
    toml_path.write_text(head + sep + tail)
    with pytest.raises(ConfigError, match=r"\[subagent\] enabled = true needs"):
        load(toml_path)


def test_prompt_window_defaults_to_20_clamps_to_a_small_read_and_refuses_an_explicit_excess():
    from nora.config import StoreConfig

    def store(**extra):
        raw = {"data_dir": "data", "max_per_conversation": 500, "context_window": 40, **extra}
        return StoreConfig.model_validate(raw, context={"base": Path("base")})

    assert store().effective_prompt_window == 20
    assert store(context_window=10).effective_prompt_window == 10   # omitted key still loads
    assert store(prompt_window=8).effective_prompt_window == 8
    with pytest.raises(ValueError, match="prompt_window"):
        store(prompt_window=41)


# ------------------------------------------------- [video_gen]
def _section_on(text: str, section: str) -> str:
    head, sep, tail = text.partition(f"\n[{section}]\n")
    assert sep, f"the template has no [{section}] section"
    tail, n = re.subn(r"(?m)^enabled = false$", "enabled = true", tail, count=1)
    assert n == 1, f"the [{section}] section states no `enabled = false`"
    return head + sep + tail


def test_video_gen_ships_off_with_the_operators_settings(toml_path):
    video = load(toml_path).video_gen
    assert video.enabled is False and video.allow_reference_images is False
    assert (video.resolution, video.duration_seconds, video.ratio) == ("480P", 15, "3:4")
    assert video.max_clips_per_room_per_day == 1
    assert (video.minimax_cn.model, video.minimax_cn.api_key_env) == (
        "MiniMax-H3", "MINIMAX_CN_API_KEY")
    # A clip or a voice note leaves the box like a photo, and a voice note lends a voice:
    # each kind is off until switched on by itself.
    assert video.allow_reference_video is False and video.allow_reference_audio is False
    assert video.reference_seconds == 6.0


@pytest.mark.parametrize("seconds", ["5.0", "15.5"])
def test_video_gen_reference_seconds_stays_inside_what_the_provider_takes(toml_path, seconds):
    """Under 6, three references of one kind would each fall below the provider's 2 s floor;
    over 15, a kind would pass the provider's 15 s total."""
    text = toml_path.read_text().replace(
        "reference_seconds = 6.0", f"reference_seconds = {seconds}")
    toml_path.write_text(text)
    with pytest.raises(ConfigError, match="reference_seconds"):
        load(toml_path)


def test_video_gen_refuses_the_old_all_rooms_cap_key_naming_the_new_one(toml_path):
    """The cap is per room (`max_clips_per_day` is not a key). A file that still sets
    `max_clips_per_day = 2` must not boot as if it meant two clips for the whole house."""
    head, sep, tail = toml_path.read_text().partition("\n[video_gen]\n")
    assert sep, "the template has no [video_gen] section"
    toml_path.write_text(head + sep + "max_clips_per_day = 2\n" + tail)
    with pytest.raises(ConfigError, match="max_clips_per_room_per_day"):
        load(toml_path)


def test_video_gen_on_with_the_poller_loads(toml_path):
    text = _section_on(_section_on(toml_path.read_text(), "video_gen"), "compute")
    toml_path.write_text(text)
    assert load(toml_path).video_gen.enabled is True


def test_video_gen_on_without_the_poller_refuses_to_load(toml_path):
    toml_path.write_text(_section_on(toml_path.read_text(), "video_gen"))
    with pytest.raises(ConfigError, match=r"needs \[compute\] enabled"):
        load(toml_path)


@pytest.mark.parametrize(("ratio", "says"), [
    ("adaptive", "live API rejected it"), ("Adaptive", "live API rejected it"),
    ("wide", "must be"), ("3:0", "must be"),
])
def test_video_gen_refuses_a_ratio_that_is_not_explicit(toml_path, ratio, says):
    """`adaptive` is refused with the reason (the live API rejects it), not
    only as a malformed value."""
    text = toml_path.read_text()
    assert 'ratio = "3:4"' in text
    toml_path.write_text(text.replace('ratio = "3:4"', f'ratio = "{ratio}"', 1))
    with pytest.raises(ConfigError, match=says):
        load(toml_path)


def test_video_gen_refuses_a_resolution_other_than_480p(toml_path):
    text = toml_path.read_text()
    toml_path.write_text(text.replace('resolution = "480P"', 'resolution = "768P"', 1))
    with pytest.raises(ConfigError):
        load(toml_path)


@pytest.mark.parametrize(('section', 'key', 'new_home'), [
    ('persona', None, '[memory.profile]'),
    ('profile', None, '[memory.profile]'),
    ('message_readings', None, '[memory.extraction]'),
    ('memory_index', None, '[memory.index]'),
    ('persona', 'enabled', '[memory.profile] write'),
    ('persona', 'render', '[memory.profile] read'),
    ('profile', 'enabled', '[memory.profile] write'),
    ('profile', 'render', '[memory.profile] read'),
    ('message_readings', 'batch_size', '[memory.extraction] messages_per_call'),
    ('message_readings', 'context_size', '[memory.extraction] context_messages'),
    ('message_readings', 'settle_seconds', '[memory.extraction] min_message_age_seconds'),
    ('memory_index', 'enabled', '[memory.index] read'),
    ('memory_index', 'drain_seconds', '[memory.index] refresh_seconds'),
    *[('memory', key, f'[honcho] {key}') for key in (
        'base_url', 'workspace_id', 'ai_peer', 'tagger_card_conclusions',
        'people_in_window_conclusions', 'people_in_window_speakers', 'read_timeout_seconds',
    )],
    ('memory.extraction', 'batch_size', '[memory.extraction] messages_per_call'),
    ('memory.extraction', 'context_size', '[memory.extraction] context_messages'),
    ('memory.extraction', 'settle_seconds', '[memory.extraction] min_message_age_seconds'),
    ('memory.index', 'enabled', '[memory.index] read'),
    ('memory.index', 'drain_seconds', '[memory.index] refresh_seconds'),
    ('memory.profile', 'enabled', '[memory.profile] write'),
    ('memory.profile', 'render', '[memory.profile] read'),
    ('culture', 'person_wake_batch', '[memory.profile.person] update_every_messages'),
    ('culture', 'unit_wake_batch', '[memory.profile.unit] update_every_room_profile_changes'),
    ('culture', 'nora_wake_batch', '[memory.profile.nora] update_every_replies'),
])
def test_retired_memory_config_names_are_refused_with_the_new_home(
    toml_path, section, key, new_home,
):
    import tomli_w

    data = tomllib.loads(toml_path.read_text())
    target = data
    for part in section.split('.'):
        target = target.setdefault(part, {})
    if key is not None:
        target[key] = 'retired-value'
    toml_path.write_text(tomli_w.dumps(data))
    with pytest.raises(ConfigError, match=re.escape(new_home)):
        load(toml_path)


def test_example_loads_with_the_memory_config_names(toml_path):
    cfg = load(toml_path)
    assert cfg.honcho.workspace_id == 'nora'
    assert cfg.memory.extraction.messages_per_call == 20
    assert cfg.memory.extraction.context_messages == 20
    assert cfg.memory.extraction.min_message_age_seconds == 2
    assert cfg.memory.index.read is False
    assert cfg.memory.index.refresh_seconds == 60
    assert cfg.memory.profile.write is False
    assert cfg.memory.profile.read is False
    assert cfg.memory.profile.person.update_every_messages == 100
    assert cfg.memory.profile.unit.update_every_room_profile_changes == 5
    assert cfg.memory.profile.nora.update_every_replies == 100
    assert cfg.memory.profile.update_every_seconds == cfg.culture.stale_seconds == 604800
