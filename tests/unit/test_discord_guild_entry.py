"""A `guild = "<id>"` `[[conversations]]` entry: one whole Discord server,
every room-channel a room of the entry's unit, threads never rooms.

The directory the rooms are discovered through is
`StaticChannelDirectory`, the same port `ClientChannelDirectory` answers in
production — channel ids and names here are generated, like every fixture.
"""

from __future__ import annotations

import pytest

from nora.platform.discord.channels import StaticChannelDirectory
from nora.policy import PolicyError, load_policy

GUILD = "100000000000000042"
CHANNEL_A = "100000000000000101"
CHANNEL_B = "100000000000000102"
CHANNEL_C = "100000000000000103"
THREAD = "100000000000000201"
VOICE = "100000000000000301"


def _write(tmp_path, body):
    p = tmp_path / "policy.toml"
    p.write_text(body)
    return p


def _entry(*, label="Zone 9", unit="", extra="", fields="", persons='"*"'):
    return (
        '[[conversations]]\nplatform = "discord"\n'
        f'guild = "{GUILD}"\nlabel = "{label}"\n'
        f"{unit}{fields}persons = {persons}\n{extra}"
    )


def _policy(tmp_path, body, channels=None):
    return load_policy(
        _write(tmp_path, body),
        channels=StaticChannelDirectory(channels or {}, threads={}),
    )


DISCOVERED = {GUILD: {CHANNEL_A: "general", CHANNEL_B: "discussion",
                      CHANNEL_C: "admin-log"}}


def test_a_server_entry_admits_a_discovered_channel_by_guild_id(tmp_path):
    policy = _policy(tmp_path, 'default = "deny"\n' + _entry(), DISCOVERED)

    room = policy.conversation("discord", f"{GUILD}/{CHANNEL_A}")

    assert room is not None
    assert room.discovered
    assert room.unit == "nora"
    assert room.label == "general"
    assert room.ids == {f"{GUILD}/{CHANNEL_A}"}


def test_a_discovered_channel_is_mention_only_by_default(tmp_path):
    policy = _policy(tmp_path, 'default = "deny"\n' + _entry(), DISCOVERED)

    room = policy.conversation("discord", f"{GUILD}/{CHANNEL_A}")

    assert room is not None
    assert not room.relevance_gate
    assert not room.absorbed
    # …but still admitted and still gated on persons the entry names.
    assert policy.is_allowed(
        platform="discord", conversation_id=f"{GUILD}/{CHANNEL_A}", person_id="miles"
    )


def test_an_ambient_named_channel_is_a_full_ambient_room(tmp_path):
    policy = _policy(
        tmp_path, 'default = "deny"\n' + _entry(extra='ambient = ["discussion"]\n'),
        DISCOVERED,
    )

    room = policy.conversation("discord", f"{GUILD}/{CHANNEL_B}")

    assert room is not None
    assert room.relevance_gate
    assert room.absorbed
    # `rooms()` — the enumeration lineage, the Honcho router and the fold
    # scopes all read — sees it too.
    assert f"{GUILD}/{CHANNEL_B}" in {
        cid for c in policy.rooms() for cid in c.ids
    }


def test_an_ignored_named_channel_is_never_admitted(tmp_path):
    policy = _policy(
        tmp_path, 'default = "deny"\n' + _entry(extra='ignore = ["admin-log"]\n'),
        DISCOVERED,
    )

    assert policy.conversation("discord", f"{GUILD}/{CHANNEL_C}") is None
    assert f"{GUILD}/{CHANNEL_C}" not in {
        cid for c in policy.rooms() for cid in c.ids
    }


def test_an_explicit_per_channel_entry_wins_over_the_server_entry(tmp_path):
    body = (
        'default = "deny"\n' + _entry(unit='unit = "nora"\n')
        + '[[conversations]]\nplatform = "discord"\n'
        f'id = "{GUILD}/{CHANNEL_A}"\nlabel = "Front Porch"\n'
        'persons = ["miles"]\n'
    )
    policy = _policy(tmp_path, body, DISCOVERED)

    room = policy.conversation("discord", f"{GUILD}/{CHANNEL_A}")

    assert room is not None
    assert not room.discovered
    assert room.label == "Front Porch"
    assert room.persons == {"miles"}
    # `rooms()` must yield the channel exactly once, as the explicit room —
    # a second, synthesized room under the same id would shadow it.
    matching = [
        c for c in policy.rooms() if f"{GUILD}/{CHANNEL_A}" in c.ids
    ]
    assert matching == [room]


def test_two_channels_with_the_same_name_fail_closed(tmp_path):
    channels = {GUILD: {CHANNEL_A: "general", CHANNEL_B: "General"}}
    policy = _policy(tmp_path, 'default = "deny"\n' + _entry(), channels)

    assert policy.conversation("discord", f"{GUILD}/{CHANNEL_A}") is None
    assert policy.conversation("discord", f"{GUILD}/{CHANNEL_B}") is None


def test_a_channel_name_clashing_with_a_room_label_fails_closed(tmp_path):
    body = (
        'default = "deny"\n' + _entry()
        + '[[conversations]]\nplatform = "signal"\n'
        'id = "sig-room-1"\nlabel = "General"\npersons = "*"\n'
    )
    policy = _policy(tmp_path, body, DISCOVERED)

    assert policy.conversation("discord", f"{GUILD}/{CHANNEL_A}") is None
    assert policy.conversation("discord", f"{GUILD}/{CHANNEL_B}") is not None


def test_a_channel_named_for_a_network_is_refused(tmp_path):
    channels = {GUILD: {CHANNEL_A: "discord", CHANNEL_B: "general"}}
    policy = _policy(tmp_path, 'default = "deny"\n' + _entry(), channels)

    assert policy.conversation("discord", f"{GUILD}/{CHANNEL_A}") is None
    assert policy.conversation("discord", f"{GUILD}/{CHANNEL_B}") is not None


def test_an_unguilded_channel_or_a_guild_the_bot_cannot_see_is_denied(tmp_path):
    policy = _policy(tmp_path, 'default = "deny"\n' + _entry(), DISCOVERED)

    # A channel id the directory does not know — the bot is not in this guild,
    # or the channel is a voice/stage type the entry never expands into.
    assert policy.conversation("discord", f"{GUILD}/{VOICE}") is None
    # Another guild entirely.
    assert policy.conversation("discord", f"999999999999999999/{CHANNEL_A}") is None
    # A bare id that is not "<guild>/<channel>" at all.
    assert policy.conversation("discord", GUILD) is None


def test_a_thread_id_is_never_itself_a_room(tmp_path):
    policy = _policy(tmp_path, 'default = "deny"\n' + _entry(), DISCOVERED)

    # The parser already maps a thread's message to its parent; anything
    # asking about the thread's own id gets no room.
    assert policy.conversation("discord", f"{GUILD}/{THREAD}") is None


def test_entry_fields_apply_to_every_discovered_channel(tmp_path):
    body = 'default = "deny"\n' + _entry(
        unit='unit = "friends"\n',
        fields='tools = ["pulse"]\nlocation = true\n'
               'description = "the server"\n',
    )
    policy = _policy(tmp_path, body, DISCOVERED)

    room = policy.conversation("discord", f"{GUILD}/{CHANNEL_B}")

    assert room is not None
    assert room.unit == "friends"
    assert room.tools == {"pulse"}
    assert room.location
    assert room.description == "the server"
    # The unit scopes an existing static room sees must include it.
    assert policy.is_allowed(
        platform="discord", conversation_id=f"{GUILD}/{CHANNEL_B}", person_id="miles"
    )


def test_a_voice_room_still_resolves_beside_a_server_entry(tmp_path):
    body = (
        'default = "deny"\n' + _entry()
        + '[[conversations]]\nplatform = "discord"\n'
        f'id = "{GUILD}/{CHANNEL_A}"\nlabel = "Voice Room"\n'
        'persons = "*"\nvoice = true\n'
    )
    policy = _policy(tmp_path, body, DISCOVERED)

    assert policy.voice_room("discord", GUILD) is not None


@pytest.mark.parametrize("toml, frag", [
    ('id = "100000000000000101/100000000000000102"\n', "both guild and"),
    ('voice = true\n', "voice"),
    ('music_channel = "100000000000000999"\n', "music_channel"),
    ('home_room = true\n', "home_room"),
    ('proactive = true\n', "proactive"),
    ('also_ids = ["100000000000000105"]\n', "both guild and"),
])
def test_a_server_entry_refuses_per_channel_keys(tmp_path, toml, frag):
    body = (
        'default = "deny"\n[[conversations]]\nplatform = "discord"\n'
        f'guild = "{GUILD}"\nlabel = "Zone 9"\npersons = "*"\n{toml}'
    )
    with pytest.raises(PolicyError, match=frag):
        load_policy(_write(tmp_path, body))


def test_ignore_or_ambient_on_a_per_channel_entry_is_refused(tmp_path):
    body = (
        'default = "deny"\n[[conversations]]\nplatform = "discord"\n'
        f'id = "{GUILD}/{CHANNEL_A}"\nlabel = "Zone 9"\npersons = "*"\n'
        'ignore = ["general"]\n'
    )
    with pytest.raises(PolicyError, match="ignore/ambient"):
        load_policy(_write(tmp_path, body))


def test_a_guild_entry_on_another_platform_is_refused(tmp_path):
    body = (
        'default = "deny"\n[[conversations]]\nplatform = "signal"\n'
        f'guild = "{GUILD}"\nlabel = "Zone 9"\npersons = "*"\n'
    )
    with pytest.raises(PolicyError, match="platform"):
        load_policy(_write(tmp_path, body))


def test_a_malformed_guild_id_is_refused(tmp_path):
    body = (
        'default = "deny"\n[[conversations]]\nplatform = "discord"\n'
        'guild = "not-a-guild"\nlabel = "Zone 9"\npersons = "*"\n'
    )
    with pytest.raises(PolicyError, match="guild id"):
        load_policy(_write(tmp_path, body))


def test_two_entries_covering_one_guild_are_refused(tmp_path):
    body = (
        'default = "deny"\n'
        + _entry(label="Zone 9") + _entry(label="Zone 9B")
    )
    with pytest.raises(PolicyError, match="two server entries"):
        load_policy(_write(tmp_path, body))


def test_an_entry_with_neither_id_nor_guild_is_refused(tmp_path):
    body = (
        'default = "deny"\n[[conversations]]\nplatform = "discord"\n'
        'label = "Zone 9"\npersons = "*"\n'
    )
    with pytest.raises(PolicyError, match="id or guild"):
        load_policy(_write(tmp_path, body))


# ── the absorb boundary in the live pipeline ─────────────────────────────

from nora.domain.models import NormalizedMessage, PersonRef  # noqa: E402
from nora.ingest.pipeline import IngestPipeline  # noqa: E402
from nora.ingest.policy import TriggerTextPolicy  # noqa: E402
from nora.obs.metrics import Metrics  # noqa: E402
from nora.persons import Persons  # noqa: E402
from nora.testing.fakes import FakeMemoryStore, FakeRecordStore  # noqa: E402


def _pipeline_policy(tmp_path, extra=""):
    body = 'default = "deny"\n' + _entry(extra=extra)
    return load_policy(
        _write(tmp_path, body),
        channels=StaticChannelDirectory(DISCOVERED, threads={}),
    )


def _discord_msg(channel_id, body="hello"):
    return NormalizedMessage(
        platform="discord", conversation_id=f"{GUILD}/{channel_id}",
        conversation_type="group", sender_id="424242424242424242",
        sender_name="Miles", timestamp=1, body=body, raw={},
    )


def _persons():
    return Persons(
        by_person={"miles": PersonRef(
            person_id="miles", name="Miles", honcho_peer_id="miles",
        )},
        index={("discord", "424242424242424242"): "miles"},
    )


def _pipeline(allowlist, memory, spawned, stimulus_calls, monkeypatch, audit=None):
    import nora.ingest.stages.stimulus as stimulus_stage

    async def _spy(**kwargs):
        stimulus_calls.append(kwargs)

    monkeypatch.setattr(stimulus_stage, "run", _spy)
    return IngestPipeline(
        audit=audit or FakeRecordStore(), memory=memory, persons=_persons(),
        policy=TriggerTextPolicy("nora.000"), allowlist=allowlist,
        metrics=Metrics(), spawn=spawned.append,
        stimulus_wiring=object(),
    )


async def test_a_mention_only_channel_records_but_is_never_absorbed(tmp_path, monkeypatch):
    """The server-entry default: transcript kept, tag path open, but no
    Honcho feed and no stimulus — `Conversation.absorbed` is False."""
    memory = FakeMemoryStore()
    spawned, stimuli = [], []
    pipeline = _pipeline(
        _pipeline_policy(tmp_path), memory, spawned, stimuli, monkeypatch,
    )

    result = await pipeline.run(message=_discord_msg(CHANNEL_A))

    assert result.message is not None          # admitted and recorded
    for coro in spawned:
        await coro
    assert memory.fed == []                    # never absorbed into memory
    assert stimuli == []                       # and never a stimulus


async def test_an_ambient_channel_is_fed_and_stimulated(tmp_path, monkeypatch):
    """`ambient = [...]` is the opt-in: the same turn absorbs fully."""
    memory = FakeMemoryStore()
    spawned, stimuli = [], []
    pipeline = _pipeline(
        _pipeline_policy(tmp_path, extra='ambient = ["discussion"]\n'),
        memory, spawned, stimuli, monkeypatch,
    )

    await pipeline.run(message=_discord_msg(CHANNEL_B))

    for coro in spawned:
        await coro
    assert memory.fed != []
    assert len(stimuli) == 1


# ── `rooms()` is the enumeration every room consumer reads ─────────────

import dataclasses  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import nora.ingest.stages.scope as scope_stage  # noqa: E402
from nora.app.composition import (  # noqa: E402
    _profile_room_keys,
    character_person_keys,
    scope_home_rooms,
    social_unit_rooms,
)
from nora.app.documents_service import room_label, rooms_named  # noqa: E402
from nora.config_reload import LivePolicy  # noqa: E402
from nora.domain.models import EditTarget  # noqa: E402
from nora.engagement.self_presence import PresenceReader  # noqa: E402


def _friends_entry(persons='"*"'):
    return _entry(unit='unit = "friends"\n', persons=persons)


async def test_discovered_channels_are_unit_rooms_in_presence(tmp_path):
    """`<presence>`/`room_list` enumerate `rooms()`: a channel the file never
    names is still a sibling room of the unit, named by its channel name."""
    policy = _policy(tmp_path, 'default = "deny"\n' + _friends_entry(), DISCOVERED)
    unit_rooms = await PresenceReader(policy=policy, adapters={}).rooms(
        platform="discord", conversation_id=f"{GUILD}/{CHANNEL_A}",
    )
    assert unit_rooms.rooms is not None
    assert len(unit_rooms.rooms) == 3     # the room itself is not a sibling
    assert {r.label for r in unit_rooms.rooms} == {
        "general", "discussion", "admin-log",
    }


def test_rooms_named_and_room_label_reach_a_discovered_channel(tmp_path):
    policy = _policy(
        tmp_path, 'default = "deny"\n' + _friends_entry(persons='["miles"]'),
        DISCOVERED,
    )
    assert ("discord", f"{GUILD}/{CHANNEL_B}") in rooms_named(policy, "miles")
    assert room_label(policy, "discord", f"{GUILD}/{CHANNEL_B}") == "discussion"


def test_character_person_keys_see_discovered_rooms(tmp_path):
    policy = _policy(
        tmp_path, 'default = "deny"\n' + _friends_entry(persons='["miles"]'),
        DISCOVERED,
    )
    keys = character_person_keys(policy, _persons())
    (key,) = [v for v in keys.values() if v.unit == "friends"]
    assert {r.key for r in key.rooms} == {
        f"{GUILD}/{CHANNEL_A}", f"{GUILD}/{CHANNEL_B}", f"{GUILD}/{CHANNEL_C}",
    }
    assert key.home == f"{GUILD}/{CHANNEL_A}"


def test_profile_room_keys_cover_a_discovered_channel(tmp_path):
    policy = _policy(tmp_path, 'default = "deny"\n' + _friends_entry(), DISCOVERED)
    keys = _profile_room_keys(policy)
    assert keys[f"{GUILD}/{CHANNEL_A}"].unit == "friends"


def test_social_unit_rooms_include_discovered_channels(tmp_path):
    policy = _policy(tmp_path, 'default = "deny"\n' + _friends_entry(), DISCOVERED)
    assert ("discord", f"{GUILD}/{CHANNEL_B}") in social_unit_rooms(policy)["friends"]


def test_scope_home_rooms_map_a_discovered_channel(tmp_path):
    policy = _policy(tmp_path, 'default = "deny"\n' + _friends_entry(), DISCOVERED)
    assert scope_home_rooms(policy)[f"{GUILD}/{CHANNEL_A}"].platform == "discord"


async def test_the_self_profile_read_renders_discovered_rooms_as_targets(tmp_path):
    """The grounder's self-profile read builds its lineage targets from
    `rooms()`, so a discovered channel is one of the rooms an excluded or
    located line is checked against."""
    from nora.app.cognition_grounding import unit_self_profile_reader

    policy = _policy(tmp_path, 'default = "deny"\n' + _friends_entry(), DISCOVERED)
    seen = []
    boundary = SimpleNamespace(
        render_room=lambda platform, conversation_id: seen.append(
            (platform, conversation_id)
        ),
    )

    class _Heads:
        async def account_heads(self, *, key):
            return ()

    gated = unit_self_profile_reader(_Heads(), policy, lineage=boundary)
    assert gated is not None
    await gated("friends")
    assert ("discord", f"{GUILD}/{CHANNEL_A}") in seen


# ── the absorb read fails closed on the hot-reload race ─────────────────


def _denying_policy(tmp_path):
    """A file that no longer lists the guild's channels — the post-swap
    snapshot the race drops the room under."""
    p = tmp_path / "later.toml"
    p.write_text(
        'default = "deny"\n[[conversations]]\nplatform = "signal"\n'
        'id = "sig-room-1"\nlabel = "Elsewhere"\npersons = "*"\n'
    )
    return load_policy(p)


async def _run_past_a_swap(tmp_path, monkeypatch, message, audit=None):
    """The hot-reload race, faithfully: `LivePolicy.swap` between the scope
    read (old snapshot admits) and the absorb read (current snapshot denies)."""
    live = LivePolicy(
        _pipeline_policy(tmp_path, extra='ambient = ["discussion"]\n')
    )
    later = _denying_policy(tmp_path)
    real_run = scope_stage.run

    async def _swap_after_scope(**kwargs):
        admitted = await real_run(**kwargs)
        live.swap(later)
        return admitted

    monkeypatch.setattr(scope_stage, "run", _swap_after_scope)
    memory = FakeMemoryStore()
    spawned, stimuli = [], []
    pipeline = _pipeline(live, memory, spawned, stimuli, monkeypatch, audit=audit)
    result = await pipeline.run(message=message)
    for coro in spawned:
        await coro
    return result, memory, stimuli


async def test_a_room_vanishing_between_scope_and_the_absorb_read_is_not_absorbed(
    tmp_path, monkeypatch,
):
    """A message the CURRENT policy denies is never fed or stimulated — the
    absorb read fails closed, never `or room.absorbed` on a missing room."""
    result, memory, stimuli = await _run_past_a_swap(
        tmp_path, monkeypatch, _discord_msg(CHANNEL_B),
    )
    assert result.message is not None          # recorded
    assert memory.fed == []                    # but never absorbed
    assert stimuli == []


async def test_an_edit_whose_room_vanishes_mid_turn_is_not_absorbed(
    tmp_path, monkeypatch,
):
    store = FakeRecordStore()
    original = dataclasses.replace(
        _discord_msg(CHANNEL_B, body="first"), message_id="edit-target-1",
    )
    await store.append(message=original, person_id="miles")
    edit = dataclasses.replace(
        _discord_msg(CHANNEL_B, body="second"),
        timestamp=5, edit_of=EditTarget(message_id="edit-target-1"),
    )
    result, memory, stimuli = await _run_past_a_swap(
        tmp_path, monkeypatch, edit, audit=store,
    )
    assert result.message is not None          # the edit itself is recorded
    assert memory.fed == []                    # but never absorbed
    assert stimuli == []


# ── `nora groups` ────────────────────────────────────────────────────────

from nora.app.report import render_discord_guilds  # noqa: E402
from nora.platform.discord.directory import DiscordChannel, DiscordGuild  # noqa: E402


def test_groups_lists_the_guild_and_each_channel_with_its_mode(tmp_path):
    policy = _policy(
        tmp_path,
        'default = "deny"\n' + _entry(
            extra='ignore = ["admin-log"]\nambient = ["discussion"]\n',
        ),
        DISCOVERED,
    )
    lines = render_discord_guilds(
        guilds=[DiscordGuild(id=GUILD, name="Zone 9")],
        channels={GUILD: [
            DiscordChannel(id=CHANNEL_A, name="general", type=0),
            DiscordChannel(id=CHANNEL_B, name="discussion", type=0),
            DiscordChannel(id=CHANNEL_C, name="admin-log", type=0),
        ]},
        policy=policy,
    )
    text = "\n".join(lines)

    assert "COVERED" in text and GUILD in text
    assert "mention-only" in text and "#general" in text
    assert "ambient" in text and "#discussion" in text
    assert "ignored" in text and "#admin-log" in text


def test_groups_marks_a_guild_no_entry_covers(tmp_path):
    policy = _policy(tmp_path, 'default = "deny"\n' + _entry(), DISCOVERED)
    lines = render_discord_guilds(
        guilds=[DiscordGuild(id=GUILD, name="Zone 9"),
                DiscordGuild(id="999999999999999999", name="Elsewhere")],
        channels={GUILD: [DiscordChannel(id=CHANNEL_A, name="general", type=0)]},
        policy=policy,
    )
    text = "\n".join(lines)

    assert "not listed" in text
