"""The composition root is the ONE place that reads config. Everything else
receives dependencies. This test pins that property."""

import ast
import base64
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nora.agent import tool_choice
from nora.agent.tools import GRAPH_MEMORY_TOOLS
from nora.app.composition import (
    Dependencies,
    StartupError,
    _require_a_sendable_public_url,
    build_dependencies,
)
from nora.app.fire import FirePath
from nora.app.heartbeat import HEARTBEAT_NOT_RUNNING
from nora.config import load
from nora.domain.models import DeliveryStatus, NormalizedMessage, TranscriptRow
from nora.infra.blobs import FilesystemBlobStore, RustfsBlobStore
from nora.platform.discord.media import DiscordMediaSource
from nora.platform.instagram.media import InstagramMediaSource
from nora.platform.registry import MediaSourceRegistry
from nora.platform.signal.media import SignalMediaSource
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
    # DATABASE_URL, which build_dependencies() reads (store.database_url_env) to run
    # migrations and wire the store: a throwaway database, never production.
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    dst = example_config_path
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

    from nora.agent.tools import NATIVE_TOOLS
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

    It has to be a tool whose name is an ordinary English word, such as `now` or `about`.
    Every other native is snake_case, which the STRUCTURAL detector catches on its own, so
    injecting one of those would make this pass whether or not `tool_names` was ever
    handed over.
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
    """Honcho v2 corpus-safety: the aiPeer id is Nora's self-model peer. A human mapped to it
    would feed under it and merge IRREVERSIBLY into her card (Honcho keeps conclusions,
    not text). Setting `memory.ai_peer` to an existing person id ("miles" in this fixture)
    must refuse to start, not silently corrupt the corpus."""
    with pytest.raises(StartupError, match="reserved for Nora"):
        await build_dependencies(override_config(cfg, {"memory.ai_peer": "miles"}))


async def test_no_enabled_platform_refuses_to_start(cfg):
    """`platforms.signal.enabled = false` is honoured
    (nora.platform.registry.AdapterRegistry): SignalAdapter does not start. With no
    platform enabled there is nothing for the engagement pipeline to send through, so this
    is a startup failure, not a degraded start."""
    disabled = override_config(cfg, {"platforms.signal.enabled": False})
    with pytest.raises(StartupError, match="no platform adapter is enabled"):
        await build_dependencies(disabled)


def test_build_dependencies_never_wires_the_real_database(cfg, tmp_path, pg_url):
    """A fixture that forgot to point the store at a throwaway database would wire a real
    SqlRecordStore against production. The store's URL comes from DATABASE_URL, which
    `pg_url` set to an ephemeral `t_…` database; the media root is under tmp_path. Both
    being throwaways is what proves it."""
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


async def test_an_enabled_instagram_gets_its_own_roster_even_while_degraded(cfg):
    """ONE ROSTER PER PLATFORM, and Instagram's is built for a DEGRADED adapter.

    Every Instagram room needs a roster: without one `group_members` answers
    `unavailable`, `where_is` cannot establish the room and refuses, and every outbound
    @-mention resolves to nobody and goes out as plain text with no ping.

    The adapter here IS degraded: the bridge base_url refuses instantly, so
    `resolve_identity()` fails and M0 registers it anyway. That is the point: an Instagram
    checkpoint clears without a restart, so a roster withheld on the startup state would
    stay withheld for the life of the process, with the room-scoped tools silently
    unavailable long after the session came back. The READ is what fails closed: a
    checkpointed bridge answers 409 and the roster is `available=False` for exactly as
    long as that is true.
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


async def test_the_heartbeat_is_wired_to_the_real_fire_path(cfg):
    """The tick's `fire` collaborator is a `FirePath`, not a stub.

    `HeartbeatLoop` takes `fire` as a collaborator and calls whatever it is handed, so a
    stub left in place claims every due row, delivers nothing, and resolves it, with every
    unit test green because each one supplies its own fake `fire`.

    Reaching into `background._tasks` and `_fire` is the only way to see it from out here:
    `Dependencies` deliberately exposes no heartbeat, and widening it to make one test
    easier is exactly what its docstring refuses.
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
        "[participation] enabled alone builds NOTHING (design 36): the trigger "
        "is the gate's, and a turn with no gate in front of it is the cost "
        "design 36 exists to stop paying"
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


async def test_remind_writes_a_real_row_through_the_registry_the_root_builds(cfg, pg_url):
    """The live `ToolRegistry` gets the reminders store, and `[heartbeat]` being off does not
    change that.

    Without `reminders=` on that constructor, `remind` answers `unavailable` in production
    while every test in `tests/test_reminder_tools.py` passes: each builds its OWN
    registry around the fake, so none of them can see the wiring. That is why this goes
    all the way to a row in the database rather than asserting on an attribute: a store
    held but never reached would satisfy the weaker check.

    `cfg.heartbeat.enabled` is False here (the shipped default). The tick is gated; the
    TOOLS are not: the model may set, list and cancel reminders on a deployment that never
    fires them, and the rows wait for the flag.
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
        # The caps come from `[reminders]`, converted at the composition root —
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
    """The sweep is the only path that resolves reminders with no tick running, and
    `nora_reminders_fired_total`'s spec says a reminder that vanishes must never vanish
    from the metrics too. A sweep that only logs a count and increments nothing leaves a
    crash that ate reminders with no trace on any dashboard.

    This asserts the WIRING, with a real stranded row in the real database: the counting
    function itself is unit-tested, and a function nobody calls would pass that test.
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
    """`[heartbeat] enabled = false` SHIPS, so the gauge must also be seeded outside
    `HeartbeatLoop.__init__`; bound only inside it, on every default deployment the
    series exports `0.0` forever, which reads exactly like "ticked a moment ago". The one
    reading an operator must be able to trust is the one it could not give.
    """
    deps = await build_dependencies(cfg)
    assert cfg.heartbeat.enabled is False
    assert deps.metrics.get(
        "nora_heartbeat_last_tick_age_seconds",
    ) == HEARTBEAT_NOT_RUNNING


async def test_compute_boot_recovery_sweeps_a_stranded_row_even_when_disabled(cfg, pg_url):
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

    run_migrations(pg_url)
    store = SqlComputeJobStore(create_sql_engine(pg_url))
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
    `test_the_heartbeat_is_wired_to_the_real_fire_path` does."""
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
        from nora.agent.tools import SUBAGENT_TOOLS

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
        "dreamer.enabled": True, "compute.enabled": True, "subagent.enabled": True,
        "heartbeat.enabled": False,
    })
    deps = await build_dependencies(on)
    try:
        assert isinstance(deps.subagent_backend, SubagentBackend)
        # the tools are offered, and their desk launches into THIS backend
        from nora.agent.tools import SUBAGENT_TOOLS

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




# --- Standing / the debrief stage ---------------------------------------

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
    """**One instance, two callers**, and this is the structural half of a
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
    from nora.engagement.stages.debrief import OBJECTIVE

    deps = await build_dependencies(override_config(cfg, {"standing.enabled": True}))
    body = deps.engagement._debrief_wiring.objective.objective_body(OBJECTIVE)
    assert "revise ONE short paragraph" in body


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










# --- the proactive adaptor (DEC-162) ------------------------------------






async def test_the_culture_adaptor_is_wired_with_its_grant_and_its_OWN_room(
    cfg, tmp_path,
):
    """**The grant is not inert.** Three things have to line up or the grant is inert and
    nothing says so: the runtime must hold a grant under the adaptor's OWN name (a second
    spelling is a grant that silently does not apply), the tool source must hold this
    key's room in `scope_rooms`, and the room must carry the platform. Without it there is
    no roster source, `view.available` is `False`, and every room-scoped verb answers
    `unavailable` forever while every counter stays green.

    `home_rooms` is asserted NOT to be the resolution path: culture is scope-keyed and
    `DreamerRuntime.run` supplies `unit=None` for it, so a resolution through `home_rooms`
    would look up `""` and find nothing."""
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
    """The property the grant exists for, measured at the seam rather than inferred from it:
    `RegistryToolSource.build` with the culture adaptor's own name, key and grant returns
    the room-scoped verbs (`group_members`, `ask_memory`) over a room with a real platform
    rather than the empty one.

    Without the scope-keyed resolution this returns the same NAMES built against
    `Room(platform="", roster_source=None)`, which is why the room is asserted too: a
    green name list is exactly what the defect looks like."""
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
    # `[memory_index]` is off here: the Honcho memory surface, so the graph's
    # `memory_search` is granted and not built (DEC-316).
    expected = set(culture.tools) - GRAPH_MEMORY_TOOLS
    assert names == expected, sorted(expected - names)
    assert "group_members" in names and "ask_memory" in names
    room = captured["room"]
    assert room.platform == "signal", "the verbs were built over the EMPTY room"
    assert room._roster_source is not None, "no roster: every verb is unavailable"
    assert captured["conversation_id"] == "g1"
    assert captured["location_trusted"] is True
    assert captured["supplied_unit"] is None




async def test_the_heartbeat_screens_proactive_rows_and_the_fire_path_can_stay_silent(
    cfg, tmp_path,
):
    """At the root: the heartbeat holds a `ProactiveScreen` over
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



async def test_the_proactive_runner_thinks_at_its_own_effort(cfg, tmp_path):
    """The proactive act turn reasons at `[proactive] reasoning_effort`, not
    `[participation]`'s. With participation ON, the two runners are distinct and each
    carries its own effort; a shared runner would speak on every case where silence was
    right at effort `none`."""
    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    deps = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": True, "notes.enabled": True, "heartbeat.enabled": True,
        "participation.enabled": True, "participation.reasoning_effort": "none",
        "proactive.reasoning_effort": "low",
    }))
    try:
        (loop,) = _spawned(deps, "HeartbeatLoop.run")
        path = loop._fire.__self__
        assert path._proactive_runner is not path._participation_runner
        assert path._proactive_runner._models._spec.reasoning_effort == "low"
        assert path._participation_runner._models._spec.reasoning_effort == "none"
        assert path._proactive_runner._models._spec.max_tokens == cfg.proactive.max_tokens
    finally:
        await deps.background.cancel_all()

async def test_a_home_room_keeps_the_proactive_runner_even_with_the_dreamer_off(
    cfg, tmp_path,
):
    """A proactive row written yesterday still comes due after `[dreamer] enabled` is flipped
    off, so the act turn's runner is gated on the home room (the row's existence
    condition), not on the adaptor being wired. Without it every such row would die
    `failed_error`."""
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


async def test_cognition_off_builds_no_stimulus_stage_and_no_evaluator(cfg):
    """`[cognition] enabled = false` is the shipped default: nothing is written
    to `stimulus`, no evaluator is spawned. Adapter health is recorded anyway —
    it is Record, not a subsystem that acts."""
    deps = await build_dependencies(cfg)
    assert deps.wake_evaluator is None
    assert deps.ingest._stimulus_wiring is None
    assert deps.health_recorder is not None




# --- the Cognition session (DEC-244) ------------------------------------------

_SESSION_ON = {
    "dreamer.enabled": True, "notes.enabled": True, "cognition.enabled": True,
}


async def test_cognition_off_imports_no_model_machinery(cfg, tmp_path, monkeypatch):
    """The shipped default: no runner, no evaluator, no shutdown to register, and
    `nora.app.cognition_agent_runner` (Strands and the model machinery) is not even
    imported."""
    import sys

    monkeypatch.delitem(sys.modules, "nora.app.cognition_agent_runner", raising=False)
    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    deps = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": True, "notes.enabled": True,
    }))
    assert deps.wake_evaluator is None
    assert deps.cognition_runner is None and deps.cognition_shutdown is None
    assert "nora.app.cognition_agent_runner" not in sys.modules


async def test_the_evaluators_floors_ready_follows_the_one_agenda_table(
    cfg, tmp_path, monkeypatch,
):
    """`nora.app.cognition_wiring.AGENDA_SKILLS` is the table the evaluator and the age gauge
    both read. Production maps the `time` skill, so floors are ready; a table without it
    must leave every floor inert (a root that hard-coded the answer would not follow it)."""
    from types import MappingProxyType

    import nora.app.cognition_wiring as wiring

    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    shipped = await build_dependencies(override_config(cfg, _SESSION_ON))
    assert shipped.wake_evaluator is not None and shipped.wake_evaluator._floors_ready is True
    monkeypatch.setattr(wiring, "AGENDA_SKILLS", MappingProxyType(
        {k: v for k, v in wiring.AGENDA_SKILLS.items() if k != "time"},
    ))
    patched = await build_dependencies(override_config(cfg, _SESSION_ON))
    assert patched.wake_evaluator is not None and patched.wake_evaluator._floors_ready is False


async def test_cognition_on_builds_one_runner_and_hands_its_hook_to_the_evaluator(
    cfg, tmp_path,
):
    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    deps = await build_dependencies(override_config(cfg, _SESSION_ON))
    runner, evaluator = deps.cognition_runner, deps.wake_evaluator
    assert runner is not None and evaluator is not None
    assert evaluator.has_wake_hook
    assert evaluator._on_wake == runner.wake_hook          # the SAME hook, not a copy
    assert deps.cognition_shutdown is not None and not deps.cognition_shutdown.done
    # A stopping process lets a running session finish for this long before it cancels it
    # (the PR-7 grace; `docker-compose.yml`'s `stop_grace_period` is sized above it).
    from nora.app.cognition_wiring import SESSION_SHUTDOWN_GRACE_SECONDS

    assert deps.cognition_shutdown._grace == SESSION_SHUTDOWN_GRACE_SECONDS > 0
    # The Dreamer's own budget entry, converted: rounds, tokens and the clock,
    # and no call ceiling (DEC-247); the clock ends before the wake's.
    assert (runner._budget.turns, runner._budget.timeout_seconds) == (200, 3600.0)
    assert (runner._budget.max_memory_calls, runner._budget.max_tool_calls) == (None, None)
    assert runner._budget.timeout_seconds <= cfg.cognition.session_timeout_seconds - 60.0
    # Its guard holds the result caps and the R-99 argument table, and no call ceiling.
    from nora.agent.dreamer import TOOL_RESULT_CAPS

    agent = runner._agent_runner
    assert agent._result_caps == dict(TOOL_RESULT_CAPS)
    assert not hasattr(agent, "_read_tool_budgets")
    # DEC-246: the two reads are wired and BUILT through the real
    # registry (a tool whose read the composition root did not hand the registry
    # is withheld from every surface and would be «wired» in name only).
    unit, home = next(iter(runner._home_rooms.items()))
    built = {t.tool_name for t in runner._read_tools_for(unit, [home])}
    assert {"rollup_read", "standing_read"} <= built
    # Read tools only: the registry's table also names the tag path's
    # `remind`/`note_create`, whose arguments differ from the session's own
    # verbs of those names, and R-99 would veto every call to them.
    assert agent._param_names is not None and "note_read" in agent._param_names
    # R-99 must accept the `room` argument the routed tools of a multi-room agenda take,
    # for every read tool held.
    assert all("room" in names for names in agent._param_names.values())
    assert set(agent._param_names) <= runner._read_only_tools
    # The session's instruction is the Dreamer's objective for it, read from
    # the bind-mounted corpus, its own prelude first (not the shared doctrine),
    # and the composed runner injects the Register skill for a
    # `message` agenda and records it; `time` is mapped, so the floors wake.
    text, sha = runner._doctrine()
    assert text.startswith('<dreamer_doctrine name="cognition-prelude">')
    assert '<dreamer_objective name="cognition">' in text and sha
    assert "# Skill: cognition-register" in runner._skill_text_for(["message"])
    assert set(runner._skill_versions_for(["message"])) == {"cognition-register"}
    assert evaluator._floors_ready is True
    # Only units with a home room are woken: `wedding` names none.
    assert sorted(evaluator._units()) == ["friends"]
    assert set(evaluator._session_units()) == {"friends"}
    assert set(runner._home_rooms) == {"friends"}


def _dreamer_tree(tmp_path: Path) -> Path:
    """A copy of the shipped `nora/dreamer`, so a test can take a file out of it."""
    import shutil

    root = tmp_path / "dreamer-copy"
    shutil.copytree(ROOT / "nora" / "dreamer", root)
    return root


@pytest.mark.parametrize("how", ["prelude deleted", "prelude applies to nothing",
                                 "prelude applies elsewhere", "skill deleted"])
async def test_a_session_whose_prelude_or_skill_is_missing_is_a_startup_error(
    cfg, tmp_path, how,
):
    """Without its prelude, `cognition` silently reads the shared Dreamer doctrine («you
    look; you do not act»), and a mapped skill that is not installed would raise a raw
    `LookupError` out of the composition. Both must be a named `StartupError`."""
    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    root = _dreamer_tree(tmp_path)
    prelude = root / "preludes" / "cognition-prelude.md"
    if how == "prelude deleted":
        prelude.unlink()
    elif how == "skill deleted":
        (root / "skills" / "cognition-register.md").unlink()
    else:
        applies = "[]" if how == "prelude applies to nothing" else "[note-merge]"
        prelude.write_text(
            prelude.read_text(encoding="utf-8").replace(
                "applies_to: [cognition]", f"applies_to: {applies}",
            ),
            encoding="utf-8",
        )
    expect = "cognition-register" if how == "skill deleted" else "cognition-prelude"
    with pytest.raises(StartupError, match=expect):
        await build_dependencies(override_config(cfg, {**_SESSION_ON, "dreamer.dir": root}))


async def test_the_same_tree_builds_when_nothing_is_taken_out_of_it(cfg, tmp_path):
    """The positive control of the three refusals above: an untouched copy is accepted."""
    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    deps = await build_dependencies(override_config(
        cfg, {**_SESSION_ON, "dreamer.dir": _dreamer_tree(tmp_path)},
    ))
    assert deps.cognition_runner is not None


async def test_a_prelude_deleted_after_startup_is_refused_per_session(cfg, tmp_path):
    """Hot reload: the file goes after a good start. The next session's instruction would
    be the shared doctrine; it is refused by name instead."""
    from nora.app.cognition_wiring import PreludeMissingError

    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    root = _dreamer_tree(tmp_path)
    deps = await build_dependencies(override_config(cfg, {**_SESSION_ON, "dreamer.dir": root}))
    runner = deps.cognition_runner
    assert runner is not None and runner._doctrine()[0].startswith(
        '<dreamer_doctrine name="cognition-prelude">',
    )
    (root / "preludes" / "cognition-prelude.md").unlink()
    with pytest.raises(PreludeMissingError, match="cognition-prelude"):
        runner._doctrine()


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
    # `character_read` follows `[culture] enabled` the same way (self-growth C4).
    with_culture = await build_dependencies(override_config(
        cfg, {**_SESSION_ON, "culture.enabled": True},
    ))
    assert "character_read" not in off.cognition_runner._read_only_tools
    assert "character_read" in with_culture.cognition_runner._read_only_tools
    wired = off.cognition_runner._read_only_tools
    assert {"note_read", "about", "where_been", "event_search"} <= wired
    assert {"rollup_read", "standing_read"} <= wired                    # DEC-246
    assert "memo_read" in wired                                         # DEC-306
    assert "social_items" not in wired  # not built yet
    # Built for a real room they are real, read-only tools, and only wired ones.
    built = on.cognition_runner._read_tools_for("friends", [Room("signal", "g1")])
    names = {t.tool_name for t in built}
    assert names and names <= on.cognition_runner._read_only_tools
    assert {"note_read", "record_query"} <= names


async def test_the_configured_model_window_reaches_the_runner_the_overflow_proof_reads(
    cfg, tmp_path,
):
    """Unset (the default) is «window unknown», so no overflow is the agenda's;
    a configured window and fraction are what the runner proves one against."""
    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    default = (await build_dependencies(override_config(cfg, _SESSION_ON))).cognition_runner
    assert default is not None and default._window_tokens is None
    deps = await build_dependencies(override_config(cfg, {
        **_SESSION_ON, "cognition.model_window_tokens": 123_456,
        "cognition.overflow_window_fraction": 0.5,
    }))
    assert deps.cognition_runner is not None
    assert (deps.cognition_runner._window_tokens, deps.cognition_runner._window_fraction) == (
        123_456, 0.5)


async def test_cognition_on_that_built_no_runner_refuses_to_boot(
    cfg, tmp_path, monkeypatch,
):
    """The boot check: a loop that wakes and runs no session must not boot."""
    from nora.app import composition

    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    monkeypatch.setattr(composition, "build_cognition_runner", lambda **kw: None)
    with pytest.raises(composition.StartupError, match="no session runner"):
        await build_dependencies(override_config(cfg, _SESSION_ON))


async def test_the_session_is_grounded_from_the_record_with_record_query_off(
    cfg, tmp_path, pg_url,
):
    """The rows that raised a stimulus are read by code for the agenda whether or not
    `[record_query] enabled` offers a MODEL the tool. Real Postgres, the composition's own
    grounder: who said what, with the id."""
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
    """Units without a home room stay in the evaluator, but a shadow week in which NO unit
    can start a session would record would-wakes and look healthy."""
    from nora.app import composition

    (tmp_path / "policy.toml").write_text(
        'default = "deny"\n[[conversations]]\nplatform = "signal"\nid = "g1"\npersons = "*"\n'
        'unit = "friends"\nlabel = "Friends"\n',
    )
    with pytest.raises(composition.StartupError, match="no unit can run a session"):
        await build_dependencies(override_config(cfg, _SESSION_ON))


async def test_units_without_a_home_room_are_gauged_and_never_woken(cfg, tmp_path):
    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    deps = await build_dependencies(override_config(cfg, _SESSION_ON))
    assert deps.metrics.get("nora_cognition_session_units", state="runnable") == 1
    assert deps.metrics.get("nora_cognition_session_units", state="no_home_room") == 1
    assert "wedding" not in list(deps.wake_evaluator._units())


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
    fire = override_config(cfg, {"documents.enabled": True, "heartbeat.enabled": True})
    deps = await build_dependencies(fire)
    try:
        loops = [
            task.get_coro().cr_frame.f_locals["self"]
            for task in deps.background._tasks
            if task.get_coro().__qualname__ == "HeartbeatLoop.run"
        ]
        assert loops, "the heartbeat loop was not spawned"
        assert loops[0]._fire.__self__._documents is deps.assembler._documents_reader
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
        _require_a_sendable_public_url(origin)


@pytest.mark.parametrize("origin", ["", "https://nora.67.zone", "http://localhost:3000"])
def test_a_sendable_public_url_or_none_starts(origin) -> None:
    _require_a_sendable_public_url(origin)


async def test_no_wired_adaptor_holds_an_act_verb(cfg, tmp_path):
    """`verify_tool_subsets` runs at startup, so reaching this line is half the
    assertion; the other half names it: with every tier on, every Dreamer
    adaptor stays inside the read-only ceiling (the Cognition session writes
    through its own verbs, not registry tools)."""
    from nora.agent.registry import ToolRegistry

    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    deps = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": True, "notes.enabled": True, "culture.enabled": True,
    }))

    ceiling = ToolRegistry().read_only_tools()
    assert deps.dreamer_runtime is not None
    assert [a.name for a in deps.dream_adaptors] == [
        "culture", "character-person", "character-nora", "character-unit",
    ]
    assert deps.dream_schedules["character-nora"].wake_batch == 100
    assert deps.dream_schedules["character-unit"].wake_batch == 5
    # A person session wakes on its own count, on the room fold's stale clock,
    # and runs on the room fold's budget when the deployment set one.
    person = deps.dream_schedules["character-person"]
    assert person.wake_batch == 100
    assert person.min_interval_seconds == deps.dream_schedules["culture"].min_interval_seconds
    for adaptor in deps.dream_adaptors:
        assert adaptor.tools <= ceiling, adaptor.name


async def test_a_person_session_reads_its_wake_batch_not_a_rooms_batch_limit(cfg, tmp_path):
    """Cost: a person session read `[culture] batch_limit` (300, sized for a
    whole room's corpus) messages. It reads the newest `person_wake_batch`,
    as Nora's session reads its own wake batch."""
    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    deps = await build_dependencies(override_config(cfg, {
        "dreamer.enabled": True, "notes.enabled": True, "culture.enabled": True,
        "culture.person_wake_batch": 40, "culture.batch_limit": 300,
    }))
    assert deps.dream_schedules["character-person"].batch_limit == 40
    assert deps.dream_schedules["character-nora"].batch_limit == 100
    assert deps.dream_schedules["culture"].batch_limit == 300


async def test_culture_resolves_a_mapped_senders_canonical_name(cfg):
    """`CultureAdaptor` names its `<room_messages>` through `Persons.name_transcript`
    (`identity.toml`'s `[persons.miles]` maps signal `+852111` to `Miles`, the `cfg`
    fixture's own shape)."""
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


async def test_an_eval_subject_with_the_subagent_on_builds_the_tools_and_a_recorder(
    cfg, no_build_stamp,
):
    """`recording_subagent_worker` (what `obtain` wraps composition in under `[subagent]
    enabled`): the four tools are offered, the desk and the poller hold a recorder under kind
    `subagent`, and no worker is built, so nothing can start. The patch is gone afterwards."""
    from nora.agent.tools import SUBAGENT_TOOLS
    from nora.app import composition
    from nora.subject.recording_subagent import (
        RecordingSubagentBackend,
        recording_subagent_worker,
    )

    real = composition.build_subagent_backend
    on = override_config(cfg, {
        "dreamer.enabled": True, "compute.enabled": True, "subagent.enabled": True,
        "heartbeat.enabled": False,
    })
    with recording_subagent_worker():
        deps = await build_dependencies(on)
    assert composition.build_subagent_backend is real
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


@pytest.mark.parametrize("graph", [False, True])
async def test_cognition_holds_the_memory_surface_the_flag_selects(cfg, tmp_path, graph):
    """DEC-316, through the real composition: `[memory_index] enabled` picks
    the session's memory verbs exactly as it picks every other occasion's.
    The session's `wired` set (its guard's read-only set) and what it BUILDS
    agree, and neither holds the other surface."""
    from nora.agent.tools import GRAPH_MEMORY_TOOLS, HONCHO_MEMORY_TOOLS

    (tmp_path / "policy.toml").write_text(_HOME_POLICY)
    deps = await build_dependencies(override_config(
        cfg, {**_SESSION_ON, "memory_index.enabled": graph},
    ))
    runner = deps.cognition_runner
    assert runner is not None
    unit, home = next(iter(runner._home_rooms.items()))
    built = {t.tool_name for t in runner._read_tools_for(unit, [home])}
    on, off = (GRAPH_MEMORY_TOOLS, HONCHO_MEMORY_TOOLS) if graph else (
        HONCHO_MEMORY_TOOLS, GRAPH_MEMORY_TOOLS)
    assert ("memory_search" in built) is graph
    assert not built & off and not runner._read_only_tools & off
    assert built & on and runner._read_only_tools & on
