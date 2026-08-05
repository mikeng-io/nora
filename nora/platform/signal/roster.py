"""`SignalRoster` — who is in one Signal group, over the read-only directory RPC.

`nora.domain.ports.RosterSource`'s Signal implementation, and deliberately a
thin one: `nora.platform.signal.directory.list_groups` already wraps
`listGroups` (the read-method allowlist, the verified field names, the
`members[]` id pairing), it is already exercised by `nora groups` and
`nora persons pending`, and reimplementing the RPC here would mean two places
that have to agree about the daemon's shape. This adds exactly two things the
control plane does not need: selecting ONE group by id, and never raising.

**Never raising is the contract, not a convenience.** The caller is a tool
running inside a live turn — a `DirectoryError` escaping here would either kill
the tag or, worse, be caught by a blanket handler and turned into "nobody is in
this group". `Roster.available` carries the difference instead.

**The timeout is lower than `directory`'s own default (10 s) on purpose.** Ten
seconds is a sensible budget for an operator command run from a terminal; it is
not one for a turn where the human is watching a 👀 reaction and every tool call
already costs a model round trip. A wedged daemon must cost a few seconds and an
honest "I can't see that", not most of the tag budget.
"""

from __future__ import annotations

import logging

from nora.domain.ports import Roster

from . import directory

log = logging.getLogger("nora.platform.signal.roster")

# Two failed reads still fit inside a single model round trip (~4 s), which is
# the unit a wedged daemon has to be cheap relative to.
DEFAULT_TIMEOUT_SECONDS = 4.0


class SignalRoster:
    """Satisfies `nora.domain.ports.RosterSource` for Signal."""

    platform = "signal"

    def __init__(
        self,
        *,
        base_url: str,
        account: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url
        self._account = account
        self._timeout = timeout_seconds

    async def roster(self, *, conversation_id: str) -> Roster:
        """The members of `conversation_id`, or `available=False`.

        A group the daemon does not list is `available=False`, NOT an empty
        roster: signal-cli returning `[]` for a group Nora is demonstrably in
        is a real, observed state — group state goes stale on a linked device
        and `listGroups` answered `[]` for the `Ridgeline` group until a
        `sendSyncRequest` refreshed it (CLAUDE.md, operational constraint 3).
        Reporting that as "this group has no members" would be a confident lie
        told by the exact failure mode this deployment has already hit.
        """
        try:
            groups = await directory.list_groups(
                base_url=self._base_url, account=self._account, timeout=self._timeout,
            )
        except directory.DirectoryError as exc:
            log.warning("roster unavailable for %s: %s", conversation_id, exc)
            return Roster(available=False)
        for group in groups:
            if group.id == conversation_id:
                return Roster(available=True, members=group.members)
        log.warning(
            "roster unavailable: signal-cli did not list conversation %s at all "
            "(stale group state? see CLAUDE.md constraint 3)", conversation_id,
        )
        return Roster(available=False)
