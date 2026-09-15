"""`voice_join_decision` (nora/platform/discord/voice_gate.py) — the join
admission matrix (design/voice-channel/07 §12.2/§12.3). Shared by `/music
play`, `/discord join`, and the agent tool bridge, so the whole matrix is
pinned here exactly once.
"""

from __future__ import annotations

import pytest

from nora.platform.discord.voice_gate import voice_join_decision

GUILD = "111"
CHANNEL = "222"
ALLOWED = frozenset({f"{GUILD}/{CHANNEL}"})


@pytest.mark.parametrize(
    ("is_member", "caller_channel_id", "allowed", "expected"),
    [
        # Not a guild member (a DM shape) — never a join.
        (False, CHANNEL, ALLOWED, "not_in_voice"),
        (False, None, ALLOWED, "not_in_voice"),
        # A member in no voice channel.
        (True, None, ALLOWED, "not_in_voice"),
        # A member in a channel that is NOT on the allow-list.
        (True, "999", ALLOWED, "not_in_allowed_channel"),
        # A member in the allowed channel — the one "ok".
        (True, CHANNEL, ALLOWED, "ok"),
        # An EMPTY allow-list fails closed even for a member in a channel.
        (True, CHANNEL, frozenset(), "not_in_allowed_channel"),
        # The allow-list is guild-scoped: the same channel id in ANOTHER
        # guild's entry does not admit this guild.
        (True, CHANNEL, frozenset({"999/222"}), "not_in_allowed_channel"),
    ],
)
def test_the_join_decision_matrix(is_member, caller_channel_id, allowed, expected):
    assert (
        voice_join_decision(
            is_member=is_member, guild_id=GUILD,
            caller_channel_id=caller_channel_id, allowed=allowed,
        )
        == expected
    )
