"""`nora memory detach-unit --unit <unit> --from-honcho-workspace <id>` —
remove a unit's rooms from the Honcho workspace it LEFT (DEC-214). Dry run
unless `--apply`; the operator decides when, after the backfill has landed.

WHY. When a unit moves to a Nora workspace of its own, its old rooms' sessions
stay in the old Honcho workspace. Scoped reads of the units that stayed never
see them (those rooms are in none of their scopes), but two of Nora's reads
are UNSCOPED: the peer card (Honcho's card endpoint takes no scope) and the
portal's own-memory page (a whole-workspace read). Through those, what the
departed unit's people said still shapes the old workspace's picture of them.

WHAT IT DOES. For every conversation id of the unit's rooms, it deletes that
room's session (`nora-conv-<id>`) from the old Honcho workspace. Honcho's
session delete (`DELETE /sessions/{id}`, 202) marks the session inactive at
once and its queue then hard-deletes the session's messages, their
embeddings, and every DOCUMENT stored under that session — conclusions
included, selected by `session_name` (`src/crud/session.py::delete_session`,
honcho v3.1.2). It then schedules a `card_refresh` dream with `rebuild=True`
for every peer of those sessions — as its own observer and as Nora's
observed — so their cards are rebuilt from what remains
(`ScheduleDreamRequest.rebuild`, «use after removals»).

WHAT IT CANNOT REACH. A conclusion with NO session — a dream's deduction
reasoned across sessions, or a `remember` written before DEC-214 — is not
under the deleted session and survives. Honcho offers no way to tell which of
those drew on the deleted messages, so they stay; the card rebuild does not
remove them either.

REFUSED: the unit's current binding being the SAME Honcho workspace (that
would delete its live memory), the old workspace not being Nora's (the
default `memory.workspace_id`, or marked with her owner key), and any room of
the unit whose session in the NEW workspace holds no message yet — the
backfill has not landed, and this would leave the unit with no history
anywhere. Irreversible once applied.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from nora.infra.honcho import WorkspaceRouter
from nora.infra.honcho.client import conversation_session_id
from nora.policy import Policy


class DetachRefusedError(Exception):
    """The run cannot be made safely. Nothing has been deleted."""


class _Memory(Protocol):
    async def inspect_workspace(self, workspace: str) -> str | None: ...

    async def session_holds_messages_in(
        self, *, workspace: str, conversation_id: str,
    ) -> bool | None: ...

    async def session_peer_ids_in(
        self, *, workspace: str, session_id: str,
    ) -> list[str] | None: ...

    async def delete_session_in(self, *, workspace: str, session_id: str) -> bool: ...

    async def refresh_card_in(
        self, *, workspace: str, observer: str, observed: str,
    ) -> bool: ...


@dataclass
class DetachReport:
    unit: str
    old_workspace: str
    new_workspace: str
    applied: bool
    sessions: list[str] = field(default_factory=list)
    peers: list[str] = field(default_factory=list)
    deleted: int = 0
    refreshed: int = 0
    failed: bool = False


async def detach_unit(
    *,
    unit: str,
    from_honcho_workspace: str,
    policy: Policy,
    router: WorkspaceRouter,
    memory: _Memory,
    ai_peer: str,
    apply: bool = False,
) -> DetachReport:
    """See the module docstring."""
    route = router.for_unit(unit)
    if route is None:
        raise DetachRefusedError(f"policy.toml routes no unit named {unit!r}")
    if route.workspace == from_honcho_workspace:
        raise DetachRefusedError(
            f"unit {unit!r} is bound to {from_honcho_workspace!r} now; detaching it "
            "from there would delete its live memory"
        )
    if (
        from_honcho_workspace != router.default
        and await memory.inspect_workspace(from_honcho_workspace) != "owned"
    ):
        raise DetachRefusedError(
            f"Honcho workspace {from_honcho_workspace!r} is not this Nora's; nothing "
            "was deleted"
        )
    rooms = [c for c in policy.conversations if c.unit == unit]
    for convo in rooms:
        landed = [
            await memory.session_holds_messages_in(
                workspace=route.workspace, conversation_id=cid,
            )
            for cid in sorted(convo.ids)
        ]
        if any(v is None for v in landed):
            raise DetachRefusedError(
                f"could not read {convo.label!r}'s session in {route.workspace!r}; "
                "nothing was deleted"
            )
        if not any(landed):
            raise DetachRefusedError(
                f"{convo.label!r} has no message in {route.workspace!r} yet — run "
                "`nora memory backfill` first; nothing was deleted"
            )
    report = DetachReport(
        unit=unit, old_workspace=from_honcho_workspace,
        new_workspace=route.workspace, applied=apply,
    )
    peers: set[str] = set()
    for convo in rooms:
        for cid in sorted(convo.ids):
            sid = conversation_session_id(cid)
            found = await memory.session_peer_ids_in(
                workspace=from_honcho_workspace, session_id=sid,
            )
            if found is None:
                raise DetachRefusedError(
                    f"could not read the peers of {sid} in {from_honcho_workspace!r}; "
                    "nothing was deleted"
                )
            report.sessions.append(sid)
            peers.update(found)
    report.peers = sorted(peers)
    if not apply:
        return report
    for sid in report.sessions:
        if not await memory.delete_session_in(workspace=from_honcho_workspace, session_id=sid):
            report.failed = True
            return report
        report.deleted += 1
    for peer in report.peers:
        pairs = {(peer, peer), (ai_peer, peer)}
        for observer, observed in sorted(pairs):
            if await memory.refresh_card_in(
                workspace=from_honcho_workspace, observer=observer, observed=observed,
            ):
                report.refreshed += 1
            else:
                report.failed = True
    return report


def render(report: DetachReport) -> list[str]:
    head = "APPLIED" if report.applied else "dry run: nothing deleted"
    lines = [
        f"unit {report.unit!r}: detach from Honcho workspace {report.old_workspace!r} "
        f"(now bound to {report.new_workspace!r}) — {head}",
        f"  sessions: {len(report.sessions)}; peers whose cards are rebuilt: "
        f"{len(report.peers)}",
    ]
    if report.applied:
        lines.append(
            f"  deleted {report.deleted} session(s); scheduled {report.refreshed} card "
            "refresh(es)" + ("; FAILED — re-run, a deleted session counts as done"
                            if report.failed else "")
        )
    else:
        lines.append("re-run with --apply to delete (irreversible)")
    return lines
