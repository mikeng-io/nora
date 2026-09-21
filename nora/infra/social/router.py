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
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from ...domain.ports import SocialProvider
from .results import UNAVAILABLE

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
    """

    def __init__(
        self,
        *,
        default: SocialProvider | None,
        exceptions: Mapping[tuple[str, str], SocialProvider] | None = None,
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

    async def read(
        self, *, platform: str, what: str, target: str, limit: int,
    ) -> dict[str, Any]:
        """Route one read. NEVER raises (R-5), NEVER fails over.

        The router adds no states of its own beyond `unavailable` for a
        backend that is not wired: whatever the chosen backend answers is
        returned VERBATIM, because the router's job is to pick, not to
        interpret. Re-mapping a backend's status here would put a second
        opinion about what `nothing_found` means in front of the one that
        performed the read.
        """
        if (platform, what) in EXCEPTION_ROUTES:
            provider = self._exceptions.get((platform, what))
            if provider is None:
                # The exception backend is not wired. `unavailable`, and the
                # request does NOT fall through to `default` — the default is
                # the backend that structurally cannot serve this row, so
                # sending it there would produce a refusal that reads like an
                # answer about the subject.
                log.warning("social router: no backend for an exception row")
                return dict(UNAVAILABLE)
        else:
            provider = self._default
            if provider is None:
                return dict(UNAVAILABLE)
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
