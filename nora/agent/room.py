"""The room a tag happened in, as PEOPLE — the scope every room-scoped tool resolves within.

Scope has to be enforced where the answer is ASSEMBLED: a roster block that
carries no phone number or ACI gives R-18 (`nora.agent.egress`) nothing to
block, and by the time text exists the disclosure is already authorised. So
this module resolves WITHIN scope, never resolve-then-filter:

* `RoomView.people` is built from the platform's own membership list for THIS
  conversation, intersected with `identity.toml` — never from
  `Persons.index`, which is Nora's address book across every room she is in.
* `RoomView.find` builds its name/alias index from `people` alone. A person who
  is in `identity.toml` but not in this room is not "found and rejected", they
  are **not in the index at all** — indistinguishable from a person who does
  not exist. Resolve-then-filter would leak membership through the difference
  between "no such person" and "not here".

Alias matching is safe here because an alias never resolves a SENDER (a
nickname is operator-authored and a display name is settable by anyone, so
matching one to decide *who sent this* hands identity to whoever can type it).
Resolving a lookup TARGET is a different operation: the answer is bounded
before any alias is consulted.

The bound is the room for `group_members`, `lookup_person` and `about`. For
`whereis` it is the location-sharing CIRCLE unioned with the room (R-47),
resolved through `find_by_name`: a wrong alias match there can name a circle
member who is NOT in the room, which is a stronger claim than "somebody already
here". The room checks still run in full first, so a wrong match cannot reach a
room that may not be told anything at all; it can only answer about the wrong
SUBJECT. `nora.persons.Persons.index` is untouched, and
`tests/test_persons.py::test_an_alias_never_resolves_a_sender` holds.

**Nora is not one of the people.** Her own account is in the group's member
list, resolves to nobody in `identity.toml`, and would otherwise be reported as
"one account I don't have a name for" — a stranger in the room, who is her.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from ..domain.models import PersonRef, TranscriptRow
from ..domain.ports import Roster, RosterSource
from ..persons import Persons

log = logging.getLogger("nora.agent.room")

# Punctuation a name may arrive wrapped in when the model lifts it out of a
# Cantonese message — 「阿豹」 is how the name appears in the transcript.
# RUF001 is suppressed because the ambiguous characters ARE the data: this is a
# set of quote marks to strip, and replacing them would change which inputs the
# function handles.
_WRAPPERS = "「」『』《》〈〉“”‘’\"'()（）[]【】 \t　"  # noqa: RUF001


def _key(name: str) -> str:
    """The one normalisation both sides of a name match go through."""
    return str(name).strip(_WRAPPERS).strip().casefold()


@dataclass(frozen=True)
class RoomView:
    """Who is in this conversation, as far as Nora can tell right now.

    `available` is a separate axis from emptiness for the reason
    `nora.domain.ports.Roster` documents: a room with nobody in it and a room
    Nora could not look into must never produce the same answer.

    `unnamed` counts member ACCOUNTS that resolve to no person — reported as a
    number, never as ids, because the only thing Nora holds for them is a phone
    number or an ACI and those are exactly what R-18 exists to keep off the
    wire. It is also the operator's tell that `identity.toml` is incomplete.

    `degraded` means the scope was narrowed to the tagger alone because the
    platform roster could not be read. It is a strict SUBSET of the real room
    (Nora received their message in it, which is stronger evidence than
    `listGroups`), so no lookup it permits could reach outside this
    conversation — but it is not the whole room, and a tool that answers from
    it has to say so rather than imply the room is that small.
    """

    available: bool
    people: tuple[PersonRef, ...] = ()
    unnamed: int = 0
    degraded: bool = False

    def find(self, name: str) -> tuple[PersonRef, ...]:
        """Every person IN THIS ROOM that `name` could mean.

        Returns a tuple rather than an optional so the caller can tell nobody
        from more than one: two people in one room can genuinely share a
        nickname, and silently picking the first would attach one person's
        memory to another's name.

        Matched against, per person: the name Nora calls them, their first and
        last names separately and together, and every alias the operator wrote
        down. Exact match after normalisation only — substring matching would
        make 「東」 resolve 阿豹 and 阿豹妹 both, and a wrong confident answer
        about a person in the room is worse than «I'm not sure who you mean».
        """
        return find_by_name(self.people, name)


def find_by_name(people, name: str) -> tuple[PersonRef, ...]:
    """Every person in `people` that `name` could mean.

    The one matching rule for "who do they mean", applied to a set that need not
    be a room: `RoomView.find` uses it on the room, `whereis` on the
    location-sharing circle (R-47). Two matching rules would drift.
    """
    wanted = _key(name)
    if not wanted:
        return ()
    return tuple(p for p in people if wanted in _names_of(p))


def tell_apart(
    matches: Sequence[PersonRef], among: Sequence[PersonRef], *, by_id: bool,
) -> list[str | None]:
    """What to call each of `matches` so that naming it again resolves to it
    alone among `among`: the first of its full name and its aliases that
    `find_by_name` resolves to exactly that person. With none, its
    `person_id` where `by_id` (a Cognition session, whose tools accept an id
    back), else `None`: a tag turn's model is never handed a person id."""
    out: list[str | None] = []
    for p in matches:
        full = " ".join(n for n in (p.first_name, p.last_name) if n)
        label = next(
            (c for c in (full, *p.aliases) if c and find_by_name(among, c) == (p,)),
            p.person_id if by_id else None,
        )
        out.append(label)
    return out


def _names_of(person: PersonRef) -> frozenset[str]:
    """Every string this person may be called by, normalised. Scoped by the
    caller: `RoomView.people` for room lookups, a circle's people for
    `whereis` (R-47)."""
    full = " ".join(p for p in (person.first_name, person.last_name) if p)
    candidates = [person.name, person.first_name, person.last_name, full, *person.aliases]
    return frozenset(key for c in candidates if (key := _key(c)))


def trigger_message_ids(
    transcript: Sequence[TranscriptRow], *, sender_id: str, timestamp: int,
) -> tuple[int, ...]:
    """The Record ids of the message a tag answers, found in the window the
    prompt was built from: a row that is not Nora's, from the tagger's platform
    id, at the message's own timestamp. Empty when the row is not in the window
    (the tag's own message is the newest row, so this is the case of a window
    read that failed or was cut to nothing): a write then cites nothing and keeps
    its room-only lineage. Never guesses a nearby row: a wrong id would put an
    edge to a message that did not raise the turn, and the cascade would then
    withdraw an event for the wrong person's deletion."""
    return tuple(
        r.id for r in transcript
        if not r.is_nora and r.sender_id == sender_id and r.ts == timestamp
    )


class Room:
    """One tag's conversation, resolved to people. Constructed fresh per tag.

    Holds the roster read for the length of the tag: `group_members` followed
    by two `lookup_person` calls is three tool calls and must stay ONE daemon
    round trip. The cache is per-instance and the instance dies with the tag,
    so it can neither go stale within a turn nor survive into the next one —
    the same "fresh per tag, no bookkeeping" construction `ReplyGuard` uses.
    """

    def __init__(
        self,
        *,
        platform: str,
        conversation_id: str,
        roster_source: RosterSource | None,
        persons: Persons,
        tagger: PersonRef | None = None,
        self_ids: frozenset[str] = frozenset(),
        tagger_card_injected: bool = False,
        trigger_message_ids: tuple[int, ...] = (),
    ) -> None:
        self._trigger_message_ids = tuple(trigger_message_ids)
        self._platform = platform
        self._conversation_id = conversation_id
        self._roster_source = roster_source
        self._persons = persons
        self._tagger = tagger
        self._self_ids = frozenset(self_ids)
        self._tagger_card_injected = tagger_card_injected
        self._view: RoomView | None = None

    @property
    def platform(self) -> str:
        """Which platform this conversation is on.

        Read by `remind`, which stores `(platform, conversation_id,
        conversation_type)` because every conversation key is a tuple: a row
        holding `conversation_id` alone can build neither a
        `ConversationTarget`, nor the `ConversationSlots` key, nor a
        `Policy.is_allowed` call. Both constructors of this object (the tag
        pipeline and the fire path) pass the authoritative value, so it is read
        here rather than carried as a second parameter.
        """
        return self._platform

    @property
    def trigger_message_ids(self) -> tuple[int, ...]:
        """The Record ids of the message(s) that raised this turn (`messages.id`),
        read by the event verbs so an event a tag turn writes cites what it rests
        on (the withdrawal cascade can only reach a row through an edge). Empty
        when the turn was not raised by a Record message (the fire path) or the
        row is not in the window: the write then keeps the room-only lineage and
        records no edge."""
        return self._trigger_message_ids

    @property
    def tagger(self) -> PersonRef | None:
        """Who this turn is FOR, or None when the sender is unmapped.

        `scope()` surfaces the tagger only in its degraded branch, and a
        `None` check is not what the caller needs: `remind` writes
        `tagger.person_id` into `reminders.requester_person_id`, the id the
        fire-time policy re-check runs against. So this is a reader, not a
        predicate: «is there a mapped requester» and «who is it» are one
        question, and answering only the first would have the tool invent an
        owner for the row.

        Read-only on purpose: the tagger is fixed for the life of the turn, and
        this object is constructed fresh per turn.
        """
        return self._tagger

    @property
    def tagger_card_injected(self) -> bool:
        """Whether `<peer_history>` for the tagger is ALREADY in the prompt.

        Read by `about`, and only to answer the tagger's own name with a
        pointer instead of a second identical `representation` call — the
        conclusions are already in front of her, and fetching them again would
        spend a round trip to print the same lines twice.

        Carries whether the block LANDED, never whether it was configured. An
        unreachable Honcho and `tagger_card_conclusions = 0` both yield no
        block, and in both cases `about` must do the real lookup rather than
        point at an empty space — so this is derived from the assembled
        `TagContext`, not from config.
        """
        return self._tagger_card_injected

    async def members(self) -> RoomView:
        """The room as the platform reports it. `available=False` if it could not be read."""
        if self._view is None:
            self._view = self._resolve(await self._read())
        return self._view

    def find(self, view: RoomView, name: str) -> tuple[PersonRef, ...]:
        """Every person in `view` that `name` could mean: `view.find`'s
        names, the same name with a leading `@` dropped (a tag as typed), and
        an Instagram `username` the operator recorded for them
        (`Persons.instagram_username_owners`, casefolded, `@` stripped).

        Scoped by `view`, the room this tool may resolve within: a username
        recorded for someone who is not in it resolves to nobody, exactly as
        their name would. One person matched two ways is one match."""
        bare = _key(name).removeprefix("@")
        owners = self._persons.instagram_username_owners(bare) if bare else frozenset()
        found: dict[str, PersonRef] = {}
        for ref in (
            *view.find(name), *view.find(bare),
            *(p for p in view.people if p.person_id in owners),
        ):
            found.setdefault(ref.person_id, ref)
        return tuple(found.values())

    async def scope(self) -> RoomView:
        """The set a name lookup may resolve within.

        Identical to `members()` when the roster is readable. When it is not,
        this narrows to the tagger alone rather than going dark: Nora received
        their message in this conversation, so their membership is established
        by construction, and a strict subset of the room can never resolve
        somebody outside it. `degraded` says which of the two happened, because
        "I only know about you right now" and "you are the only one here" are
        different sentences.
        """
        view = await self.members()
        if view.available:
            return view
        if self._tagger is None:
            return view
        return RoomView(available=True, people=(self._tagger,), degraded=True)

    async def _read(self) -> Roster:
        source = self._roster_source
        if source is None:
            # No roster source wired (offline and eval compositions have no
            # daemon to ask). Reported as unavailable rather than empty: a
            # composition that answered "nobody is here" would make a wiring
            # mistake look like a quiet group (R-5).
            return Roster(available=False)
        try:
            return await source.roster(conversation_id=self._conversation_id)
        except Exception:
            # `RosterSource.roster` is documented never to raise. A tool inside
            # a live turn must degrade, never propagate; the failure is logged
            # loudly because an implementation that raises violates its port.
            log.exception("roster source raised for %s", self._conversation_id)
            return Roster(available=False)

    def _resolve(self, roster: Roster) -> RoomView:
        if not roster.available:
            return RoomView(available=False)
        people: dict[str, PersonRef] = {}
        unnamed = 0
        for account in roster.members:
            if account & self._self_ids:
                continue                      # Nora is in the room; she is not a member of it
            ref = next(
                (r for value in sorted(account)
                 if (r := self._persons.resolve(self._platform, value)) is not None),
                None,
            )
            if ref is None:
                unnamed += 1
                continue
            # Keyed by person_id: one human holding two accounts in the same
            # group is one person in the room, not two.
            people[ref.person_id] = ref
        return RoomView(
            available=True,
            people=tuple(sorted(people.values(), key=lambda p: (p.name, p.person_id))),
            unnamed=unnamed,
        )
