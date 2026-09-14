"""Fakes for the `online_now` tool's two ports (design/online-status/).

`FakeSteamPresenceProvider` (`SteamPresenceProvider`, bound to
`nora.infra.steam.SteamPresenceClient` by
`tests/contract/test_steam_presence_contract.py`) and
`FakeVoicePresenceSource` (`VoicePresenceSource`, bound to
`nora.platform.discord.voice_presence.DiscordVoicePresence` by
`tests/contract/test_voice_presence_contract.py`).

Both behave like the real thing rather than answering every call the same
way (the repo's fake rule): they answer per-id from the data they were
configured with and no other id, they distinguish the WHOLE READ failing
(`fail=True` -> `None`, the tool's `unavailable`) from one id having no
reading (an absent key, that id's `nothing_found`), and they record every
call so a test can prove the batch shape the tool promised — one
`summaries` call carrying the room's whole id set, never one per person.
"""

from __future__ import annotations

from collections.abc import Mapping

from nora.domain.ports import SteamPlayerState, VoiceChannelRef


class FakeSteamPresenceProvider:
    """In-memory `SteamPresenceProvider`. Passes
    tests/contract/test_steam_presence_contract.py."""

    def __init__(
        self, *, players: Mapping[str, SteamPlayerState] | None = None, fail: bool = False,
    ) -> None:
        self._players = dict(players or {})
        self.fail = fail
        self.calls: list[frozenset[str]] = []

    async def summaries(
        self, *, steam_ids: frozenset[str],
    ) -> dict[str, SteamPlayerState] | None:
        self.calls.append(steam_ids)
        if self.fail:
            return None
        if not steam_ids:
            return {}
        return {sid: self._players[sid] for sid in steam_ids if sid in self._players}


class FakeVoicePresenceSource:
    """In-memory `VoicePresenceSource`. Passes
    tests/contract/test_voice_presence_contract.py."""

    def __init__(
        self,
        *,
        channels: Mapping[str, VoiceChannelRef] | None = None,
        fail: bool = False,
    ) -> None:
        self._channels = dict(channels or {})
        self.fail = fail
        self.calls: list[frozenset[str]] = []

    async def in_voice(
        self, *, member_ids: frozenset[str],
    ) -> dict[str, VoiceChannelRef] | None:
        self.calls.append(member_ids)
        if self.fail:
            return None
        if not member_ids:
            return {}
        return {mid: self._channels[mid] for mid in member_ids if mid in self._channels}
