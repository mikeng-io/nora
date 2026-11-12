"""The frozen roster answers as the live reader would."""

from scripts.cognition_loop.rosters import FrozenRoster, frozen_rosters


async def test_a_frozen_room_answers_its_members_and_an_unread_room_is_unavailable():
    rosters = frozen_rosters({"rosters": {"g/1": [["u-a", "+000"], ["u-b"]]}})
    source = FrozenRoster("discord", rosters)
    read = await source.roster(conversation_id="g/1")
    assert read.available and set(read.members) == {frozenset({"u-a", "+000"}), frozenset({"u-b"})}
    assert not (await source.roster(conversation_id="g/2")).available


def test_the_composed_rosters_gain_the_frozen_discord_reader():
    from types import SimpleNamespace

    from scripts.cognition_loop.run import _with_frozen_rosters

    def policies(**_k):
        return SimpleNamespace(rosters={})

    wiring = _with_frozen_rosters(policies, {"g/1": [["u-a"]]})()
    assert wiring.rosters["discord"].platform == "discord"


async def test_a_room_listed_without_a_guild_entry_gets_its_roster_frozen(
    monkeypatch, tmp_path,
):
    import json
    from types import SimpleNamespace

    from scripts.cognition_loop import __main__ as cli

    class _Client:
        def __init__(self, endpoint):
            pass

        async def read(self, op, params):
            assert op == "roster"
            return {"available": True, "value": {"members": [["u-a"]]}}

        async def close(self):
            pass

    monkeypatch.setattr(cli, "BridgeControlClient", _Client)
    cfg = SimpleNamespace(
        platforms=SimpleNamespace(discord=SimpleNamespace(base_url="http://bridge")),
        bridges=SimpleNamespace(token_env="T"),  # noqa: S106 — env var name
        credentials={})
    args = SimpleNamespace(room_directory=None, channel_reads_url=None)
    policy = SimpleNamespace(conversations=[
        SimpleNamespace(platform="discord", guild=None, ids=frozenset({"g/9"}))])
    (tmp_path / "out").mkdir()
    await cli._room_directory(args, cfg, policy, tmp_path / "out")
    frozen = json.loads((tmp_path / "out" / "room-directory.json").read_text())
    assert frozen["rosters"] == {"g/9": [["u-a"]]}
    assert args.frozen_rosters == {"g/9": [["u-a"]]}
