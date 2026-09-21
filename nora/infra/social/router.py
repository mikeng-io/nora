"""`SocialRouter` — one `SocialProvider` in front of two, dispatching per
`(platform, what)` (design/instagram-adoption/08-social-adapter-architecture).

The router exists so **the model never names a backend**. It names
`(platform, what, target)`; the router decides. That is what keeps "OpenCLI
plus the Go package" from becoming two integrations the model has to choose
between — a choice it would make badly and inconsistently, and which would
leak the topology into the prompt. If a doctrine line or a tool docstring ever
says "instameow" or "opencli", this boundary has already failed.

## The rule: the default is the RULE, the table is the EXCEPTION

    Social reads go through the OpenCLI bridge. The Go package does messaging.

`EXCEPTION_ROUTES` is a short, justified exception list, NOT a per-verb
preference. A row belongs on it only when all three hold:

1. OpenCLI **structurally cannot** serve it — not "serves it worse", not "is
   flaky today", but has no such command;
2. a named use case actually wants the capability;
3. the exception is recorded in the design node with the measurement that
   established (1).

Today the list has exactly one row, and both halves of the proof are
recorded: OpenCLI 1.8.7's `instagram` surface marks `story` as `[write]` — it
POSTS a story — and has no story read anywhere, while instameow's `FetchReel`
is the only path that exists. Two things this deliberately EXCLUDES although
instameow can do them: `friendship_status` (real, OpenCLI has no equivalent,
but no use case has asked — fails test 2) and `latest_reel_media` as a cheap
"is a story up" probe (an optimisation of a row that is already routed — fails
test 1: it is not a capability, it is a faster way to do one).

The rule matters more than the row. A router whose entries are chosen by
"which backend is better here" drifts into two parallel integrations within a
year, because every verb has a better backend on some axis. A router whose
entries each carry a proof of impossibility cannot drift: the list SHRINKS if
OpenCLI grows a verb, and grows only against evidence.

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

## The router also AUTHORIZES, and that is why it is worth having

Dispatch alone would not justify a class. The reason subject resolution and
the tier check live here — moved out of `InstagramSocialClient` on 2026-09-22
(design/instagram-adoption/10 §"Who owns what") — is that this is the single
choke point every social read passes through: below the tool, so the model
cannot bypass it; above the backends, so no backend carries its own copy of an
authorization rule. A second backend would otherwise duplicate the tier check,
and a duplicated authorization rule is one that diverges.

The consequence that actually mattered is not the story row. It is that a
check inside the Instagram client **structurally could not reach the other
seven rows** (`x` post/profile/posts/search, `instagram` profile/posts/search),
which therefore had NO subject authorization at all while node 07 says the
ladder governs *all* social reads. `SUBJECT_TIERS` closes that by being TOTAL:
every pair the tool can construct names its tier here, and a pair that does
not is `unavailable` rather than served.

## The seven default rows are Tier 2, and "allowed" is a decision, not a gap

Node 07's ladder classifies a subject by **how its identity was obtained**:

* **Tier 0 — the room.** `instagram/story`'s `target` is a `person_id`, turned
  into an igid by the person index. The room is the grant, so the check is
  «is this person in a room Nora is in», answered by `SocialSubjectIndex`.
* **Tier 2 — the named.** The other seven take a handle, a post URL or a
  search query *typed by a person in the conversation*. Node 07: «allowed,
  never resolved implicitly.»

So Tier 2 is allowed AS IS — and the half that is enforceable here is the
second clause, not the first. Whether a human typed the handle is a fact about
the conversation that no code below the tool can see; whether Nora RESOLVED
one herself is a fact about this function, and it is the one the ladder
actually forbids («the model may not invent a subject»). A Tier 2 row
therefore passes its `target` through UNTOUCHED and the subject index is never
consulted for it — asserted as a negative in
`tests/unit/test_social_router.py`, because "we did not look anything up" is
invisible in a status.

Writing that as a table entry rather than as an `if what == "story"` is the
point: the absence of a check is now spelled, dated and tested, so the next
row added has to say which tier it is in or it does not get served.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from ...domain.ports import SocialProvider, SocialSubjectIndex
from .results import AMBIGUOUS, REFUSED, UNAVAILABLE

log = logging.getLogger("nora.social.router")

#: The exception list, frozen: `(platform, what)` pairs the DEFAULT backend
#: structurally cannot serve. This constant is the single source of truth for
#: the routing rule — `tests/test_acceptance_rules.py::test_r86_…` derives the
#: tool's vocabulary against it plus the host allowlist, so a row added here
#: without a matching vocabulary row (or vice versa) goes red rather than
#: shipping a pair that resolves to no backend at all.
EXCEPTION_ROUTES: frozenset[tuple[str, str]] = frozenset({
    ("instagram", "story"),
})

#: Tier 0 — «the room». The subject is a PERSON Nora already knows, named by
#: `person_id`, and the network id behind them is a fact in the person index.
#: The room is the grant, so the authorization question is «is this person in
#: a room I am in» and `SocialSubjectIndex` is the only thing allowed to
#: answer it (design/instagram-adoption/07 §tiers).
TIER_ROOM = 0

#: Tier 2 — «the named». The subject is a handle, a post URL or a search query
#: that a person in the conversation typed. Allowed as-is; the ladder's
#: clause that bites down here is "never resolved implicitly", which means the
#: `target` rides through UNTOUCHED and the person index is never asked about
#: it. Nora may look at a handle someone gave her; she may not turn a NAME
#: into a handle herself.
TIER_NAMED = 2

#: Every `(platform, what)` the tool can construct and a backend can serve,
#: against the tier its SUBJECT sits in. TOTAL by construction and by test:
#: `tests/unit/test_social_router.py` derives its key set from
#: `READ_ONLY_ALLOWLIST | EXCEPTION_ROUTES`, so a row that reaches a backend
#: without a tier goes red rather than being read unauthorized.
#:
#: This table is the whole reason the check moved up here. Living in
#: `InstagramSocialClient` it could only ever govern one row; the other seven
#: had no subject authorization at all, which is not "Tier 2 is allowed" — it
#: is an absence that happens to look like one.
SUBJECT_TIERS: Mapping[tuple[str, str], int] = MappingProxyType({
    ("instagram", "story"): TIER_ROOM,
    ("x", "post"): TIER_NAMED,
    ("x", "profile"): TIER_NAMED,
    ("x", "posts"): TIER_NAMED,
    ("x", "search"): TIER_NAMED,
    ("instagram", "profile"): TIER_NAMED,
    ("instagram", "posts"): TIER_NAMED,
    ("instagram", "search"): TIER_NAMED,
})


class SocialRouter(SocialProvider):
    """Dispatch per `(platform, what)`; itself a `SocialProvider`.

    Composed at the composition root in the position `OpenCliBridgeClient`
    held alone, so `nora.agent.registry` and `social_fetch` are unchanged and
    unaware — the whole point of the port.

    `default` and each exception backend are OPTIONAL, because "not wired" is
    an ordinary state for both and they switch on independently: `[social]
    enabled` gates the OpenCLI path, and the Instagram path additionally needs
    a bridge and a subject index that the platform-adapter milestone has not
    landed yet. An absent backend answers `unavailable` for ITS OWN rows only
    — it never widens or narrows the other's reach.

    `subjects` is OPTIONAL and yet REQUIRED, which is not a contradiction: it
    may be absent, but not while a backend that would serve a Tier 0 row is
    wired. That pairing is checked at CONSTRUCTION for the same reason the
    exception list is — a router that serves an unauthorized read on one rare
    verb is discovered by a wrong answer in a room, and «refuses to start» is
    the cheaper failure. Today nothing wires the Instagram backend, so nothing
    needs an index; the moment something does, it cannot be composed without
    one.
    """

    def __init__(
        self,
        *,
        default: SocialProvider | None,
        exceptions: Mapping[tuple[str, str], SocialProvider] | None = None,
        subjects: SocialSubjectIndex | None = None,
    ) -> None:
        unknown = sorted(set(exceptions or {}) - EXCEPTION_ROUTES)
        if unknown:
            # A composition root cannot invent an exception row. The list is a
            # design artifact carrying a proof of impossibility per entry
            # (module docstring); letting a caller pass an arbitrary pair would
            # move the routing rule from code somebody reviews to a wiring line
            # somebody edits, which is how the "two parallel integrations"
            # failure actually arrives. Raising at CONSTRUCTION, not at read
            # time: this is a boot-time wiring error, and a mis-wired router
            # that only misbehaves on one rare verb is worse than one that
            # refuses to start.
            raise ValueError(
                f"{unknown} is not on the social exception list "
                f"{sorted(EXCEPTION_ROUTES)} — a verb moves to a second "
                "backend only against a recorded measurement that the default "
                "cannot serve it (design/instagram-adoption/08)"
            )
        self._default = default
        self._exceptions: Mapping[tuple[str, str], SocialProvider] = (
            MappingProxyType(dict(exceptions or {}))
        )
        self._subjects = subjects
        if subjects is None:
            # A Tier 0 row is authorized by the person index and by nothing
            # else. Composing a backend that serves one WITHOUT an index would
            # leave the check to a fallback — and the only two fallbacks are
            # «allow», which is an unauthorized read, and «deny», which is a
            # boundary reported for a wiring gap. Neither is a thing to
            # discover in production, so it is a boot error instead.
            ungoverned = sorted(
                row for row, tier in SUBJECT_TIERS.items()
                if tier == TIER_ROOM and self._provider_for(row) is not None
            )
            if ungoverned:
                raise ValueError(
                    f"{ungoverned} is a Tier 0 social row and a backend for "
                    "it is wired, but no SocialSubjectIndex was given — the "
                    "person index is the only thing that may resolve a "
                    "subject or decide reach (design/instagram-adoption/07)"
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
    ) -> dict[str, Any]:
        """Route one read. NEVER raises (R-5), NEVER fails over.

        Three steps, in this order: **pick, authorize, dispatch.**

        Picking first looks like the wrong order for an authorization check
        and is not: choosing a backend touches nothing and reaches nobody, so
        nothing has happened yet when the tier is decided. What it buys is
        that an UNWIRED backend still answers `unavailable` — «I couldn't
        check» — rather than a boundary or a missing-index sentence about a
        read that was never possible in the first place. The property that
        matters is that authorization happens before DISPATCH, and it does.

        Beyond `unavailable` for an unwired backend and the two tier answers
        it owns outright (`ambiguous`, `refused`), the router adds no states:
        whatever the chosen backend answers is returned VERBATIM, because the
        router's job is to pick and to authorize, not to interpret. Re-mapping
        a backend's status here would put a second opinion about what
        `nothing_found` means in front of the one that performed the read.
        """
        provider = self._provider_for((platform, what))
        if provider is None:
            # Not wired. For an exception row this is also the no-failover
            # rule doing its work: the request does NOT fall through to
            # `default`, which is the backend that structurally cannot serve
            # it, so sending it there would produce a refusal that reads like
            # an answer about the subject.
            log.warning("social router: no backend for %s/%s", platform, what)
            return dict(UNAVAILABLE)
        authorized = await self._authorize(platform=platform, what=what, target=target)
        if isinstance(authorized, dict):
            return authorized
        target = authorized
        try:
            return await provider.read(
                platform=platform, what=what, target=target, limit=limit,
            )
        except Exception:
            # Every implementation is documented never to raise; this is the
            # belt for those braces. It lands on `unavailable` and NOT on the
            # other backend — a raising backend is exactly the case where
            # "try the other one" is most tempting and most wrong.
            log.exception("social backend raised")
            return dict(UNAVAILABLE)

    async def _authorize(
        self, *, platform: str, what: str, target: str,
    ) -> str | dict[str, Any]:
        """The tier ladder, before dispatch. Returns the `target` the backend
        should be given, or the REFUSAL PAYLOAD to answer instead.

        Two return types rather than a bool plus an out-parameter because the
        two outcomes carry different values: an allowed Tier 0 read continues
        with a DIFFERENT target than it arrived with — the person index turned
        a `person_id` into a network id, which is the whole point of the
        resolution step — and a blocked one carries the sentence Nora says.

        Fail-closed everywhere, including on the two paths that are not a
        decision at all: an unclassified row and an index that is missing
        under a Tier 0 row are both `unavailable` («I couldn't check»), which
        is true of a wiring gap, and never `refused`, which would report a
        developer's omission as a boundary the operator set.
        """
        tier = SUBJECT_TIERS.get((platform, what))
        if tier == TIER_NAMED:
            # Allowed as-is, and the index is deliberately NOT consulted.
            # «Never resolved implicitly» is the clause of node 07 that code
            # below the tool can actually hold: whether a human typed this
            # handle is a fact about the conversation, but whether Nora
            # invented it is a fact about this line.
            return target
        if tier == TIER_ROOM:
            if self._subjects is None:
                # Unreachable through the constructor, which refuses this
                # pairing at boot. Kept because it is the fail-closed half of
                # that check and costs one branch: a router reaching here with
                # no index must not read.
                log.error("social router: a Tier 0 row with no subject index")
                return dict(UNAVAILABLE)
            try:
                # RESOLVE before AUTHORIZE: a tier is a property of a person,
                # so there is nothing to authorize until the subject is one.
                # The order also keeps the two answers distinct — «who?» and
                # «not them» are different sentences and the model says a
                # different thing for each.
                igid = await self._subjects.igid_for(person_id=target)
                if not igid:
                    return dict(AMBIGUOUS)
                if not await self._subjects.is_tier_zero(person_id=target):
                    return dict(REFUSED)
            except Exception:
                # The port documents both methods as never raising and as
                # failing CLOSED; this is the belt for those braces, and it
                # lands on `unavailable` rather than on a read.
                log.exception("social subject index raised")
                return dict(UNAVAILABLE)
            return igid
        # No tier. A pair reaching a backend without one is a vocabulary that
        # grew without an authorization decision — the exact drift the table
        # exists to make impossible — so it is not served. `unavailable`, and
        # logged at ERROR because somebody has to fix it.
        log.error("social router: no subject tier for %s/%s", platform, what)
        return dict(UNAVAILABLE)
