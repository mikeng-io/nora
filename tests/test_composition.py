"""The composition root is the ONE place that reads config. Everything else
receives dependencies. This test pins that property."""

import ast
import base64
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nora.agent import tool_choice
from nora.app.composition import Dependencies, StartupError, build_dependencies
from nora.app.fire import FirePath
from nora.app.heartbeat import HEARTBEAT_NOT_RUNNING
from nora.config import load
from nora.domain.models import DeliveryStatus, NormalizedMessage, TranscriptRow
from nora.infra.blobs import FilesystemBlobStore, RustfsBlobStore
from nora.platform.discord.media import DiscordMediaSource
from nora.platform.instagram.media import InstagramMediaSource
from nora.platform.registry import MediaSourceRegistry
from nora.platform.signal.media import SignalMediaSource
from nora.policy import load_policy
from tests.builders import override_config
from tests.conftest import RUSTFS_TEST_PASSWORD, RUSTFS_TEST_USER

ROOT = Path(__file__).resolve().parents[1]

_HOME_POLICY = (
    'default = "deny"\n'
    '[[conversations]]\nplatform = "signal"\nid = "g1"\npersons = "*"\n'
    'unit = "friends"\nlabel = "Friends"\nhome_room = true\nproactive = true\n'
    'location = true\n'
    '[[conversations]]\nplatform = "signal"\nid = "g2"\npersons = "*"\n'
    'unit = "wedding"\nlabel = "Wedding"\n'
)


@pytest.fixture
def cfg(tmp_path, monkeypatch, pg_url, example_config_path):
    # `pg_url` (tests/conftest.py) creates a fresh Postgres database and exports
    # DATABASE_URL, which build_dependencies() reads (store.database_url_env) to
    # run migrations and wire the store — a throwaway database, never production.
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    dst = example_config_path
    # config.example.toml's identity_file ("identity.toml") resolves relative to
    # the config file's own directory (nora/config.py's path-resolution context);
    # build_dependencies() calls load_persons() against it, so it must exist.
    (tmp_path / "identity.toml").write_text(
        '[persons.miles]\nname = "Miles"\n[[persons.miles.signal]]\nphone = "+852111"\n'
    )
    # policy.toml resolves the same way. `persons = "*"` keeps these wiring
    # tests about the dependency graph rather than the allowlist. `label` is
    # required at startup as of #200 part 2 (an unlabelled allowed room is a
    # `PolicyError`, the way a second home room already is) — this fixture's
    # room needs one for `load_policy` to succeed at all.
    (tmp_path / "policy.toml").write_text(
        'default = "deny"\n[[conversations]]\nplatform = "signal"\n'
        'id = "g1"\nlabel = "g1"\npersons = "*"\n'
    )
    # store.data_dir is overridden to tmp_path so media custody lands in a
    # throwaway directory; the record store's own database is the throwaway
    # `pg_url` (via DATABASE_URL), never production.
    #
    # platforms.signal.account overrides the empty "derive from the daemon" default
    # (config.example.toml) with a fixed value: derive_account() returns an override
    # immediately, with no network call, so build_dependencies() never needs a real
    # (or faked) signal-cli daemon reachable at the example config's placeholder URL.
    #
    # doctrine.dir ("nora/doctrine", config.example.toml) resolves relative to the
    # copied config file's directory (tmp_path here), which has no such subdirectory
    # — point it at the real, already-valid doctrine files instead of copying them.
    #
    # `dreamer.dir` resolves the same way and for the same reason - the Dreamer's
    # instruction corpus is validated at STARTUP (a malformed objective must not
    # become a silent Dreamer three hours later), so an unpointed `[dreamer]`
    # section makes every wiring test here fail on a missing directory rather
    # than on the thing it is testing.
    return override_config(load(dst), {
        "store.data_dir": tmp_path,
        "platforms.signal.account": "+85290000100",
        "doctrine.dir": ROOT / "nora" / "doctrine",
        "dreamer.dir": ROOT / "nora" / "dreamer",
    })


async def test_build_dependencies_returns_the_wired_graph(cfg, tmp_path):
    deps = await build_dependencies(cfg)
    assert isinstance(deps, Dependencies)
    assert deps.audit is not None
    assert deps.memory is not None
    assert deps.persons is not None
    assert deps.metrics is not None
    assert deps.policy is not None
    assert deps.assembler is not None
    assert deps.ingest is not None
    assert deps.engagement is not None
    assert deps.dispatcher is not None


async def test_the_trigger_phrase_engages_by_default(cfg):
    """`[engagement.trigger].enabled` defaults to True — the trigger phrase
    already engaged her on every deployment before this flag existed, so the
    wired `CompositePolicy` must still include `TriggerTextPolicy` when the
    key is left at its default."""
    deps = await build_dependencies(cfg)
    msg = NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=1,
        body="nora.000 you around?", raw={},
    )

    decision = await deps.policy.should_engage(message=msg)

    assert decision.engage is True
    assert decision.reason == "trigger_text"


async def test_disabling_the_trigger_removes_only_that_path(cfg):
    """Turning the flag off narrows `CompositePolicy` down to `MentionPolicy` +
    `ReplyToNoraPolicy` — the trigger PHRASE alone stops matching, which is
    the only property this test needs to pin (the two structural policies
    have their own coverage in `tests/unit/test_ingest_policy.py`)."""
    disabled = override_config(cfg, {"engagement.trigger.enabled": False})
    deps = await build_dependencies(disabled)
    msg = NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=1,
        body="nora.000 you around?", raw={},
    )

    decision = await deps.policy.should_engage(message=msg)

    assert decision.engage is False
    assert decision.reason == "no_trigger"


async def test_dependencies_exposes_the_reminders_store(cfg):
    """The web API's status route (nora/web/status.py) needs to read pending
    reminders, and the only reminders store this process builds lives inside
    build_dependencies() as a local — this pins it onto Dependencies so
    anything outside composition.py can reach it."""
    from nora.infra.sql.reminders import SqlReminderStore

    deps = await build_dependencies(cfg)
    assert isinstance(deps.reminders, SqlReminderStore)


async def test_dependencies_games_client_is_none_without_pulse_api_token(cfg, monkeypatch):
    """`nora.web.games`'s `PulseGamesClient` is a separate, REST credential
    (`PULSE_API_TOKEN`) from the LLM tool-calling path's `[tools.mcp.pulse]`
    MCP mount (`PULSE_MCP_TOKEN`) — this fixture's environment sets neither,
    so the web layer must degrade to `None` (a 503), never a guessed client
    pointed at nothing."""
    monkeypatch.delenv("PULSE_API_TOKEN", raising=False)
    deps = await build_dependencies(cfg)
    assert deps.games_client is None


async def test_dependencies_builds_a_games_client_when_pulse_api_token_is_set(cfg, monkeypatch):
    monkeypatch.setenv("PULSE_API_TOKEN", "test-pulse-token")
    deps = await build_dependencies(cfg)
    assert deps.games_client is not None


async def test_dependencies_speaker_is_none_when_tts_is_disabled(cfg, monkeypatch):
    """`[speech.tts]` ships INERT like every other optional capability: the
    template config has `enabled = false`, so even with a key in the
    environment the root wires no speaker and nothing about the deployment
    changes."""
    monkeypatch.setenv("MINIMAX_CN_API_KEY", "test-minimax-key")
    deps = await build_dependencies(cfg)
    assert deps.speaker is None


async def test_dependencies_speaker_is_none_when_tts_is_enabled_but_keyless(cfg, monkeypatch):
    """The keyless-`route`/`search` shape (design/voice-channel/01 §5):
    enabled with a provider named but the env var unset is a loud warning and
    NO speaker — never a speaker that fails on every call."""
    monkeypatch.delenv("MINIMAX_CN_API_KEY", raising=False)
    keyless = override_config(cfg, {
        "speech.tts.enabled": True,
        "speech.tts.provider": "minimax_cn",
    })
    deps = await build_dependencies(keyless)
    assert deps.speaker is None


async def test_dependencies_builds_a_speaker_when_tts_is_enabled_and_keyed(cfg, monkeypatch):
    from nora.infra.tts import MiniMaxTtsSpeaker

    monkeypatch.setenv("MINIMAX_CN_API_KEY", "test-minimax-key")
    keyed = override_config(cfg, {
        "speech.tts.enabled": True,
        "speech.tts.provider": "minimax_cn",
        "speech.tts.voice": "Cantonese_CuteGirl",
    })
    deps = await build_dependencies(keyed)
    assert isinstance(deps.speaker, MiniMaxTtsSpeaker)


async def test_the_doctrine_the_root_builds_carries_no_capability_section(cfg):
    """DEC-150: the composition root must no longer wire a capability block.

    The store assembles only the authored sections; the schema is the single
    source of truth for what she can do. If the root starts passing a
    capability= argument again, the generated section reappears and this
    reddens (and `DoctrineStore` no longer even accepts the parameter)."""
    deps = await build_dependencies(cfg)
    text = deps.doctrine.doctrine_text
    assert '<section kind="capability"' not in text
    # The capability block's signature line was "- <tool> — <when clause>".
    # Assert that shape is gone: no bullet that begins with a registered tool
    # name followed by an em-dash.
    import re

    from nora.agent.tools import NATIVE_TOOLS
    names = "|".join(re.escape(n) for n in NATIVE_TOOLS)
    assert not re.search(rf"^- ({names}) — ", text, re.MULTILINE), "tool-catalogue line present"


async def test_the_runtime_context_carries_the_configured_language_tag(cfg):
    """The composition root must hand the loader `doctrine.language_hint`, or
    every tag's runtime_context ships a blank `language:` line — the model is
    then never told, from outside, what to speak. This was a live defect: the
    DoctrineStore had the parameter and defaulted it to "", and the root never
    passed it. The value is a BCP 47 tag (RFC 5646), asserted here against the
    real assembled runtime_context. Drop `language_hint=` from the root and the
    line reads `language:` with nothing after it, and this goes red.
    """
    deps = await build_dependencies(cfg)
    assert "language: yue-Hant-HK" in deps.doctrine.runtime_context
    assert "language: \n" not in deps.doctrine.runtime_context


async def test_the_root_hands_the_loader_the_tool_names_its_gate_needs(cfg, tmp_path):
    """The catalogue gate's backticked half is inert without them, and an inert
    gate that nobody notices is worse than no gate.

    It has to be a tool whose name is an ordinary English word — `now` or
    `about`. Every other native is snake_case, which the STRUCTURAL detector
    catches on its own, so injecting one of those would make this pass whether
    or not `tool_names` was ever handed over. This used to inject `recall`;
    DEC-39 renamed that tool to `about`, which is still in the same class.
    """
    import shutil


    d = tmp_path / "doctrine"
    shutil.copytree(ROOT / "nora" / "doctrine", d)
    conduct = d / "rooms" / "conduct.md"
    conduct.write_text(conduct.read_text().replace(
        "# Honesty", "Reach for `about` when you need to.\n\n# Honesty", 1))
    with pytest.raises(StartupError, match="may not name a tool"):
        await build_dependencies(override_config(cfg, {"doctrine.dir": d}))


async def test_the_nora_aipeer_id_is_reserved_and_no_human_may_hold_it(cfg):
    """Honcho v2 corpus-safety (council 2026-08-07): the aiPeer id is Nora's
    self-model peer. A human mapped to it would feed under it and merge
    IRREVERSIBLY into her card (Honcho keeps conclusions, not text). Setting
    `memory.ai_peer` to an existing person id ("miles" in this fixture) must
    refuse to start, not silently corrupt the corpus."""
    with pytest.raises(StartupError, match="reserved for Nora"):
        await build_dependencies(override_config(cfg, {"memory.ai_peer": "miles"}))


async def test_no_enabled_platform_refuses_to_start(cfg):
    """`platforms.signal.enabled = false` is honoured now (nora.platform.registry
    .AdapterRegistry) — previously (config.py's "NOT YET HONOURED" comment)
    SignalAdapter started regardless. With no platform enabled there is nothing
    for the engagement pipeline to send through, so this is a startup failure,
    not a degraded start."""
    disabled = override_config(cfg, {"platforms.signal.enabled": False})
    with pytest.raises(StartupError, match="no platform adapter is enabled"):
        await build_dependencies(disabled)


def test_build_dependencies_never_wires_the_real_database(cfg, tmp_path, pg_url):
    """Regression guard: a fixture that forgot to point the store at a throwaway
    database would wire a real SqlRecordStore against production. The store's URL
    comes from DATABASE_URL, which `pg_url` set to an ephemeral `t_…` database;
    the media root is under tmp_path. Both being throwaways is what proves it."""
    assert os.environ["DATABASE_URL"] == pg_url
    assert "/t_" in pg_url                          # the ephemeral-database marker
    assert cfg.store.data_dir == tmp_path


async def test_dependencies_do_not_expose_settings(cfg):
    """A stage must never be able to reach the whole config through its deps —
    that is how a god object grows back."""
    deps = await build_dependencies(cfg)
    assert not hasattr(deps, "config")
    assert not hasattr(deps, "settings")


async def test_media_custody_is_off_when_the_key_env_is_unset(cfg):
    """config.example.toml ships `[media] enabled = true`, but the key env var
    is not set in this fixture — so custody degrades to OFF (never a crash), and
    the ingest pipeline is wired with no source and no store. That is the one
    state `nora.ingest.stages.media` counts as `not_configured`."""
    deps = await build_dependencies(cfg)
    assert deps.ingest._media_sources is None
    assert deps.ingest._blobs is None


async def test_a_valid_key_turns_media_custody_on_with_an_encrypting_store(cfg, monkeypatch):
    """A base64 32-byte key present in the named env var is what wires the
    MediaSourceRegistry (Signal + Discord) + the encrypting FilesystemBlobStore
    into the pipeline."""
    monkeypatch.setenv("NORA_MEDIA_KEY", base64.b64encode(os.urandom(32)).decode())
    deps = await build_dependencies(cfg)
    assert isinstance(deps.ingest._media_sources, MediaSourceRegistry)
    assert isinstance(deps.ingest._media_sources.for_platform("signal"), SignalMediaSource)
    assert isinstance(deps.ingest._media_sources.for_platform("discord"), DiscordMediaSource)
    # Instagram is wired UNCONDITIONALLY, like its two siblings: without a
    # fetcher here `nora.ingest.stages.media` counts every Instagram
    # attachment `not_supported` and records a bare `[image]` with no handle,
    # which is exactly what the live room saw.
    assert isinstance(
        deps.ingest._media_sources.for_platform("instagram"), InstagramMediaSource,
    )
    assert isinstance(deps.ingest._blobs, FilesystemBlobStore)


async def test_an_enabled_instagram_gets_its_own_roster_even_while_degraded(cfg):
    """ONE ROSTER PER PLATFORM, and Instagram's is built for a DEGRADED adapter.

    The map used to hold Signal and Discord only, so every Instagram room was
    looked up in nothing at all: `group_members` answered `unavailable`,
    `where_is` could not establish the room and refused, and every outbound
    @-mention resolved to nobody and went out as plain text with no ping.

    The adapter here IS degraded — the bridge base_url refuses instantly, so
    `resolve_identity()` fails and M0 registers it anyway. That is the point:
    an Instagram checkpoint clears without a restart, so a roster withheld on
    the startup state would stay withheld for the life of the process, with the
    room-scoped tools silently unavailable long after the session came back.
    The READ is what fails closed — a checkpointed bridge answers 409 and the
    roster is `available=False` for exactly as long as that is true.
    """
    from nora.platform.instagram.roster import InstagramRoster

    deps = await build_dependencies(override_config(cfg, {
        "platforms.instagram.enabled": True,
        "platforms.instagram.base_url": "http://127.0.0.1:1",
    }))

    assert "instagram" in deps.registry
    assert deps.registry.get("instagram").degraded, "the fixture cannot reach a bridge"
    roster = deps.engagement._rosters.get("instagram")
    assert isinstance(roster, InstagramRoster)
    assert roster.platform == "instagram"
    # Not Signal's, which has never heard of an Instagram thread id and would
    # answer `available=False` forever — fail-closed, and therefore invisible.
    assert deps.engagement._rosters["signal"] is not roster


async def test_a_disabled_instagram_gets_no_roster(cfg):
    """A roster for a platform with no adapter would be a client nothing can
    use — and `Room` reads `rosters.get(platform)`, so a stray entry would be
    reached by nothing but a misconfiguration."""
    deps = await build_dependencies(cfg)

    assert "instagram" not in deps.registry
    assert "instagram" not in deps.engagement._rosters


async def test_rustfs_disabled_keeps_the_filesystem_store(cfg, monkeypatch):
    """`[media.rustfs].enabled` defaults False (DEC-136) — a config.toml
    written before this existed must keep writing to the filesystem exactly
    as before, with no new failure mode to configure around."""
    monkeypatch.setenv("NORA_MEDIA_KEY", base64.b64encode(os.urandom(32)).decode())
    deps = await build_dependencies(cfg)
    assert isinstance(deps.ingest._blobs, FilesystemBlobStore)


async def test_rustfs_enabled_with_no_endpoint_falls_back_to_the_filesystem(
    cfg, monkeypatch,
):
    """`enabled = true` with a blank endpoint is a misconfiguration, not a
    startup failure — media is a bonus (the section's own docstring), so this
    degrades to the same filesystem store an unset key already degrades to."""
    monkeypatch.setenv("NORA_MEDIA_KEY", base64.b64encode(os.urandom(32)).decode())
    deps = await build_dependencies(
        override_config(cfg, {"media.rustfs.enabled": True})
    )
    assert isinstance(deps.ingest._blobs, FilesystemBlobStore)


async def test_rustfs_enabled_with_unresolved_credentials_falls_back(cfg, monkeypatch):
    """`endpoint` set but the named credential env vars are not exported —
    the same "a bonus feature never crashes the daemon" posture as a
    misconfigured encryption key."""
    monkeypatch.setenv("NORA_MEDIA_KEY", base64.b64encode(os.urandom(32)).decode())
    deps = await build_dependencies(override_config(cfg, {
        "media.rustfs.enabled": True,
        "media.rustfs.endpoint": "http://rustfs:9000",
    }))
    assert isinstance(deps.ingest._blobs, FilesystemBlobStore)


async def test_rustfs_enabled_and_reachable_wires_a_rustfs_blob_store(
    cfg, monkeypatch, rustfs_container,
):
    """The happy path: a real, reachable RustFS with resolved credentials is
    what actually wires `RustfsBlobStore` into the pipeline — everything
    about ITS behaviour (put/get, dedup, encryption-through) is already
    covered by tests/contract/test_blob_store_contract.py; this test is only
    about the WIRING decision."""
    monkeypatch.setenv("NORA_MEDIA_KEY", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setenv("RUSTFS_ACCESS_KEY_TEST", RUSTFS_TEST_USER)
    monkeypatch.setenv("RUSTFS_SECRET_KEY_TEST", RUSTFS_TEST_PASSWORD)
    endpoint = (
        f"http://{rustfs_container.get_container_host_ip()}:"
        f"{rustfs_container.get_exposed_port(9000)}"
    )
    deps = await build_dependencies(override_config(cfg, {
        "media.rustfs.enabled": True,
        "media.rustfs.endpoint": endpoint,
        "media.rustfs.bucket": f"composition-test-{uuid.uuid4().hex[:8]}",
        "media.rustfs.access_key_env": "RUSTFS_ACCESS_KEY_TEST",
        "media.rustfs.secret_key_env": "RUSTFS_SECRET_KEY_TEST",
    }))
    assert isinstance(deps.ingest._blobs, RustfsBlobStore)


async def test_rustfs_unreachable_at_startup_falls_back_rather_than_crashing(
    cfg, monkeypatch,
):
    """A fully-configured RustFS that is simply not there (network refused)
    must degrade the same way a misconfiguration does — this runs BEFORE the
    ingest pipeline's own per-message protection exists
    (nora.ingest.stages.media._take), so an unhandled exception here would
    take the whole daemon down over a bonus feature."""
    monkeypatch.setenv("NORA_MEDIA_KEY", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setenv("RUSTFS_ACCESS_KEY_TEST", "irrelevant")
    monkeypatch.setenv("RUSTFS_SECRET_KEY_TEST", "irrelevant")
    deps = await build_dependencies(override_config(cfg, {
        "media.rustfs.enabled": True,
        # Port 1 on loopback: nothing listens there, so this is a clean
        # connection-refused rather than a slow timeout — the test stays fast.
        "media.rustfs.endpoint": "http://127.0.0.1:1",
        "media.rustfs.access_key_env": "RUSTFS_ACCESS_KEY_TEST",
        "media.rustfs.secret_key_env": "RUSTFS_SECRET_KEY_TEST",
    }))
    assert isinstance(deps.ingest._blobs, FilesystemBlobStore)


async def test_a_malformed_key_degrades_to_off_rather_than_crashing(cfg, monkeypatch):
    """The deliberate asymmetry with the LLM key: media is a bonus, so a bad key
    is custody OFF (warned), never a startup failure and never plaintext."""
    monkeypatch.setenv("NORA_MEDIA_KEY", "not-a-valid-base64-32-byte-key!!")
    deps = await build_dependencies(cfg)
    assert deps.ingest._media_sources is None
    assert deps.ingest._blobs is None


async def test_media_disabled_is_off_even_with_a_valid_key(cfg, monkeypatch):
    monkeypatch.setenv("NORA_MEDIA_KEY", base64.b64encode(os.urandom(32)).decode())
    deps = await build_dependencies(override_config(cfg, {"media.enabled": False}))
    assert deps.ingest._media_sources is None
    assert deps.ingest._blobs is None


async def test_the_heartbeat_is_wired_to_the_real_fire_path(cfg):
    """The tick's `fire` collaborator is a `FirePath`, not a stub.

    This replaces the startup refusal that guarded the stub while it existed.
    The refusal was a statement about the stub; this is a statement about the
    feature, and it is the assertion the whole wiring step needs: `HeartbeatLoop`
    takes `fire` as a collaborator and calls whatever it is handed, so a stub
    left in place claims every due row, delivers nothing, and resolves it — with
    every unit test green, because each one supplies its own fake `fire`.

    Reaching into `background._tasks` and `_fire` is the only way to see it from
    out here: `Dependencies` deliberately exposes no heartbeat, and widening it
    to make one test easier is exactly what its docstring refuses.
    """
    on = override_config(cfg, {"heartbeat.enabled": True})
    deps = await build_dependencies(on)
    try:
        loops = [
            task.get_coro().cr_frame.f_locals["self"]
            for task in deps.background._tasks
            if task.get_coro().__qualname__ == "HeartbeatLoop.run"
        ]
        assert loops, "the heartbeat loop was not spawned"
        assert isinstance(loops[0]._fire.__self__, FirePath)
    finally:
        await deps.background.cancel_all()


async def test_the_participation_trigger_is_wired_to_the_real_fire_path(cfg):
    """The participation occasion's wiring pin (design/dreamer-agent-loop/35
    §3.5/§4), the heartbeat test's shape for the same reason: every unit test
    supplies its own fake `speak`, so only here is it asserted that the
    composition root hands the trigger `FirePath.speak` itself — and that the
    FirePath's participation runner composes `tool_choice="auto"`, without
    which a determination could never end in silence (the tag runner's
    `required` pin demands a call, and the only terminal call is a send).

    Reaching through `_speak.__self__` and `_participation_runner` is the only
    way to see it from out here, exactly as with the heartbeat above.
    """
    assert (await _participation_trigger_of(cfg)) is None, (
        "off by default: no trigger unless [relevance_gate] enabled = true"
    )
    assert (await _participation_trigger_of(
        override_config(cfg, {"participation.enabled": True})
    )) is None, (
        "[participation] enabled alone builds NOTHING (design 36): the trigger "
        "is the gate's, and a turn with no gate in front of it is the cost "
        "design 36 exists to stop paying"
    )

    # `shadow_channel` is set because the shipped default is `shadow = true`
    # and `_shadow_needs_a_channel` refuses that combination without one.
    # `override_config` uses `model_copy`, which does NOT re-run validators,
    # so a test can silently assert against a config that would refuse to
    # start — this one deliberately does not. Which is why it carries the
    # GUILD half too: the bare `"999"` this used to pass is a value the
    # schema now refuses, and model_copy is exactly the door through which a
    # test keeps asserting against a config the deployment could not load.
    on = override_config(cfg, {
        "participation.enabled": True, "relevance_gate.enabled": True,
        "relevance_gate.shadow_channel": "888/999",
    })
    deps = await build_dependencies(on)
    try:
        trigger = deps.participation_trigger
        assert trigger is not None
        fire = trigger._speak.__self__
        assert isinstance(fire, FirePath)
        runner = fire._participation_runner
        assert runner is not None
        assert runner._models._spec.tool_choice == tool_choice.AFTER_SPEAKING

        # The gate itself, and the shadow poster. Without these two the pin
        # above still passes while the composition root builds the WORST
        # state this branch can produce: no admission call at all, shadow
        # off, and every quiescence-closed window running a live turn
        # straight into the room. Setting `gate=None`/`gate_shadow=None` in
        # composition.py left all 65 tests in this file green.
        assert trigger._gate is not None, (
            "no gate wired: every closed window would run a full turn"
        )
        assert trigger._gate._client.model == on.relevance_gate.model
        assert trigger._gate._persons is not None, (
            "no Persons: every speaker label falls back to the platform "
            "profile name (DEC-80's two-name-space bug)"
        )
        assert trigger._gate_shadow is not None, (
            "shadow ships ON: without the poster a GO speaks into the room"
        )
        assert trigger._gate_shadow.__self__ is fire
        assert fire._gate_shadow_channel == on.relevance_gate.shadow_channel
    finally:
        await deps.background.cancel_all()


async def test_the_gate_is_not_wired_when_the_gate_is_off(cfg):
    """`[participation] enabled` alone must build no gate AND no trigger —
    and the composition root says so loudly rather than sitting inert, which
    is the converse of `_the_gate_needs_something_behind_it` and cannot live
    in that validator (a turn with no gate is a legal config that simply does
    nothing)."""
    on = override_config(cfg, {"participation.enabled": True})
    deps = await build_dependencies(on)
    try:
        assert deps.participation_trigger is None
    finally:
        await deps.background.cancel_all()


async def _participation_trigger_of(cfg):
    deps = await build_dependencies(cfg)
    try:
        return deps.participation_trigger
    finally:
        await deps.background.cancel_all()


async def test_an_unstamped_build_spawns_no_announcer(cfg, no_build_stamp):
    """The gate that keeps a dev checkout (and any image not built through
    `scripts/build-image.sh`) quiet — DEC-79.

    `[version] announce` defaults TRUE, so this is the only thing standing
    between "no stamp" and a boot-time announcement of a version nobody knows.
    The composition root checks the manifest itself rather than spawning an
    announcer that would immediately answer `unavailable`: there is nothing to
    announce, and the warning belongs at boot, once, where an operator reads it.

    Reaching into `background._tasks` for the same reason
    `test_the_heartbeat_is_wired_to_the_real_fire_path` does: `Dependencies`
    deliberately exposes neither loop.

    The absent stamp is FORCED, not assumed. This passed for as long as nobody
    had ever run a real build, and went red the first time one did (V1,
    2026-08-11): `scripts/build-image.sh` writes `nora/version.json` into the
    working tree, it is gitignored so it survives unnoticed, and this test then
    exercised the STAMPED path while claiming to prove the unstamped one. A test
    whose meaning depends on ambient filesystem state proves whatever the
    machine happens to be in the mood for.
    """
    deps = await build_dependencies(cfg)
    try:
        assert not _spawned(deps, "VersionAnnouncer.run")
    finally:
        await deps.background.cancel_all()


async def test_a_stamped_build_announces_into_every_allowed_room(cfg, tmp_path, monkeypatch):
    """The other half: a stamped build DOES wire the announcer, at the rooms
    `policy.toml` allows — not a second list in config.toml (DEC-8).

    Without this the test above passes against a feature that never wires at
    all, which is the exact shape of a capability that ships inert by accident
    (DEC-42's `send_reply` cap, DEC-50's `skills` budget).
    """
    stamp = tmp_path / "version.json"
    stamp.write_text(json.dumps({
        "version": "V3",
        "versions": [{"version": "V3", "notes": "She can say her own version."}],
    }))
    monkeypatch.setattr("nora.infra.version.DEFAULT_MANIFEST_PATH", stamp)

    deps = await build_dependencies(cfg)
    try:
        announcers = _spawned(deps, "VersionAnnouncer.run")
        assert announcers, "the version announcer was not spawned"
        announcer = announcers[0]
        # Bound to the REAL fire turn, not a stub — the failure
        # `test_the_heartbeat_is_wired_to_the_real_fire_path` exists for, on the
        # second occasion.
        assert isinstance(announcer._speak.__self__, FirePath)
        assert [t.conversation_id for t in announcer._targets] == [
            conv.primary_id for conv in load_policy(cfg.policy_file).conversations
            if conv.platform == "signal"
        ]
    finally:
        await deps.background.cancel_all()


def _spawned(deps, qualname: str) -> list:
    """Every live background task running `qualname`, as its `self`."""
    return [
        task.get_coro().cr_frame.f_locals["self"]
        for task in deps.background._tasks
        if task.get_coro().__qualname__ == qualname
    ]


async def test_remind_writes_a_real_row_through_the_registry_the_root_builds(cfg, pg_url):
    """The live `ToolRegistry` gets the reminders store, and `[heartbeat]` being
    off does not change that.

    Without `reminders=` on that constructor, `remind` answers `unavailable` in
    production while every test in `tests/test_reminder_tools.py` passes — each
    builds its OWN registry around the fake, so none of them can see the wiring.
    That is the whole failure mode this test exists for, which is why it goes all
    the way to a row in the database rather than asserting on an attribute: a
    store held but never reached would satisfy the weaker check.

    `cfg.heartbeat.enabled` is False here (the shipped default). The tick is
    gated; the TOOLS are not — the model may set, list and cancel reminders on a
    deployment that never fires them, and the rows wait for the flag.
    """
    from nora.agent.room import Room
    from nora.domain.models import PersonRef
    from nora.domain.reminders import ReminderCaps, ReminderState
    from nora.infra.sql.engine import create_sql_engine
    from nora.infra.sql.reminders import SqlReminderStore

    assert cfg.heartbeat.enabled is False
    deps = await build_dependencies(cfg)
    try:
        registry = deps.engagement._tool_registry
        # §9's caps came from `[reminders]`, converted at the composition root —
        # `nora.agent` may not import `nora.config`, so a caps object that
        # silently fell back to its defaults would ignore every operator value.
        assert registry._caps == ReminderCaps(**cfg.reminders.model_dump())

        miles = PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")
        specs = registry.specs(
            store=deps.audit, conversation_id="g1", conversation_type="group",
            persons=deps.persons, honcho=deps.memory, deliverer=None,
            metrics=deps.metrics,
            room=Room(platform="signal", conversation_id="g1", roster_source=None,
                      persons=deps.persons, tagger=miles),
        )
        remind = {spec.name: spec.call for spec in specs}["remind"]
        result = await remind(
            when=(datetime.now(UTC) + timedelta(hours=1)).isoformat(), text="洗衫",
        )
        assert result["status"] == "ok", result
    finally:
        await deps.background.cancel_all()

    check = SqlReminderStore(create_sql_engine(pg_url))
    try:
        rows = await check.all_pending(limit=10)
        assert [r.text for r in rows] == ["洗衫"]
        assert rows[0].requester_person_id == "miles"
        assert rows[0].conversation_id == "g1"
        assert rows[0].state is ReminderState.PENDING
    finally:
        await check.aclose()


async def test_boot_recovery_counts_the_rows_it_swept(cfg, pg_url):
    """The sweep is the only path that resolves reminders with no tick running,
    and `nora_reminders_fired_total`'s spec says a reminder that vanishes must
    never vanish from the metrics too. It logged a count and incremented
    nothing, so a crash that ate reminders left no trace on any dashboard.

    This asserts the WIRING, with a real stranded row in the real database — the
    counting function itself is unit-tested; a function nobody calls would pass
    that test and leave this defect exactly where it was.
    """
    from nora.domain.reminders import Reminder, ReminderState
    from nora.infra.sql import create_sql_engine, run_migrations
    from nora.infra.sql.reminders import SqlReminderStore

    run_migrations(pg_url)
    store = SqlReminderStore(create_sql_engine(pg_url))
    now = datetime.now(UTC)
    await store.create(Reminder(
        id=uuid.uuid4(), requester_person_id="miles", platform="signal",
        conversation_id="g1", conversation_type="group", text="洗衫",
        created_at=now - timedelta(hours=1), due_at=now - timedelta(minutes=5),
        state=ReminderState.FIRING, fired_at=None, outcome=None,
    ))
    await store.aclose()

    deps = await build_dependencies(cfg)

    assert deps.metrics.get(
        "nora_reminders_fired_total", outcome="failed_error",
    ) == 1


async def test_the_liveness_gauge_is_seeded_even_with_the_heartbeat_off(cfg):
    """`[heartbeat] enabled = false` SHIPS, and the gauge's callback was bound
    inside `HeartbeatLoop.__init__` — so on every default deployment the series
    exported `0.0` forever, which reads exactly like "ticked a moment ago". The
    one reading an operator must be able to trust is the one it could not give.
    """
    deps = await build_dependencies(cfg)
    assert cfg.heartbeat.enabled is False
    assert deps.metrics.get(
        "nora_heartbeat_last_tick_age_seconds",
    ) == HEARTBEAT_NOT_RUNNING


async def test_compute_boot_recovery_sweeps_a_stranded_row_even_when_disabled(cfg, pg_url):
    """Mirrors `test_boot_recovery_counts_the_rows_it_swept`: a `FIRING`
    `ComputeJob` (a crash mid-delivery) must resolve to `FAILED` at boot
    whether or not `[compute] enabled` is true — the whole point of the
    unconditional `compute_poller_recover` call in `build_dependencies`
    (composition.py, beside `reminders`' own unconditional sweep). An earlier
    draft gated this call behind `cfg.compute.enabled`, which would leave a
    row stranded forever on exactly the deployment that looks safest: one
    that crashed with compute on and was then switched off, unrelated to the
    crash. `cfg.compute.enabled` is False here (the shipped default) — the
    sweep must still run.
    """
    from nora.domain.compute import ComputeJob, ComputeJobState
    from nora.infra.sql import create_sql_engine, run_migrations
    from nora.infra.sql.compute_jobs import SqlComputeJobStore

    assert cfg.compute.enabled is False

    run_migrations(pg_url)
    store = SqlComputeJobStore(create_sql_engine(pg_url))
    now = datetime.now(UTC)
    await store.create(ComputeJob(
        id=uuid.uuid4(), backend_job_id="job_stranded", task="make a chart",
        artifact_id=None, platform="signal", conversation_id="g1",
        conversation_type="group", requester_person_id="miles",
        created_at=now - timedelta(hours=1), state=ComputeJobState.FIRING,
        completed_at=None, result_url=None, result_summary=None, error=None,
        task_sha256="hash-task", context_sha256="hash-context",
    ))
    await store.aclose()

    deps = await build_dependencies(cfg)

    assert deps.metrics.get(
        "nora_compute_jobs_delivered_total", outcome="failed_error",
    ) == 1


# `[compute] enabled` plus a model URL a job can reach through compute-egress:
# composition refuses to start when the endpoint it hands a job is off
# EGRESS_ALLOWLIST (test_compute_refuses_a_model_url_its_jobs_cannot_reach).
_COMPUTE_ON = {
    "compute.enabled": True,
    "llm.base_url": "http://host.docker.internal:20128/v1",
}


def _compute_environment(monkeypatch, cfg) -> Path:
    """Every host-injected value `[compute] enabled = true` refuses to start
    without, set to a plausible value. Returns the host data root, which is the
    one of them a test may want to assert against.

    Reads the variable NAMES off `cfg` rather than hardcoding them, for the
    reason DEC-8 gives: config names the variable, the host holds the value, and
    a test that hardcodes the name passes after a rename that would break the
    deployment."""
    host_root = Path("/srv/nora/data")
    monkeypatch.setenv(cfg.compute.data_host_root_env, str(host_root))
    monkeypatch.setenv(cfg.shortener.token_env, "test-drop-token")
    monkeypatch.setenv(cfg.route.api_key_env, "test-maps-key")
    return host_root


@pytest.mark.parametrize(
    "unset",
    ["data_host_root_env", "shortener_token_env", "route_api_key_env"],
)
async def test_compute_refuses_to_start_without_its_host_injected_values(
    cfg, monkeypatch, unset,
):
    """`policy.toml`'s discipline (R-5: refuse to start rather than run in a
    state that looks like working), applied to `[compute]`.

    Every one of these is read with a `""` default, so without the check the
    daemon starts CLEANLY and then fails every job minutes later — an empty
    bearer token on the Drop mint, an empty key handed to Smith, or a
    workspace bind-mounted from a host path that does not exist. All three
    surface in the group as «it failed», with an error string nobody can act
    on, and none of them are things a group can fix. They are operator
    problems and they belong in the operator's terminal.

    Parametrized over each one INDIVIDUALLY: a check that only fired when all
    three were missing would pass a single combined test.
    """
    on = override_config(cfg, {**_COMPUTE_ON, "heartbeat.enabled": False})
    _compute_environment(monkeypatch, cfg)
    monkeypatch.delenv({
        "data_host_root_env": cfg.compute.data_host_root_env,
        "shortener_token_env": cfg.shortener.token_env,
        "route_api_key_env": cfg.route.api_key_env,
    }[unset])

    with pytest.raises(StartupError):
        await build_dependencies(on)


@pytest.mark.parametrize("base_url", [
    "http://localhost:20128/v1",               # right port, a host the proxy never allows
    "http://host.docker.internal:8000/v1",     # Honcho's port
])
async def test_compute_refuses_a_model_url_its_jobs_cannot_reach(cfg, monkeypatch, base_url):
    """A job is handed Nora's own `[llm] base_url`, and its only way out is
    `compute-egress`. A URL off EGRESS_ALLOWLIST would fail every job with a
    proxy 403 in front of the group, so it is refused here, at boot."""
    on = override_config(cfg, {**_COMPUTE_ON, "llm.base_url": base_url,
                                "heartbeat.enabled": False})
    _compute_environment(monkeypatch, cfg)

    with pytest.raises(StartupError, match="compute-egress allowlist"):
        await build_dependencies(on)


async def test_compute_refuses_a_relative_host_root(cfg, monkeypatch):
    """`./data` is what the compose mount falls back to when nobody sets the
    variable, and it is exactly the value that must NOT reach the backend: the
    host Docker daemon resolves a bind source against a working directory this
    process knows nothing about. Unset is caught above; a relative value is the
    same defect wearing a plausible-looking string."""
    on = override_config(cfg, {**_COMPUTE_ON, "heartbeat.enabled": False})
    _compute_environment(monkeypatch, cfg)
    monkeypatch.setenv(cfg.compute.data_host_root_env, "./data")

    with pytest.raises(StartupError):
        await build_dependencies(on)


async def test_the_sandbox_bind_source_is_the_host_path_not_noras_own(
    cfg, monkeypatch, tmp_path, no_build_stamp,
):
    """The wiring half of the two-roots rule (`LocalDockerBackend.__init__`).

    Nora's own `store.data_dir` is `/app/data` inside her container, and that
    is the right root for the `task.txt` SHE writes. It is the wrong root for
    the bind-mount source she hands the host Docker daemon, which resolves it
    on the host filesystem — where `/app/data` does not exist, so the daemon
    creates an empty directory and every job silently produces nothing.

    Asserted as a PAIR: same trailing directory name, different roots. Wiring
    that passed `data_dir` to both (the shipped defect) fails the second
    assertion; wiring that appended a different subdirectory to each fails the
    first.
    """
    on = override_config(cfg, {**_COMPUTE_ON, "heartbeat.enabled": False})
    host_root = _compute_environment(monkeypatch, cfg)
    deps = await build_dependencies(on)
    try:
        backend = _spawned(deps, "ComputeJobPoller.run")[0]._backend
        assert backend._workspace_root == cfg.store.data_dir / "compute-workspaces"
        assert backend._workspace_host_root == host_root / "compute-workspaces"
        assert backend._workspace_host_root != backend._workspace_root
    finally:
        await deps.background.cancel_all()


async def test_compute_only_deployment_still_gets_a_live_fire_path(
    cfg, monkeypatch, no_build_stamp,
):
    """The widened `fire_path` gate: `[compute] enabled` alone — heartbeat
    off, no version announcement (`no_build_stamp` forces the unstamped path,
    the same way `test_an_unstamped_build_spawns_no_announcer` does) — must
    still construct a real `FirePath` and spawn `ComputeJobPoller`.

    Before the fix, `fire_path` was built only under `cfg.heartbeat.enabled or
    announcing`; a compute-only deployment (a real, plausible configuration —
    "I just want make_artifact") would build a live `compute_backend`, accept
    `make_artifact` slow-path jobs, and then never spawn a poller to deliver
    them — every such job would sit `pending` forever with no path to ever
    reach the group that asked for it. Reaching into `background._tasks` for
    the same reason `test_the_heartbeat_is_wired_to_the_real_fire_path` does:
    `Dependencies` deliberately exposes neither loop.
    """
    on = override_config(cfg, {
        **_COMPUTE_ON,
        "heartbeat.enabled": False,
    })
    assert on.version.announce is True  # the shipped default; no_build_stamp is what mutes it
    _compute_environment(monkeypatch, cfg)
    deps = await build_dependencies(on)
    try:
        pollers = _spawned(deps, "ComputeJobPoller.run")
        assert pollers, "the compute job poller was not spawned"
        assert isinstance(pollers[0]._speak.__self__, FirePath)
    finally:
        await deps.background.cancel_all()


def _resolved_module_name(rel: Path) -> str:
    """The dotted module name `nora/<rel>` would have at import time, e.g.
    `infra/sql/migrations_runner.py` -> `nora.infra.sql.migrations_runner`."""
    parts = rel.with_suffix("").parts
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(("nora", *parts))


def _resolve_import_from(module_name: str, node: ast.ImportFrom) -> str:
    """Resolve an `ast.ImportFrom` node's target to a fully-qualified dotted name,
    following Python's own relative-import rule so `from ..config import X` inside
    a deeply nested module resolves to the package it actually names — not to
    whatever text happens to follow the dots."""
    if node.level == 0:
        return node.module or ""
    # node.level dots: 1 means "this module's own package"; each extra dot climbs
    # one more level. A non-package module's own package is its dotted name minus
    # its last component.
    package_parts = module_name.split(".")[:-1]
    base_parts = package_parts[: len(package_parts) - (node.level - 1)]
    if node.module:
        base_parts = [*base_parts, node.module]
    return ".".join(base_parts)


def test_only_app_and_main_import_config():
    """Enforced properly by import-linter too (contract 3); asserted here as an
    AST-level check so the rule also has a home in the suite, with no allowlist."""
    offenders = []
    for p in (ROOT / "nora").rglob("*.py"):
        rel = p.relative_to(ROOT / "nora")
        # `app/`, `presence/` and `analytics/` are all composition roots — the
        # three packages allowed to read Config (import-linter contract 3
        # lists none as forbidden), same standing as `__main__.py`/`config.py`
        # themselves.
        #
        # `subject/` is the FOURTH, and it is the one that needs an argument
        # rather than a precedent (`design/evaluation/01-boundaries.md` §6,
        # edit 2 — which that node flagged as arguable before this test ever
        # ran). It is a composition root by the same definition: `obtain()`
        # calls `build_dependencies(cfg)`, so it must build a `Config` to call
        # it with. Composing the real graph means reading the real config, and
        # a subject that read something else would not be Nora — which is the
        # entire property it exists to hold.
        #
        # The exemption is narrow: it admits ONE caller of `build_dependencies`
        # outside the application, and `tests/architecture/
        # test_the_subject_is_obtained_not_assembled.py` holds that there is
        # exactly one. Without that pin this line would be the door the eighth
        # assembly walks through.
        if rel.parts[0] in ("app", "presence", "analytics", "subject") or str(rel) in (
            "__main__.py", "config.py",
        ):
            continue
        module_name = _resolved_module_name(rel)
        tree = ast.parse(p.read_text())
        for n in ast.walk(tree):
            if not isinstance(n, ast.ImportFrom):
                continue
            resolved = _resolve_import_from(module_name, n)
            if resolved == "nora.config" or resolved.startswith("nora.config."):
                offenders.append(str(rel))
    assert not offenders, f"config imported outside app/ and __main__: {offenders}"


# ---- the Dreamer runtime (substrate plan Phase 2) -------------------------


async def test_the_dreamer_runtime_is_not_built_when_it_is_off(cfg):
    """The shipped default. A subsystem that is off builds NOTHING, so a wiring
    mistake in it cannot cost a deployment that never turned it on — the shape
    `language_store` and `portrait_synthesizer` already use."""
    deps = await build_dependencies(cfg)

    assert deps.dreamer_runtime is None
    assert deps.dream_adaptors == ()


async def test_the_dreamer_runtime_is_built_when_it_is_on(cfg):
    deps = await build_dependencies(
        override_config(cfg, {"dreamer.enabled": True})
    )

    assert deps.dreamer_runtime is not None


async def test_the_dreamer_runs_on_a_model_that_can_stop(cfg):
    """The gap between the factory's `tool_choice` parameter and the runtime
    actually getting one: `ModelSource` builds the provider, and it built every
    one from the tag path's spec.

    Under `"required"` the model must call something on every pass, and the
    only thing that ends a turn is a guard observing a TERMINAL call — which
    the Dreamer has none of. So this asserts the property the whole phase
    depends on, at the one place it is decided."""
    deps = await build_dependencies(
        override_config(cfg, {"dreamer.enabled": True})
    )
    assert deps.dreamer_runtime is not None

    model = deps.dreamer_runtime._models.create()
    assert model.config["params"]["tool_choice"] == "auto"


async def test_the_curators_adaptors_are_appended_not_assigned(cfg):
    """The note tier's blocks APPEND to `dream_adaptors` rather than assigning
    it, so a future tier arriving beside them cannot drop one silently. The
    knowledge tier's three adaptors used to be the other half of this test and
    are deleted with it; the append discipline is what survives and is what a
    new tier will land next to.

    Also covers what were `test_the_revisitor_needs_both_the_dreamer_and_the_
    notes_gate`/`test_the_revisitor_is_wired_and_is_not_silently_inert`/
    `test_the_revisitor_is_additive_to_the_fact_tiers_adaptors` before the
    note-curator merge (design/dreamer-agent-loop/34, issue #99) folded
    `NoteRevisitor` into `NoteCuratorAdaptor`: there is one gate now
    (`curator_enabled`, already covered by `test_the_curator_needs_three_gates_
    not_two`/`test_all_three_gates_on_builds_the_curator` above), not two.

    `note-entity-resolution` (issue #100) rode the SAME `curator_enabled`
    gate, appended after `note-curation`, until the entity chain was removed
    on 2026-09-08."""
    deps = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": True, "notes.enabled": True,
        "notes.curator_enabled": True,
    }))

    assert [a.name for a in deps.dream_adaptors] == ["note-curation"]


async def test_every_wired_adaptor_stays_inside_the_read_only_ceiling(cfg, tmp_path):
    """`verify_tool_subsets` runs at startup in R-5's refuse-to-start shape, so
    reaching this line at all is the assertion — a subset naming an act verb
    with no grant behind it would have raised inside `build_dependencies`.

    REWRITTEN ON PURPOSE (design/dreamer-agent-loop/39 D3, DEC-162): the
    ceiling is the read-only set PLUS exactly the adaptor's own grant's
    acts, which for every adaptor but the proactive one is the bare
    ceiling. Restated here against the registry AND the runtime's grants so
    the property is checked rather than merely implied by the absence of
    an exception — and the one act verb that clears it is named, so a
    second one joining silently is red."""
    from nora.agent.registry import ToolRegistry

    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    deps = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": True, "notes.enabled": True,
        "notes.curator_enabled": True, "notes.candidates_enabled": True,
    }))

    ceiling = ToolRegistry().read_only_tools()
    assert deps.dreamer_runtime is not None
    names = [a.name for a in deps.dream_adaptors]
    assert names == ["note-curation", "note-candidates", "proactive"]
    for adaptor in deps.dream_adaptors:
        acts = deps.dreamer_runtime.grant_for(adaptor.name).acts
        assert adaptor.tools <= ceiling | acts, adaptor.name
        assert (adaptor.tools - ceiling) == (
            frozenset({"remind"}) if adaptor.name == "proactive" else frozenset()
        ), adaptor.name


async def test_unit_scopes_are_the_allowlists_units_deduplicated(cfg):
    """Two rooms of one unit are ONE unit of work, not two — which is the
    whole reason a unit-keyed adaptor could not ride the per-scope
    schedule, and the reason this closure exists beside `language_scopes`.

    Sorted, so the tick order is stable between runs of the same build; a set
    would make «did adaptor X run for unit Y this tick» unanswerable from
    a log.

    Asserted against the loaded allowlist rather than a hand-built one, so it
    fails if `Conversation.unit`'s default ever stops being honoured."""
    deps = await build_dependencies(cfg)

    units = deps.unit_scopes()

    assert units == tuple(sorted(set(units)))
    assert units, "a deployment with allowed conversations has a unit"
    # NARROWER than the per-scope population, or the distinction this closure
    # exists for would be untestable here: DEC-105 defaults every conversation
    # to one unit, so several rooms collapse to one key.
    assert len(units) <= len(deps.language_scopes())




# --- Standing / the debrief stage (design/dreamer-agent-loop/26-standing) ---

async def test_standing_disabled_wires_no_debrief_and_no_block(cfg):
    """`[standing] enabled = false` is the shipped default, and OFF has to mean
    two things at once — no fold and no block. Wiring one without the other is
    the failure this asserts against: a store wired into the assembler with no
    `Wiring` renders an empty block forever (harmless), and a `Wiring` with no
    reader folds a position nothing ever shows her (invisible work)."""
    deps = await build_dependencies(cfg)
    assert deps.engagement._debrief_wiring is None
    assert deps.engagement._context._standing_store is None


async def test_standing_enabled_wires_the_same_store_to_both_halves(cfg):
    """**One instance, two callers**, and this is the structural half of §4's
    non-negotiable: the fold must read the position it is about to overwrite.
    Two separate store objects would still satisfy every type here and still be
    the same row, but it is the kind of wiring that invites a later change to
    give the reader a cache — at which point the fold corrects a stale position
    and the contradiction is back."""
    deps = await build_dependencies(override_config(cfg, {"standing.enabled": True}))
    wiring = deps.engagement._debrief_wiring
    assert wiring is not None
    assert wiring.store is deps.engagement._context._standing_store
    assert wiring.max_chars == 1200
    # Read the value from the config under test, not a hardcoded literal —
    # the operator owns fold_batch and changed it 200 -> 40 on 2026-09-05
    # (0d96b49c), which silently broke this assertion.
    assert wiring.fold_batch == cfg.standing.fold_batch
    assert wiring.record_store is deps.ingest._audit


async def test_the_debrief_model_defaults_to_the_reply_model(cfg):
    """`[standing] model = ""` means `[llm] model`, the same defaulting
    `[language] exchange_model` uses. An empty string reaching the provider
    would be a 400 on the first fold, in production, after a config change that
    looked like it left the model alone."""
    deps = await build_dependencies(override_config(cfg, {"standing.enabled": True}))
    assert deps.engagement._debrief_wiring.client.model == cfg.llm.model


async def test_the_debrief_reads_its_objective_from_the_instruction_substrate(cfg):
    """The text is a SOURCE, not a string, so an operator's edit on the bind
    mount reaches the next fold rather than the next rebuild. Asserted by
    actually resolving the objective, because a `Wiring` holding an object with
    the right method name and no such objective behind it would look identical
    here and fail on the first fold."""
    from nora.engagement.stages.debrief import OBJECTIVE

    deps = await build_dependencies(override_config(cfg, {"standing.enabled": True}))
    body = deps.engagement._debrief_wiring.objective.objective_body(OBJECTIVE)
    assert "revise ONE short paragraph" in body


def _effort_of(client: object) -> str:
    """The `reasoning_effort` actually on the model provider's params."""
    provider = client._provider
    return str((provider.config.get("params") or {}).get("reasoning_effort", ""))


async def test_the_debrief_declares_a_reasoning_effort_rather_than_inheriting_silently(cfg):
    """It passed none at all, so the fold ran at the provider's default — an
    unchosen setting on a call that spends the conversation's slot, while the
    tag path passes `[llm] reasoning_effort` explicitly.

    Empty means inherit `[llm]`, which is the honest default while the spike
    that would CHOOSE a value has not been run: `18-flows.md` calls the fold's
    effort "a measurement, not a setting" and `09-measurement.md` forbids
    asserting the plausible-sounding answer without it.
    """
    # PRECEDENCE, not one branch of it: an explicit `[standing]` value wins,
    # and empty falls back to `[llm]`. The first version asserted only the
    # fallback and went red the moment the operator set a value — which is a
    # test pinning today's config rather than the rule.
    explicit = await build_dependencies(override_config(
        cfg, {"standing.enabled": True, "standing.reasoning_effort": "none"}))
    # Read off the PROVIDER the client built, not a field it stored. Since the
    # fold moved onto the Agent SDK the effort's whole job is to reach the
    # request; an attribute nobody sends would satisfy a stored-field check and
    # change nothing about the call.
    assert _effort_of(explicit.engagement._debrief_wiring.client) == "none"

    inherited = await build_dependencies(override_config(
        cfg, {"standing.enabled": True, "standing.reasoning_effort": ""}))
    assert (_effort_of(inherited.engagement._debrief_wiring.client)
            == cfg.llm.reasoning_effort)


async def test_an_unusable_reasoning_effort_refuses_to_start(cfg):
    """A typo currently reaches the provider and returns a 400 DURING a fold,
    inside the slot, on a turn that had already replied. Narrowed at
    composition so it is a boot failure instead — the same refuse-to-start
    posture `max_chars`-above-the-ceiling and the slot-hold budget use."""
    # `standing.reasoning_effort` is a typed Literal, so pydantic refuses a typo
    # there before composition ever runs. The reachable hole is the INHERITED
    # `[llm]` value, which is a bare `str` — so this leaves standing's empty and
    # corrupts the one it falls back to.
    with pytest.raises(StartupError, match="reasoning effort"):
        await build_dependencies(override_config(cfg, {
            "standing.enabled": True,
            "standing.reasoning_effort": "",
            "llm.reasoning_effort": "hgh",
        }))


async def test_the_curator_needs_three_gates_not_two(cfg):
    """`[dreamer]` decides the runtime exists, `[notes] enabled` decides the
    tier is on, and `curator_enabled` decides a background pass MERGES AND
    RETIRES.

    Three rather than two because the third is a different risk from the other
    two: the tag path only reads, and the curator is the one thing in this tier
    that REMOVES something from her index. An operator turning notes on has not
    thereby signed up for a pass that reorganises them."""
    for overrides in (
        {"dreamer.enabled": True},
        {"dreamer.enabled": True, "notes.enabled": True},
        {"notes.enabled": True, "notes.curator_enabled": True},
    ):
        deps = await build_dependencies(override_config(cfg, overrides))
        assert "note-curation" not in [a.name for a in deps.dream_adaptors], (
            f"{overrides} must not produce a curator"
        )


async def test_all_three_gates_on_builds_the_curator(cfg):
    """Also covers what the pre-merge `test_the_revisitor_is_wired_and_is_not_
    silently_inert` asserted: `objective`/`envelope_key` are BOTH load-bearing
    against the Dreamer's egress screen (a note id in an envelope item is
    dropped wholesale before `accept` ever runs, which is what made an earlier
    revision of the note-revisit machinery 100% inert with no counter of its
    own moving), and `tools == frozenset()` is the reaching-a-person half
    staying deliberately unbuilt post-merge too."""
    deps = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": True,
        "notes.enabled": True,
        "notes.curator_enabled": True,
    }))

    curator = next(
        a for a in deps.dream_adaptors if a.name == "note-curation"
    )
    # Unit-keyed and watermark-free: the notebook is unit-scoped, and
    # the questions it asks (what collides, what has expired, what is due) have
    # no "since when" — a duplicate is a duplicate however long it has been
    # one.
    assert curator.key_kind == "unit"
    assert curator.uses_watermark is False
    assert curator.tools == frozenset(), "the curator has nothing to look up"
    assert curator.objective == "note-curation"
    assert curator.envelope_key == "verdicts"


async def test_the_curator_is_scheduled_as_a_true_cron_daily(cfg):
    """Issue #102 / design 33 §5: the curator is time-driven, not data-driven
    — an anchor date passes whether or not the room speaks — so it needs the
    non-watermark schedule path (33 §5 N1a) rather than every-tick-with-
    empty-`inputs()`-suppression. Daily, matching the fact tier's own cadence
    (33 §5: "the curator is the tier's decay — daily matches the fact tier's
    cadence")."""
    deps = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": True,
        "notes.enabled": True,
        "notes.curator_enabled": True,
    }))

    schedule = deps.dream_schedules["note-curation"]
    assert schedule.min_interval_seconds == 86400, "daily (design 33 §5)"
    # The slot change gave every watermark-free daily adaptor a time of day.
    # An unset offset is 00:00 UTC = 08:00 Asia/Hong_Kong, eight hours behind
    # the local day boundary the test below argues is the one that matters.
    # 16:00 UTC is HK midnight — pinned so it stays a choice, not a default.
    assert schedule.phase_offset_seconds == 16 * 3600, "HK midnight, not 08:00"


async def test_note_merge_is_built_only_off_its_own_switch_and_samples_once(cfg):
    """Design 41 §7.3/§7.8: `[notes] merge_mode = "off"` (the default) builds
    nothing; `"shadow"` builds the pass, and its schedule PINS one sample —
    the runtime unions samples, so a second one could only add folds, the
    direction this pass must not fail in."""
    off = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": True, "notes.enabled": True,
    }))
    assert "note-merge" not in [a.name for a in off.dream_adaptors]

    deps = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": True, "notes.enabled": True,
        "notes.merge_mode": "shadow",
    }))
    merge = next(a for a in deps.dream_adaptors if a.name == "note-merge")
    assert merge.key_kind == "unit" and merge.uses_watermark is False
    assert merge.tools == frozenset()
    assert merge._mode == "shadow"
    assert deps.dream_schedules["note-merge"].samples == 1


async def test_every_watermark_free_adaptor_that_is_wired_has_a_schedule(cfg):
    """**The control that would have caught the bug this branch fixes.**

    A watermark-free adaptor with no `dream_schedules` entry inherits the bare
    5-minute tick. `fact-adjudication` and `fact-entity-resolution` did, for
    eleven days, while `config.toml` claimed otherwise — and it was invisible
    because both return `nothing_found` in ~0.4s on an empty queue, which is
    what a healthy quiet tier looks like.

    Asserting the two names is what the branch originally did and is not
    enough: the next adaptor added would repeat it. This asserts the PROPERTY
    over whatever is actually wired, so a new watermark-free adaptor either
    gets a deliberate schedule or fails here.

    A watermark-BEARING adaptor is exempt: it has a cursor to age, and no
    entry means the shared `[dreamer]` lag/quiet/age shape, which is a real
    schedule rather than every-tick.
    """
    deps = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": True,
        "notes.enabled": True,
        "notes.curator_enabled": True,
    }))

    unscheduled = sorted(
        a.name for a in deps.dream_adaptors
        if not a.uses_watermark and a.name not in deps.dream_schedules
    )
    assert not unscheduled, (
        "watermark-free adaptor(s) with no AdaptorSchedule — each one runs on "
        f"the bare 5-minute tick: {unscheduled}"
    )


async def test_the_curator_reads_the_day_from_the_operators_timezone(cfg):
    """«Has the 9th passed» is a question about the ROOM's day, not the
    process's — a UTC boundary retires the 9th's note during the evening of the
    9th, local, while people are still deciding where to eat.

    **This test was decorative until 2026-08-28**, proven by mutation:
    replacing `ZoneInfo(cfg.reminders.timezone)` with `ZoneInfo("UTC")` left it
    green, because it set a zone and then asserted only `_today() >= 20260101`
    — true of any working clock. It NAMED the thing it did not measure, which
    is the shape I documented in the bundle an hour before writing it.

    It now builds two curators whose zones are 25 hours apart. Kiritimati is
    +14 and Niue is -11, so their calendar days differ at EVERY instant and no
    clock-freezing is needed. Any hardcoded zone makes them equal — which is
    the property under test, rather than a fact about today."""
    async def _day(zone: str) -> int:
        deps = await build_dependencies(override_config(cfg, {
            "dreamer.enabled": True,
            "notes.enabled": True,
            "notes.curator_enabled": True,
            "reminders.timezone": zone,
        }))
        curator = next(
            a for a in deps.dream_adaptors if a.name == "note-curation"
        )
        return curator._today()

    earliest = await _day("Pacific/Kiritimati")
    latest = await _day("Pacific/Niue")

    assert earliest != latest, (
        "zones 25 hours apart always differ in calendar day; equal here means "
        "the configured zone is being ignored"
    )
    assert earliest > latest, "Kiritimati is ahead of Niue, always"


async def test_note_candidates_needs_three_gates_not_two(cfg):
    """`[dreamer]` decides the runtime exists, `[notes] enabled` decides the
    tier is on, and `candidates_enabled` decides a background pass WRITES NEW
    NOTES into that index at all — the create path's own two-risks argument,
    on `curator_enabled`'s own precedent (design 33 §4, issue #98)."""
    for overrides in (
        {"dreamer.enabled": True},
        {"dreamer.enabled": True, "notes.enabled": True},
        {"notes.enabled": True, "notes.candidates_enabled": True},
    ):
        deps = await build_dependencies(override_config(cfg, overrides))
        assert "note-candidates" not in [a.name for a in deps.dream_adaptors], (
            f"{overrides} must not produce the create-path adaptor"
        )


async def test_all_three_gates_on_builds_note_candidates(cfg):
    deps = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": True,
        "notes.enabled": True,
        "notes.candidates_enabled": True,
    }))

    candidates = next(
        a for a in deps.dream_adaptors if a.name == "note-candidates"
    )
    # Scope-keyed and watermarked, unlike the curator/revisitor: a note's
    # arrangement spans days of conversation, so this needs a per-room
    # watermark the same way `fact-candidates` does.
    assert candidates.key_kind == "scope"
    assert candidates.uses_watermark is True
    # A RESEARCH LOOP (design 43 §2): every read verb the registry admits,
    # DERIVED off its ceiling, less only the named withholdings — and never an
    # act verb, because the ceiling it is derived from holds none.
    from nora.agent.registry import ToolRegistry
    from nora.language.note_candidates import WITHHELD_TOOLS

    ceiling = ToolRegistry.read_only_tools(None)  # type: ignore[arg-type]
    assert candidates.tools == ceiling - frozenset(WITHHELD_TOOLS), (
        "the pass holds every allowed read verb, derived, not a hand list"
    )
    assert candidates.tools <= ceiling, "the create path never gets a write tool"
    assert {"load_transcript", "ask_memory", "note_read"} <= candidates.tools
    schedule = deps.dream_schedules["note-candidates"]
    assert schedule.min_interval_seconds == 3600, "hourly (design 33 §5)"


async def test_note_candidates_builds_its_own_unit_resolver(cfg):
    """`unit_for` is built fresh under the notes gate rather than reused
    from another tier's block — this must not depend on any other tier having
    run at all."""
    deps = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": True,
        "notes.enabled": True,
        "notes.candidates_enabled": True,
    }))

    assert any(a.name == "note-candidates" for a in deps.dream_adaptors)


async def test_note_candidates_resolves_a_mapped_senders_canonical_name(cfg):
    """#200 part 1: the composition root wires `persons.name_transcript` into
    `NoteCandidateAdaptor` the way `MediaJoin` gets `scan_identifiers` —
    nothing on the Dreamer path ever called it before, so a mapped sender's
    batch line carried their self-set, spoofable platform profile name
    instead of the operator's canonical one for them (`identity.toml`'s
    `[persons.miles]` maps signal `+852111` to `Miles`, the `cfg` fixture's own
    shape)."""
    deps = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": True,
        "notes.enabled": True,
        "notes.candidates_enabled": True,
    }))
    candidates = next(
        a for a in deps.dream_adaptors if a.name == "note-candidates"
    )
    batch = (
        TranscriptRow(
            id=1, sender_id="+852111", sender_name="not-miles", ts=1_000,
            body="dinner Saturday?", is_nora=False,
            delivery_status=DeliveryStatus.SENT, raw_json=None,
        ),
    )

    text = await candidates.inputs(key="g1", batch=batch)

    assert 'from="Miles"' in text
    assert "not-miles" not in text


async def test_culture_resolves_a_mapped_senders_canonical_name(cfg):
    """#200 follow-up: the review on #544 found `CultureAdaptor` still built
    its own `<room_messages>` block from the raw batch and never called
    `Persons.name_transcript` either — the SAME gap, one tier over. Wired
    the same way `NoteCandidateAdaptor` already is: off `persons` and the
    allowlist's `platform_for` (`identity.toml`'s `[persons.miles]` maps
    signal `+852111` to `Miles`, the `cfg` fixture's own shape)."""
    deps = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": True, "culture.enabled": True,
    }))
    culture = next(a for a in deps.dream_adaptors if a.name == "culture")
    batch = (
        TranscriptRow(
            id=1, sender_id="+852111", sender_name="not-miles", ts=1_000,
            body="dinner Saturday?", is_nora=False,
            delivery_status=DeliveryStatus.SENT, raw_json=None,
        ),
    )

    text = await culture.inputs(key="g1", batch=batch)

    assert 'from="Miles"' in text
    assert "not-miles" not in text


async def test_note_curator_resolves_a_mapped_senders_canonical_name(cfg):
    """#200 follow-up: `NoteCuratorAdaptor` carries the same gap a tier over
    again — `_gather_evidence` builds a note's evidence rows off
    `record.recent` and never called `Persons.name_transcript` either.
    Composition wires the resolver the same way, keyed as `room_for` already
    is (every id a room is known by, within its unit — `Note.established_in`
    carries no platform of its own).

    Exercised against the built closures directly rather than through a
    seeded note: reaching `_gather_evidence` needs a real due-or-expired
    note in `SqlNoteStore`, which is `tests/unit/test_note_curator.py`'s own
    job (revert-proved there against a fake resolver). What only the
    composition root can get wrong is whether these two callables reach the
    adaptor at all, and resolve against the SAME allowlist row `room_for`
    does — which is what this pins."""
    deps = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": True,
        "notes.enabled": True,
        "notes.curator_enabled": True,
    }))
    curator = next(
        a for a in deps.dream_adaptors if a.name == "note-curation"
    )

    assert curator._platform_for("g1", "nora") == "signal"
    assert curator._platform_for("g1", "some-other-unit") is None
    assert curator._platform_for("no-such-room", "nora") is None

    batch = (
        TranscriptRow(
            id=1, sender_id="+852111", sender_name="not-miles", ts=1_000,
            body="dinner Saturday?", is_nora=False,
            delivery_status=DeliveryStatus.SENT, raw_json=None,
        ),
    )
    resolved = curator._resolve_names(batch, "signal")

    assert resolved[0].person_name == "Miles"


async def test_the_dreamer_model_is_built_with_the_dreamers_own_effort(cfg):
    """**A config key nothing reads is the phantom-metric rule again**, and
    the first version of this change had exactly that: three config tests
    asserting the VALUE, and removing the line in composition that uses it
    left them all green.

    The effort is baked into the model object at construction rather than
    passed per call, so the only place it can be observed is the spec the
    Dreamer's own `ModelSource` was built with. Asserted with a value that is
    NOT the tier's, so a composition still reading `[llm]` fails rather than
    coincidentally agreeing.
    """
    deps = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": True,
        "dreamer.reasoning_effort": "xhigh",
    }))

    assert deps.dreamer_runtime is not None, "the runtime must be built at all"
    spec = deps.dreamer_runtime._models._spec
    assert spec.reasoning_effort == "xhigh"


async def test_an_unset_dreamer_effort_leaves_the_model_on_the_tiers(cfg):
    """The inherit path, and the positive control for the test above: a
    composition that hard-coded the Dreamer's key would pass the first
    assertion and break every deployment that never set it."""
    deps = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": True,
        "dreamer.reasoning_effort": "",
    }))

    assert deps.dreamer_runtime is not None
    spec = deps.dreamer_runtime._models._spec
    assert spec.reasoning_effort == cfg.llm.reasoning_effort


async def test_the_dreamer_is_built_with_its_own_per_call_max_tokens(cfg):
    """Issue #291: the Dreamer's PER-CALL ceiling is its own, not the tier's.

    Asserted on the SPEC and not on the config value, for the reason the
    effort test above states outright — a config key nothing reads is a
    phantom. Reverting `composition.py` to `max_tokens=tier.max_tokens` must
    turn THIS red, so the value asserted is deliberately not the tier's.

    This is the ceiling that raises. `[dreamer.budgets.*] output_tokens` is
    the Strands LOOP budget and binds without raising
    (`nora/agent/factory.py:238`); exceeding THIS one raises
    `MaxTokensReachedException` -> `PartialReplyError` (`factory.py:541`),
    which is the error that held one scope's watermark for ~24 hours.
    """
    deps = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": True,
        "dreamer.max_tokens": 16000,
    }))

    assert deps.dreamer_runtime is not None, "the runtime must be built at all"
    spec = deps.dreamer_runtime._models._spec
    assert spec.max_tokens == 16000
    assert spec.max_tokens != cfg.llm.max_tokens, (
        "the fixture's tier value would make this test pass by coincidence"
    )


async def test_an_unset_dreamer_max_tokens_leaves_it_on_the_tiers(cfg):
    """The inherit path, and the positive control for the test above.

    `0` is the sentinel. A composition that read the raw field instead of
    `Config.dreamer_max_tokens()` would build the Dreamer with `max_tokens=0`
    — not "no limit" but a model asked to emit nothing — and every deployment
    that never set the key would break. That is the failure this pins.
    """
    deps = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": True,
        "dreamer.max_tokens": 0,
    }))

    assert deps.dreamer_runtime is not None
    spec = deps.dreamer_runtime._models._spec
    assert spec.max_tokens == cfg.llm.max_tokens
    assert spec.max_tokens > 0, "0 is the inherit sentinel, never a ceiling"


async def test_a_misspelt_inherited_effort_refuses_to_start(cfg):
    """**The typed declaration alone does not give the boot refusal it claims.**

    `[dreamer] reasoning_effort` is typed, so it cannot arrive wrong. But `""`
    is its default AND its documented inherit value — and what it inherits is
    `[llm] reasoning_effort`, a bare `str`. Unwrapped, `[llm] = "hgh"` with
    `[dreamer] = ""` LOADS and resolves to `"hgh"`, which reaches the provider
    as a 400 mid-tick and surfaces as one more `unavailable` in a background
    pass nobody watches — the exact scenario the declaration's comment says is
    now impossible.

    Copying `StandingConfig`'s declaration without its RESOLUTION is half the
    precedent; `_reasoning_effort` is the other half and exists for this case.
    """
    with pytest.raises(StartupError, match="reasoning effort"):
        await build_dependencies(override_config(cfg, {
            "dreamer.enabled": True,
            "dreamer.reasoning_effort": "",
            "llm.reasoning_effort": "hgh",
        }))


async def test_a_valid_inherited_effort_still_starts(cfg):
    """The positive control: a refusal that fired on every inherited value
    would satisfy the test above while making `""` — the default — unusable."""
    deps = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": True,
        "dreamer.reasoning_effort": "",
        "llm.reasoning_effort": "medium",
    }))

    assert deps.dreamer_runtime is not None
    assert deps.dreamer_runtime._models._spec.reasoning_effort == "medium"


async def test_candidate_samples_reaches_the_note_candidates_schedule(cfg):
    """`[notes] candidate_samples` is model calls per fire, unioned (design 37
    / issue #118). A knob that did not reach `AdaptorSchedule.samples` would
    be the config-file form of a declared metric with no call site — set and
    read by nothing."""
    async def _samples(n: int) -> int:
        deps = await build_dependencies(override_config(cfg, {
            "dreamer.enabled": True, "notes.enabled": True,
            "notes.candidates_enabled": True, "notes.candidate_samples": n,
        }))
        return deps.dream_schedules["note-candidates"].samples

    assert await _samples(1) == 1
    assert await _samples(4) == 4


async def test_note_candidates_renders_moments_in_the_operators_timezone(cfg):
    """The sibling of `test_the_curator_reads_the_day_from_the_operators_
    timezone`, for the create path: «this Saturday» in a message is resolved
    against the moment the model is SHOWN, so that moment must be the room's
    (`[reminders] timezone`), not the process's. Two zones 25 hours apart
    cannot agree; a hardcoded zone makes them equal."""
    from zoneinfo import ZoneInfo

    async def _zone(zone: str):
        deps = await build_dependencies(override_config(cfg, {
            "dreamer.enabled": True, "notes.enabled": True,
            "notes.candidates_enabled": True, "reminders.timezone": zone,
        }))
        adaptor = next(a for a in deps.dream_adaptors if a.name == "note-candidates")
        return adaptor._tz

    assert await _zone("Pacific/Kiritimati") == ZoneInfo("Pacific/Kiritimati")
    assert await _zone("Pacific/Niue") == ZoneInfo("Pacific/Niue")
    assert await _zone("Pacific/Kiritimati") != await _zone("Pacific/Niue")


# --- the proactive adaptor (design/dreamer-agent-loop/39, DEC-162) -----------




async def test_a_home_room_wires_the_proactive_adaptor_its_schedule_and_its_grant(
    cfg, tmp_path,
):
    """39 D2: the home room IS the opt-in. With one named, the Dreamer holds
    the proactive adaptor on a daily HK-morning slot, the runtime holds its
    grant, and the tool source holds the room — the same room, read once."""
    from nora.agent.dreamer import proactive_grant

    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    deps = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": True, "notes.enabled": True, "heartbeat.enabled": True,
    }))

    assert [a.name for a in deps.dream_adaptors] == ["proactive"]
    sched = deps.dream_schedules["proactive"]
    # Hourly (issue #136): the tick is the ceiling on how soon a change is
    # seen; the adaptor's own check decides whether a tick renders, and the
    # daily floor lives there (07:00 HK), not in a phase offset.
    assert sched.min_interval_seconds == 3600
    assert sched.phase_offset_seconds == 0
    proactive = next(a for a in deps.dream_adaptors if a.name == "proactive")
    assert proactive._clock_store is deps.dream_schedule_clock
    assert proactive._records is not None
    assert proactive._quiet_ms == 1800 * 1000        # [dreamer] quiet_seconds, the template's
    assert deps.dreamer_runtime is not None
    assert deps.dreamer_runtime.grant_for("proactive") == proactive_grant()
    assert deps.dreamer_runtime.grant_for("note-curation").acts == frozenset()
    source = deps.dreamer_runtime._tools
    assert set(source._home_rooms) == {"friends"}
    home = source._home_rooms["friends"]
    assert (home.platform, home.conversation_id, home.location_trusted) == ("signal", "g1", True)


async def test_the_culture_adaptor_is_wired_with_its_grant_and_its_OWN_room(
    cfg, tmp_path,
):
    """**The end of the wiring defect** (design/culture-tier `03-decisions`
    correction 14). Three things have to line up or the grant is inert and
    nothing says so: the runtime must hold a grant under the adaptor's OWN name
    (a second spelling is a grant that silently does not apply), the tool
    source must hold this key's room in `scope_rooms`, and the room must carry
    the platform — without it there is no roster source, `view.available` is
    `False`, and every room-scoped verb answers `unavailable` forever while
    every counter stays green.

    `home_rooms` is asserted NOT to be the resolution path: culture is
    scope-keyed and `DreamerRuntime.run` supplies `unit=None` for it, so a
    resolution through `home_rooms` would look up `""` and find nothing — which
    is exactly the branch this change replaces."""
    from nora.agent.dreamer import culture_grant

    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    deps = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": True, "culture.enabled": True,
    }))

    assert "culture" in [a.name for a in deps.dream_adaptors]
    assert deps.dreamer_runtime is not None
    assert deps.dreamer_runtime.grant_for("culture") == culture_grant()
    source = deps.dreamer_runtime._tools
    # Every policy conversation, by every id form it answers to — the second
    # room too, which has no `home_room = true` and needs none.
    assert set(source._scope_rooms) == {"g1", "g2"}
    room = source._scope_rooms["g1"]
    assert (room.platform, room.conversation_id) == ("signal", "g1")
    assert room.location_trusted is True and room.unit == "friends"
    assert "g1" not in source._home_rooms


async def test_the_granted_verbs_are_actually_BUILT_for_a_culture_key(cfg, tmp_path):
    """The property the grant exists for, measured at the seam rather than
    inferred from it: `RegistryToolSource.build` with the culture adaptor's own
    name, key and grant returns the room-scoped verbs — including `where_been`,
    which was in no adaptor's grant at all before this — over a room with a
    real platform rather than the empty one.

    Without the scope-keyed resolution this returns the same NAMES built
    against `Room(platform="", roster_source=None)`, which is why the room is
    asserted too: a green name list is exactly what the defect looked like."""
    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    deps = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": True, "culture.enabled": True,
    }))

    assert deps.dreamer_runtime is not None
    source = deps.dreamer_runtime._tools
    culture = next(a for a in deps.dream_adaptors if a.name == "culture")
    # THE ROOM, captured off the one call that receives it. Asserting the
    # returned NAMES alone does not measure this property and a mutation
    # proved it: with the grant reverted to `AdaptorGrant()` all nine verbs
    # still build — they are read-only, so the ceiling admits them — over
    # `Room(platform="", roster_source=None)`, where every one of them answers
    # `unavailable` forever. A green name list IS the defect.
    captured: dict[str, object] = {}
    real_surface = source._registry.surface

    def _spy(**kw: object) -> object:
        captured.update(kw)
        return real_surface(**kw)

    source._registry.surface = _spy
    try:
        built = source.build(
            key="g1", names=frozenset(culture.tools), unit=None,
            adaptor="culture", grant=deps.dreamer_runtime.grant_for("culture"),
        )
    finally:
        del source._registry.surface

    names = {
        getattr(t, "tool_name", None) or getattr(t, "__name__", "") for t in built
    }
    assert names == set(culture.tools), sorted(set(culture.tools) - names)
    assert "where_been" in names and "ask_memory" in names
    room = captured["room"]
    assert room.platform == "signal", "the verbs were built over the EMPTY room"
    assert room._roster_source is not None, "no roster: every verb is unavailable"
    assert captured["conversation_id"] == "g1"
    assert captured["location_trusted"] is True
    assert captured["supplied_unit"] is None


async def test_no_home_room_wires_no_proactive_adaptor(cfg):
    deps = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": True, "notes.enabled": True,
    }))

    assert "proactive" not in [a.name for a in deps.dream_adaptors]
    assert "proactive" not in deps.dream_schedules


async def test_the_heartbeat_screens_proactive_rows_and_the_fire_path_can_stay_silent(
    cfg, tmp_path,
):
    """39 D5/D11/D12 at the root: the heartbeat holds a `ProactiveScreen` over
    the SAME allowlist and note store, `event_registry` maps the kind, and
    the fire path holds an auto-runner under `proactive_runner` even with
    participation off. Reached the way the heartbeat wiring test reaches
    it: `Dependencies` deliberately exposes neither loop."""
    from nora.app.proactive_screen import ProactiveScreen

    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    deps = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": True, "notes.enabled": True, "heartbeat.enabled": True,
        "participation.enabled": False,
    }))
    try:
        (loop,) = _spawned(deps, "HeartbeatLoop.run")
        assert isinstance(loop._proactive, ProactiveScreen)
        assert loop._proactive._policy is loop._policy
        path = loop._fire.__self__
        assert isinstance(path, FirePath)
        assert "proactive" in path._event_registry
        assert path._proactive_runner is not None
        assert path._participation_runner is None
    finally:
        await deps.background.cancel_all()


async def test_a_home_room_keeps_the_proactive_runner_even_with_the_dreamer_off(
    cfg, tmp_path,
):
    """Audit 2026-09-09: a proactive row written yesterday still comes due
    after `[dreamer] enabled` is flipped off, and the screen admits it — so
    the act turn's runner is gated on the home room (the row's existence
    condition), not on the adaptor being wired. Without it every such row
    would die `failed_error`."""
    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    deps = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": False, "notes.enabled": True, "heartbeat.enabled": True,
        "participation.enabled": False,
    }))
    try:
        assert "proactive" not in [a.name for a in deps.dream_adaptors]
        (loop,) = _spawned(deps, "HeartbeatLoop.run")
        path = loop._fire.__self__
        assert path._proactive_runner is not None
    finally:
        await deps.background.cancel_all()


async def test_the_instagram_backend_is_composed_with_the_person_index(cfg):
    """design/instagram-adoption/18, P5. `[social.instagram] enabled` composes
    the bridge client behind BOTH Instagram rows, with `PersonsSubjectIndex`
    beside it. The router refuses to be built with a person-keyed backend and
    no index, so a composition that forgot the index would not boot — and one
    that wired only the story row would leave profiles `unavailable` forever
    while the flag read as on."""
    from nora.infra.social import (
        EXCEPTION_ROUTES,
        InstagramSocialClient,
        PersonsSubjectIndex,
        SocialRouter,
    )

    deps = await build_dependencies(override_config(cfg, {"social.instagram.enabled": True}))
    try:
        router = deps.engagement._tool_registry._social
        assert isinstance(router, SocialRouter)
        assert isinstance(router._subjects, PersonsSubjectIndex)
        assert set(router._exceptions) == EXCEPTION_ROUTES
        assert all(
            isinstance(client, InstagramSocialClient)
            for client in router._exceptions.values()
        )
        # P10: a handle or a link resolves through the SAME client's exact
        # username lookup. Without it every handle and link would answer
        # `unavailable` while the flag read as on.
        assert isinstance(router._accounts, InstagramSocialClient)
    finally:
        await deps.background.cancel_all()


@pytest.mark.parametrize("linkedin_on", [False, True])
async def test_the_linkedin_tools_get_the_router_only_when_switched_on(cfg, linkedin_on):
    """design/social-per-network/10 §Config (DEC-201). The router's one
    default backend serves X AND LinkedIn, so LinkedIn's switch is at the
    composition root: `[social.linkedin] enabled` hands the SAME router to the
    LinkedIn tools, and off hands them `None` while X keeps it. A root that
    ignored the flag would read LinkedIn on a deployment whose operator never
    set up a dedicated, private-viewing account."""
    from nora.infra.social import SocialRouter

    deps = await build_dependencies(override_config(cfg, {
        "social.enabled": True, "social.linkedin.enabled": linkedin_on,
    }))
    try:
        registry = deps.engagement._tool_registry
        assert isinstance(registry._social, SocialRouter)
        if linkedin_on:
            assert registry._linkedin is registry._social
        else:
            assert registry._linkedin is None
    finally:
        await deps.background.cancel_all()


@pytest.mark.parametrize(("instagram", "summons"), [(False, False), (True, False)])
async def test_the_summons_consumer_is_not_spawned_unless_both_flags_are_on(
    cfg, instagram, summons,
):
    """design/instagram-adoption/19: the summons consumer runs only under
    `[social.instagram] enabled` AND `[social.instagram.summons] enabled`
    (the config refuses the second without the first). Off is the shipped
    state, and a summons spends requests on the session her group chats run
    on, so off must mean not spawned."""
    on = override_config(cfg, {
        "social.instagram.enabled": instagram,
        "social.instagram.summons.enabled": summons,
    })
    deps = await build_dependencies(on)
    try:
        assert not _spawned(deps, "SummonsConsumer.run")
    finally:
        await deps.background.cancel_all()


async def test_the_summons_consumer_is_spawned_with_both_flags_on_and_no_poller_remains(cfg):
    """The positive half, so the test above cannot pass against a feature
    that never wires at all. The consumer drains an ENABLED inbox (the same
    `summons_wired` gate), its occasion holds an `AFTER_SPEAKING` runner
    (silence is expressible) and the mode the config names. And nothing polls
    the notification feed any more: no poller task, no poller module."""
    import importlib.util

    on = override_config(cfg, {
        "social.instagram.enabled": True,
        "social.instagram.summons.enabled": True,
    })
    deps = await build_dependencies(on)
    try:
        (consumer,) = _spawned(deps, "SummonsConsumer.run")
        assert consumer._inbox.enabled is True
        occasion = consumer._occasion
        assert occasion.mode == "dry_run"
        spec = occasion._runner._models._spec
        assert spec.tool_choice == tool_choice.AFTER_SPEAKING
        assert not _spawned(deps, "SummonsPoller.run")
        assert importlib.util.find_spec("nora.app.summons_poller") is None
    finally:
        await deps.background.cancel_all()


async def test_cognition_off_builds_no_stimulus_stage_and_no_evaluator(cfg):
    """`[cognition] mode = "off"` is the shipped default: nothing is written
    to `stimulus`, no evaluator is spawned. Adapter health is recorded anyway —
    it is Record, not a subsystem that acts (20-observe.md §1)."""
    deps = await build_dependencies(cfg)
    assert deps.wake_evaluator is None
    assert deps.ingest._stimulus_wiring is None
    assert deps.health_recorder is not None


async def test_cognition_shadow_adds_the_evaluator_and_leaves_the_clocks_alone(cfg):
    """Migration step 1: «the wake evaluator in shadow ... the clocks still run
    everything». Turning shadow on adds a stimulus stage and an evaluator and
    changes nothing the Dreamer's clock schedules."""
    clocks = {"dreamer.enabled": True, "notes.enabled": True,
              "notes.curator_enabled": True, "notes.candidates_enabled": True}
    shadow_on = override_config(cfg, {**clocks, "cognition.mode": "shadow"})
    clocks_only = override_config(cfg, clocks)
    shadow = await build_dependencies(shadow_on)
    baseline = await build_dependencies(clocks_only)
    assert shadow.wake_evaluator is not None
    assert shadow.ingest._stimulus_wiring is not None
    assert baseline.dream_adaptors, "positive control: the clock schedules something"
    assert [a.name for a in shadow.dream_adaptors] == [a.name for a in baseline.dream_adaptors]
    assert set(shadow.dream_schedules) == set(baseline.dream_schedules)


# --- the shadow Cognition session (migration step 3a-iii, DEC-244) -----------

_SESSION_ON = {
    "dreamer.enabled": True, "notes.enabled": True,
    "cognition.mode": "shadow", "cognition.session_mode": "shadow",
}


async def test_session_mode_off_builds_nothing_model_related(cfg, tmp_path, monkeypatch):
    """The shipped default: no runner, no hook on the evaluator, no shutdown
    to register, and `nora.app.cognition_agent_runner` (Strands and the model
    machinery) is not even imported."""
    import sys

    monkeypatch.delitem(sys.modules, "nora.app.cognition_agent_runner", raising=False)
    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    deps = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": True, "notes.enabled": True, "cognition.mode": "shadow",
    }))
    assert deps.wake_evaluator is not None, "positive control: the evaluator is on"
    assert deps.wake_evaluator.has_wake_hook is False
    assert deps.cognition_runner is None and deps.cognition_shutdown is None
    assert "nora.app.cognition_agent_runner" not in sys.modules


async def test_session_mode_shadow_builds_one_runner_and_hands_its_hook_to_the_evaluator(
    cfg, tmp_path,
):
    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    deps = await build_dependencies(override_config(cfg, _SESSION_ON))
    runner, evaluator = deps.cognition_runner, deps.wake_evaluator
    assert runner is not None and evaluator is not None
    assert evaluator.has_wake_hook
    assert evaluator._on_wake == runner.wake_hook          # the SAME hook, not a copy
    assert deps.cognition_shutdown is not None and not deps.cognition_shutdown.done
    # The Dreamer's own budget entry, converted; 600 s ends before the wake's 900 s.
    assert (runner._budget.turns, runner._budget.timeout_seconds) == (24, 600.0)
    assert (runner._budget.max_memory_calls, runner._budget.max_tool_calls) == (6, 60)
    # Its guard holds every ceiling a Dreamer sample's does, from the same
    # tables: the registry's per-tool budget, the result caps, the memory
    # cluster and the R-99 argument table.
    from nora.agent.dreamer import MEMORY_TOOLS, TOOL_RESULT_CAPS

    agent = runner._agent_runner
    assert agent._result_caps == dict(TOOL_RESULT_CAPS)
    assert agent._memory_tools == MEMORY_TOOLS
    assert agent._read_tool_budgets["note_read"] > 0
    assert set(agent._read_tool_budgets) <= runner._read_only_tools
    # Read tools only: the registry's table also names the tag path's
    # `remind`/`note_create`, whose arguments differ from the session's own
    # verbs of those names, and R-99 would veto every call to them.
    assert agent._param_names is not None and "note_read" in agent._param_names
    # R-99 must accept the `room` argument the routed tools of a multi-room
    # agenda take (#637 review, major 3), for every read tool held.
    assert all("room" in names for names in agent._param_names.values())
    assert set(agent._param_names) <= runner._read_only_tools
    # The session's instruction is the Dreamer's objective for it, read from
    # the bind-mounted corpus, doctrine first.
    text, sha = runner._doctrine()
    assert text.startswith('<dreamer_doctrine name="dreamer">')
    assert '<dreamer_objective name="cognition">' in text and sha
    # EVERY unit stays in the evaluator (its would-wakes are still recorded);
    # only units with a home room can start a session: `wedding` names none
    # (#637 review, minor 1).
    assert sorted(evaluator._units()) == ["friends", "wedding"]
    assert set(evaluator._session_units()) == {"friends"}
    assert set(runner._home_rooms) == {"friends"}


async def test_the_sessions_read_tools_are_the_catalogue_less_what_a_flag_withholds(
    cfg, tmp_path,
):
    from nora.domain.cognition_session import Room

    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    off = await build_dependencies(override_config(cfg, _SESSION_ON))
    on = await build_dependencies(override_config(
        cfg, {**_SESSION_ON, "record_query.enabled": True},
    ))
    assert "record_query" not in off.cognition_runner._read_only_tools
    assert "record_query" in on.cognition_runner._read_only_tools
    wired = off.cognition_runner._read_only_tools
    assert {"note_read", "about", "where_been", "event_search"} <= wired
    assert not {"rollup_read", "memo_read", "character_read"} & wired    # not built yet
    # Built for a real room they are real, read-only tools, and only wired ones.
    built = on.cognition_runner._read_tools_for("friends", [Room("signal", "g1")])
    names = {t.tool_name for t in built}
    assert names and names <= on.cognition_runner._read_only_tools
    assert {"note_read", "record_query"} <= names


async def test_a_shadow_session_mode_that_built_no_runner_refuses_to_boot(
    cfg, tmp_path, monkeypatch,
):
    """The boot check (#627 review, minor 4): a shadow week that records
    would-wakes and starts no session must not look healthy."""
    from nora.app import composition

    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    monkeypatch.setattr(composition, "build_cognition_runner", lambda **kw: None)
    with pytest.raises(composition.StartupError, match="no session runner"):
        await build_dependencies(override_config(cfg, _SESSION_ON))


async def test_the_shadow_session_is_grounded_from_the_record_with_record_query_off(
    cfg, tmp_path, pg_url,
):
    """#637 review, major 2: the rows that raised a stimulus are read by code for
    the agenda whether or not `[record_query] enabled` offers a MODEL the tool.
    Real Postgres, the composition's own grounder: who said what, with the id."""
    import sqlalchemy as sa

    from nora.domain.cognition_session import Room, build_situation
    from nora.domain.stimuli import Stimulus
    from nora.infra.sql.engine import create_sql_engine

    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    deps = await build_dependencies(override_config(cfg, _SESSION_ON))
    assert "record_query" not in deps.cognition_runner._read_only_tools   # the tool is OFF
    engine = create_sql_engine(pg_url)
    now = 1_790_000_000_000
    try:
        async with engine.begin() as conn:
            ids = []
            for room, sender, name, body, hidden in (
                ("g1", "+85211110001", "Ada Self-Set", "dinner friday?", None),
                ("g1", "+85211110002", "Ben Self-Set", "a hidden one", now),
                ("g2", "+85211110003", "Cy Self-Set", "other unit words", None),
            ):
                ids.append((await conn.execute(sa.text(
                    "INSERT INTO messages (platform, conversation_id, conversation_type, "
                    "sender_id, sender_name, ts, body, is_nora, hidden_at) VALUES "
                    "('signal', :room, 'group', :s, :n, :ts, :b, 0, :h) RETURNING id"
                ), {"room": room, "s": sender, "n": name, "ts": now - 600_000, "b": body,
                    "h": hidden})).scalar_one())
    finally:
        await engine.dispose()
    pending = tuple(
        Stimulus(id=i + 1, unit="friends", kind="message", item="message", ref=f"messages:{mid}",
                 at_ms=now - 600_000, recorded_at_ms=now - 600_000, platform="signal",
                 conversation_id=room)
        for i, (mid, room) in enumerate(zip(ids, ("g1", "g1", "g2"), strict=True))
    )
    situation = build_situation(
        unit="friends", pending=pending, home_room=Room("signal", "g1"), now_ms=now,
        last_session_ms=None,
    )
    grounding = await deps.cognition_runner._grounder("friends", situation.agenda.items, pending)
    said = {r.message_id: r for r in grounding.records.values()}
    assert set(said) == {ids[0]}                    # the hidden one and the OTHER UNIT's are absent
    assert said[ids[0]].text == "dinner friday?" and said[ids[0]].room == Room("signal", "g1")
    assert "+852" not in repr(grounding)            # R-18: no platform id reaches the prompt


async def test_shadow_sessions_with_no_unit_that_can_run_them_refuse_to_boot(cfg, tmp_path):
    """#637 review, minor 1: units without a home room stay in the evaluator,
    but a shadow week in which NO unit can start a session would record
    would-wakes and look healthy."""
    from nora.app import composition

    (tmp_path / "policy.toml").write_text(
        'default = "deny"\n[[conversations]]\nplatform = "signal"\nid = "g1"\npersons = "*"\n'
        'unit = "friends"\nlabel = "Friends"\n',
    )
    with pytest.raises(composition.StartupError, match="no unit can run a session"):
        await build_dependencies(override_config(cfg, _SESSION_ON))


async def test_units_without_a_home_room_stay_in_the_evaluator_and_are_gauged(cfg, tmp_path):
    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    deps = await build_dependencies(override_config(cfg, _SESSION_ON))
    assert deps.metrics.get("nora_cognition_session_units", state="runnable") == 1
    assert deps.metrics.get("nora_cognition_session_units", state="no_home_room") == 1
    assert "wedding" in list(deps.wake_evaluator._units())
