"""Frozen config tree (spec D.1), pydantic loader over config.toml."""

import shutil
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from nora.config import Config, ConfigError, LimitsConfig, LLMConfig, load

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
    assert cfg.platforms.signal.nora_sender_id == "nora"
    # memory replaces honcho
    assert cfg.memory.workspace_id == "nora"
    # store replaces buffer
    assert cfg.store.max_per_conversation == 500
    assert cfg.store.context_window == 40
    # tiers replace default/deep, and carry their own timeout
    assert cfg.llm.model == "opencode-zen/deepseek-v4-flash"
    assert cfg.llm.timeout_seconds == 30
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
    assert cfg.presence.retention_days == 180


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
