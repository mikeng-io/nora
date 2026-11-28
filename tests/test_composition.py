"""The composition root is the ONE place that reads config. Everything else
receives dependencies. This test pins that property."""

import ast
import base64
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import nora.documents
from nora.agent import tool_choice
from nora.app.composition import (
    Dependencies,
    build_dependencies,
)
from nora.app.fire import FirePath
from nora.app.startup import StartupError
from nora.config import load
from nora.domain.models import NormalizedMessage
from nora.infra.blobs import FilesystemBlobStore, RustfsBlobStore
from nora.memory import DeliveryArbitration
from nora.platform.discord.media import DiscordMediaSource
from nora.platform.instagram.media import InstagramMediaSource
from nora.platform.registry import MediaSourceRegistry
from nora.platform.signal.media import SignalMediaSource
from tests.builders import override_config
from tests.conftest import RUSTFS_TEST_PASSWORD, RUSTFS_TEST_USER

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
async def owned_dependencies(clean_pg_url, monkeypatch):
    build = build_dependencies
    owned = []

    async def build_owned(cfg, **kwargs):
        deps = await build(cfg, **kwargs)
        owned.append(deps)
        return deps

    monkeypatch.setattr(sys.modules[__name__], "build_dependencies", build_owned)
    yield
    for deps in reversed(owned):
        await deps.background.cancel_all()
        await deps.audit.aclose()

_HOME_POLICY = (
    'default = "deny"\n'
    '[[conversations]]\nplatform = "signal"\nid = "g1"\npersons = "*"\n'
    'unit = "friends"\nlabel = "Friends"\nhome_room = true\nproactive = true\n'
    'location = true\n'
    '[[conversations]]\nplatform = "signal"\nid = "g2"\npersons = "*"\n'
    'unit = "wedding"\nlabel = "Wedding"\n'
)


@pytest.fixture
def cfg(tmp_path, monkeypatch, clean_pg_url, example_config_path, owned_dependencies):
    # `clean_pg_url` (tests/conftest.py) supplies a reset module database and exports
    # DATABASE_URL, which build_dependencies() reads (store.database_url_env) to run
    # migrations and wire the store: a throwaway database, never production.
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    # config.example.toml's identity_file ("identity.toml") resolves relative to
    # the config file's own directory (nora/config.py's path-resolution context);
    # build_dependencies() calls load_persons() against it, so it must exist.
    (tmp_path / "identity.toml").write_text(
        '[persons.miles]\nname = "Miles"\n[[persons.miles.signal]]\nphone = "+852111"\n'
    )
    # policy.toml resolves the same way. `persons = "*"` keeps these wiring tests about
    # the dependency graph rather than the allowlist. `label` is required at startup (an
    # unlabelled allowed room is a `PolicyError`, the way a second home room is), so this
    # fixture's room needs one for `load_policy` to succeed.
    (tmp_path / "policy.toml").write_text(
        'default = "deny"\n[[conversations]]\nplatform = "signal"\n'
        'id = "g1"\nlabel = "g1"\npersons = "*"\n'
    )
    # store.data_dir is overridden to tmp_path so media custody lands in a
    # throwaway directory; the record store's own database is the throwaway
    # `clean_pg_url` (via DATABASE_URL), never production.
    #
    # platforms.signal.account overrides the empty "derive from the daemon" default
    # (config.example.toml) with a fixed value: derive_account() returns an override
    # immediately, with no network call, so build_dependencies() never needs a real
    # (or faked) signal-cli daemon reachable at the example config's placeholder URL.
    #
    # The template now defaults signal to mode = "bridge" (base_url names the
    # standalone bridge, which build_dependencies() dials for /v1/self). These
    # tests' subject is the DIRECT graph — the app holds the signal-cli
    # connection itself — so the fixture pins direct mode and a loopback
    # daemon address explicitly rather than inheriting a default that changed.
    # Bridge-mode composition has its own suite
    # (tests/integration/test_composition_bridge.py).
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
    return override_config(load(example_config_path), {
        "store.data_dir": tmp_path,
        "platforms.signal.mode": "direct",
        "platforms.signal.base_url": "http://127.0.0.1:18080",
        "platforms.signal.account": "+85290000100",
        "doctrine.dir": ROOT / "nora" / "doctrine",
        "dreamer.dir": ROOT / "nora" / "dreamer",
    })


async def test_build_dependencies_returns_the_wired_graph(cfg, tmp_path):
    deps = await build_dependencies(cfg)
    assert isinstance(deps, Dependencies)
    assert deps.audit is not None
    assert deps.persons is not None
    assert deps.metrics is not None
    assert deps.policy is not None
    assert deps.assembler is not None
    assert deps.ingest is not None
    assert deps.engagement is not None
    assert deps.dispatcher is not None


async def test_profile_portal_is_wired_with_its_scope_reader(cfg):
    deps = await build_dependencies(cfg)
    assert callable(deps.profile_portal)
    assert callable(deps.profile_scopes_of)


async def test_the_trigger_phrase_engages_by_default(cfg):
    """`[engagement.trigger].enabled` defaults to True: the trigger phrase engages her on
    every deployment, so the wired `CompositePolicy` must include `TriggerTextPolicy` when
    the key is left at its default."""
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








async def test_dependencies_speaker_is_none_when_tts_is_disabled(cfg, monkeypatch):
    """`[speech.tts]` ships INERT like every other optional capability: the
    template config has `enabled = false`, so even with a key in the
    environment the root wires no speaker and nothing about the deployment
    changes."""
    monkeypatch.setenv("MINIMAX_CN_API_KEY", "test-minimax-key")
    deps = await build_dependencies(cfg)
    assert deps.speaker is None


async def test_dependencies_speaker_is_none_when_tts_is_enabled_but_keyless(cfg, monkeypatch):
    """The keyless-`route`/`search` shape: enabled with a provider named but the env var
    unset is a loud warning and NO speaker, never a speaker that fails on every call."""
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


@pytest.mark.parametrize(("mode", "kind"), [
    ("off", type(None)), ("normalise", "_NormaliseOnly"), ("model", "SpeechFormatter"),
])
async def test_the_speech_formatter_is_wired_by_its_mode(cfg, monkeypatch, mode, kind):
    """`off` (the template's value) wires NO formatter, so the speaker sends her reply
    exactly as written; the other two modes wire the deterministic half alone or the model
    call."""
    monkeypatch.setenv("MINIMAX_CN_API_KEY", "test-minimax-key")
    keyed = override_config(cfg, {
        "speech.tts.enabled": True,
        "speech.tts.provider": "minimax_cn",
        "speech.tts.format.mode": mode,
    })
    deps = await build_dependencies(keyed)
    formatter = deps.speaker._formatter  # type: ignore[union-attr]
    if kind is type(None):
        assert formatter is None
    else:
        assert type(formatter).__name__ == kind


async def test_the_doctrine_the_root_builds_carries_no_capability_section(cfg):
    """DEC-150: the composition root must not wire a capability block.

    The store assembles only the authored sections; the schema is the single source of
    truth for what she can do. If the root passes a `capability=` argument, the generated
    section reappears and this reddens (and `DoctrineStore` does not accept the
    parameter)."""
    deps = await build_dependencies(cfg)
    text = deps.doctrine.doctrine_text
    assert '<section kind="capability"' not in text
    # The capability block's signature line was "- <tool> — <when clause>".
    # Assert that shape is gone: no bullet that begins with a registered tool
    # name followed by an em-dash.
    import re

    from nora.agent.catalog.entries import NATIVE_TOOLS
    names = "|".join(re.escape(n) for n in NATIVE_TOOLS)
    assert not re.search(rf"^- ({names}) — ", text, re.MULTILINE), "tool-catalogue line present"


async def test_the_runtime_context_carries_the_configured_language_tag(cfg):
    """The composition root must hand the loader `doctrine.language_hint`, or every tag's
    runtime_context ships a blank `language:` line and the model is never told, from
    outside, what to speak. The value is a BCP 47 tag (RFC 5646), asserted against the
    real assembled runtime_context. Drop `language_hint=` from the root and the line reads
    `language:` with nothing after it, and this goes red.
    """
    deps = await build_dependencies(cfg)
    assert "language: yue-Hant-HK" in deps.doctrine.runtime_context
    assert "language: \n" not in deps.doctrine.runtime_context


async def test_the_root_hands_the_loader_the_tool_names_its_gate_needs(cfg, tmp_path):
    """The catalogue gate's backticked half is inert without `tool_names`, and an inert gate
    that nobody notices is worse than no gate.

    It has to be a tool whose name is an ordinary English word, such as `now`.
    Every other native is snake_case, which the STRUCTURAL detector catches on its own, so
    injecting one of those would make this pass whether or not `tool_names` was ever
    handed over.
    """
    import shutil


    d = tmp_path / "doctrine"
    shutil.copytree(ROOT / "nora" / "doctrine", d)
    conduct = d / "rooms" / "conduct.md"
    conduct.write_text(conduct.read_text().replace(
        "# Ground your understanding",
        "Reach for `now` when you need to.\n\n# Ground your understanding", 1))
    with pytest.raises(StartupError, match="may not name a tool"):
        await build_dependencies(override_config(cfg, {"doctrine.dir": d}))


async def test_the_nora_aipeer_id_is_reserved_and_no_human_may_hold_it(cfg):
    """The aiPeer id is `[agent].name`, Nora's own person id. A human mapped to it
    would be read as her. Setting `agent.name` to an existing person id ("miles" in
    this fixture) must refuse to start, not silently merge the two."""
    with pytest.raises(StartupError, match="reserved for Nora"):
        await build_dependencies(override_config(cfg, {"agent.name": "miles"}))


async def test_no_enabled_platform_refuses_to_start(cfg):
    """`platforms.signal.enabled = false` is honoured
    (nora.platform.registry.AdapterRegistry): SignalAdapter does not start. With no
    platform enabled there is nothing for the engagement pipeline to send through, so this
    is a startup failure, not a degraded start."""
    disabled = override_config(cfg, {"platforms.signal.enabled": False})
    with pytest.raises(StartupError, match="no platform adapter is enabled"):
        await build_dependencies(disabled)


def test_build_dependencies_never_wires_the_real_database(cfg, tmp_path, clean_pg_url):
    """A fixture that forgot to point the store at a throwaway database would wire a real
    SqlRecordStore against production. The store's URL comes from DATABASE_URL, which
    `clean_pg_url` set to an ephemeral `t_…` database; the media root is under tmp_path. Both
    being throwaways is what proves it."""
    assert os.environ["DATABASE_URL"] == clean_pg_url
    assert "/t_" in clean_pg_url                          # the ephemeral-database marker
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
    assert deps.story_expiry is None, "no bytes in custody, none to expire"


async def test_a_valid_key_turns_media_custody_on_with_an_encrypting_store(cfg, monkeypatch):
    """A base64 32-byte key present in the named env var is what wires the
    MediaSourceRegistry (Signal + Discord) + the encrypting FilesystemBlobStore
    into the pipeline."""
    monkeypatch.setenv("MEDIA_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
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
    # A story frame's bytes are deleted only once an operator turns it on.
    assert deps.story_expiry is None, "[social] story_expiry_enabled is off by default"


async def test_story_expiry_runs_on_custodys_store_once_turned_on(cfg, monkeypatch):
    """With custody on and `[social] story_expiry_enabled`, the pass is built
    on the store custody writes to, poller on or off (`[social.poll]` is off
    in the template)."""
    monkeypatch.setenv("MEDIA_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    on = cfg.model_copy(update={
        "social": cfg.social.model_copy(update={"story_expiry_enabled": True}),
    })
    deps = await build_dependencies(on)
    assert deps.story_expiry is not None
    assert deps.story_expiry._blobs is deps.ingest._blobs


async def test_an_enabled_instagram_gets_its_own_roster_even_while_degraded(cfg):
    """ONE ROSTER PER PLATFORM, and Instagram's is built for a DEGRADED adapter.

    Every Instagram room needs a roster: without one `group_members` answers
    `unavailable`, `where_is` cannot establish the room and refuses, and every outbound
    @-mention resolves to nobody and goes out as plain text with no ping.

    The adapter here IS degraded (the base_url refuses), and that is the point: a
    checkpoint clears without a restart, so a roster withheld on the startup state would
    stay withheld for the process's life. The READ fails closed instead. WhatsApp, the
    other bridge with its own roster read, is wired the same way.
    """
    from nora.platform.bridge.client import BridgeRoster
    from nora.platform.instagram.roster import InstagramRoster

    deps = await build_dependencies(override_config(cfg, {
        "platforms.instagram.enabled": True,
        "platforms.instagram.base_url": "http://127.0.0.1:1",
        "platforms.whatsapp.enabled": True, "platforms.whatsapp.base_url": "http://127.0.0.1:1",
    }))
    assert isinstance(deps.engagement._rosters.get("whatsapp"), BridgeRoster)

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
    monkeypatch.setenv("MEDIA_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    deps = await build_dependencies(cfg)
    assert isinstance(deps.ingest._blobs, FilesystemBlobStore)


async def test_rustfs_enabled_with_no_endpoint_falls_back_to_the_filesystem(
    cfg, monkeypatch,
):
    """`enabled = true` with a blank endpoint is a misconfiguration, not a
    startup failure — media is a bonus (the section's own docstring), so this
    degrades to the same filesystem store an unset key already degrades to."""
    monkeypatch.setenv("MEDIA_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    deps = await build_dependencies(
        override_config(cfg, {"media.rustfs.enabled": True})
    )
    assert isinstance(deps.ingest._blobs, FilesystemBlobStore)


async def test_rustfs_enabled_with_unresolved_credentials_falls_back(cfg, monkeypatch):
    """`endpoint` set but the named credential env vars are not exported —
    the same "a bonus feature never crashes the daemon" posture as a
    misconfigured encryption key."""
    monkeypatch.setenv("MEDIA_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
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
    monkeypatch.setenv("MEDIA_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
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
    monkeypatch.setenv("MEDIA_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
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
    monkeypatch.setenv("MEDIA_ENCRYPTION_KEY", "not-a-valid-base64-32-byte-key!!")
    deps = await build_dependencies(cfg)
    assert deps.ingest._media_sources is None
    assert deps.ingest._blobs is None


async def test_media_disabled_is_off_even_with_a_valid_key(cfg, monkeypatch):
    monkeypatch.setenv("MEDIA_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    deps = await build_dependencies(override_config(cfg, {"media.enabled": False}))
    assert deps.ingest._media_sources is None
    assert deps.ingest._blobs is None


async def test_heartbeat_config_wires_only_the_graph_proactive_poller(cfg):
    """Inspect the spawned fire loops because Dependencies keeps them private."""
    on = override_config(cfg, {
        "heartbeat.enabled": True, "heartbeat.tick_seconds": 42, "heartbeat.timeout_seconds": 71,
    })
    deps = await build_dependencies(on)
    try:
        loops = [
            task.get_coro().cr_frame.f_locals["self"]
            for task in deps.background._tasks
            if task.get_coro().__qualname__ == "HeartbeatLoop.run"
        ]
        assert loops == []
        [poller] = [
            task.get_coro().cr_frame.f_locals["self"]
            for task in deps.background._tasks
            if task.get_coro().__qualname__ == "ProactivePoller.run"
        ]
        assert poller._tick_seconds == 42
        assert poller._bound.total_seconds() == 71
        assert isinstance(poller._proactive._recheck, DeliveryArbitration)
    finally:
        await deps.background.cancel_all()


async def test_the_participation_trigger_is_wired_to_the_real_fire_path(cfg):
    """The participation occasion's wiring pin, the heartbeat test's shape for the same
    reason: every unit test supplies its own fake `speak`, so only here is it asserted
    that the composition root hands the trigger `FirePath.speak` itself, and that the
    FirePath's participation runner composes `tool_choice="auto"`, without which a
    determination could never end in silence (the tag runner's `required` pin demands a
    call, and the only terminal call is a send).

    Reaching through `_speak.__self__` and `_participation_runner` is the only way to see
    it from out here, exactly as with the heartbeat above.
    """
    assert (await _participation_trigger_of(cfg)) is None, (
        "off by default: no trigger unless [relevance_gate] enabled = true"
    )
    assert (await _participation_trigger_of(
        override_config(cfg, {"participation.enabled": True})
    )) is None, (
        "[participation] enabled alone builds NOTHING: the trigger "
        "is the gate's, and a turn with no gate in front of it is the cost "
        "docs/design/cognition/loop.md exists to stop paying"
    )

    # `shadow_channel` is set because the shipped default is `shadow = true`
    # and `_shadow_needs_a_channel` refuses that combination without one.
    # `override_config` uses `model_copy`, which does NOT re-run validators, so a test can
    # silently assert against a config that would refuse to start. This one deliberately
    # does not, which is why it carries the GUILD half too: a bare `"999"` is a value the
    # schema refuses, and model_copy is exactly the door through which a test keeps
    # asserting against a config the deployment could not load.
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


def _spawned(deps, qualname: str) -> list:
    """Every live background task running `qualname`, as its `self`."""
    return [
        task.get_coro().cr_frame.f_locals["self"]
        for task in deps.background._tasks
        if task.get_coro().__qualname__ == qualname
    ]






async def test_compute_boot_recovery_sweeps_a_stranded_row_even_when_disabled(cfg, clean_pg_url):
    """Mirrors `test_boot_recovery_counts_the_rows_it_swept`: a `FIRING` `ComputeJob` (a
    crash mid-delivery) must resolve to `FAILED` at boot whether or not `[compute]
    enabled` is true. That is the point of the unconditional `compute_poller_recover` call
    in `build_dependencies` (composition.py, beside `reminders`' own unconditional sweep):
    gating it behind `cfg.compute.enabled` would leave a row stranded forever on a
    deployment that crashed with compute on and was then switched off.
    `cfg.compute.enabled` is False here (the shipped default); the sweep must still run.
    """
    from nora.domain.compute import ComputeJob, ComputeJobState
    from nora.infra.sql import create_sql_engine, run_migrations
    from nora.infra.sql.compute_jobs import SqlComputeJobStore

    assert cfg.compute.enabled is False

    run_migrations(clean_pg_url)
    store = SqlComputeJobStore(create_sql_engine(clean_pg_url))
    now = datetime.now(UTC)
    await store.create(ComputeJob(
        id=uuid.uuid4(), backend_job_id="job_stranded", task="make a chart",
        artifact_id=None, platform="signal", conversation_id="g1",
        conversation_type="group", requester_person_id="miles",
        created_at=now - timedelta(hours=1), state=ComputeJobState.FIRING,
        completed_at=None, result_url=None, result_summary=None, error=None,
        task_sha256="hash-task", context_sha256="hash-context", kind="subagent",
    ))
    await store.aclose()

    deps = await build_dependencies(cfg)

    assert deps.metrics.get(
        "nora_compute_jobs_delivered_total", outcome="failed_error",
    ) == 1


@pytest.mark.parametrize("token", [None, "", "   "])
async def test_computer_refuses_to_start_without_its_token(cfg, monkeypatch, token):
    """`[computer] enabled` with computerd's bearer token unset or blank refuses to start
    (R-5), the `policy.toml` discipline: otherwise every computer call would answer
    `unavailable` from computerd's 401, in front of the group, for a fault only the
    operator can fix. The variable NAME is read off `cfg` (DEC-8)."""
    on = override_config(cfg, {"computer.enabled": True, "heartbeat.enabled": False})
    if token is None:
        monkeypatch.delenv(cfg.computer.token_env, raising=False)
    else:
        monkeypatch.setenv(cfg.computer.token_env, token)

    with pytest.raises(StartupError, match=cfg.computer.token_env):
        await build_dependencies(on)


async def test_compute_enabled_without_an_adapter_boots_and_spawns_no_poller(
    cfg, monkeypatch, no_build_stamp,
):
    """The outbox, the poller and boot recovery are generic deferred delivery. With
    `[subagent]` off no `ComputeBackend` is registered, so a deployment whose
    `config.toml` says `[compute] enabled = true` must still BOOT, and the poller must
    not be spawned: it has nothing to poll, so a spawned one would claim `pending` rows
    it cannot answer for. Reaching into `background._tasks` for the same reason
    `test_heartbeat_config_wires_only_the_graph_proactive_poller` does."""
    on = override_config(cfg, {"compute.enabled": True, "heartbeat.enabled": False})
    deps = await build_dependencies(on)
    try:
        assert _spawned(deps, "ComputeJobPoller.run") == []
    finally:
        await deps.background.cancel_all()


async def test_the_composed_registry_holds_the_rollup_read_and_the_standing_head(
    cfg, no_build_stamp,
):
    """Both reads are SELECTs with no flag: a composed graph always wires them, so
    `rollup_read` reaches a tag turn and `standing_read` the Cognition session.
    Unwired, the registry withholds each from every surface, silently."""
    deps = await build_dependencies(cfg)
    try:
        registry = deps.engagement._tool_registry
        assert registry._rollup is not None and registry._standing_head is not None
        assert {"rollup_read", "standing_read"}.isdisjoint(registry._withheld)
    finally:
        await deps.background.cancel_all()


async def test_subagent_off_registers_no_backend(cfg, no_build_stamp):
    """`[subagent] enabled = false` is the shipped default: nothing is built, nothing is
    registered with the poller, and boot is unchanged."""
    assert cfg.subagent.enabled is False
    deps = await build_dependencies(cfg)
    try:
        assert deps.subagent_backend is None
        # and the four model-facing tools are withheld from every turn
        from nora.agent.catalog.groups import SUBAGENT_TOOLS

        assert deps.engagement._tool_registry._withheld >= SUBAGENT_TOOLS
    finally:
        await deps.background.cancel_all()


async def test_subagent_on_registers_its_backend_under_its_kind_and_the_poller_runs(
    cfg, no_build_stamp,
):
    """On, the worker is the one `ComputeBackend` the poller resolves by kind `subagent`, and
    the poller (which needs a backend to be spawned) runs."""
    from nora.app.subagent_backend import SubagentBackend

    on = override_config(cfg, {
        "compute.enabled": True, "subagent.enabled": True,
        "heartbeat.enabled": False,
    })
    deps = await build_dependencies(on)
    try:
        assert isinstance(deps.subagent_backend, SubagentBackend)
        # the tools are offered, and their desk launches into THIS backend
        from nora.agent.catalog.groups import SUBAGENT_TOOLS

        registry = deps.engagement._tool_registry
        assert registry._withheld.isdisjoint(SUBAGENT_TOOLS)
        assert registry._subagent._worker is deps.subagent_backend
        (poller,) = _spawned(deps, "ComputeJobPoller.run")
        assert dict(poller._backends) == {"subagent": deps.subagent_backend}
    finally:
        await deps.background.cancel_all()


async def test_video_off_builds_nothing_and_withholds_the_tool(cfg, no_build_stamp):
    """`[video_gen] enabled = false`, the shipped default: no backend, no tool."""
    assert cfg.video_gen.enabled is False
    deps = await build_dependencies(cfg)
    try:
        registry = deps.engagement._tool_registry
        assert "video_create" in registry._withheld and registry._video is None
    finally:
        await deps.background.cancel_all()


async def test_video_on_without_its_key_boots_and_withholds_the_tool(
    cfg, monkeypatch, no_build_stamp,
):
    """Enabled with the key unset is a wiring gap: boot goes on, the tool is withheld, and
    no backend is registered with the poller. Never a crash."""
    monkeypatch.delenv("MINIMAX_CN_API_KEY", raising=False)
    on = override_config(cfg, {"compute.enabled": True, "video_gen.enabled": True,
                               "heartbeat.enabled": False})
    deps = await build_dependencies(on)
    try:
        registry = deps.engagement._tool_registry
        assert "video_create" in registry._withheld and registry._video is None
        assert _spawned(deps, "ComputeJobPoller.run") == []
    finally:
        await deps.background.cancel_all()


async def test_video_on_with_its_key_registers_its_backend_under_its_kind(
    cfg, monkeypatch, no_build_stamp,
):
    from nora.infra.video_gen import MiniMaxVideoBackend

    monkeypatch.setenv("MINIMAX_CN_API_KEY", "test-key")
    on = override_config(cfg, {"compute.enabled": True, "video_gen.enabled": True,
                               "heartbeat.enabled": False})
    deps = await build_dependencies(on)
    try:
        registry = deps.engagement._tool_registry
        assert "video_create" not in registry._withheld and registry._video is not None
        (poller,) = _spawned(deps, "ComputeJobPoller.run")
        assert isinstance(poller._backends["video"], MiniMaxVideoBackend)
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
        # `subject/` is the FOURTH, and it needs an argument rather than a precedent. It
        # is a composition root by the same definition: `obtain()` calls
        # `build_dependencies(cfg)`, so it must build a `Config` to call it with.
        # Composing the real graph means reading the real config, and a subject that read
        # something else would not be Nora, which is the entire property it exists
        # to hold.
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








async def test_standing_disabled_still_wires_graph_debrief_without_legacy_fold(cfg):
    """Disabling the room fold leaves canonical action facts readable, without
    asking the shared reader to read a dormant room position store."""
    deps = await build_dependencies(cfg)
    assert deps.engagement._debrief_wiring is not None
    assert deps.engagement._standing_replay_wiring is None
    reader = deps.engagement._context._shared_reader
    assert reader is not None
    view = await reader(unit="friends", now_ms=1000)
    assert "findings" not in dict(view.sources)


async def test_standing_enabled_wires_the_same_store_to_both_halves(cfg, tmp_path):
    """**One instance, two callers**, and this is the structural half of a
    non-negotiable: the fold must read the position it is about to overwrite.
    Two separate store objects would still satisfy every type here and still be
    the same row, but it is the kind of wiring that invites a later change to
    give the reader a cache — at which point the fold corrects a stale position
    and the contradiction is back."""
    from unittest.mock import patch

    from nora.domain.standing import Standing

    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    deps = await build_dependencies(override_config(cfg, {"standing.enabled": True}))
    wiring = deps.engagement._standing_replay_wiring
    assert wiring is not None
    await wiring.store.write(standing=Standing(
        platform="signal", conversation_id="g1", body="generated open decision",
        fold_count=1, updated_at=1000,
    ))
    with patch.object(wiring.store, "read", wraps=wiring.store.read) as read:
        view = await deps.engagement._context._shared_reader(unit="friends", now_ms=2000)
    read.assert_any_await(platform="signal", conversation_id="g1")
    assert any(e.kind == "standing" and e.subject == "generated open decision"
               for e in view.entries)
    assert "acts" not in dict(view.sources)
    assert wiring.max_chars == 1200
    # Read the value from the config under test, not a hardcoded literal: the operator
    # owns fold_batch, and a literal breaks when it is changed.
    assert wiring.fold_batch == cfg.standing.fold_batch
    assert wiring.record_store is deps.ingest._audit


async def test_the_debrief_model_defaults_to_the_reply_model(cfg):
    """`[standing] model = ""` means `[llm] model`, the same defaulting `[language]
    exchange_model` uses. An empty string reaching the provider would be a 400 on the
    first fold, in production, after a config change that looked like it left the model
    alone."""
    deps = await build_dependencies(override_config(cfg, {"standing.enabled": True}))
    assert deps.engagement._debrief_wiring.client.model == cfg.llm.model


async def test_the_debrief_reads_its_objective_from_the_instruction_substrate(cfg):
    """The text is a SOURCE, not a string, so an operator's edit on the bind
    mount reaches the next fold rather than the next rebuild. Asserted by
    actually resolving the objective, because a `Wiring` holding an object with
    the right method name and no such objective behind it would look identical
    here and fail on the first fold."""
    deps = await build_dependencies(override_config(cfg, {"standing.enabled": True}))
    body = deps.engagement._debrief_wiring.objective.objective_body("debrief")
    assert "Re-judge each open intention" in body
    assert "pass's recorded items" in body


def _effort_of(client: object) -> str:
    """The `reasoning_effort` actually on the model provider's params."""
    provider = client._provider
    return str((provider.config.get("params") or {}).get("reasoning_effort", ""))


async def test_the_debrief_declares_a_reasoning_effort_rather_than_inheriting_silently(cfg):
    """Passing none runs the fold at the provider's default — an unchosen setting
    on a call that spends the conversation's slot, while the tag path passes
    `[llm] reasoning_effort` explicitly.

    Empty means inherit `[llm]`, the honest default while no measurement has
    chosen a value for the fold.
    """
    # PRECEDENCE, not one branch of it: an explicit `[standing]` value wins,
    # and empty falls back to `[llm]`. Asserting only the fallback goes red once
    # the operator sets a value, which pins today's config rather than the rule.
    explicit = await build_dependencies(override_config(
        cfg, {"standing.enabled": True, "standing.reasoning_effort": "none"}))
    # Read off the PROVIDER the client built, not a field it stored. The
    # effort's whole job is to reach the request; an attribute nobody sends would
    # satisfy a stored-field check and change nothing about the call.
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


























async def test_the_dreamer_model_is_built_with_the_dreamers_own_effort(cfg):
    """**A config key nothing reads is the phantom-metric rule again.** Config tests that
    assert only the VALUE stay green when the line in composition that uses it is removed.

    The effort is baked into the model object at construction rather than passed per call,
    so the only place it can be observed is the spec the Dreamer's own `ModelSource` was
    built with. Asserted with a value that is NOT the tier's, so a composition still
    reading `[llm]` fails rather than coincidentally agreeing.
    """
    deps = await build_dependencies(override_config(cfg, {
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
        "dreamer.reasoning_effort": "",
    }))

    assert deps.dreamer_runtime is not None
    spec = deps.dreamer_runtime._models._spec
    assert spec.reasoning_effort == cfg.llm.reasoning_effort


async def test_the_dreamer_is_built_with_its_own_per_call_max_tokens(cfg):
    """The Dreamer's PER-CALL ceiling is its own, not the tier's.

    Asserted on the SPEC and not on the config value, for the reason the effort test above
    states outright: a config key nothing reads is a phantom. Reverting `composition.py`
    to `max_tokens=tier.max_tokens` must turn THIS red, so the value asserted is
    deliberately not the tier's.

    This is the ceiling that raises. `[dreamer.budgets.*] output_tokens` is the Strands
    LOOP budget and binds without raising (`nora/agent/factory.py`); exceeding THIS one
    raises `MaxTokensReachedException` -> `PartialReplyError`, which holds one scope's
    watermark.
    """
    deps = await build_dependencies(override_config(cfg, {
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
        "dreamer.max_tokens": 0,
    }))

    assert deps.dreamer_runtime is not None
    spec = deps.dreamer_runtime._models._spec
    assert spec.max_tokens == cfg.llm.max_tokens
    assert spec.max_tokens > 0, "0 is the inherit sentinel, never a ceiling"


async def test_a_misspelt_inherited_effort_refuses_to_start(cfg):
    """**The typed declaration alone does not give the boot refusal it claims.**

    `[dreamer] reasoning_effort` is typed, so it cannot arrive wrong. But `""` is its
    default AND its documented inherit value, and what it inherits is `[llm]
    reasoning_effort`, a bare `str`. Unwrapped, `[llm] = "hgh"` with `[dreamer] = ""`
    LOADS and resolves to `"hgh"`, which reaches the provider as a 400 mid-tick and
    surfaces as one more `unavailable` in a background pass nobody watches.

    A declaration copied from `StandingConfig` needs its RESOLUTION too:
    `_reasoning_effort` is that half and exists for this case.
    """
    with pytest.raises(StartupError, match="reasoning effort"):
        await build_dependencies(override_config(cfg, {
            "dreamer.reasoning_effort": "",
            "llm.reasoning_effort": "hgh",
        }))


async def test_a_valid_inherited_effort_still_starts(cfg):
    """The positive control: a refusal that fired on every inherited value
    would satisfy the test above while making `""` — the default — unusable."""
    deps = await build_dependencies(override_config(cfg, {
        "dreamer.reasoning_effort": "",
        "llm.reasoning_effort": "medium",
    }))

    assert deps.dreamer_runtime is not None
    assert deps.dreamer_runtime._models._spec.reasoning_effort == "medium"










# --- the proactive adaptor (DEC-162) ------------------------------------









async def test_the_proactive_runner_thinks_at_its_own_effort(cfg, tmp_path):
    """The proactive act turn reasons at `[proactive] reasoning_effort`, not
    `[participation]`'s. With participation ON, the two runners are distinct and each
    carries its own effort; a shared runner would speak on every case where silence was
    right at effort `none`."""
    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    deps = await build_dependencies(override_config(cfg, {
        "notes.enabled": True, "heartbeat.enabled": True,
        "participation.enabled": True, "participation.reasoning_effort": "none",
        "proactive.reasoning_effort": "low",
    }))
    try:
        (poller,) = _spawned(deps, "ProactivePoller.run")
        path = deps.fire_path
        assert path is not None
        assert poller._proactive._agent.runner is path._proactive_runner
        assert path._proactive_runner is not path._participation_runner
        assert path._proactive_runner._models._spec.reasoning_effort == "low"
        assert path._participation_runner._models._spec.reasoning_effort == "none"
        assert path._proactive_runner._models._spec.max_tokens == cfg.proactive.max_tokens
    finally:
        await deps.background.cancel_all()



async def test_the_instagram_backend_is_composed_with_the_person_index(cfg):
    """`[social.instagram] enabled` composes the bridge client behind BOTH Instagram rows,
    with `PersonsSubjectIndex` beside it. The router refuses to be built with a
    person-keyed backend and no index, so a composition that forgot the index would not
    boot, and one that wired only the story row would leave profiles `unavailable` forever
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
    """DEC-201: the router's one default backend serves X AND LinkedIn, so LinkedIn's switch
    is at the composition root: `[social.linkedin] enabled` hands the SAME router to the
    LinkedIn tools, and off hands them `None` while X keeps it. A root that ignored the
    flag would read LinkedIn on a deployment whose operator never set up a dedicated,
    private-viewing account."""
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
    """The summons consumer runs only under `[social.instagram] enabled` AND
    `[social.instagram.summons] enabled` (the config refuses the second without the
    first). Off is the shipped state, and a summons spends requests on the session her
    group chats run on, so off must mean not spawned."""
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
    the notification feed: no poller task, no poller module."""
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


async def test_the_social_poller_is_built_only_when_enabled(cfg, monkeypatch):
    """`[social.poll] enabled = false` is the shipped state, and off builds no
    poller for `__main__` to start. On, it reads through the Instagram bridge
    with the same custody every room's media takes."""
    from nora.app.social_poller import SocialPoller

    monkeypatch.setenv("MEDIA_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    deps = await build_dependencies(override_config(cfg, {"social.instagram.enabled": True}))
    try:
        assert deps.social_poller is None
        assert deps.social_perception is None
    finally:
        await deps.background.cancel_all()

    deps = await build_dependencies(override_config(cfg, {
        "social.instagram.enabled": True, "social.poll.enabled": True,
    }))
    try:
        poller = deps.social_poller
        assert isinstance(poller, SocialPoller)
        assert poller._source.network == "instagram"
        assert isinstance(poller._media, InstagramMediaSource)
        assert poller._blobs is deps.blobs
        # Its perception pass reads through the SAME store, the one holding
        # who opted out.
        assert deps.social_perception is not None
        assert deps.social_perception._store is poller._store
    finally:
        await deps.background.cancel_all()


# --- the graph wake -------------------------------------------------------------

async def test_the_graph_wake_builds_from_the_example_config_with_no_switch(
    cfg, tmp_path,
):
    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    assert "enabled" not in type(cfg.cognition).model_fields
    assert "enabled" not in type(cfg.dreamer).model_fields
    deps = await build_dependencies(cfg)
    assert deps.wake_poller is not None
    assert deps.dreamer_runtime is not None



async def test_the_document_tools_reach_the_registry_only_while_documents_are_on(cfg):
    """`[documents] enabled` ships false, and off the registry is built with no document
    service, so no turn is offered `document_publish` or `document_read`. A published
    document with nowhere to open it is worse than no tool."""
    off = await build_dependencies(cfg)
    assert off.engagement._tool_registry._documents is None
    on = await build_dependencies(override_config(cfg, {"documents.enabled": True}))
    assert on.engagement._tool_registry._documents is on.document_service


async def test_the_documents_block_reaches_both_turn_paths_only_while_documents_are_on(cfg):
    """One `DocumentsContextReader`, built only under `[documents] enabled` (the flag that
    withholds the two tools), handed to the tag path's assembler and to the fire path.
    Off, no room renders `<documents>`."""
    off = await build_dependencies(cfg)
    assert off.assembler._documents_reader is None
    on = await build_dependencies(override_config(cfg, {"documents.enabled": True}))
    assert on.assembler._documents_reader is not None
    fire = override_config(cfg, {"documents.enabled": True, "compute.enabled": True})
    deps = await build_dependencies(fire)
    try:
        assert deps.fire_path is not None
        assert deps.fire_path._documents is deps.assembler._documents_reader
    finally:
        await deps.background.cancel_all()


async def test_computer_sweeper_and_block_exist_only_while_on(cfg, monkeypatch):
    """Composition builds the session sweeper (and `__main__` spawns its loop) only under
    `[computer] enabled`."""
    off = await build_dependencies(cfg)
    assert off.computer_sweeper is None
    # `<computer>` likewise: no reader, so no room renders the block.
    assert off.assembler._computer_reader is None
    monkeypatch.setenv(cfg.computer.token_env, "t")
    on = override_config(cfg, {
        "computer.enabled": True, "heartbeat.enabled": False,
        "computer.sessions.sweep_interval_seconds": 42.0,
    })
    deps = await build_dependencies(on)
    assert deps.computer_sweeper is not None
    assert deps.computer_sweep_seconds == 42.0
    assert deps.assembler._computer_reader is not None
    # DEC-261: the probe of computerd exists only while on, and "off" leaves no
    # `nora_computer_up` sample (not applicable), where "on" has primed series
    # at zero and still no `up` until the first probe has run.
    assert off.computer_probe is None
    assert not [ln for ln in off.metrics.render().splitlines()
                if ln.startswith(("nora_computer_up", "nora_computer_calls_total"))]
    assert deps.computer_probe is not None
    on_lines = deps.metrics.render().splitlines()
    assert any(ln.startswith("nora_computer_calls_total{") for ln in on_lines)
    assert not [ln for ln in on_lines if ln.startswith("nora_computer_up")]


@pytest.mark.parametrize(
    "origin",
    [
        "https://12345678-1234-1234-1234-123456789012.example.com",  # UUID-shaped host
        "https://api-abcdefghijklmnop.example.com",  # key-shaped host
    ],
)
def test_a_public_url_egress_would_block_refuses_to_start(origin) -> None:
    # The validator accepts these origins; egress blocks the whole link, so every reply that
    # carried it would vanish. The boot check uses egress's own scan.
    with pytest.raises(StartupError, match="egress would block the link"):
        nora.documents.wire._require_a_sendable_public_url(origin)


@pytest.mark.parametrize("origin", ["", "https://nora.67.example", "http://localhost:3000"])
def test_a_sendable_public_url_or_none_starts(origin) -> None:
    nora.documents.wire._require_a_sendable_public_url(origin)


async def test_an_eval_subject_with_the_subagent_on_builds_the_tools_and_a_recorder(
    cfg, no_build_stamp,
):
    """`recording_subagent_worker` (what `obtain` wraps composition in under `[subagent]
    enabled`): the four tools are offered, the desk and the poller hold a recorder under kind
    `subagent`, and no worker is built, so nothing can start. The patch is gone afterwards."""
    from nora.agent.catalog.groups import SUBAGENT_TOOLS
    from nora.app import composition_agent
    from nora.subject.recording_subagent import (
        RecordingSubagentBackend,
        recording_subagent_worker,
    )

    real = composition_agent.build_subagent_backend
    on = override_config(cfg, {
        "compute.enabled": True, "subagent.enabled": True,
        "heartbeat.enabled": False,
    })
    with recording_subagent_worker():
        deps = await build_dependencies(on)
    assert composition_agent.build_subagent_backend is real
    try:
        recorder = deps.subagent_backend
        assert isinstance(recorder, RecordingSubagentBackend)
        registry = deps.engagement._tool_registry
        assert registry._withheld.isdisjoint(SUBAGENT_TOOLS)
        assert registry._subagent._worker is recorder
        (poller,) = _spawned(deps, "ComputeJobPoller.run")
        assert dict(poller._backends) == {"subagent": recorder}
        # launch records and returns an id; poll stays RUNNING; nothing ran
        from nora.domain.ports import JobStatus, LaunchRequest

        job_id = await recorder.launch(request=LaunchRequest(
            task="t", platform="signal", conversation_id="g1", conversation_type="group",
            requester_person_id="p", tag_id=None, payload=None,
        ))
        assert (await recorder.poll(job_id=job_id)).status is JobStatus.RUNNING
        assert len(recorder.launched) == 1
    finally:
        await deps.background.cancel_all()


@pytest.mark.parametrize("missing", ["secret_env", "api_token_env"])
async def test_otp_refuses_to_start_without_both_secrets(cfg, monkeypatch, missing):
    """DEC-287: `[otp] enabled` with the HMAC key or the API token unset
    refuses to start. An empty key would hash every code under nothing; an
    empty token would leave the sign-in routes open to the network."""
    on = override_config(cfg, {"otp.enabled": True, "heartbeat.enabled": False})
    monkeypatch.setenv(cfg.otp.secret_env, "k" * 32)
    monkeypatch.setenv(cfg.otp.api_token_env, "t")
    monkeypatch.delenv(getattr(cfg.otp, missing))

    with pytest.raises(StartupError, match=getattr(cfg.otp, missing)):
        await build_dependencies(on)


async def test_the_senders_hold_identity_toml_as_their_direct_message_allowlist(
    cfg, monkeypatch,
):
    """R-101's wiring: without `dm_recipients` reaching the adapter, every
    direct message (every one-time code included) would be refused, quietly.
    The fixture's identity.toml holds one Signal number, and that is the
    whole set."""
    on = override_config(cfg, {"otp.enabled": True, "heartbeat.enabled": False})
    monkeypatch.setenv(cfg.otp.secret_env, "k" * 32)
    monkeypatch.setenv(cfg.otp.api_token_env, "t")

    deps = await build_dependencies(on)

    assert deps.otp is not None and deps.otp_api_token == "t"  # noqa: S105 — the fixture's token
    assert deps.registry.adapters["signal"]._sender._dm_recipients == frozenset({"+852111"})







async def test_markdown_identity_reaches_the_live_tools(cfg, tmp_path):
    import shutil

    from nora.agent.room import Room
    from nora.domain.models import DeliveryStatus, TranscriptRow

    directory = tmp_path / "renamed-doctrine"
    shutil.copytree(cfg.doctrine.dir, directory)
    identity = directory / "core/identity.md"
    identity.write_text(
        identity.read_text().replace("display_name: Nora", "display_name: AgentAlpha")
    )
    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    deps = await build_dependencies(override_config(cfg, {"doctrine.dir": directory}))
    registry = deps.engagement._tool_registry
    own = TranscriptRow(id=1, sender_id="self", sender_name="PreviousLabel", ts=1,
                        body="I checked the venue", is_nora=True,
                        delivery_status=DeliveryStatus.SENT, raw_json=None)
    tools = {t.name: t.call for t in registry.specs(
        store=deps.audit, conversation_id="g1", conversation_type="group",
        persons=deps.persons, deliverer=None, metrics=deps.metrics,
        room=Room(platform="signal", conversation_id="g1", roster_source=None,
                  persons=deps.persons), transcript=(own,),
    )}
    result = await tools["who_said"](words="checked the venue")
    assert result["status"] == "ok"
    assert result["said_by"] == [{"who": "AgentAlpha", "line": "I checked the venue"}]
