"""The THREAD from `policy.toml` to the three announcing surfaces.

Both ENDS are tested elsewhere — `tests/test_policy.py` pins
`Policy.music_channel_for` and `tests/unit/test_music_notify.py` pins
`make_notify` — and this file covers the four hops that connect them
(`composition._music_channel_of` → `AdapterRegistry.from_config` →
`DiscordAdapter` → `music_commands.register` / `resume_persisted_sessions`,
and separately `DiscordMusicToolBridge`).

That seam matters more than an ordinary one because of the direction it fails
in: `no_music_channel` is the default at five parameters, so a DROPPED hop
answers `None` for every guild — byte-identical to the operator not having
set the field. Fail-open, invisible, and the only part of this feature that
can regress with no symptom at all.
"""

from __future__ import annotations

import inspect

import pytest

from nora.app.composition import _music_channel_of
from nora.platform.discord import music_commands
from nora.platform.discord.adapter import DiscordAdapter
from nora.platform.discord.music_notify import no_music_channel
from nora.platform.discord.music_tool_bridge import DiscordMusicToolBridge
from nora.platform.registry import AdapterRegistry
from nora.policy import load_policy

GUILD = "100000000000000203"
DECLARING_ROOM = f"{GUILD}/100000000000000204"
MUSIC_CHANNEL = "1480387369830973999"

TOML = f"""
default = "deny"

[[conversations]]
platform = "discord"
id       = "{DECLARING_ROOM}"
label    = "Ridgeline #discussion"
persons  = ["miles"]
music_channel = "{MUSIC_CHANNEL}"
"""


@pytest.fixture
def resolver(tmp_path):
    """What `build_dependencies` builds and hands to the registry."""
    path = tmp_path / "policy.toml"
    path.write_text(TOML)
    return _music_channel_of(load_policy(path))


def test_the_composition_resolver_answers_the_channel(resolver):
    assert resolver(GUILD) == MUSIC_CHANNEL


def test_the_composition_resolver_falls_back_for_any_other_guild(resolver):
    assert resolver("999999999999999999") is None


def test_the_adapter_carries_the_resolver_to_both_music_entry_points(resolver):
    """`DiscordAdapter` holds it and passes it to BOTH `register` and
    `resume_persisted_sessions` — the resume hop is the one a reader is most
    likely to forget, because it runs on every `on_ready` rather than once."""
    adapter = DiscordAdapter("token", resolve_music_channel=resolver)

    assert adapter._resolve_music_channel is resolver
    assert adapter._resolve_music_channel(GUILD) == MUSIC_CHANNEL


def test_the_bridge_carries_the_resolver(resolver):
    bridge = DiscordMusicToolBridge(
        adapter=DiscordAdapter("token"), manager=None,  # type: ignore[arg-type]
        resolve_music_channel=resolver,
    )

    assert bridge._resolve_music_channel(GUILD) == MUSIC_CHANNEL


@pytest.mark.parametrize(
    ("func", "param"),
    [
        (AdapterRegistry.from_config, "resolve_music_channel"),
        (DiscordAdapter.__init__, "resolve_music_channel"),
        (music_commands.register, "resolve_music_channel"),
        (music_commands.resume_persisted_sessions, "resolve_music_channel"),
        (music_commands.MusicCommands.__init__, "resolve_music_channel"),
        (DiscordMusicToolBridge.__init__, "resolve_music_channel"),
    ],
)
def test_every_hop_accepts_the_resolver_and_defaults_to_the_fallback(func, param):
    """Each hop must both ACCEPT the resolver and default to
    `no_music_channel` — a hop defaulting to anything else would make a
    harness's announcements route somewhere nobody configured, and a hop
    missing the parameter is the dropped-thread failure this module exists
    for."""
    sig = inspect.signature(func)

    assert param in sig.parameters, f"{func.__qualname__} dropped {param}"
    assert sig.parameters[param].default is no_music_channel


@pytest.mark.parametrize(
    ("func", "param"),
    [
        (music_commands.register, "client_getter"),
        (music_commands.resume_persisted_sessions, "client_getter"),
        (music_commands.MusicCommands.__init__, "client"),
    ],
)
def test_the_client_getter_reaches_the_surfaces_that_capture_one(func, param):
    """The second thread: the slash and resume paths
    must be able to take `DiscordAdapter.current_client` rather than capturing
    a client that `listen()` will later close."""
    assert param in inspect.signature(func).parameters


def test_the_adapter_hands_its_own_getter_and_not_a_captured_client():
    """`DiscordAdapter._register_music_commands` must pass `self.current_client`
    — the bound method, re-reading `_current_client` on every call — to both
    music entry points. A `lambda: client` over the `on_ready` client would
    satisfy the signature and defeat the point."""
    source = inspect.getsource(DiscordAdapter.listen)

    assert source.count("client_getter=self.current_client") == 2
