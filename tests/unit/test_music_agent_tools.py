"""`play_music`/`skip_music`/`stop_music`/`music_status`
(design/discord-music/07-agent-tool.md) — the platform gate, the
wiring-gap/no-tagger refusals, and the happy-path delegation to
`MusicToolBridge`. `DiscordMusicToolBridge` itself is covered by
`tests/unit/test_music_tool_bridge.py`; this file is about `nora.agent.tools`
never reaching it on the wrong platform or with no tagger."""

from __future__ import annotations

from nora.agent.room import Room
from nora.agent.tools import build_tool_specs
from nora.domain.models import PersonRef
from nora.domain.ports import MusicToolOutcome
from nora.obs.metrics import Metrics


class _FakePersons:
    """`platform_ids` is the only method the music tools call."""

    def __init__(self, ids: frozenset[str] = frozenset()) -> None:
        self._ids = ids

    def platform_ids(self, platform: str, person_id: str) -> frozenset[str]:
        return self._ids if platform == "discord" else frozenset()


class _FakeBridge:
    def __init__(self, *, play_outcome=None, skip_outcome=None,
                 stop_outcome=None, status_outcome=None) -> None:
        self.play_outcome = play_outcome
        self.skip_outcome = skip_outcome
        self.stop_outcome = stop_outcome
        self.status_outcome = status_outcome
        self.play_calls: list[dict] = []

    async def play(self, *, conversation_id, sender_id, query_or_url):
        self.play_calls.append(
            {"conversation_id": conversation_id, "sender_id": sender_id,
             "query_or_url": query_or_url},
        )
        return self.play_outcome

    async def skip(self, *, conversation_id):
        return self.skip_outcome

    async def stop(self, *, conversation_id):
        return self.stop_outcome

    async def status(self, *, conversation_id):
        return self.status_outcome


_MILES = PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")


def _specs(*, platform: str, persons=None, music=None, tagger=None) -> dict:
    room = Room(
        platform=platform, conversation_id="g1/c1", roster_source=None,
        persons=persons or _FakePersons(), tagger=tagger,
    )
    specs = build_tool_specs(
        store=None, conversation_id="g1/c1", conversation_type="group",
        persons=persons or _FakePersons(), honcho=None, deliverer=None,
        metrics=Metrics(), room=room, transit_source=None,
        music=music,
    )
    return {s.name: s.call for s in specs}


async def test_play_music_is_unavailable_off_discord():
    tools = _specs(platform="signal", music=_FakeBridge())
    out = await tools["play_music"](query="apt")
    assert out["status"] == "unavailable"


async def test_skip_music_is_unavailable_off_discord():
    tools = _specs(platform="signal", music=_FakeBridge())
    out = await tools["skip_music"]()
    assert out["status"] == "unavailable"


async def test_play_music_is_unavailable_with_no_bridge_wired():
    tools = _specs(platform="discord", music=None)
    out = await tools["play_music"](query="apt")
    assert out["status"] == "unavailable"


async def test_play_music_is_unavailable_with_no_tagger():
    tools = _specs(platform="discord", music=_FakeBridge(), tagger=None)
    out = await tools["play_music"](query="apt")
    assert out["status"] == "unavailable"


async def test_play_music_is_unavailable_when_tagger_has_no_discord_id():
    tools = _specs(
        platform="discord", music=_FakeBridge(), tagger=_MILES,
        persons=_FakePersons(frozenset()),
    )
    out = await tools["play_music"](query="apt")
    assert out["status"] == "unavailable"


async def test_play_music_passes_the_taggers_discord_id_and_the_query():
    bridge = _FakeBridge(
        play_outcome=MusicToolOutcome(status="ok", title="APT.", url="https://y/1"),
    )
    tools = _specs(
        platform="discord", music=bridge, tagger=_MILES,
        persons=_FakePersons(frozenset({"9999"})),
    )
    out = await tools["play_music"](query="apt")
    assert out == {"status": "ok", "title": "APT.", "url": "https://y/1"}
    assert bridge.play_calls == [
        {"conversation_id": "g1/c1", "sender_id": "9999", "query_or_url": "apt"},
    ]


async def test_skip_music_reports_nothing_found():
    bridge = _FakeBridge(skip_outcome=MusicToolOutcome(status="nothing_found"))
    tools = _specs(platform="discord", music=bridge)
    out = await tools["skip_music"]()
    assert out == {"status": "nothing_found"}


async def test_stop_music_reports_ok():
    bridge = _FakeBridge(stop_outcome=MusicToolOutcome(status="ok"))
    tools = _specs(platform="discord", music=bridge)
    out = await tools["stop_music"]()
    assert out == {"status": "ok"}


async def test_music_status_reports_now_playing():
    bridge = _FakeBridge(
        status_outcome=MusicToolOutcome(status="ok", title="APT.", url="https://y/1"),
    )
    tools = _specs(platform="discord", music=bridge)
    out = await tools["music_status"]()
    assert out == {"status": "ok", "title": "APT.", "url": "https://y/1"}
