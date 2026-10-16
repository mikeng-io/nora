"""One voice connection, one owner — and the owner is NOT this process.

Discord allows ONE voice connection per bot token per guild. If nora held one locally
(`discord.py` `channel.connect()`, a `VoiceClient`, a reconnect path) while the
voice-sidecar held another via `joinVoiceChannel` on the same token, the second join would
silently kill the first, and `/music play` would reply "playing" into a dead socket. So
the sidecar (the audio-controller) is the SOLE holder of the Discord voice socket; nora
drives it over HTTP (`nora/infra/voice/http_controller.py`) and never connects itself.

This invariant is CODE-ENFORCED, not compose prose — this gate is that enforcement. It
forbids, anywhere under `nora/`:

1. `channel.connect(` — the discord.py call that opens a voice connection.
2. `VoiceClient` — the type every local voice-session implementation is built on
   (annotation, isinstance, or construction are all ownership).
3. `change_voice_state(` — discord.py's OTHER join path:
   `Guild.change_voice_state(channel=...)` moves the bot into a voice channel via the raw
   gateway op, bypassing `channel.connect()` and `VoiceClient` entirely while still
   acquiring the same ownership this gate exists to forbid.

No local-implementation package exists, so the allowed set is EMPTY and the invariant is
absolute. Reintroducing local voice ownership means creating that package AND pinning it
here — a visible, reviewable edit, never a quiet one.

The positive control pins the detector against a synthetic sample so a regex that stops
matching fails loudly rather than passing with an empty offender list.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: The two ownership shapes. `channel.connect(` is a SUBSTRING anchored on the receiver
#: discord.py's join goes through — no leading `\b`, because `_` is a word character and a
#: reconnect path calling `self._reconnect_channel.connect()` would never match
#: `\bchannel`. Receivers like `mychannel` also match; that over-match is deliberate — the
#: fix for a false positive is renaming the variable, not weakening the detector.
#: `VoiceClient` is a bare word — there is no legitimate mention of the type in the
#: production tree.
_CHANNEL_CONNECT = re.compile(r"channel\.connect\s*\(")
_VOICE_CLIENT = re.compile(r"\bVoiceClient\b")
#: `change_voice_state` is not a substring of any legitimate identifier in the tree (same
#: reasoning as `VoiceClient` above), so a bare, unanchored match is enough — no
#: false-positive risk to trade away for a narrower pattern.
_CHANGE_VOICE_STATE = re.compile(r"change_voice_state\s*\(")

#: Empty by design — see the module docstring. A future local voice
#: implementation lives in a new package and is pinned HERE, with the
#: decision that reopens the sole-owner rule.
_ALLOWED: frozenset[str] = frozenset()


def _production_sources() -> list[Path]:
    return sorted(
        p for p in (ROOT / "nora").rglob("*.py") if ".venv" not in p.parts
    )


def _offenders(pattern: re.Pattern[str]) -> list[str]:
    found = []
    for path in _production_sources():
        rel = path.relative_to(ROOT).as_posix()
        if rel in _ALLOWED:
            continue
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if pattern.search(line):
                found.append(f"{rel}:{number}: {line.strip()}")
    return found


def test_the_detector_matches_both_ownership_shapes() -> None:
    """Positive control: a detector that no longer sees the shapes it
    exists to forbid must fail here, not pass with an empty tree scan."""
    assert _CHANNEL_CONNECT.search("voice_client = await channel.connect()")
    assert _CHANNEL_CONNECT.search("await self._channel.connect()")
    assert _CHANNEL_CONNECT.search("await self._reconnect_channel.connect()")
    assert _VOICE_CLIENT.search("client: discord.VoiceClient")
    assert _CHANGE_VOICE_STATE.search("await guild.change_voice_state(channel=channel)")
    assert not _CHANNEL_CONNECT.search("await client.connect_to_database()")
    assert not _CHANNEL_CONNECT.search("await client.connect()")
    assert not _VOICE_CLIENT.search("the voice client registry")
    assert not _CHANGE_VOICE_STATE.search("# do not change voice state directly")


def test_nora_never_opens_a_discord_voice_connection() -> None:
    offenders = _offenders(_CHANNEL_CONNECT)
    assert not offenders, (
        "nora must never call channel.connect() — the audio-controller is the "
        "sole voice-connection owner (design/voice-channel/07, B1):\n"
        + "\n".join(offenders)
    )


def test_nora_never_holds_a_voice_client() -> None:
    offenders = _offenders(_VOICE_CLIENT)
    assert not offenders, (
        "VoiceClient has no place in nora's tree — the audio-controller is the "
        "sole voice-connection owner (design/voice-channel/07, B1):\n"
        + "\n".join(offenders)
    )


def test_nora_never_calls_change_voice_state() -> None:
    """The ownership gate's other blind spot: `Guild.change_voice_state(channel=...)` joins a
    voice channel without ever touching `channel.connect()` or `VoiceClient`."""
    offenders = _offenders(_CHANGE_VOICE_STATE)
    assert not offenders, (
        "change_voice_state has no place in nora's tree — the audio-controller "
        "is the sole voice-connection owner (design/voice-channel/07, B1):\n"
        + "\n".join(offenders)
    )
