"""`policy.toml` → the grant predicate.

WHAT IS ALLOWED. Separate from `config.toml` (deploy cadence) and
`identity.toml` (changes when a human joins) because policy changes when TRUST
changes — the same argument DEC-8 used to split config from identity. An
allowlist buried among port numbers is an allowlist nobody reviews.

Everything here names people by `person_id`, never by a phone number or a
platform id. Adding an account to `identity.toml` therefore grants it access
everywhere that person is already allowed — one edit, not one per room — and
this file stays readable without knowing anyone's phone number. Note the
consequence: allowlisting `miles` grants EVERY account he holds.

Deny by default, and an empty allowlist REFUSES TO START. Under R-5 Nora is
silent when broken, so a silently-denied Nora is indistinguishable from a
working one; a loud startup error is the only honest failure here. This
codebase's own `.env` states the principle: "You get a loud error instead of a
silent mute."
"""

from __future__ import annotations

import logging
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from nora.domain.ports import ChannelDirectory
from nora.domain.self_presence import PLATFORM_NAMES

# Not a person_id: the wildcard admitting anyone in a conversation.
EVERYONE = "*"

log = logging.getLogger("nora.policy")

#: The Nora WORKSPACE every unit belongs to when `policy.toml` has no `[units]`
#: table at all (DEC-214). A workspace holds units,
#: a unit holds rooms; a policy written before the hierarchy existed names no
#: workspace, so all of its units share this implicit one, and that one is
#: bound to `config.toml`'s `honcho.workspace_id` rather than to its own name
#: (`nora.app.memory_routing`). Not a Honcho workspace id: a Nora workspace
#: and a Honcho workspace are separate names for separate things.
DEFAULT_WORKSPACE = "default"

#: A Nora workspace name may be used verbatim as a Honcho workspace id (when
#: `identity.toml` binds it to nothing), and Honcho refuses any resource id
#: outside this alphabet with a 422 — at feed time, silently, off the reply
#: path. Refused here, at startup, instead.
_WORKSPACE_NAME = re.compile(r"^[a-zA-Z0-9_-]+$")


#: A Discord snowflake is a 64-bit id — 17-20 decimal digits in practice. The
#: length bound is a sanity check on a hand-copied value, not a parser.
_SNOWFLAKE_DIGITS = range(17, 21)


def _is_snowflake(value: str) -> bool:
    """Whether `value` can name a Discord channel.

    ASCII decimal digits only, and NOT `str.isdigit()`, which is fail-open in
    both directions: it accepts superscripts (`"²"`), which `int()` then refuses, so a line
    that can NEVER route passed startup and failed at send time
    indistinguishably from a deleted channel; and it accepts Arabic-Indic
    digits, which `int()` DOES parse, so such an id silently routed while
    reading as something else. `nora.platform.discord.music_notify.
    is_snowflake` is the same predicate at the other end of the wire — two
    copies because `nora.policy` and `nora.platform` may not import each
    other, pinned by a test that asserts they agree."""
    return (
        value.isascii()
        and value.isdecimal()
        and len(value) in _SNOWFLAKE_DIGITS
    )


class PolicyError(Exception):
    """A policy problem that must stop the process at startup."""


@dataclass(frozen=True)
class Conversation:
    platform: str
    ids: frozenset[str]         # `id` plus `also_ids`, matched identically
    persons: frozenset[str] | None   # None == EVERYONE
    label: str = ""             # never matched on
    # The MCP servers this conversation may use. Empty means none — deny-by-default,
    # the same posture as the rest of this file (R-5).
    tools: frozenset[str] = frozenset()
    # Location opt-in remains independent of `[content_checks]`. The audience
    # checks below apply only in enabled mode; collection consent is unchanged.
    # LOCATION-DISCLOSURE TRUST (DEC-74). Default False: `where_is` refuses to
    # disclose a member's position unless this conversation is a verified SUBSET
    # of that member's Life360 circle (R-31/DisclosureGuard). Setting `location =
    # true` is the operator saying "I trust THIS room with my location."
    #
    # It is a WHOLE-ROOM switch, not a per-condition one (DEC-79): it relaxes the
    # SUBSET check AND the unnamed-account refusal, because real rooms hold silent
    # sealed-sender members with no id to map. It does NOT relax the degraded-view
    # refusal — a roster Nora could not read is an R-27 `unavailable`, and trust
    # can overrule what a roster says but not the absence of one.
    location: bool = False
    # The `id` the operator actually WROTE, kept alongside `ids` (which folds it
    # together with `also_ids` because matching must not care which form
    # arrived). Anything that SPEAKS INTO a conversation rather than matching one
    # needs this: `also_ids` exists for the id forms a platform hands us over
    # time — a Telegram supergroup's retired id, a re-encoded Signal group id —
    # and sending to a retired form fails. `ids` is a set, so "pick one" would be
    # arbitrary and would differ between runs. First user: the version
    # announcement (DEC-97), which speaks into every allowed room on the boot
    # after an upgrade. Defaulted so nothing that constructs a `Conversation`
    # without one breaks; `load_policy` always sets it.
    primary_id: str = ""
    # WHICH SOCIAL UNIT this room belongs to (DEC-105). Rooms sharing a
    # unit are the same group of people reachable in more than one place —
    # a friend group with a Signal group AND a Discord channel — and that is
    # what makes a relay between them something other than a leak: the audience
    # is already the same audience. Rooms in DIFFERENT units are different
    # groups and may never be relayed between, however trusted each is on its
    # own.
    #
    # Defaults to `honcho.workspace_id`'s shipped value rather than being
    # required, so an existing policy.toml keeps working — but the default is
    # the LOOSE direction, so an operator who runs two real social units must
    # say so. That is why `send_to` also requires the requester to be allowed
    # in the destination independently: the unit bounds WHICH rooms may
    # ever be paired, and the persons list still decides who may use the pair.
    unit: str = "nora"
    # UNPROMPTED-SPEECH TRUST.
    # Default False: being allowed to ANSWER this room when tagged has never
    # implied being allowed to speak in it uncalled. `relevance_gate = true` is
    # the operator saying "the Relevance Gate may consider THIS room's untagged
    # traffic" — and it still does nothing while `[relevance_gate] enabled =
    # false` in config.toml (the shipped default).
    relevance_gate: bool = False
    # THE UNIT'S HOME ROOM (DEC-162).
    # Default False. A unit has no conversation of its own, and every
    # room-scoped read — `where_is`, `about`, `ask_memory`, `group_members` —
    # resolves through ONE room's roster. `home_room = true` is the operator
    # saying "when Nora acts for this unit with nobody asking, THIS is
    # the room she stands in": the proactive adaptor's `Room` is built from
    # it (with this room's `location` flag threaded exactly as on a tag), and
    # the rows it writes fire into it. That is the R-31 disclosure decision
    # `UNIT_SAFE_TOOLS` said «nobody has made», made here, per
    # unit, in the file where room trust already lives. At most one per
    # unit; a second is a startup error, not a guess.
    home_room: bool = False
    # PROACTIVE-SPEECH TRUST. Default False: a `kind="proactive"`
    # reminder row is owned by Nora's own `ai_peer` id, which is deliberately
    # not a person in `identity.toml`, so the fire-time `is_allowed` screen
    # cannot admit it — this flag is what the heartbeat checks instead of
    # persons membership. A home room without it refuses every proactive row
    # (`failed_policy`). The screen reads the ROW's room, so a row written
    # into a room that later stopped being the home room still fires there
    # while this flag is on — moving a home room means clearing this on the
    # old one, the same way withdrawing a person from `persons` is.
    proactive: bool = False
    # WHAT KIND OF ROOM THIS IS, in the operator's own words.
    # `None` — not `""` — when the operator wrote nothing, so "absent" and
    # "present but blank" are one state rather than two.
    #
    # `label` says what the room is CALLED; this says what it IS, and only the
    # operator can state it. A background pass reading a games room's batch
    # sees 「2200」 and 「today no?」 and has nothing in its prompt that would
    # tell it 2200 is when people log on rather than an appointment. It is
    # deliberately POLICY rather than config: it is about a room's people, it
    # lives beside the room's trust flags, and the deployment's copy of this
    # file is the only place the truth about a real room is written down.
    #
    # It is rendered as DATA the model reads about the batch, never as an
    # instruction to it (the culture fold's `_room_block`).
    description: str | None = None
    # WHERE THIS GUILD'S SPOKEN UTTERANCES LAND. Default False.
    #
    # Voice is not a room. It is a SURFACE of a room: `voice = true` on a
    # guild's text room means "every voice channel in this guild speaks into
    # HERE". Per-channel allowlisting would be unmaintainable (a server has
    # many voice channels). One designated text room gives spoken and typed
    # plans the same notebook, culture and `persons` list —
    # a plan spoken aloud and the same plan typed land in the same place.
    # The GUILD is what is checked; the channel is provenance only.
    #
    # At most one per guild; a second is a startup error, not a guess — the
    # same shape as `home_room`.
    voice: bool = False
    # WHERE THIS GUILD'S MUSIC ANNOUNCEMENTS LAND — a SIBLING channel id, not
    # this room's own. `None` (the default) keeps the where-it-started
    # fallback: the system's own music announcements — `Now playing:` on a
    # natural track-end, `Autoplaying:`, the next track after a skip, the
    # connection-lost teardown — go to whichever text channel the session's
    # FIRST play was invoked from, captured once and never moved. That
    # channel need not be an allowlisted room: `/music play` is gated by
    # GUILD, never by room.
    #
    # A FIELD ON THIS ROOM, NOT A FLAG CLAIMING IT. A DEDICATED music channel
    # is by definition NOT the guild's discussion room, so a flag claiming the
    # declaring room's own channel would force a SECOND `[[conversations]]`
    # entry per guild.
    #
    # An announcement destination is an OUTBOUND target, not a conversation.
    # A `[[conversations]]` entry is a bundle of answers ABOUT PEOPLE —
    # `persons`, `unit`, `location`, `relevance_gate`, `proactive`,
    # `home_room` — and every one of them is meaningless for a channel that is
    # written to and never read. Listing one means inventing trust answers
    # about a non-conversation and makes Nora RECORD whatever else is said
    # there — her OWN posts are dropped
    # before ingest (`parser.py`), but nobody else's are, and `persons` gates
    # ANSWERING rather than recording. That is why the loader REFUSES a
    # `music_channel` that is itself a listed conversation, rather than merely
    # discouraging it.
    #
    # Still resolved by GUILD with exactly one answer, because Discord allows
    # one voice connection per guild and so `MusicPlayerManager` holds one
    # player per guild: at most one room per guild may carry this, and a
    # second is a startup error rather than a guess.
    #
    # SYSTEM announcements only. A slash command still replies where it was
    # invoked (the person who typed it is looking there), and Nora's
    # conversational reply on the tag path still lands in the room she was
    # tagged in.
    music_channel: str | None = None
    # WHOLE-SERVER ENTRY. Non-empty marks this Conversation as a
    # `guild = "<id>"` `[[conversations]]` entry rather than a room: the
    # `[[conversations]]` entry names the GUILD, and every room-channel the
    # platform knows becomes a room of the entry's `unit`, discovered at
    # runtime through `Policy._channels` — never written into the file.
    # `persons`/`tools`/`location`/`unit`/`relevance_gate`/`description`
    # apply to every channel it expands into; `ignore`/`ambient` name
    # channels by NAME. The entry itself is never a room (`ids` and
    # `primary_id` stay empty); the synthesized rooms carry `discovered`.
    guild: str = ""
    # Channel NAMES (as the operator wrote them, folded at match) that this
    # server entry never admits — Discord's own names, so the operator never
    # copies a channel id into policy.toml. Only meaningful on a guild entry.
    ignore: frozenset[str] = frozenset()
    # Channel NAMES admitted AMBIENT: `relevance_gate` is set on their
    # synthesized rooms exactly as a static room's `relevance_gate = true`
    # opted it in. Every other channel of the entry is MENTION-ONLY.
    ambient: frozenset[str] = frozenset()
    # This room was EXPANDED from a server entry at runtime rather than
    # listed in the file. Paired with `relevance_gate` it decides `absorbed`
    # — a static room is always absorbed; a discovered one only when it is
    # ambient.
    discovered: bool = False

    @property
    def absorbed(self) -> bool:
        """Whether this room's traffic enters memory/ambient processing.

        A listed room is always absorbed (fed to Honcho, stimulated, folded):
        listing it was the opt-in. A discovered channel is absorbed only when
        the entry's `ambient` names it — mention-only is the server-entry
        default, so an undiscovered-by-name channel records and answers tags
        but is never absorbed into memory or background passes."""
        return not (self.discovered and not self.relevance_gate)


class _NoChannels:
    """The empty `ChannelDirectory`: every guild unknown, every read `None`.

    The default `Policy._channels` — loaders outside the live composition
    (the CLI, harnesses, the presence and analytics processes) enumerate no
    guild, and a guild entry then expands to no rooms rather than guessing.
    """

    def guild_channels(self, *, guild_id: str) -> Mapping[str, str] | None:
        return None

    def parent_channel(self, *, channel_id: str) -> str | None:
        return None


def _fold(name: str) -> str:
    """The case-and-spacing fold `_refuse_ambiguous_room_names` and the room
    tools' resolver share — a room name and a channel name compare only
    through it, never byte-for-byte."""
    return " ".join(name.split()).casefold()


@dataclass(frozen=True)
class Policy:
    """The resolved allowlists. `is_allowed` is the whole public surface."""

    conversations: tuple[Conversation, ...]
    dm_persons: frozenset[str] = frozenset()
    _by_id: dict[tuple[str, str], Conversation] = field(repr=False, default_factory=dict)
    _voice_by_guild: dict[tuple[str, str], Conversation] = field(
        repr=False, default_factory=dict,
    )
    _music_by_guild: dict[tuple[str, str], Conversation] = field(
        repr=False, default_factory=dict,
    )
    # unit -> the Nora WORKSPACE it belongs to (the hierarchy workspace > unit
    # > room). Filled only from a `[units]` table; empty
    # when the file has none, and then `units_declared` is False and every
    # unit belongs to `DEFAULT_WORKSPACE`.
    _unit_workspace: dict[str, str] = field(repr=False, default_factory=dict)
    units_declared: bool = False
    # `(platform, guild_id)` -> the whole-server `[[conversations]]` entry that
    # covers it (a `Conversation` with `guild` set, never a room).
    _by_guild: dict[tuple[str, str], Conversation] = field(
        repr=False, compare=False, default_factory=dict,
    )
    # The live channel directory a server entry expands through — `_NoChannels`
    # outside the main process, so a guild entry in any other loader expands
    # to nothing. Not part of the file's truth: `compare=False`, and
    # `load_policy` re-injects the process's one instance on every reload so a
    # swap carries the SAME discovery state forward.
    _channels: ChannelDirectory = field(
        repr=False, compare=False, default_factory=_NoChannels,
    )
    # Channel ids whose refusal class was already logged under THIS snapshot —
    # `conversation` runs per message, and a denied active channel must not log
    # per message. A fresh set per loaded policy: a new file may have resolved
    # the collision, so it is worth one line again.
    _refusal_log: set[tuple[str, str]] = field(
        repr=False, compare=False, default_factory=set,
    )

    def conversation(self, platform: str, conversation_id: str) -> Conversation | None:
        convo = self._by_id.get((platform, conversation_id))
        if convo is not None:
            return convo          # the explicit per-channel entry always wins
        guild_id, sep, channel_id = conversation_id.partition("/")
        if not sep or not channel_id:
            return None
        entry = self._by_guild.get((platform, guild_id))
        if entry is None:
            return None
        return self._admit_channel(entry, channel_id)

    def rooms(self) -> tuple[Conversation, ...]:
        """Every room this policy currently admits, static AND discovered.

        `conversations` is the FILE: its `guild` entries name servers, not
        rooms. `rooms()` adds the channels the directory knows, each as the
        `Conversation` `conversation()` would synthesize — so a consumer that
        enumerates rooms (the Honcho router, lineage, the fold scopes) sees
        the same set the grant predicate admits, and reloads see new entries.
        """
        out = [c for c in self.conversations if not c.guild]
        covered = set(self._by_id)
        for key, entry in self._by_guild.items():
            channels = self._channels.guild_channels(guild_id=key[1])
            if channels is None:
                continue
            for channel_id in sorted(channels):
                if (entry.platform, f"{entry.guild}/{channel_id}") in covered:
                    continue     # the explicit per-channel entry already owns it
                convo = self._admit_channel(entry, channel_id, channels=channels)
                if convo is not None:
                    out.append(convo)
        return tuple(out)

    def _admit_channel(
        self,
        entry: Conversation,
        channel_id: str,
        *,
        channels: Mapping[str, str] | None = None,
    ) -> Conversation | None:
        """The room `channel_id` of `entry`'s guild becomes, or `None`.

        Fail-closed, every branch: the guild cannot be enumerated, the channel
        is not a room-channel there, its name is in `ignore`, or its name
        collides inside the unit — each denies, and the denied-counter in the
        scope stage counts it. Collision and unavailability log once per
        snapshot, naming the CLASS and never the channel name.
        """
        if channels is None:
            channels = self._channels.guild_channels(guild_id=entry.guild)
        if channels is None:
            self._log_once(entry, channel_id, "guild-unavailable")
            return None
        name = channels.get(channel_id)
        if name is None:
            # Not a room-channel of this guild — a voice channel, a thread id,
            # or an id the directory does not know. Silent deny: the platform
            # simply delivered traffic to a non-room id.
            return None
        if _fold(name) in {_fold(n) for n in entry.ignore}:
            return None                     # the operator named it `ignore`
        if self._collides(entry, channel_id, name, channels):
            self._log_once(entry, channel_id, "label-collision")
            return None
        ambient = entry.relevance_gate or _fold(name) in {_fold(n) for n in entry.ambient}
        cid = f"{entry.guild}/{channel_id}"
        return Conversation(
            platform=entry.platform, ids=frozenset({cid}),
            persons=entry.persons, label=name, tools=entry.tools,
            location=entry.location, primary_id=cid, unit=entry.unit,
            relevance_gate=ambient, description=entry.description,
            discovered=True,
        )

    def _collides(
        self,
        entry: Conversation,
        channel_id: str,
        name: str,
        channels: Mapping[str, str],
    ) -> bool:
        """Whether `name` fails the unit's unique-names rule for this channel.

        Three ways to collide, all fail-closed rather than merged: a second
        channel of the same guild folding to the same name; a label already
        taken by another room or server entry of the unit; and a reserved
        network name (Discord channels CAN be named "discord" — a room so
        named would be unaddressable by name the moment the unit holds a
        second Discord room, so it is refused outright here)."""
        folded = _fold(name)
        # ponytail: the check covers only ONE server entry — `channels` is this
        # entry's guild and the label set is the file's — so two server entries
        # in one unit holding same-named channels are BOTH admitted with an
        # identical label. The failure mode is safe: room-name resolution
        # refuses ambiguously rather than guessing (no memory crosses). The
        # upgrade is `_collides` reading each sibling entry's live
        # `guild_channels` too.
        if sum(1 for n in channels.values() if _fold(n) == folded) > 1:
            return True
        if folded in {_fold(c.label) for c in self.conversations
                      if c.unit == entry.unit and c.label}:
            return True
        return folded in {n.casefold() for n in PLATFORM_NAMES.values()}

    def _log_once(self, entry: Conversation, channel_id: str, kind: str) -> None:
        key = (kind, channel_id)
        if key not in self._refusal_log:
            self._refusal_log.add(key)
            log.warning(
                "discord server-entry admission refused: %s (guild %s, unit %s)",
                kind, entry.guild, entry.unit,
            )

    def _location_flags(self, unit: str) -> set[bool]:
        return {c.location for c in self.conversations if c.unit == unit}

    def shows_self_in(self, platform: str, conversation_id: str) -> bool:
        """Whether her unit-wide account (`<self>`) may render in this room
        (R-31). The account is written from her sends in
        every room of the unit, including rooms with `location` on, where she
        may have answered where someone is. So a room renders it only if it
        has `location = true` itself (its audience already holds that trust),
        or no room of its unit has (then no send came from a location room). A
        location-off room of a mixed unit does not: no block, not an empty one.
        An unknown room does not."""
        convo = self.conversation(platform, conversation_id)
        if convo is None or not convo.unit:
            return False
        return convo.location or True not in self._location_flags(convo.unit)

    def shows_self_to_unit(self, unit: str) -> bool:
        """Whether `<self>` may render in a unit-scoped prompt (a Cognition
        session, whose agenda may span rooms and whose sends can reach any
        room of the unit): only when every room of the unit agrees on
        `location`, so the account is never shown where a location-off room
        could be the audience."""
        return len(self._location_flags(unit)) == 1

    def workspace_of(self, unit: str) -> str | None:
        """The Nora workspace `unit` belongs to, or `None` when the file
        declares `[units]` and this unit is not in it.

        With no `[units]` table every unit — named in this file or not —
        belongs to `DEFAULT_WORKSPACE`: that is the back-compat reading of a
        policy written before workspaces existed, and `load_policy` refuses a
        `[units]` table that leaves any room's unit out, so `None` is only
        ever the answer for a unit no room here has."""
        if not self.units_declared:
            return DEFAULT_WORKSPACE
        return self._unit_workspace.get(unit)

    def unit_workspaces(self) -> Mapping[str, str]:
        """Every unit a room here belongs to -> its Nora workspace. The
        implicit case maps each of them to `DEFAULT_WORKSPACE`, so a caller
        never has to know which form the file took."""
        units = {convo.unit for convo in self.conversations}
        return {
            unit: self.workspace_of(unit) or DEFAULT_WORKSPACE for unit in sorted(units)
        }

    def voice_room(self, platform: str, guild_id: str) -> Conversation | None:
        """The room a spoken utterance from `guild_id` belongs to, or `None`.

        `None` is a REFUSAL, not a fallback: a guild with no room marked
        `voice = true` has no place to put speech, and the wake path answers
        400 rather than inventing one. Deny-by-default, exactly as the room
        gate is — and the reason the guild id is the thing checked at all
        (see `Conversation.voice`).

        The resolution is explicit rather than inferred from "the only room in
        that guild". Today every guild happens to hold exactly one allowlisted
        room, so inference would work and would keep working right up until it
        silently picked the wrong one."""
        return self._voice_by_guild.get((platform, guild_id))

    def music_channel_for(self, platform: str, guild_id: str) -> str | None:
        """The CHANNEL id `guild_id`'s music announcements are posted in, or
        `None`.

        Returns the id rather than the declaring `Conversation` — unlike
        `voice_room`, whose caller records INTO the room it gets back. Here
        the room is only where the operator wrote the answer down; the answer
        itself is a sibling channel that is not a room at all (see
        `Conversation.music_channel`), so handing back the declaring room
        would invite a caller to treat it as the destination.

        `None` is a FALLBACK rather than a refusal: a guild that has not opted
        in announces into the channel its session started in. Speech has no
        such fallback — there is no place to put an utterance other than the
        room the operator named — which is why `voice_room`'s `None` is a 400
        and this one is not.

        Explicit rather than inferred from "the only room in that guild", for
        `voice_room`'s reason: inference works right up until it silently
        picks the wrong one."""
        convo = self._music_by_guild.get((platform, guild_id))
        return convo.music_channel if convo is not None else None

    def home_rooms(self) -> dict[str, Conversation]:
        """Unit -> its home room, for every unit that named
        one. A unit with none is absent, never guessed: the proactive
        adaptor does not run there, and its tool source builds nothing
        room-scoped for it. `load_policy` has already refused two."""
        return {
            convo.unit: convo
            for convo in self.conversations
            if convo.home_room
        }

    def proactive_allowed(self, *, platform: str, conversation_id: str) -> bool:
        """The fire-time screen for a `kind="proactive"` row: the ROOM
        must be listed AND flagged `proactive = true`. No person is checked,
        because the row's owner is Nora's `ai_peer` id and that id is, by
        construction, nobody in `identity.toml`. Deny by default, like
        `is_allowed`: an unlisted room and an unflagged room answer the same
        `False`."""
        convo = self.conversation(platform, conversation_id)
        return convo is not None and convo.proactive

    def is_allowed(self, *, platform: str, conversation_id: str, person_id: str | None) -> bool:
        """The grant predicate, in full.

        Both gates must pass: the ROOM must be listed, and the SENDER must be
        allowed within it.

        `persons = "*"` admits an UNMAPPED sender too. The room is the trust
        boundary the operator drew, and D6 already settled that an unmapped
        sender is answered but never remembered (no `person_id`, so nothing is
        fed to Honcho). Refusing them here would both reverse that and make
        `"*"` almost pointless — it would then admit only people who are
        already mapped, which is what an explicit list is for.

        An explicit list still excludes them: `None` is not in `persons`, so
        an unrecognised sender in a `persons = ["miles"]` room is refused.
        """
        convo = self.conversation(platform, conversation_id)
        if convo is None:
            return False                      # room not listed → deny
        if convo.persons is None:
            return True                       # "*" — anyone in this room
        return person_id in convo.persons


def permissive_policy() -> Policy:
    """A Policy that allows every conversation and every sender.

    OFFLINE AND EVAL HARNESSES ONLY. `nora.app.offline` builds pipelines for
    `tests/integration` and `evals/`, which drive synthetic conversation ids
    that no real `policy.toml` would list; without this every harness message
    would be refused by the room gate and every test would exercise nothing.

    Production must never reach this — deny-by-default is the entire point of
    the file — and `tests/test_boundaries.py` asserts `nora/app/composition.py`
    does not reference it.
    """
    return _PermissivePolicy(conversations=())


@dataclass(frozen=True)
class _PermissivePolicy(Policy):
    def conversation(self, platform: str, conversation_id: str) -> Conversation:
        return Conversation(
            platform=platform, ids=frozenset({conversation_id}), persons=None,
            primary_id=conversation_id,
            # Opted IN to the Relevance Gate:
            # the offline/eval harnesses this policy exists for must be able to
            # drive the gate — an untagged message reaching
            # `RelevanceGatePolicy` here marks `EngageDecision.relevance_gate`
            # exactly as an opted-in production room's would. Inert everywhere
            # else: the verdict only matters when a harness has ALSO wired a
            # `RelevanceGatePolicy` into its engage chain, which nothing does
            # by default.
            relevance_gate=True,
        )

    def is_allowed(self, *, platform: str, conversation_id: str, person_id: str | None) -> bool:
        return True

    def voice_room(self, platform: str, guild_id: str) -> Conversation:
        """Any guild resolves, to a room named for that guild — the harnesses'
        reason again. A synthetic `"<guild>/voice"` rather than a real channel
        id: this policy invents rooms by construction, and a fabricated
        channel snowflake would read like one a deployment might actually
        have."""
        return self.conversation(platform, f"{guild_id}/voice")

    # NO `music_channel_for` override, deliberately: the inherited one answers
    # `None` for every guild, and `None` is music's FALLBACK (announce where
    # the session started), not its refusal. Inventing a channel here would
    # route a harness's announcements at an id nothing can send to —
    # `voice_room` overrides precisely because `None` there WOULD refuse.

    def proactive_allowed(self, *, platform: str, conversation_id: str) -> bool:
        """Every room, for the harnesses' reason: the act suite drives
        proactive rows through a heartbeat over synthetic ids. `home_rooms`
        stays empty — allowing a row to FIRE anywhere is not the same as
        inventing a room to DECIDE in."""
        return True


class _ConversationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: str
    # `None` only on a `guild` entry, which names the whole server instead
    # (see `Conversation.guild`); every other entry still requires it.
    id: str | None = None
    # A WHOLE DISCORD SERVER, by its one guild id. Every room-channel of the
    # guild becomes a room of this entry's `unit` at runtime — the file never
    # names a channel id. `ignore`/`ambient` then name channels by NAME:
    # ignored channels are never admitted; ambient ones get the entry's own
    # `relevance_gate` opt-in regardless of it. Discord only; a snowflake.
    guild: str | None = None
    ignore: list[str] = Field(default_factory=list)
    ambient: list[str] = Field(default_factory=list)
    # Telegram CHANGES chat.id when a group is upgraded to a supergroup,
    # reporting the old id once via migrate_to_chat_id. Without this the
    # allowlist silently stops matching and Nora goes quiet — invisible under
    # R-5.
    also_ids: list[str] = Field(default_factory=list)
    label: str = ""
    persons: list[str] | str = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    # See `Conversation.location` (DEC-74/79): relaxes `where_is`'s disclosure
    # gate for this room. Default False — the disclosure guard stays strict.
    location: bool = False
    # See `Conversation.unit` (DEC-105): the social unit this room belongs
    # to. `send_to` may only relay BETWEEN rooms that share it.
    unit: str = "nora"
    # See `Conversation.relevance_gate`: opts
    # THIS room into the Relevance Gate's consideration of its untagged
    # traffic. Default False — the right to speak uncalled is granted per
    # room, never inherited from being answerable.
    relevance_gate: bool = False
    # See `Conversation.home_room`: the ONE
    # room of this unit the proactive adaptor stands in. Default False.
    home_room: bool = False
    # See `Conversation.proactive`: may a proactive row fire HERE.
    # Default False — unprompted speech is granted per room, never inherited.
    proactive: bool = False
    # See `Conversation.voice`: every voice channel in THIS room's guild speaks into this
    # room. Default False — a room takes no speech until the operator says so.
    voice: bool = False
    # See `Conversation.music_channel`: the SIBLING channel id this guild's music
    # announcements are posted in. Absent is the default — an unset guild keeps
    # announcing where its session started.
    music_channel: str | None = None
    # See `Conversation.description`: what kind of room this is, in the
    # operator's own words. Absent is the default — `extra = "forbid"` is in
    # force here, so this entry is what makes the key legal at all.
    description: str | None = None

    @field_validator("music_channel")
    @classmethod
    def _blank_music_channel_is_absent(cls, value: str | None) -> str | None:
        """`music_channel = ""` is the operator writing nothing, and it must
        arrive as `None`: two spellings of "no destination" is how one of them
        reaches `isdigit()` and refuses startup over a line that meant
        nothing. Same reason `description` normalises."""
        if value is None:
            return None
        return value.strip() or None

    @field_validator("description")
    @classmethod
    def _blank_description_is_absent(cls, value: str | None) -> str | None:
        """`description = ""` is the operator writing nothing, and it must
        arrive as `None` rather than as an empty string: the render asks
        «is there a description» and two spellings of "no" is how a block
        ends up printed with nothing in it."""
        if value is None:
            return None
        return value.strip() or None

    @field_validator("persons")
    @classmethod
    def _check_persons(cls, value: list[str] | str) -> list[str] | str:
        if isinstance(value, str) and value != EVERYONE:
            raise ValueError(
                f"persons must be a list of person_ids or the string "
                f'"{EVERYONE}"; got {value!r}'
            )
        return value


class _UnitEntry(BaseModel):
    """One `[units.<unit>]` table: which Nora workspace the unit belongs to.

    Only the STRUCTURE lives here. Which Honcho workspace a Nora workspace is
    bound to is an external binding and lives in `identity.toml`'s
    `[workspaces.<name>]` (`nora.persons_file`), for DEC-8's reason: this file
    changes when trust changes, and a Honcho id is not a trust answer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace: str

    @field_validator("workspace")
    @classmethod
    def _check_workspace(cls, value: str) -> str:
        if not _WORKSPACE_NAME.match(value):
            raise ValueError(
                f"workspace {value!r} must match ^[a-zA-Z0-9_-]+$ — a workspace "
                "with no binding in identity.toml is used verbatim as its "
                "Honcho workspace id, and Honcho refuses any other character"
            )
        return value


class _PolicyFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    default: str = "deny"
    dm: dict[str, list[str]] = Field(default_factory=dict)
    conversations: list[_ConversationEntry] = Field(default_factory=list)
    # `None` — the table is ABSENT — is the back-compat case: every unit
    # belongs to `DEFAULT_WORKSPACE`. Present, it must be complete; see
    # `load_policy`.
    units: dict[str, _UnitEntry] | None = None

    @field_validator("default")
    @classmethod
    def _only_deny(cls, value: str) -> str:
        if value != "deny":
            raise ValueError(
                'default must be "deny" — an allow-by-default Nora would join '
                "any group she is added to, and there is no second gate behind "
                "this one"
            )
        return value


def load_policy(path: Path, *, channels: ChannelDirectory | None = None) -> Policy:
    path = Path(path).expanduser()
    if not path.exists():
        raise PolicyError(
            f"policy file not found: {path}. Nora refuses to start without one "
            "rather than running mute — see policy.example.toml, and "
            "`nora groups` for the conversation ids to put in it."
        )
    with path.open("rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as exc:
            raise PolicyError(f"invalid TOML in {path}: {exc}") from exc
    try:
        parsed = _PolicyFile.model_validate(data)
    except ValidationError as exc:
        raise PolicyError(str(exc)) from exc

    conversations: list[Conversation] = []
    by_id: dict[tuple[str, str], Conversation] = {}
    by_guild: dict[tuple[str, str], Conversation] = {}
    for entry in parsed.conversations:
        persons = None if entry.persons == EVERYONE else frozenset(entry.persons)
        if entry.guild is not None:
            # A server entry expands at RUNTIME, so it cannot carry any key
            # that answers per-room: an `id`/`also_ids` is a room's, `voice`/
            # `music_channel`/`home_room`/`proactive` each name ONE room's
            # job a whole server cannot take. Refused, never silently
            # half-applied — the operator adds a per-channel entry for those.
            if entry.id is not None or entry.also_ids:
                raise PolicyError(
                    f"{entry.label or entry.guild!r} sets both guild and "
                    "id/also_ids. A server entry names the guild only; list "
                    "the channel as its own [[conversations]] entry instead."
                )
            if entry.platform != "discord":
                raise PolicyError(
                    f"{entry.label or entry.guild!r} sets guild on platform "
                    f"{entry.platform!r} — only Discord rooms are named by "
                    '"<guild_id>/<channel_id>", so only Discord has a guild '
                    "to expand."
                )
            if not _is_snowflake(entry.guild):
                raise PolicyError(
                    f"{entry.label or 'a server entry'} has guild = "
                    f"{entry.guild!r}, which is not a Discord guild id — "
                    "ASCII digits, 17-20 of them."
                )
            for flag in ("voice", "music_channel", "home_room", "proactive"):
                if getattr(entry, flag) not in (False, None):
                    raise PolicyError(
                        f"{entry.label or entry.guild!r} sets {flag} on a "
                        "server entry — that flag names ONE room's job and a "
                        "whole server cannot take it. Add a per-channel "
                        "[[conversations]] entry for it."
                    )
            convo = Conversation(
                platform=entry.platform, ids=frozenset(), persons=persons,
                label=entry.label, tools=frozenset(entry.tools),
                location=entry.location, primary_id="", unit=entry.unit,
                relevance_gate=entry.relevance_gate,
                description=entry.description, guild=entry.guild,
                ignore=frozenset(entry.ignore), ambient=frozenset(entry.ambient),
            )
            key = (entry.platform, entry.guild)
            if key in by_guild:
                raise PolicyError(
                    f"guild {entry.guild} is covered by two server entries — "
                    "the second would be unreachable, so one of the two "
                    "allowlists you think you wrote is doing nothing"
                )
            by_guild[key] = convo
            conversations.append(convo)
            continue
        if entry.ignore or entry.ambient:
            raise PolicyError(
                f"{entry.label or entry.id!r} sets ignore/ambient on a "
                "per-channel entry — those name channels of a server entry. "
                "Remove them, or admit the whole server with guild instead."
            )
        if entry.id is None:
            raise PolicyError(
                "a [[conversations]] entry needs id or guild — a room is "
                "named by its platform id, a whole Discord server by its "
                "guild id"
            )
        ids = frozenset([entry.id, *entry.also_ids])
        convo = Conversation(
            platform=entry.platform, ids=ids, persons=persons, label=entry.label,
            tools=frozenset(entry.tools), location=entry.location,
            primary_id=entry.id, unit=entry.unit,
            relevance_gate=entry.relevance_gate,
            home_room=entry.home_room, proactive=entry.proactive,
            description=entry.description, voice=entry.voice,
            music_channel=entry.music_channel,
        )
        conversations.append(convo)
        for cid in ids:
            key = (entry.platform, cid)
            if key in by_id:
                raise PolicyError(
                    f"conversation id {cid!r} on {entry.platform} is listed "
                    "twice — the second entry would be unreachable, so one of "
                    "the two allowlists you think you wrote is doing nothing"
                )
            by_id[key] = convo

    # EVERY ALLOWED ROOM CARRIES A LABEL. `UnitRoom.label`
    # (`nora.app.composition._unit_rooms`) renders `label` or a floor —
    # `UNNAMED_ROOM`, «a room with no name» — for a room left unlabelled, so a
    # missing label never puts a raw `primary_id` on the model's wire (R-18).
    # That floor is the right failure mode for OLD data a render meets after
    # the fact; it is the wrong one for a room the operator is choosing to ADD
    # to `policy.toml` — two unlabelled rooms then render identically,
    # silently, and the
    # operator who wrote neither label finds out from a confused reply
    # instead of from this file. Refused here, in `policy.toml`'s own
    # refuse-to-start shape (R-5), the same way a second home room already
    # is — never left to the render-time floor to paper over.
    for convo in conversations:
        if not convo.label:
            raise PolicyError(
                f"{convo.platform}:{convo.primary_id!r} has no label. Every "
                "allowed room needs one: it is what every model-facing "
                "render calls the room, and two unlabelled rooms would "
                "otherwise render as the same room. Add "
                f'label = "..." to this conversation in {path}.'
            )

    # ONE home room per unit. Two would make «which room does the
    # unit-keyed adaptor stand in» a guess, and a guessed room is a
    # disclosure decision nobody made — the exact thing `home_room` exists to
    # have the operator make. Refused at startup, in `policy.toml`'s own
    # refuse-to-start shape (R-5), never resolved by picking the first.
    homes: dict[str, Conversation] = {}
    for convo in conversations:
        if not convo.home_room:
            continue
        other = homes.get(convo.unit)
        if other is not None:
            raise PolicyError(
                f"unit {convo.unit!r} names two home rooms "
                f"({other.label or other.primary_id!r} and "
                f"{convo.label or convo.primary_id!r}). A unit has exactly "
                "one room its proactive adaptor stands in; mark one."
            )
        homes[convo.unit] = convo

    # ONE voice room per guild. `voice = true` claims every voice channel in a guild, so two
    # rooms claiming the same guild is not a preference to resolve — it is the
    # operator having written two different answers to "where does speech go",
    # and picking one would put a spoken plan in a room nobody chose.
    voice_by_guild: dict[tuple[str, str], Conversation] = {}
    for convo in conversations:
        if not convo.voice:
            continue
        if convo.platform != "discord":
            raise PolicyError(
                f"{convo.label or convo.primary_id!r} sets voice = true on "
                f"platform {convo.platform!r}. Voice is resolved by GUILD, and "
                "only Discord rooms have one — no other platform reaches this "
                "path at all."
            )
        guild_id, sep, channel_id = convo.primary_id.partition("/")
        if not sep or not guild_id or not channel_id:
            raise PolicyError(
                f"{convo.label or convo.primary_id!r} sets voice = true but its "
                f"id {convo.primary_id!r} is not \"<guild_id>/<channel_id>\". "
                "The guild half is what a spoken utterance is matched on, so "
                "an id without one can never be reached."
            )
        key = (convo.platform, guild_id)
        other = voice_by_guild.get(key)
        if other is not None:
            raise PolicyError(
                f"guild {guild_id} names two voice rooms "
                f"({other.label or other.primary_id!r} and "
                f"{convo.label or convo.primary_id!r}). Every voice channel in "
                "a guild speaks into exactly one room; mark one."
            )
        voice_by_guild[key] = convo

    # ONE music room per guild. Mirrors the voice block above, and for the same structural reason
    # rather than by imitation: Discord allows ONE voice connection per guild, so
    # `MusicPlayerManager` holds one player and one notify target per guild.
    # Two rooms claiming it is the operator having written two answers to
    # "where do the announcements go", and picking one would put them in a
    # room nobody chose.
    music_by_guild: dict[tuple[str, str], Conversation] = {}
    for convo in conversations:
        if convo.music_channel is None:
            continue
        if convo.platform != "discord":
            raise PolicyError(
                f"{convo.label or convo.primary_id!r} sets music_channel on "
                f"platform {convo.platform!r}. Music is a Discord voice-channel "
                "capability and is resolved by GUILD; no other platform reaches "
                "this path at all."
            )
        guild_id, sep, channel_id = convo.primary_id.partition("/")
        if not sep or not guild_id or not channel_id:
            raise PolicyError(
                f"{convo.label or convo.primary_id!r} sets music_channel but its "
                f"OWN id {convo.primary_id!r} is not \"<guild_id>/<channel_id>\". "
                "The guild half of the declaring room is what a playing session "
                "is matched on, so a room without one can never be reached."
            )
        if not _is_snowflake(convo.music_channel):
            raise PolicyError(
                f"{convo.label or convo.primary_id!r} sets music_channel = "
                f"{convo.music_channel!r}, which is not a Discord channel id. It "
                "is a bare channel snowflake — ASCII digits, 17-20 of them — in "
                "THIS room's guild. Not a \"<guild>/<channel>\" pair and not a "
                "channel name, because names are not stable ids. (A bare id from "
                "ANOTHER guild has the same shape and cannot be told apart here; "
                "`music_notify` checks the channel's actual guild at send time, "
                "which is the first moment that is knowable.)"
            )
        # The destination must NOT be a listed room. The whole reason this is a
        # field rather than a `voice = true`-shaped flag is that an
        # announcement channel is an OUTBOUND target and not a conversation —
        # `[[conversations]]` carries `persons`, `unit`, `location`,
        # `proactive`, `relevance_gate`, `home_room`, and every one of them is
        # an answer about people that a write-only channel has no business
        # having. Pointing `music_channel` at a listed room puts the
        # announcements back inside ingest and makes that argument false, so it
        # is refused rather than merely discouraged.
        destination = (convo.platform, f"{guild_id}/{convo.music_channel}")
        listed = by_id.get(destination)
        if listed is not None:
            raise PolicyError(
                f"{convo.label or convo.primary_id!r} sets music_channel = "
                f"{convo.music_channel}, but that channel is itself a listed "
                f"conversation ({listed.label or listed.primary_id!r}). An "
                "announcement channel is written to and never read from, so it "
                "must not be a room: a room carries persons/unit/location/"
                "proactive answers that mean nothing for it, and Nora would "
                "ingest whatever else is said there. Point it at a channel that "
                "is not in this file — or omit it, which announces where each "
                "session started."
            )
        key = (convo.platform, guild_id)
        other = music_by_guild.get(key)
        if other is not None:
            raise PolicyError(
                f"guild {guild_id} names two music channels "
                f"({other.music_channel} on {other.label or other.primary_id!r} "
                f"and {convo.music_channel} on "
                f"{convo.label or convo.primary_id!r}). A guild has one voice "
                "connection and one player, so its announcements land in exactly "
                "one channel; keep one."
            )
        music_by_guild[key] = convo

    _refuse_ambiguous_room_names(conversations)
    unit_workspace = _unit_workspaces(parsed.units, conversations, path)

    if not conversations:
        raise PolicyError(
            f"{path} allows no conversations, so Nora would ingest and answer "
            "nothing. That is indistinguishable from a working Nora in a quiet "
            "group (R-5), so it is a startup error rather than a silent mute. "
            "Run `nora groups` for the ids, or delete the file to see this "
            "message again."
        )
    return Policy(
        conversations=tuple(conversations),
        dm_persons=frozenset(parsed.dm.get("persons", [])),
        _by_id=by_id,
        _voice_by_guild=voice_by_guild,
        _music_by_guild=music_by_guild,
        _unit_workspace=unit_workspace,
        units_declared=parsed.units is not None,
        _by_guild=by_guild,
        _channels=channels if channels is not None else _NoChannels(),
    )


def _refuse_ambiguous_room_names(conversations: list[Conversation]) -> None:
    """Room labels and network addresses must name one thing within a unit."""
    seen: dict[tuple[str, str], Conversation] = {}
    networks = {name.casefold(): key for key, name in PLATFORM_NAMES.items()}
    networks.update((c.platform.casefold(), c.platform) for c in conversations)
    for convo in conversations:
        # Same case and spacing fold as the room tools' resolver.
        name = " ".join(convo.label.split()).casefold()
        key = (convo.unit, name)
        other = seen.get(key)
        if other is not None:
            raise PolicyError(
                f"unit {convo.unit!r} has duplicate room names "
                f"({other.label!r} on {other.platform}:{other.primary_id!r} and "
                f"{convo.label!r} on {convo.platform}:{convo.primary_id!r}). "
                "Room names must be unique after case and spacing are folded."
            )
        seen[key] = convo

    for convo in conversations:
        network = networks.get(" ".join(convo.label.split()).casefold())
        if network is None:
            continue
        other = next((c for c in conversations if c.unit == convo.unit
                      and c.platform == network and c is not convo), None)
        if convo.platform == network and other is None:
            continue
        conflict = (
            f"; it conflicts with {other.label!r} on "
            f"{other.platform}:{other.primary_id!r}" if other is not None else ""
        )
        raise PolicyError(
            f"unit {convo.unit!r} uses reserved network name {convo.label!r} "
            f"for {convo.platform}:{convo.primary_id!r}{conflict}. A network "
            "name may label only its own network's sole room in the unit."
        )


def _unit_workspaces(
    units: Mapping[str, _UnitEntry] | None,
    conversations: list[Conversation],
    path: Path,
) -> dict[str, str]:
    """unit -> Nora workspace, from the `[units]` table.

    ABSENT table: `{}`, and `Policy.workspace_of` answers `DEFAULT_WORKSPACE`
    for every unit — the policy that existed before the hierarchy keeps
    loading unchanged. PRESENT: it must cover every room's unit. A room whose
    unit has no entry would belong to no workspace, which means no Honcho
    workspace could be named for it, so it is a startup error rather than a
    guess (R-5's refuse-to-start shape). A declared unit may temporarily hold
    no rooms: its workspace is admitted at boot so removing its last room and
    adding a room back can both reload without changing workspace bindings."""
    if units is None:
        return {}
    used = {convo.unit for convo in conversations}
    missing = sorted(used - set(units))
    if missing:
        raise PolicyError(
            f"{path} declares [units] but not {missing}. Once the table exists "
            "every unit a room uses must name its workspace — add "
            f'[units.{missing[0]}] workspace = "..." — or delete [units] '
            "entirely to put every unit in the one implicit workspace."
        )
    return {unit: entry.workspace for unit, entry in units.items()}
