"""`SocialRouter` — one `SocialProvider` in front of two, dispatching per
`(platform, what)`.

The router exists so **the model never names a backend**. It names
`(platform, what, target)`; the router decides. That is what keeps "OpenCLI
plus the Go package" from becoming two integrations the model has to choose
between — a choice it would make badly and inconsistently, and which would
leak the topology into the prompt. If a doctrine line or a tool docstring ever
says "instameow" or "opencli", this boundary has already failed.

It is the single choke point for EVERY social tool, old and new: `x_read`
and `x_search` (DEC-201), `instagram_read` and `instagram_search` all hand
their reads to this one object, so no tool carries its own copy of a routing
or an authorization rule.

## The rule: X and LinkedIn go to OpenCLI, Instagram goes to the bridge

    X and LinkedIn reads go through the OpenCLI bridge (DEC-201). Every
    Instagram read goes through instagram-bridge's HTTP API.

`EXCEPTION_ROUTES` is the list of rows the instagram-bridge serves: those rows
resolve against it FIRST and never fall through to the default. The admission
rule is that the bridge is the only door for Instagram, so a row joins the list
when its bridge route works END TO END and has no OpenCLI row. `instagram/story`
and `instagram/profile` (`instagram_read`), `instagram/search`
(`instagram_search`) and `instagram/post` (one post or reel from a pasted link)
are on it: OpenCLI serves X and LinkedIn only. `instagram/posts`, a person's
post list, has no mechanism at all.

## No failover, and it is structural rather than a comment

The two backends fail for unrelated reasons with unrelated blast radii — the
OpenCLI path loses its headed browser (taking X with it), the instameow path
loses its session (taking Instagram MESSAGING with it). Both map to R-27's
`unavailable`. **Neither is ever substituted for the other.** If
`instagram/story` cannot be served, the answer is `unavailable`, not a quiet
profile read that answers a different question — a router that improvises is a
router that lies about what it looked at, and the model has no way to tell.

This is enforced by the SHAPE of `read` below, not by discipline: an exception
row is resolved against `EXCEPTION_ROUTES` FIRST, so a missing exception
backend returns `unavailable` and never falls through to the default. Writing
it as `exceptions.get(key, default)` would have been one character shorter and
would have silently routed every story read to OpenCLI the moment the
Instagram backend was not composed — which is exactly the substitution the
design forbids, wearing the costume of a sensible default.

## The router also RESOLVES and AUTHORIZES, and that is why it is worth having

Dispatch alone would not justify a class. The reason subject resolution and
the reach check live here, not in `InstagramSocialClient`, is that this is the single
choke point every social read passes through: below the tool, so the model
cannot bypass it; above the backends, so no backend carries its own copy of an
authorization rule. A second backend would otherwise duplicate the check, and
a duplicated authorization rule is one that diverges.

**Resolution and authorization are two questions with two keys:**

* **Resolution is keyed on `(platform, what, input kind)`** — `SUBJECT_INPUTS`.
  A `named` target is a handle, a post URL or a search query a person in the
  conversation typed (Tier 2, «allowed, never resolved
  implicitly»): it reaches the backend UNTOUCHED and the person index is never
  consulted — asserted as a negative in `tests/unit/test_social_router.py`,
  because "we did not look anything up" is invisible in a status. A `person`
  target is a `person_id` the TOOL resolved from a spoken name inside the room
  (only the tool holds the turn's room); the person index returns every igid
  on file for them. Exactly one is read; none is `nothing_found` («no account
  on file»), several is `ambiguous`, and an unreadable index is
  `unavailable`. A `handle` target is an Instagram username a person
  typed, or the username in a link they pasted: the bridge's EXACT username
  lookup (`InstagramAccountLookup`) turns it into the one igid that holds it,
  or says no account does. Never the person index: `identity.toml`'s username
  is display metadata, never matched. A triple that is not in the
  table is not served.
* **Authorization is keyed on the READ, never the input** — `TIER_ZERO_READS`.
  Only `instagram/story` has a reach check: the person must be Tier 0,
  listed in a group room Nora is in on any network. That is a privacy
  choice, not a claim about Instagram: Nora does not read people's stories
  unprompted or beyond the people the operator listed; whether the
  author sees the view is unmeasured. A profile, a post, and an account search need
  resolution alone. For a `handle` the check runs on the igid the handle
  NAMED, reversed to a person (`person_for_igid`), never re-derived through
  `igids_for`: a friend with two accounts is `ambiguous` by name, and a link
  to one of them says which. An igid that maps to nobody is
  `refused`, and the story is never requested. That person
  must also be on the ASKING room's roster (`room_people`), the scope a
  spoken name resolves in, so a handle cannot reach further than a name
  can and a story link is not an oracle for who is in her other rooms.
* **Not here: the asked-turn rule.** A story, post or reel is opened only in
  a turn a person asked for. `instagram_read` enforces that, because only
  it holds the turn's provenance; a caller reaching this router directly
  bypasses the rule unless it enforces its own (see `read`).

Writing both as tables rather than as an `if what == "story"` is the point:
the absence of a check is spelled and tested, so the next row added has
to say how its subject resolves or it does not get served.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Literal

from ...domain.ports import InstagramAccountLookup, SocialProvider, SocialSubjectIndex
from .readings import READS_WITH_READINGS, TurnLook
from .results import (
    AMBIGUOUS,
    NO_ACCOUNT,
    NO_SUCH_HANDLE,
    REFUSED,
    UNAVAILABLE,
    not_set_up,
)

log = logging.getLogger("nora.social.router")

#: The rows the instagram-bridge serves, frozen: `(platform, what)` pairs that
#: resolve against this list FIRST and never fall through to the OpenCLI
#: default. The name is historical (module docstring); the admission rule is:
#: an Instagram verb joins when its bridge route works end to end, and
#: leaves OpenCLI in the same change. `tests/test_acceptance_rules.py::
#: test_r86_…` derives every social tool's vocabulary against this plus the
#: host allowlist, so a row added here without a tool that constructs it (or
#: the reverse) goes red.
EXCEPTION_ROUTES: frozenset[tuple[str, str]] = frozenset({
    ("instagram", "story"),
    ("instagram", "profile"),
    # `instagram_search`: `GET /accounts?query=`. No Instagram read has two
    # backends.
    ("instagram", "search"),
    # `instagram_read` with a post or reel link: `GET /posts/{code}`.
    ("instagram", "post"),
})

#: The three input kinds a target can arrive as.
#:
#: * `named` — a string a person in the conversation typed: an X handle, a
#:   post URL or id, a search query, the shortcode of a pasted Instagram post
#:   link. Tier 2: allowed, NEVER resolved.
#: * `person` — a `person_id` the tool resolved from a spoken name within the
#:   room. Only the person index may turn it into a network id.
#: * `handle` — an Instagram username a person typed, or the username
#:   segment of a profile or story link they pasted (the tool parses the link
#:   with `nora.domain.instagram_links`, so a link never reaches the router as
#:   a URL). Only the bridge's exact username lookup may turn it into an igid.
TargetKind = Literal["named", "person", "handle"]
TARGET_NAMED: TargetKind = "named"
TARGET_PERSON: TargetKind = "person"
TARGET_HANDLE: TargetKind = "handle"

#: RESOLUTION, keyed on `(platform, what, input kind)`: every triple a tool may
#: send, and so how its target becomes what a backend is handed. TOTAL by
#: test — `tests/unit/test_social_router.py` derives the servable rows from
#: `READ_ONLY_ALLOWLIST | EXCEPTION_ROUTES` and requires each to appear here —
#: and fail-closed by construction: a triple not listed is `unavailable`.
#:
#: `instagram/profile` and `instagram/story` admit `person` and `handle`,
#: never `named`: a `named` string for either would be a handle nobody
#: resolved, and for a story it would skip the reach check. `instagram/search`
#: admits `named` only: its target is free text a person typed, sent as a
#: query and never resolved. `instagram/post` admits `named` only: its
#: target is the shortcode of a link a person pasted, and a post names no
#: person to resolve.
SUBJECT_INPUTS: frozenset[tuple[str, str, str]] = frozenset({
    ("x", "post", TARGET_NAMED),
    ("x", "profile", TARGET_NAMED),
    ("x", "posts", TARGET_NAMED),
    ("x", "search", TARGET_NAMED),
    # LinkedIn (DEC-201): a person's public id or a
    # company's slug from a pasted link, or a handle a person typed, or a
    # search query. All `named`: sent as given, never resolved, and no
    # LinkedIn read has a reach check. What holds LinkedIn's privacy line is
    # the asked-turn rule in the tools, since a read is a profile visit.
    ("linkedin", "profile", TARGET_NAMED),
    ("linkedin", "experience", TARGET_NAMED),
    ("linkedin", "posts", TARGET_NAMED),
    ("linkedin", "company", TARGET_NAMED),
    ("linkedin", "search", TARGET_NAMED),
    ("instagram", "search", TARGET_NAMED),
    ("instagram", "post", TARGET_NAMED),
    ("instagram", "profile", TARGET_PERSON),
    ("instagram", "story", TARGET_PERSON),
    ("instagram", "profile", TARGET_HANDLE),
    ("instagram", "story", TARGET_HANDLE),
})

#: AUTHORIZATION, keyed on the READ alone: the reads with a Tier 0 reach check.
#: Only a story, as a privacy choice:
#: Nora does not open the stories of people the operator has not listed. It
#: is not a claim that the author sees the view: whether the author sees
#: the view is unmeasured. Tier 0 is a
#: property of the PERSON (listed in a group room on any network, never a `*`
#: room, never a DM), answered by `SocialSubjectIndex.is_tier_zero` and by
#: nothing else.
TIER_ZERO_READS: frozenset[tuple[str, str]] = frozenset({
    ("instagram", "story"),
})

#: Every `(platform, what)` a resolution row names — what the router could
#: ever dispatch.
_SERVABLE_ROWS: frozenset[tuple[str, str]] = frozenset(
    (platform, what) for platform, what, _kind in SUBJECT_INPUTS
)

#: The rows that cannot be served without a person index: every row with a
#: `person` input (resolution) and every Tier 0 read (reach). Derived, so the
#: constructor's check below cannot drift from the two tables it guards.
_NEEDS_INDEX: frozenset[tuple[str, str]] = frozenset(
    {(platform, what) for platform, what, kind in SUBJECT_INPUTS if kind == TARGET_PERSON}
    | TIER_ZERO_READS
)

#: The rows a `handle` can reach, which the username lookup resolves. Unlike
#: the index, a missing lookup is not a boot error: it grants nothing, since
#: every `handle` read without it answers `unavailable`, and the `person`
#: path of the same rows keeps working.
_HANDLE_ROWS: frozenset[tuple[str, str]] = frozenset(
    (platform, what) for platform, what, kind in SUBJECT_INPUTS if kind == TARGET_HANDLE
)


class SocialRouter(SocialProvider):
    """Dispatch per `(platform, what)`; itself a `SocialProvider`.

    Composed at the composition root in the position `OpenCliBridgeClient`
    held alone, so `nora.agent.registry` is unchanged and unaware — the whole
    point of the port. `read` accepts one argument the port does not carry,
    `target_kind`, with the port's own meaning as its default (`named`): the
    input kind is consumed HERE, by resolution, and a backend only ever sees
    the resolved target, so the backends' port has nothing to learn from it.

    `default` and each exception backend are OPTIONAL, because "not wired" is
    an ordinary state for both and they switch on independently: `[social]
    enabled` gates the OpenCLI path, `[social.instagram] enabled` the bridge
    path. An absent backend answers `unavailable` for ITS OWN rows only — it
    never widens or narrows the other's reach.

    `subjects` is OPTIONAL and yet REQUIRED, which is not a contradiction: it
    may be absent, but not while a backend is wired for a row that needs it
    (a `person` input, or a Tier 0 read). That pairing is checked at
    CONSTRUCTION for the same reason the exception list is — a router that
    serves an unauthorized read on one rare verb is discovered by a wrong
    answer in a room, and «refuses to start» is the cheaper failure.
    """

    def __init__(
        self,
        *,
        default: SocialProvider | None,
        exceptions: Mapping[tuple[str, str], SocialProvider] | None = None,
        subjects: SocialSubjectIndex | None = None,
        accounts: InstagramAccountLookup | None = None,
    ) -> None:
        unknown = sorted(set(exceptions or {}) - EXCEPTION_ROUTES)
        if unknown:
            # A composition root cannot invent an exception row. The list is a
            # design decision (module docstring); letting a caller pass an
            # arbitrary pair would move the routing rule from code somebody
            # reviews to a wiring line somebody edits, which is how the "two
            # parallel integrations" failure actually arrives. Raising at
            # CONSTRUCTION, not at read time: this is a boot-time wiring
            # error, and a mis-wired router that only misbehaves on one rare
            # verb is worse than one that refuses to start.
            raise ValueError(
                f"{unknown} is not on the social exception list "
                f"{sorted(EXCEPTION_ROUTES)} — an Instagram verb moves to the "
                "bridge only when its route works end to end "
                "(design/instagram-adoption/15)"
            )
        self._default = default
        self._exceptions: Mapping[tuple[str, str], SocialProvider] = (
            MappingProxyType(dict(exceptions or {}))
        )
        self._subjects = subjects
        self._accounts = accounts
        if accounts is None and any(self._provider_for(row) for row in _HANDLE_ROWS):
            # Said once at boot, like an unwired backend: a handle or a link
            # to a profile or a story will answer `unavailable`.
            log.info(
                "social router: no username lookup composed; handles and links "
                "are unavailable",
            )
        # Which rows have no backend is a CONFIGURATION fact, fixed here, so it
        # is said once at boot and not as a warning on every call — a flag
        # left off would otherwise fill the log with one line per question.
        unwired = sorted(
            row for row in (_SERVABLE_ROWS | EXCEPTION_ROUTES)
            if self._provider_for(row) is None
        )
        if unwired:
            log.info("social router: no backend composed for %s", unwired)
        if subjects is None:
            # A person-keyed row is resolved, and a Tier 0 row authorized, by
            # the person index and by nothing else. Composing a backend that
            # serves one WITHOUT an index would leave the check to a fallback
            # — and the only two fallbacks are «allow», which is an
            # unauthorized read, and «deny», which is a boundary reported for
            # a wiring gap. Neither is a thing to discover in production, so
            # it is a boot error instead.
            ungoverned = sorted(
                row for row in _NEEDS_INDEX if self._provider_for(row) is not None
            )
            if ungoverned:
                raise ValueError(
                    f"{ungoverned} needs the person index (a person input, or "
                    "a Tier 0 reach check) and a backend for it is wired, but "
                    "no SocialSubjectIndex was given — the person index is the "
                    "only thing that may resolve a subject or decide reach "
                    "(design/instagram-adoption/07, /18)"
                )

    def _provider_for(self, row: tuple[str, str]) -> SocialProvider | None:
        """The ONE backend for a row, or None. Exception rows resolve against
        `EXCEPTION_ROUTES` and never fall through to `default` — that is the
        no-failover rule, expressed once so the constructor's check and `read`
        cannot disagree about which backend a row would reach."""
        if row in EXCEPTION_ROUTES:
            return self._exceptions.get(row)
        return self._default

    async def read(
        self, *, platform: str, what: str, target: str, limit: int,
        target_kind: TargetKind = TARGET_NAMED,
        room_people: frozenset[str] | None = None,
        look: TurnLook | None = None,
    ) -> dict[str, Any]:
        """Route one read. NEVER raises (R-5), NEVER fails over.

        `look` is the caller's own `media_fetch` and the turn's slots, with
        which a post or a story comes back with what each item shows. It is
        forwarded for `READS_WITH_READINGS` only, the post
        and story rows, and never to any other backend or row, whatever the
        caller passes: a profile and a search read no media. It changes
        nothing about picking, resolution or reach, which all happen first.

        `room_people` is the person ids on the asking room's roster, the
        scope a spoken name resolves in, or `None` when that roster could not
        be read. Only a Tier 0 read by `handle` uses it (a story named by a
        typed handle or a link): its person must be on this roster too, or it
        is `refused` (the operator's conservative default). A
        `person` read is already room-scoped, because the tool resolved the
        name in the room.

        **What this router does NOT hold: the asked-turn rule.** A story, a
        post or a reel is opened only in a turn a person asked for, never in
        a fire turn or a Dreamer pass. That rule lives in `instagram_read`, which holds the
        turn's provenance (`fire_turn`, `dream_adaptor`); this router is
        handed no provenance and cannot tell a background turn from an asked
        one. A future caller that reaches this router directly bypasses that
        rule unless it enforces its own.

        Three steps, in this order: **pick, resolve-and-authorize, dispatch.**

        Picking first looks like the wrong order for an authorization check
        and is not: choosing a backend touches nothing and reaches nobody, so
        nothing has happened yet when the reach is decided. What it buys is
        that an UNWIRED backend still answers `unavailable` — «I couldn't
        check» — rather than a boundary or a missing-index sentence about a
        read that was never possible in the first place. The property that
        matters is that authorization happens before DISPATCH, and it does.

        Beyond `unavailable` for an unwired backend and the two answers it
        owns outright (`ambiguous`, `refused`), the router adds no states:
        whatever the chosen backend answers is returned VERBATIM, because the
        router's job is to pick and to authorize, not to interpret. Re-mapping
        a backend's status here would put a second opinion about what
        `nothing_found` means in front of the one that performed the read.
        """
        provider = self._provider_for((platform, what))
        if provider is None:
            # Not wired. For an exception row this is also the no-failover
            # rule doing its work: the request does NOT fall through to
            # `default`, which is a backend that must not serve it, so sending
            # it there would produce a refusal that reads like an answer about
            # the subject.
            #
            # A STABLE answer, and no log line: this is configuration (the
            # backend's flag is off), said once at construction. «I couldn't
            # read that just now» would invite a retry that cannot help.
            return not_set_up(platform)
        authorized = await self._authorize(
            platform=platform, what=what, target=target, target_kind=target_kind,
            room_people=room_people,
        )
        if isinstance(authorized, dict):
            return authorized
        # The one argument the port does not carry, handed on only to the
        # rows that read media, so no other backend ever sees it.
        reads_media = look is not None and (platform, what) in READS_WITH_READINGS
        media = {"look": look} if reads_media else {}
        try:
            return await provider.read(
                platform=platform, what=what, target=authorized, limit=limit, **media,
            )
        except Exception:
            # Every implementation is documented never to raise; this is the
            # belt for those braces. It lands on `unavailable` and NOT on the
            # other backend — a raising backend is exactly the case where
            # "try the other one" is most tempting and most wrong.
            log.exception("social backend raised")
            return dict(UNAVAILABLE)

    async def _authorize(
        self, *, platform: str, what: str, target: str, target_kind: str,
        room_people: frozenset[str] | None = None,
    ) -> str | dict[str, Any]:
        """Resolution, then the reach check, before dispatch. Returns the
        `target` the backend should be given, or the PAYLOAD to answer
        instead.

        Two return types rather than a bool plus an out-parameter because the
        two outcomes carry different values: an allowed `person` read
        continues with a DIFFERENT target than it arrived with — the person
        index turned a `person_id` into a network id, which is the whole point
        of the resolution step — and a blocked one carries the sentence Nora
        says.

        Fail-closed everywhere, including on the two paths that are not a
        decision at all: an unadmitted triple and an index that is missing
        under a `person` row are both `unavailable` («I couldn't check»),
        which is true of a wiring gap, and never `refused`, which would report
        a developer's omission as a boundary the operator set.
        """
        if (platform, what, target_kind) not in SUBJECT_INPUTS:
            # No resolution row. A triple reaching a backend without one is a
            # vocabulary that grew without a resolution decision — the exact
            # drift the table exists to make impossible — so it is not
            # served. `unavailable`, and logged at ERROR because somebody has
            # to fix it.
            log.error(
                "social router: no resolution for %s/%s by %s",
                platform, what, target_kind,
            )
            return dict(UNAVAILABLE)
        if target_kind == TARGET_HANDLE:
            return await self._resolve_handle(
                platform=platform, what=what, username=target, room_people=room_people,
            )
        if target_kind == TARGET_NAMED:
            # Allowed as-is, and the index is deliberately NOT consulted.
            # «Never resolved implicitly» is the clause that code
            # below the tool can actually hold: whether a human typed this
            # handle is a fact about the conversation, but whether Nora
            # invented it is a fact about this line. No Tier 0 read admits a
            # `named` input (`SUBJECT_INPUTS`), so no reach check is skipped.
            return target
        if self._subjects is None:
            # Unreachable through the constructor, which refuses this pairing
            # at boot. Kept because it is the fail-closed half of that check
            # and costs one branch: a router reaching here with no index must
            # not read.
            log.error("social router: a person-keyed row with no subject index")
            return dict(UNAVAILABLE)
        try:
            # RESOLVE before AUTHORIZE: a tier is a property of a person, so
            # there is nothing to authorize until the subject is one account.
            # Four answers, and each is a different sentence for the model:
            #   * `None` — the index could not be read: `unavailable`;
            #   * no igid on file: `nothing_found`, saying so (NO_ACCOUNT);
            #   * several: `ambiguous`, and NEVER a pick;
            #   * exactly one: that is the account.
            igids = await self._subjects.igids_for(person_id=target)
            if igids is None:
                return dict(UNAVAILABLE)
            if not igids:
                return dict(NO_ACCOUNT)
            if len(igids) > 1:
                return dict(AMBIGUOUS)
            (igid,) = igids
            # REACH, keyed on the read and never on the input kind: a story
            # is opened only for a listed person (a privacy choice), a
            # profile needs no reach check.
            if (platform, what) in TIER_ZERO_READS and not await self._subjects.is_tier_zero(
                person_id=target,
            ):
                return dict(REFUSED)
        except Exception:
            # The port documents both methods as never raising and as failing
            # CLOSED; this is the belt for those braces, and it lands on
            # `unavailable` rather than on a read.
            log.exception("social subject index raised")
            return dict(UNAVAILABLE)
        return igid

    async def _resolve_handle(
        self, *, platform: str, what: str, username: str,
        room_people: frozenset[str] | None,
    ) -> str | dict[str, Any]:
        """A typed handle or a link's username → the igid to read, or the
        payload to answer instead.

        RESOLVE, then REACH, then return, and nothing is dispatched on any
        path that returns a payload:

        * the bridge's exact lookup: `unavailable` is `unavailable`; `none` is
          `nothing_found` («Instagram has no account with that username»);
        * a Tier 0 read (a story) then reverses the igid the handle NAMED to a
          person (`person_for_igid`) and asks `is_tier_zero` of THAT person.
          It never goes through `igids_for`: a friend with two accounts is
          `ambiguous` by name, but a handle or a link names one of them, and
          re-deriving would lose which. An igid that maps to nobody (a
          stranger, or an account nobody put in `identity.toml`) is
          `refused`, and so is a person listed in no room;
        * and that person must ALSO be on the asking room's roster
          (`room_people`), the scope a spoken name resolves in. Otherwise
          `refused`, with the SAME payload as every other story refusal, so
          nothing tells a stranger apart from a friend in another room. Without
          that check a pasted story link would be a
          membership oracle: «read» for anyone listed in any of her rooms,
          «refused» for the rest. A roster that could not be read (`None`)
          is `unavailable`, and the lookup is not even made.

        The lookup runs BEFORE the reach decision because the reach decision
        needs the igid; it is a search, invisible to the account it finds.
        The story request itself is made only after.
        """
        if self._accounts is None:
            # Said once at construction. «I couldn't check», never a boundary.
            return dict(UNAVAILABLE)
        if (platform, what) in TIER_ZERO_READS and room_people is None:
            # No roster to scope the reach check to: «I couldn't check», and
            # nothing is looked up.
            return dict(UNAVAILABLE)
        try:
            found = await self._accounts.igid_for_username(username=username)
        except Exception:
            # The port never raises; this is the belt for those braces.
            log.exception("instagram username lookup raised")
            return dict(UNAVAILABLE)
        if found.status == "none":
            return dict(NO_SUCH_HANDLE)
        if found.status != "found" or not found.igid:
            return dict(UNAVAILABLE)
        igid = found.igid
        if (platform, what) not in TIER_ZERO_READS:
            return igid
        if self._subjects is None:
            # Unreachable through the constructor (a story row needs the
            # index); fail-closed if it is ever reached.
            log.error("social router: a Tier 0 handle read with no subject index")
            return dict(UNAVAILABLE)
        try:
            person = await self._subjects.person_for_igid(igid=igid)
            if person is None or person not in (room_people or frozenset()):
                return dict(REFUSED)
            if not await self._subjects.is_tier_zero(person_id=person):
                return dict(REFUSED)
        except Exception:
            log.exception("social subject index raised")
            return dict(UNAVAILABLE)
        return igid
