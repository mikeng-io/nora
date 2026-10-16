"""`voice_join_decision` (nora/platform/discord/voice_gate.py) — the join
admission matrix. Shared by `/music play`, `/discord join`, and the agent tool
bridge, so the whole matrix is pinned here exactly once.

The matrix is GUILD-scoped: any voice channel in an allowed guild admits,
because a server's voice channels are not a fixed list anyone can keep in
config.
"""

from __future__ import annotations

import pytest

from nora.platform.discord.voice_gate import voice_join_decision

GUILD = "111"
CHANNEL = "222"
ALLOWED = frozenset({GUILD})


@pytest.mark.parametrize(
    ("is_member", "caller_channel_id", "allowed_guilds", "expected"),
    [
        # Not a guild member (a DM shape) — never a join.
        (False, CHANNEL, ALLOWED, "not_in_voice"),
        (False, None, ALLOWED, "not_in_voice"),
        # A member in no voice channel. Still checked FIRST: "you are not in a
        # voice channel" is the refusal a caller can act on, and it must not
        # be masked by the guild verdict.
        (True, None, ALLOWED, "not_in_voice"),
        # A member in the guild's configured channel.
        (True, CHANNEL, ALLOWED, "ok"),
        # A member in ANY OTHER channel of the same guild is admitted too: a
        # per-channel refusal would make a many-channel server unusable.
        (True, "999", ALLOWED, "ok"),
        (True, "a-brand-new-channel", ALLOWED, "ok"),
        # An EMPTY allow-list fails closed even for a member in a channel.
        (True, CHANNEL, frozenset(), "not_in_allowed_guild"),
        # Another guild's entry does not admit this guild.
        (True, CHANNEL, frozenset({"999"}), "not_in_allowed_guild"),
    ],
)
def test_the_join_decision_matrix(is_member, caller_channel_id, allowed_guilds, expected):
    assert (
        voice_join_decision(
            is_member=is_member, guild_id=GUILD,
            caller_channel_id=caller_channel_id, allowed_guilds=allowed_guilds,
        )
        == expected
    )
