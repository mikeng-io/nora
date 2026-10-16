"""`InstagramRoster` — who is in one Instagram group, over the bridge's
`GET /threads/{thread_fbid}/members`.

A 200 is `{room_id, title, is_group, members: [{fbid, igid, username,
full_name}]}` with no `ok` field: the status says the read
succeeded. Only `members[].fbid` is read. Any other status is the bridge's
error envelope and reads as `available=False`; 409 gets its own log line. The
bodies are pinned by `tests/fixtures/instagram_bridge/members_*`, which the
bridge's handlers generate.

`nora.domain.ports.RosterSource`'s fourth implementation. Closer to
`nora.platform.discord.roster.DiscordRoster` than to Signal's, because the read
is an HTTP call against a service that can be down rather than a wrapper over a
directory module that already exists — but it holds the same one absolute rule
both of them do, stated in the port's own docstring: **`roster()` must never
raise.** The caller is a room-scoped tool running inside a live turn, so an
unreachable bridge has to become `Roster(available=False)` — «I could not
look» — and never an empty room, which reads as «there is nobody here» (R-5).

Without a roster, three things fail the same way: `group_members` answers
`unavailable`, `where_is` cannot establish the room and refuses, and every
outbound @-mention resolves to nobody so Nora writes «@Name» as plain text with
no ping and nothing logged.

## Only the fbid goes into `Roster.members`, and that is not an oversight

The bridge returns BOTH ids per member — `fbid`
(`interop_messaging_user_fbid`, the messaging identity) and `igid` (the public
Instagram id) — because they are different spaces and different consumers need
different ones. Exactly one of them belongs in `Roster.members`:

* **A mention can only carry the fbid.** `nora.agent.outbound._recipient`
  intersects `identity.toml`'s ids with the ones the ROSTER just reported and
  hands the result to `_preferred_recipient`, whose Instagram branch accepts a
  bare digit string — which is what BOTH id kinds are. It accepts any bare
  digit string BY DESIGN: an Instagram id is never validated by shape, because
  fbids vary in length (15 and 17 digits measured) and no length or prefix rule
  separates real members from each other.
  So an igid sitting in the same frozenset
  is not inert: it is a coin toss on addressing a mention with a number no
  mention can carry, which is the silent no-ping failure this module exists to
  end, re-introduced one layer down.
* **The ingest path already binds on the fbid.** A message's `sender_id` is the
  fbid (`nora.platform.instagram.parser`), so a person whose `identity.toml`
  entry carries an igid and no fbid is unmapped on every message they send
  anyway. Admitting the igid here would not rescue them — it would make the
  roster disagree with the sender path about who is named, which is worse than
  either answer alone.

The igid is still on the wire, named, for the social surface and for an
operator reading a log line. It simply is not a membership token.

## Nora is not in her own roster, by the wire

`Users[]` excludes the viewer, and the bridge filters self as well, so this
module gets a
list Nora is already absent from. `nora.agent.room.Room` would have excluded
her anyway via `self_ids`; both are correct and neither is redundant, because
one of them is a library's behaviour and the other is Nora's rule.

## An unmapped member is `unnamed`, and `where_is` refusing over it is CORRECT

`RoomView` counts member accounts that resolve to no person as `unnamed`, and
`Whereis` fails closed on `unnamed > 0` in a room that is not location-trusted:
an account nobody can name cannot be checked against the
location circle, so disclosing a fix in that room would be disclosing to a
stranger. A member missing from `identity.toml` therefore makes location refuse
— that is the guard working, and the remedy is to map the account, never to
loosen this module.

**But read the room's `location` flag before relying on that.** DEC-79: the
flag waives the `unnamed > 0` refusal outright, and it is ON in the Instagram
room. There, an unnamed account does not
refuse: the answer is disclosed and the waiver is counted on
`nora_presence_disclosure_waived_total`. So in THAT room this roster's
completeness is not what stops a disclosure — only a roster that cannot be
read at all is, and that asymmetry is deliberate (`whereis.py`: an unnamed
account is a measurement the operator may overrule, an unreadable roster is no
measurement to overrule).

The read stays fail-closed regardless. A smaller margin is a reason to keep
this module strict, not a licence to relax it.
"""

from __future__ import annotations

import logging
import urllib.parse

import aiohttp

from ...domain.ports import Roster
from ...infra.trace_propagation import propagation_headers
from .ids import fbid_form

log = logging.getLogger("nora.instagram.roster")

# Matches `DiscordRoster`'s and `SignalRoster`'s budget, for their reason: two
# failed reads still fit inside a single model round trip, which is the unit a
# wedged dependency has to stay cheap relative to. Deliberately LOWER than the
# 10 s `send`/`react` use — nobody is waiting on a reaction, and somebody is
# waiting on this.
DEFAULT_TIMEOUT_SECONDS = 4.0


class InstagramRoster:
    """Satisfies `nora.domain.ports.RosterSource` for Instagram."""

    platform = "instagram"

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def roster(self, *, conversation_id: str) -> Roster:
        """The members of the thread `conversation_id`, or `available=False`.

        `conversation_id` is the SHORT thread id verbatim — the only thread id
        the transport contract exposes, the one `policy.toml` matches on, and
        the one every other Instagram route takes
        (`nora.platform.instagram.ids.conversation_id_for`). The bridge owns
        the short↔long translation so exactly one piece of code can step in
        that trap.

        Every failure below is `available=False`. None of them is an empty
        roster, because a room Nora could not look into and a room with nobody
        in it must never produce the same value.
        """
        # `.strip()` on a non-str raises, and this line is OUTSIDE the try
        # below — so a caller handing a non-string conversation_id would take
        # `roster()` out through an AttributeError, which the port forbids
        # outright ("It must never raise — the caller is a tool inside a live
        # turn"). Both call sites happen to wrap it today; that is their
        # belt, not this module's braces.
        room_id = conversation_id.strip() if isinstance(conversation_id, str) else ""
        if not room_id:
            # A wiring fault, not an empty room, and it must not reach the
            # network. An empty
            # id would build `/threads//members`, which the bridge's router
            # CLEANS and redirects — so aiohttp would follow it and read a 404
            # for a different path. That is a confusing failure to debug and an
            # even worse one to mistake for "this thread has no members".
            log.warning("empty instagram conversation_id; roster unavailable")
            return Roster(available=False)

        try:
            async with (
                aiohttp.ClientSession(timeout=self._timeout) as session,
                session.get(
                    f"{self._base_url}/threads/{urllib.parse.quote(room_id, safe='')}/members",
                    headers=propagation_headers(),
                ) as resp,
            ):
                if resp.status == 409:
                    # The session is checkpointed or logged out. Its own log
                    # line because it is the one failure a human can fix, and
                    # the one an operator will be looking for when every
                    # room-scoped tool has gone quiet on Instagram at once.
                    log.error(
                        "roster read refused for room %s: the instagram session is "
                        "checkpointed or logged out — a human has to clear it, and "
                        "every room-scoped tool stays unavailable until they do",
                        room_id,
                    )
                    return Roster(available=False)
                if resp.status != 200:
                    log.warning(
                        "roster read for room %s returned HTTP %s", room_id, resp.status
                    )
                    return Roster(available=False)
                body = await resp.json()
        except Exception:
            # Never raises — see the module docstring. `aiohttp` raises for an
            # unreachable bridge, a timeout AND a body that is not JSON, and
            # all three are the same answer here.
            log.warning("roster read failed for room %s", room_id, exc_info=True)
            return Roster(available=False)

        if not isinstance(body, dict):
            log.warning("roster read for room %s returned a non-object body", room_id)
            return Roster(available=False)
        rows = body.get("members")
        if not isinstance(rows, list):
            log.warning(
                "roster read for room %s carried no members list", room_id
            )
            return Roster(available=False)

        members: list[frozenset[str]] = []
        for row in rows:
            fbid = fbid_form(row.get("fbid")) if isinstance(row, dict) else ""
            if not fbid:
                # UNDER-REPORTING is the one direction that is unsafe — a room
                # short by one member is a room the disclosure gate approves
                # without ever seeing the account it should have refused over.
                # The bridge already refuses the whole read rather than send a
                # short list, so reaching here means the two disagree about the
                # shape; refusing again is the only answer that cannot be
                # acted on as a complete room.
                log.error(
                    "roster read for room %s carried a member with no fbid — "
                    "refusing the whole roster rather than under-report the room",
                    room_id,
                )
                return Roster(available=False)
            # ONE id form per member and it is the messaging fbid. See the
            # module docstring for why the igid the bridge also sends is
            # deliberately not in here.
            members.append(frozenset({fbid}))

        if not members:
            # A group Nora is in holds at least one account other than her own,
            # so an empty list is the read having nothing to say, not the room
            # having emptied. The bridge refuses this too; answering
            # `available=True, members=()` here would let «there is nobody
            # here» through on a shape that means «I could not look».
            log.warning(
                "roster read for room %s returned no members at all; unavailable",
                room_id,
            )
            return Roster(available=False)
        return Roster(available=True, members=tuple(members))
