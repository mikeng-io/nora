"""`play_music`/`skip_music`/`stop_music`/`music_status` — the platform gate, the
wiring-gap/no-tagger refusals, and the happy-path delegation to
`MusicToolBridge`. `DiscordMusicToolBridge` itself is covered by
`tests/unit/test_music_tool_bridge.py`; this file is about `nora.agent.tools`
never reaching it on the wrong platform or with no tagger."""

from __future__ import annotations

from nora.agent.room import Room
from nora.agent.surface import ToolScope
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
    def __init__(self, *, play_outcome=None, skip_outcome=None, top_outcome=None,
                 stop_outcome=None, status_outcome=None, volume_outcome=None) -> None:
        self.play_outcome = play_outcome
        self.skip_outcome = skip_outcome
        self.top_outcome = top_outcome
        self.stop_outcome = stop_outcome
        self.status_outcome = status_outcome
        self.volume_outcome = volume_outcome
        self.play_calls: list[dict] = []
        self.top_calls: list[dict] = []

    async def play(self, *, conversation_id, sender_ids, query_or_url):
        self.play_calls.append(
            {"conversation_id": conversation_id, "sender_ids": sender_ids,
             "query_or_url": query_or_url},
        )
        return self.play_outcome

    async def skip(self, *, conversation_id):
        return self.skip_outcome

    async def top(self, *, conversation_id, position):
        self.top_calls.append({"conversation_id": conversation_id, "position": position})
        return self.top_outcome

    async def stop(self, *, conversation_id):
        return self.stop_outcome

    async def status(self, *, conversation_id):
        return self.status_outcome

    async def set_volume(self, *, conversation_id, percent):
        return self.volume_outcome


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


async def test_music_verbs_are_absent_off_discord():
    """Catalog-level platform filter: music verbs are not shipped on a non-Discord
    catalog, so a Signal room never sees them."""
    # The cut is `ToolScope`'s — the one
    # scope every loop's tools pass through; `build_tool_specs` builds every
    # closure and the scope admits.
    scope = ToolScope(platform="signal")
    for verb in ("play_music", "skip_music", "top_music", "stop_music",
                 "music_status", "set_music_volume"):
        assert not scope.admits(verb)
        assert ToolScope(platform="discord").admits(verb)


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
    assert out == {
        "status": "ok", "title": "APT.", "url": "https://y/1",
        "note": "Started playing now. Your reply is the only announcement the "
                "channel gets, so name the track.",
    }
    assert bridge.play_calls == [
        {"conversation_id": "g1/c1", "sender_ids": frozenset({"9999"}), "query_or_url": "apt"},
    ]


async def test_play_music_passes_every_discord_id_the_person_has_not_just_one():
    """A person mapped to TWO Discord accounts in identity.toml: picking an
    arbitrary one of the two here (rather than handing the bridge the full set)
    would leave them in a voice channel on one account while the tool checked
    the other, and refuse them although they are already in the channel -- only
    `MusicToolBridge`, which can see who is actually in a voice channel right
    now, is in a position to pick the right one."""
    bridge = _FakeBridge(
        play_outcome=MusicToolOutcome(status="ok", title="APT.", url="https://y/1"),
    )
    tools = _specs(
        platform="discord", music=bridge, tagger=_MILES,
        persons=_FakePersons(frozenset({"9999", "8888"})),
    )

    await tools["play_music"](query="apt")

    [call] = bridge.play_calls
    assert call["sender_ids"] == frozenset({"9999", "8888"})


async def test_skip_music_reports_nothing_found():
    bridge = _FakeBridge(skip_outcome=MusicToolOutcome(status="nothing_found"))
    tools = _specs(platform="discord", music=bridge)
    out = await tools["skip_music"]()
    assert out == {"status": "nothing_found", "note": "Say so plainly, and don't guess."}


async def test_top_music_is_absent_off_discord():
    assert not ToolScope(platform="signal").admits("top_music")


async def test_top_music_reports_nothing_found():
    bridge = _FakeBridge(top_outcome=MusicToolOutcome(status="nothing_found"))
    tools = _specs(platform="discord", music=bridge)
    out = await tools["top_music"](position=5)
    assert out == {"status": "nothing_found", "note": "Say so plainly, and don't guess."}


async def test_top_music_passes_the_position_and_reports_the_moved_track():
    bridge = _FakeBridge(top_outcome=MusicToolOutcome(status="ok", title="Track C"))
    tools = _specs(platform="discord", music=bridge)

    out = await tools["top_music"](position=3)

    assert out == {"status": "ok", "title": "Track C"}
    [call] = bridge.top_calls
    assert call["position"] == 3


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


async def test_set_music_volume_reports_applied():
    bridge = _FakeBridge(
        volume_outcome=MusicToolOutcome(status="ok", note="Volume set to 50%."),
    )
    tools = _specs(platform="discord", music=bridge)
    out = await tools["set_music_volume"](percent=50)
    assert out == {"status": "ok", "note": "Volume set to 50%."}


async def test_set_music_volume_nothing_playing():
    bridge = _FakeBridge(volume_outcome=MusicToolOutcome(status="nothing_found"))
    tools = _specs(platform="discord", music=bridge)
    out = await tools["set_music_volume"](percent=200)
    assert out["status"] == "nothing_found"


async def test_set_music_volume_unavailable_with_no_bridge():
    tools = _specs(platform="discord", music=None)
    out = await tools["set_music_volume"](percent=80)
    assert out["status"] == "unavailable"


# ---- ADR 0015 §3: how to report a status rides in the result ----


def _call(tools: dict, verb: str):
    args = {"play_music": {"query": "apt"}, "top_music": {"position": 2},
            "set_music_volume": {"percent": 50}}.get(verb, {})
    return tools[verb](**args)


_VERBS = ("play_music", "skip_music", "top_music", "stop_music", "music_status",
          "set_music_volume")


async def test_every_non_ok_music_status_carries_a_note():
    """ADR 0015 §3/§6: each description only NAMES `nothing_found` and
    `unavailable`; how to report them is the result's `note`. Driven for every
    verb, with the bridge's own note and without one (a bridge that set none
    must still hand the model a reporting clause), plus the tool-side refusals."""
    tagged = {"tagger": _MILES, "persons": _FakePersons(frozenset({"9999"}))}
    cases = []
    for verb in _VERBS:
        field = {"play_music": "play_outcome", "skip_music": "skip_outcome",
                 "top_music": "top_outcome", "stop_music": "stop_outcome",
                 "music_status": "status_outcome",
                 "set_music_volume": "volume_outcome"}[verb]
        for outcome in (
            MusicToolOutcome(status="nothing_found"),
            MusicToolOutcome(status="nothing_found", note="Nothing is playing."),
            MusicToolOutcome(status="unavailable"),
            MusicToolOutcome(status="unavailable", note="Discord isn't connected right now."),
        ):
            tools = _specs(platform="discord", music=_FakeBridge(**{field: outcome}), **tagged)
            cases.append((f"{verb}/{outcome.status}/{outcome.note!r}", outcome.status,
                          await _call(tools, verb)))
        cases.append((f"{verb}/off", "unavailable",
                      await _call(_specs(platform="discord", music=None, **tagged), verb)))
        cases.append((f"{verb}/bridge_none", "unavailable",
                      await _call(_specs(platform="discord", music=_FakeBridge(), **tagged),
                                  verb)))
    cases.append(("play_music/no_tagger", "unavailable",
                  await _specs(platform="discord", music=_FakeBridge())["play_music"](
                      query="apt")))
    cases.append(("set_music_volume/range", "unavailable",
                  await _specs(platform="discord", music=_FakeBridge())["set_music_volume"](
                      percent=500)))
    for case, status, out in cases:
        assert out["status"] == status, (case, out)
        assert isinstance(out.get("note"), str) and out["note"].strip(), case
        assert "\u2014" not in out["note"] and "\u2013" not in out["note"], case
    # The bridge's own words are kept, not replaced.
    with_bridge_note = next(o for c, _, o in cases if c.startswith("skip_music/nothing_found/'N"))
    assert with_bridge_note["note"].startswith("Nothing is playing.")


async def test_play_music_ok_note_follows_queued():
    """The two ok-reporting rules not in play_music's description arrive
    only with the outcome they are about: «queued» when it went behind
    something, the only-announcement rule when it actually started."""
    def _tools(queued: bool) -> dict:
        outcome = MusicToolOutcome(status="ok", title="APT.", queued=queued)
        return _specs(platform="discord", music=_FakeBridge(play_outcome=outcome),
                      tagger=_MILES, persons=_FakePersons(frozenset({"9999"})))

    queued = await _tools(True)["play_music"](query="apt")
    assert queued["queued"] is True
    assert "queued" in queued["note"]
    assert "only announcement" not in queued["note"]

    started = await _tools(False)["play_music"](query="apt")
    assert "queued" not in started
    assert "only announcement" in started["note"]
    assert "queued" not in started["note"]


async def test_music_ok_carries_no_reporting_note_except_play():
    """`ok` from the other verbs is the bridge's data only; set_music_volume's
    `note` is the bridge's «Volume set to N%.», never overloaded."""
    ok = MusicToolOutcome(status="ok", title="T")
    tools = _specs(platform="discord", music=_FakeBridge(
        skip_outcome=ok, top_outcome=ok, stop_outcome=ok, status_outcome=ok,
        volume_outcome=MusicToolOutcome(status="ok", note="Volume set to 50%."),
    ))
    for verb in ("skip_music", "top_music", "stop_music", "music_status"):
        assert "note" not in await _call(tools, verb), verb
    assert (await tools["set_music_volume"](percent=50))["note"] == "Volume set to 50%."
