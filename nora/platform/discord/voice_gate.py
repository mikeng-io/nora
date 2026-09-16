"""The voice-join admission gate (design/voice-channel/07 §12.2/§12.3).

Pure and shared: `/music play`'s auto-join, `/discord join`, and the agent
tool bridge all apply the SAME rule — the caller must be a guild member
physically sitting in a voice channel, and that channel must be on the
configured `voice_channel_ids` allow-list (entries are
`"guild_id/channel_id"`, matching `NormalizedMessage.conversation_id`).
Fail-closed: anything else is a rejection, never a best-effort join.
"""

from __future__ import annotations

from typing import Literal

VoiceJoinDecision = Literal["ok", "not_in_voice", "not_in_allowed_channel"]


def voice_join_decision(
    *,
    is_member: bool,
    guild_id: str,
    caller_channel_id: str | None,
    allowed: frozenset[str],
) -> VoiceJoinDecision:
    """`ok` only when the caller is a guild member IN a voice channel that
    is on the allow-list. `not_in_voice` covers both "not a member" (a DM
    shape that slipped past the guild check) and "member in no channel" —
    the same rejection either surface already renders as "join a voice
    channel first"."""
    if not is_member or caller_channel_id is None:
        return "not_in_voice"
    if f"{guild_id}/{caller_channel_id}" not in allowed:
        return "not_in_allowed_channel"
    return "ok"
