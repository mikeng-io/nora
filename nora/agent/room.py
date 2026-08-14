"""The room a tag happened in, as PEOPLE — the scope every room-scoped tool resolves within.

This module exists because of one live incident, recorded in design/12 §3 and in
`nora.engagement.pipeline`'s own comment: the prefill was given the whole of
`identity.toml` as a `<roster>` block, and asked who was in the group Nora
listed five people who had never been members. The roster carried no phone
number and no ACI, so R-18 (`nora.agent.egress`) saw nothing to block — by the
time text exists the disclosure is already authorised. **Scope has to be
enforced where the answer is ASSEMBLED.**

So the rule this module implements is *resolve WITHIN scope*, never *resolve
then filter*:

* `RoomView.people` is built from the platform's own membership list for THIS
  conversation, intersected with `identity.toml` — never from
  `Persons.index`, which is Nora's address book across every room she is in.
* `RoomView.find` builds its name/alias index from `people` alone. A person who
  is in `identity.toml` but not in this room is not "found and rejected", they
  are **not in the index at all** — so they are indistinguishable from a person
  who does not exist. Resolve-then-filter would leak membership through the
  difference between "no such person" and "not here"; this cannot.

That is also why alias matching is safe here at all. design/10 §1.3 forbids an
alias ever resolving a SENDER — a nickname is operator-authored and a display
name is settable by anyone, so matching one to decide *who sent this* hands
identity to whoever can type it. Resolving a lookup TARGET is a different
operation: the answer is bounded before any alias is consulted, and the worst a
wrong match can do is name the wrong person who is already in the room.
`nora.persons.Persons.index` is untouched, and
`tests/test_persons.py::test_an_alias_never_resolves_a_sender` still holds.

**Nora is not one of the people.** Her own account is in the group's member
list, resolves to nobody in `identity.toml`, and would otherwise be reported as
"one account I don't have a name for" — a stranger in the room, who is her.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..domain.models import PersonRef
from ..domain.ports import Roster, RosterSource
from ..persons import Persons

log = logging.getLogger("nora.agent.room")

# Punctuation a name may arrive wrapped in when the model lifts it out of a
# Cantonese message — 「阿豹」 is how the name appears in the transcript, and
# asking the model to strip its own quotes is a rule it will sometimes forget.
# RUF001 is suppressed because the ambiguous characters ARE the data: this is a
# set of quote marks to strip, and "did you mean the ASCII one?" is answered by
# the ASCII ones being in the same string. Replacing them would change which
# inputs the function handles.
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

    Extracted from `RoomView.find` so the same matching rule can be applied to
    a set that is NOT a room — `whereis` resolves its target against the
    location-sharing circle (R-47), and two different matching rules for "who
    do they mean" is exactly the drift `_names_of`'s own docstring warns about.
    """
    wanted = _key(name)
    if not wanted:
        return ()
    return tuple(p for p in people if wanted in _names_of(p))


def _names_of(person: PersonRef) -> frozenset[str]:
    """Every string this person may be called by, normalised. Scoped by the
    caller — `RoomView.people` for room lookups, a circle's people for
    `whereis` (R-47)."""
    full = " ".join(p for p in (person.first_name, person.last_name) if p)
    candidates = [person.name, person.first_name, person.last_name, full, *person.aliases]
    return frozenset(key for c in candidates if (key := _key(c)))


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
    ) -> None:
        self._platform = platform
        self._conversation_id = conversation_id
        self._roster_source = roster_source
        self._persons = persons
        self._tagger = tagger
        self._self_ids = frozenset(self_ids)
        self._view: RoomView | None = None

    @property
    def platform(self) -> str:
        """Which platform this conversation is on.

        Read by `remind`, which stores `(platform, conversation_id,
        conversation_type)` because every conversation key in this codebase is a
        tuple: a row holding `conversation_id` alone can build neither a
        `ConversationTarget`, nor the `ConversationSlots` key, nor a
        `Policy.is_allowed` call. It comes from here rather than from a new
        `build_tool_specs` parameter because both constructors of this object —
        the tag pipeline and the fire path — already pass the authoritative
        value, and a second parameter carrying the same fact is how the two
        drift.
        """
        return self._platform

    @property
    def tagger(self) -> PersonRef | None:
        """Who this turn is FOR, or None when the sender is unmapped (§12).

        `scope()` surfaces the tagger only in its degraded branch, and a
        `None` check is not what the caller needs anyway: `remind` writes
        `tagger.person_id` into `reminders.requester_person_id`, which is the id
        the fire-time policy re-check then runs against. So this is a reader,
        not a predicate — «is there a mapped requester» and «who is it» are one
        question here, and answering the first without the second would have
        the tool invent an owner for the row.

        Read-only on purpose: the tagger is fixed for the life of the turn, and
        this object is constructed fresh per turn.
        """
        return self._tagger

    async def members(self) -> RoomView:
        """The room as the platform reports it. `available=False` if it could not be read."""
        if self._view is None:
            self._view = self._resolve(await self._read())
        return self._view

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
            # No roster source wired at all — the offline and eval
            # compositions, which have no daemon to ask. Reported as
            # unavailable rather than empty for the same reason a dead daemon
            # is: a composition that silently answered "nobody is here" would
            # make a wiring mistake look like a quiet group (R-5).
            return Roster(available=False)
        try:
            return await source.roster(conversation_id=self._conversation_id)
        except Exception:
            # `RosterSource.roster` is documented never to raise. This is the
            # belt for that braces: a tool inside a live turn must degrade,
            # never propagate — but the failure is logged loudly because an
            # implementation that raises here is violating its port.
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
            # group (DEC-14's baseline case, and the live `Ridgeline` group's
            # actual shape) is one person in the room, not two.
            people[ref.person_id] = ref
        return RoomView(
            available=True,
            people=tuple(sorted(people.values(), key=lambda p: (p.name, p.person_id))),
            unnamed=unnamed,
        )
