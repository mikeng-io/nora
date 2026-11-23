from __future__ import annotations

from types import SimpleNamespace
from typing import Literal

from nora.agent.room import find_by_name
from nora.agent.toolkit.results import _record, _record_room
from nora.agent.toolkit.rooms import _asked_about, _miss
from nora.agent.toolkit.spec import _spec
from nora.agent.whereis import (
    _dedupe,
)
from nora.domain.models import (
    PersonRef,
)
from nora.domain.ports import (
    DiscordPresenceState,
    SteamPlayerState,
    VoiceChannelRef,
)

from .membership import _make_member_update as _make_member_update


def _make_group_members(deps):
    metrics = deps.metrics
    room = deps.room

    async def group_members() -> dict:
        """Who is in this conversation. Call this when somebody asks who is
        here.

        SHOULD: call it rather than guess from the transcript. People who have not
        spoken lately are still here, and people named in a message may not be. `scope`
        says what the platform's roster actually covers; on Discord the member list is
        the whole server's, which can exceed this channel's audience.

        Returns `status`: ok (`members`, `scope`) or unavailable (`note`)."""
        view = await room.members()
        if not view.available:
            _record_room(metrics, "group_members", "unavailable")
            return {
                "status": "unavailable",
                "note": "I can't check who is in this group right now. Say so, "
                "and do not guess from the conversation.",
            }
        _record_room(
            metrics,
            "group_members",
            "ok",
            members=len(view.people),
            unnamed=view.unnamed,
        )
        return {
            "status": "ok",
            "members": [p.name for p in view.people],
            # A count, never the ids: the only thing Nora holds for these
            # accounts is a phone number or an ACI, kept off the wire.
            "members_i_have_no_name_for": view.unnamed,
            # What the platform's roster actually IS. Discord has no "who is in
            # this channel" endpoint — `nora.platform.discord. roster` reads the
            # GUILD's member list, which may exceed this channel's audience (the
            "scope": (
                "this Discord server's member list, which can exceed this channel's audience"
                if room.platform == "discord"
                else "this conversation"
            ),
        }

    return group_members


def _make_lookup_person(deps):
    content_checks = deps.content_checks
    metrics = deps.metrics
    room = deps.room

    async def lookup_person(name: str) -> dict:
        """Work out who a name or nickname in this conversation refers to. Call
        this when a message uses a name you do not recognise.

        Call it when knowing who it is changes your answer. It knows only people
        in THIS conversation, so do not call it for a name you were already given
        (the person who tagged you) or for someone plainly outside the chat, such
        as a singer or a company.

        Returns `status`: ok, not_found, ambiguous or unavailable, with a `note`
        on how to report it.

        Args:
            name: The name, nickname or @username exactly as it appeared in
                the message.
        """
        view = await room.scope()
        if not view.available:
            _record_room(metrics, "lookup_person", "unavailable")
            return {
                "status": "unavailable",
                "note": "I can't check who is in this group right now, so I can't "
                "tell you who that is. Say so rather than guessing.",
            }
        matches = await room.find(view, name)
        # `name` is never in the line. It is the one MODEL-SUPPLIED string
        # these tools take, and `_asked_about` exists because a model does put
        # phone numbers in it.
        if not matches:
            miss = _miss(name, view, content_checks=content_checks)
            _record_room(metrics, "lookup_person", miss["status"], candidates=0)
            return miss
        if len(matches) > 1:
            _record_room(metrics, "lookup_person", "ambiguous", candidates=len(matches))
            return {
                "status": "ambiguous",
                **_asked_about(name, content_checks=content_checks),
                "could_be": [p.name for p in matches],
                "note": "More than one person here goes by that. Ask which one.",
            }
        person = matches[0]
        _record_room(metrics, "lookup_person", "ok", candidates=1)
        return {
            "status": "ok",
            **_asked_about(name, content_checks=content_checks),
            "name": person.name,
            "also_called": list(person.aliases),
            "note": "This person is in this conversation.",
        }

    return lookup_person


def _make_online_now(deps):
    async def online_now(
        what: Literal["online", "playing", "voice"],
        names: list[str] | None = None,
    ) -> dict:
        """Live digital activity for the people in this room. Call this when a
        question or a decision turns on whether someone is actually present
        or playing right now.

        `what`: online (Steam and Discord presence), playing (which game) or
        voice (which Discord voice channel).

        Returns `status`: ok, nothing_found or unavailable, with a `note` on
        how to report it.

        Args:
            what: Which arm: "online", "playing" or "voice".
            names: Who they asked about, exactly as said. Leave it out for the
                people in THIS conversation: the room asking about the room. A
                name outside the room is dropped, never looked up.
        """
        c = SimpleNamespace(
            what=what,
            names=names,
            metrics=deps.metrics,
            online_source=deps.online_source,
            persons=deps.persons,
            presence=deps.presence,
            room=deps.room,
            voice_source=deps.voice_source,
            _online_arms=deps._online_arms,
            _online_states=deps._online_states,
        )
        k_, _, v_ = await _online_now__check_what(c)
        if k_:
            return v_
        k_, _, v_ = await _online_now__states(c)
        if k_:
            return v_
        if c.what == "playing":
            k_, _, v_ = await _online_now__missing_steam(c)
            if k_:
                return v_
        elif c.what == "voice":
            k_, _, v_ = await _online_now__missing_discord(c)
            if k_:
                return v_
        else:
            k_, _, v_ = await _online_now__resolve_states(c)
            if k_:
                return v_
        k_, _, v_ = await _online_now__result(c)
        if k_:
            return v_
        return None  # type: ignore[return-value]

    return online_now


async def _online_now__check_what(c):
    if str(c.what) not in c._online_arms:
        _record(c.metrics, "online_now", "unavailable", reason="what")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "I can't answer that with "
                    "online_now. It answers who is online, who is playing what, or who is in a "
                    "voice "
                    "channel."
                ),
            },
        )
    c.steam_wired = c.online_source is not None
    c.voice_wired = c.voice_source is not None
    c.presence_wired = c.presence is not None
    if (
        (c.what == "playing" and (not c.steam_wired))
        or (c.what == "voice" and (not c.voice_wired))
        or (c.what == ("online") and (not (c.steam_wired or c.presence_wired or c.voice_wired)))
    ):
        _record(c.metrics, "online_now", "unavailable")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "I have no way to check that right "
                    "now. Say you couldn't check, and never guess who is online, playing, or in "
                    "voice."
                ),
            },
        )
    c.view = await c.room.scope()
    if not c.view.available or c.view.degraded:
        _record(c.metrics, "online_now", "unavailable", reason="room")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "I can't confirm who is in this "
                    "conversation right now, so I can't say who is online. Say you couldn't "
                    "check, and do "
                    "not guess."
                ),
            },
        )
    c.refs = _dedupe(c.view.people)
    if c.names:
        c.picked: list[PersonRef] = []
        for _w1 in c.names:
            c.raw = _w1
            c.wanted = str(c.raw).strip()
            if c.wanted:
                c.picked.extend(find_by_name(c.refs, c.wanted))
        c.refs = _dedupe(tuple(c.picked))
    if not c.refs:
        _record(c.metrics, "online_now", "nothing_found", what=c.what)
        return (
            True,
            None,
            {
                "status": "nothing_found",
                "note": (
                    "Nobody I may check here matches "
                    "that. Say you're not sure who they mean, and do not guess."
                ),
            },
        )
    return await _online_now__fetch_states(c)


async def _online_now__fetch_states(c):
    c.steam_ids_by_person = {
        ref.person_id: c.persons.platform_ids("steam", ref.person_id) for ref in c.refs
    }
    c.discord_ids_by_person = {
        ref.person_id: c.persons.platform_ids("discord", ref.person_id) for ref in c.refs
    }
    c.players: dict[str, SteamPlayerState] | None = None
    if c.online_source is not None and c.what in ("online", "playing"):
        c.all_steam_ids = frozenset().union(*c.steam_ids_by_person.values())
        c.players = await c.online_source.summaries(steam_ids=c.all_steam_ids)
        if c.players is None:
            _record(c.metrics, "online_now", "unavailable", what=c.what)
            return (
                True,
                None,
                {
                    "status": "unavailable",
                    "note": (
                        "I couldn't check Steam just "
                        "now. Say you couldn't check; never say everyone is offline or nobody is "
                        "playing "
                        "when you haven't looked."
                    ),
                },
            )
    c.channels: dict[str, VoiceChannelRef] | None = None
    c.voice_checked = False
    if c.voice_source is not None and c.what in ("online", "voice"):
        c.all_member_ids = frozenset().union(*c.discord_ids_by_person.values())
        c.channels = await c.voice_source.in_voice(member_ids=c.all_member_ids)
        if c.channels is None and c.what == "voice":
            _record(c.metrics, "online_now", "unavailable", what=c.what)
            return (
                True,
                None,
                {
                    "status": "unavailable",
                    "note": (
                        "I couldn't check the voice "
                        "channels just now. Say you couldn't check; never say nobody is in voice "
                        "when you "
                        "haven't looked."
                    ),
                },
            )
        c.voice_checked = c.channels is not None
    c.presence_states: dict[str, DiscordPresenceState] | None = None
    c.presence_checked = False
    return (False, None, None)


async def _online_now__states(c):
    if c.presence is not None and c.what == "online":
        c.all_member_ids = frozenset().union(*c.discord_ids_by_person.values())
        c.presence_states = await c.presence.presence(member_ids=c.all_member_ids)
        c.presence_checked = c.presence_states is not None

    def _steam_state(ref: PersonRef) -> SteamPlayerState | None:
        """The one answered summary for this person, or None — an id"""
        if c.players is None:
            return None
        for sid in sorted(c.steam_ids_by_person[ref.person_id]):
            if sid in c.players:
                return c.players[sid]
        return None

    c._steam_state = _steam_state

    def _voice_ref(ref: PersonRef) -> VoiceChannelRef | None:
        if not c.voice_checked or c.channels is None:
            return None
        for did in sorted(c.discord_ids_by_person[ref.person_id]):
            if did in c.channels:
                return c.channels[did]
        return None

    c._voice_ref = _voice_ref

    def _voice_entry(hit: VoiceChannelRef) -> dict:
        out: dict = {"channel_id": hit.channel_id}
        if hit.channel_name:
            out["channel_name"] = hit.channel_name
        return out

    c._voice_entry = _voice_entry

    def _presence_state(ref: PersonRef) -> DiscordPresenceState | None:
        if not c.presence_checked or c.presence_states is None:
            return None
        for did in sorted(c.discord_ids_by_person[ref.person_id]):
            if did in c.presence_states:
                return c.presence_states[did]
        return None

    c._presence_state = _presence_state

    def _discord_fields(state: DiscordPresenceState) -> dict:
        """The Discord-presence half of an `online` entry: Discord's own"""
        out: dict = {"discord_status": state.status}
        if state.game:
            out["discord_game"] = state.game
        if state.listening:
            out["discord_listening"] = state.listening
        return out

    c._discord_fields = _discord_fields

    def _online_ok_note(out: dict) -> str:
        """Report only the live state actually checked."""
        entries = out["people"]
        parts = [
            "This is live, so report it as right now and do not repeat it later as still true."
        ]
        if c.what == "online":
            parts.append(
                "Say someone is online on Discord only from discord_status, never from in_voice."
            )
            if not all(out.get(k, True) for k in ("steam_wired", "discord_wired", "voice_wired")):
                parts.append(
                    "A *_wired flag that is false means that half was not checked, so "
                    "this is not the whole picture."
                )
        if any(e.get("state") in ("away", "snooze") for e in entries):
            parts.append(
                "away and snooze are Steam's own words: report them as they are, not as "
                "online or offline."
            )
        if any(e.get("profile_hidden") for e in entries):
            parts.append(
                "profile_hidden means Steam hides their game: say you cannot see it, not "
                "that they are offline or not playing."
            )
        if any(e.get("state") == "unknown" for e in entries):
            parts.append(
                "state unknown means that person could not be checked (reason says why): "
                "do not fill the gap."
            )
        return " ".join(parts)

    c._online_ok_note = _online_ok_note
    c.people: list[dict] = []
    c.readable = 0
    return (False, None, None)


async def _online_now__missing_steam(c):
    for _w1 in c.refs:
        c.ref = _w1
        if not c.steam_ids_by_person[c.ref.person_id]:
            c.people.append({"person": c.ref.name, "state": "unknown", "reason": "no_steam_id"})
            continue
        c.state = c._steam_state(c.ref)
        if c.state is None:
            c.people.append({"person": c.ref.name, "state": "unknown", "reason": "not_on_steam"})
            continue
        c.readable += 1
        c.word = c._online_states.get(c.state.personastate, "unknown")
        if not c.state.profile_public:
            c.people.append({"person": c.ref.name, "state": c.word, "profile_hidden": True})
        elif c.state.game:
            c.entry: dict = {"person": c.ref.name, "state": c.word, "game": c.state.game}
            if c.state.game_id:
                c.entry["game_id"] = c.state.game_id
            c.people.append(c.entry)
        else:
            c.people.append({"person": c.ref.name, "state": c.word, "playing": False})
    return (False, None, None)


async def _online_now__missing_discord(c):
    for _w1 in c.refs:
        c.ref = _w1
        if not c.discord_ids_by_person[c.ref.person_id]:
            c.people.append({"person": c.ref.name, "state": "unknown", "reason": "no_discord_id"})
            continue
        c.readable += 1
        c.hit = c._voice_ref(c.ref)
        c.people.append(
            {"person": c.ref.name, "in_voice": c._voice_entry(c.hit)}
            if c.hit is not None
            else {("person"): c.ref.name, "in_voice": False}
        )
    return (False, None, None)


async def _online_now__resolve_states(c):
    for _w1 in c.refs:
        c.ref = _w1
        c.has_steam = bool(c.steam_ids_by_person[c.ref.person_id])
        c.has_discord = bool(c.discord_ids_by_person[c.ref.person_id])
        c.state = c._steam_state(c.ref) if c.has_steam else None
        c.hit = c._voice_ref(c.ref)
        c.dstate = c._presence_state(c.ref)
        if c.state is not None:
            c.readable += 1
            c.entry = {
                "person": c.ref.name,
                "state": c._online_states.get(c.state.personastate, ("unknown")),
            }
            if not c.state.profile_public:
                c.entry["profile_hidden"] = True
            if c.state.game:
                c.entry["game"] = c.state.game
            if c.dstate is not None:
                c.entry.update(c._discord_fields(c.dstate))
            if c.hit is not None:
                c.entry["in_voice"] = c._voice_entry(c.hit)
            c.people.append(c.entry)
            continue
        if c.has_steam and c.steam_wired:
            c.entry = {"person": c.ref.name, "state": "unknown", "reason": "not_on_steam"}
            if c.dstate is not None:
                c.readable += 1
                c.entry.update(c._discord_fields(c.dstate))
            if c.hit is not None:
                c.readable += 1
                c.entry["in_voice"] = c._voice_entry(c.hit)
            c.people.append(c.entry)
            continue
        if c.dstate is not None:
            c.readable += 1
            c.entry = {"person": c.ref.name, **c._discord_fields(c.dstate)}
            if c.hit is not None:
                c.entry["in_voice"] = c._voice_entry(c.hit)
            elif c.voice_checked:
                c.entry["in_voice"] = False
            c.people.append(c.entry)
            continue
        if c.voice_checked and c.has_discord:
            c.readable += 1
            c.people.append(
                {
                    "person": c.ref.name,
                    "in_voice": c._voice_entry(c.hit) if c.hit is not None else False,
                }
            )
            continue
        c.people.append(
            {
                "person": c.ref.name,
                "state": "unknown",
                "reason": "no_steam_id" if not c.has_steam else ("no_discord_id"),
            }
        )
    return (False, None, None)


async def _online_now__result(c):
    if c.readable == 0:
        _record(c.metrics, "online_now", "nothing_found", what=c.what)
        return (
            True,
            None,
            {
                "status": "nothing_found",
                "note": (
                    "There is no live state I can see "
                    "for any of them. Say there's nothing to see, and do not guess."
                ),
            },
        )
    c.out = {
        "status": "ok",
        "what": c.what,
        "steam_wired": c.steam_wired and c.players is not None,
        ("voice_wired"): c.voice_checked,
        "people": c.people,
    }
    if c.what == "online":
        c.out["discord_wired"] = c.presence_checked
    c.out["note"] = c._online_ok_note(c.out)
    _record(c.metrics, "online_now", "ok", what=c.what, people=len(c.people))
    return (True, None, c.out)
    return (False, None, None)


def build(deps):
    online_now = _make_online_now(deps)
    group_members = _make_group_members(deps)
    lookup_person = _make_lookup_person(deps)
    member_update = _make_member_update(deps)
    return (
        _spec("online_now", online_now),
        _spec("group_members", group_members),
        _spec("lookup_person", lookup_person),
        _spec("member_update", member_update),
    )
