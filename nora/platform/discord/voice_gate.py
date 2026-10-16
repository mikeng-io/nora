"""The voice-join admission gate.

Pure and shared: `/music play`'s auto-join, `/discord join`, and the agent
tool bridge all apply the SAME rule — the caller must be a guild member
physically sitting in a voice channel, and that channel's GUILD must be on the
configured `voice_guild_ids` allow-list. Fail-closed: anything else is a
rejection, never a best-effort join.

**The guild, not the channel.** Admitting one configured channel would mean
every voice channel of a real server has to be listed by hand — an allow-list
nobody can keep true is an allow-list that is quietly wrong. The guild binding
the controller already enforces (any `guild_id` other than the configured one
is a 400) is the one doing the work; this gate keeps it and has no channel
half.

What that widens, stated plainly: any member of an allowed guild, sitting in
any of its voice channels, can make Nora join them. That is the same posture
the guild's text room already has — who may TRIGGER her is still the room's
`persons` list, checked downstream at the engage gate, and unchanged here.
"""

from __future__ import annotations

from typing import Literal

VoiceJoinDecision = Literal["ok", "not_in_voice", "not_in_allowed_guild"]


def voice_join_decision(
    *,
    is_member: bool,
    guild_id: str,
    caller_channel_id: str | None,
    allowed_guilds: frozenset[str],
) -> VoiceJoinDecision:
    """`ok` only when the caller is a guild member IN a voice channel of an
    allowed guild. `not_in_voice` covers both "not a member" (a DM shape that
    slipped past the guild check) and "member in no channel" — the same
    rejection either surface already renders as "join a voice channel first".

    `caller_channel_id` is still required and still unused for the decision:
    it is what says the caller is IN a channel at all, and the caller's channel
    is where the controller is then asked to go."""
    if not is_member or caller_channel_id is None:
        return "not_in_voice"
    if guild_id not in allowed_guilds:
        return "not_in_allowed_guild"
    return "ok"
