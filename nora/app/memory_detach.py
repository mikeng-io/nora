"""`nora memory detach-unit --unit <unit> --from-honcho-workspace <id>` —
remove a unit's rooms from the Honcho workspace it LEFT (DEC-214). Two steps,
each a dry run unless asked, both rerunnable; the operator decides when,
after the backfill has landed.

WHY. When a unit moves to a Nora workspace of its own, its old rooms' sessions
stay in the old Honcho workspace. Scoped reads of the units that stayed never
see them (those rooms are in none of their scopes), but two of Nora's reads
are UNSCOPED: the peer card (Honcho's card endpoint takes no scope) and the
portal's own-memory page (a whole-workspace read). Through those, what the
departed unit's people said still shapes the old workspace's picture of them.

STEP 1, `--apply --peers-file <path>`: DELETE. For every conversation id of
the unit's rooms it deletes that room's session (`nora-conv-<id>`) from the
old Honcho workspace. Honcho's session delete (`DELETE /sessions/{id}`, 202)
marks the session inactive at once and its QUEUE then hard-deletes the
session's messages, their embeddings, and every DOCUMENT stored under that
session — conclusions included, selected by `session_name`
(`src/crud/session.py::delete_session`, honcho v3.1.2). Before deleting, it
WRITES the sessions and their peers to `--peers-file` (merged with what an
earlier run wrote), because after the delete nothing in Honcho can name them
again: a rerun finds the sessions gone and their peers with them.

STEP 2, `--refresh-cards --peers-file <path>`: REBUILD THE CARDS. The delete
is asynchronous, so a card rebuilt at once would be rebuilt from the very
documents being deleted. This step first polls each deleted session's
remaining conclusions (`POST /conclusions/list`, filtered by `session_id`)
until there are none — bounded, and it refuses rather than refreshing early —
then schedules a `card_refresh` dream with `rebuild=True` for every peer in
the file, as its own observer and as Nora's observed (`ScheduleDreamRequest.
rebuild`, «use after removals»). Safe to run again.

WHAT IT CANNOT REACH. A conclusion with NO session — a dream's deduction
reasoned across sessions, or a session-less `remember` — is not
under the deleted session and survives. Honcho offers no way to tell which of
those drew on the deleted messages, so they stay; the card rebuild does not
remove them either.

REFUSED (step 1): the unit's current binding being the SAME Honcho workspace
(that would delete its live memory); the old workspace not being Nora's (the
default `honcho.workspace_id`, or marked with her owner key); any room of
the unit whose session in the NEW workspace holds no message yet; and a
backfill that would still feed anything (a dry run of `backfill_unit` over
the same rows must count zero) — the history has not fully landed, and this
would leave it nowhere. Irreversible once applied.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from nora.app.memory_backfill import BackfillMemory, backfill_unit
from nora.domain.ports import RecordStore
from nora.infra.honcho import WorkspaceRouter
from nora.infra.honcho.client import conversation_session_id
from nora.persons import Persons
from nora.policy import Policy


class DetachRefusedError(Exception):
    """The run cannot be made safely. Nothing has been deleted."""


class _Memory(BackfillMemory, Protocol):
    async def session_holds_messages_in(
        self, *, workspace: str, conversation_id: str,
    ) -> bool | None: ...

    async def session_peer_ids_in(
        self, *, workspace: str, session_id: str,
    ) -> list[str] | None: ...

    async def delete_session_in(self, *, workspace: str, session_id: str) -> bool: ...


class _CardMemory(Protocol):
    async def conclusions_under_session_in(
        self, *, workspace: str, session_id: str,
    ) -> int | None: ...

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
    failed: bool = False


def _strings(value: object) -> list[str]:
    return [str(x) for x in value] if isinstance(value, list) else []


def _read_peers_file(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DetachRefusedError(f"could not read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise DetachRefusedError(f"{path} is not a detach peers file")
    return data


def _write_peers_file(path: Path, report: DetachReport) -> None:
    """Merge this run's sessions and peers into the file, then write it
    atomically (tmp + rename), so a crash never leaves half a list."""
    earlier = _read_peers_file(path)
    sessions, peers = set(report.sessions), set(report.peers)
    if earlier is not None:
        if (earlier.get("unit"), earlier.get("old_workspace")) != (
            report.unit, report.old_workspace,
        ):
            raise DetachRefusedError(
                f"{path} belongs to another detach (unit {earlier.get('unit')!r} from "
                f"{earlier.get('old_workspace')!r}); give this one its own file"
            )
        sessions |= set(_strings(earlier.get("sessions")))
        peers |= set(_strings(earlier.get("peers")))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({
        "unit": report.unit, "old_workspace": report.old_workspace,
        "sessions": sorted(sessions), "peers": sorted(peers),
    }, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


async def detach_unit(
    *,
    unit: str,
    from_honcho_workspace: str,
    policy: Policy,
    persons: Persons,
    router: WorkspaceRouter,
    records: RecordStore,
    memory: _Memory,
    peers_file: Path,
    apply: bool = False,
) -> DetachReport:
    """Step 1. See the module docstring."""
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
    rooms = [c for c in policy.rooms() if c.unit == unit]
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
    # FULLY landed, not merely started: the backfill's own dry run, over the
    # same rows, must have nothing left to feed.
    pending = await backfill_unit(
        unit=unit, policy=policy, persons=persons, router=router, records=records,
        memory=memory, default_workspace=router.default, apply=False, resume=True,
    )
    left = sum(room.fed for room in pending.rooms)
    if left:
        raise DetachRefusedError(
            f"the backfill for {unit!r} would still feed {left} message(s) — run "
            "`nora memory backfill --resume --apply` until it feeds none; nothing "
            "was deleted"
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
    # WRITTEN BEFORE THE FIRST DELETE: afterwards Honcho cannot name them.
    _write_peers_file(peers_file, report)
    for sid in report.sessions:
        if not await memory.delete_session_in(workspace=from_honcho_workspace, session_id=sid):
            report.failed = True
            return report
        report.deleted += 1
    return report


@dataclass
class RefreshReport:
    old_workspace: str
    sessions: int
    refreshed: list[tuple[str, str]] = field(default_factory=list)
    failed: bool = False


async def refresh_cards(
    *,
    peers_file: Path,
    memory: _CardMemory,
    ai_peer: str,
    poll_attempts: int = 30,
    poll_seconds: float = 10.0,
) -> RefreshReport:
    """Step 2. Waits until Honcho has actually deleted what step 1 asked it
    to, then rebuilds the cards. See the module docstring."""
    data = _read_peers_file(peers_file)
    if data is None:
        raise DetachRefusedError(
            f"{peers_file} does not exist — run `detach-unit --apply` with the same "
            "--peers-file first"
        )
    workspace = str(data.get("old_workspace"))
    sessions = _strings(data.get("sessions"))
    peers = _strings(data.get("peers"))
    for sid in sessions:
        for attempt in range(poll_attempts):
            left = await memory.conclusions_under_session_in(
                workspace=workspace, session_id=sid,
            )
            if left == 0:
                break
            if attempt + 1 < poll_attempts:
                await asyncio.sleep(poll_seconds)
        else:
            raise DetachRefusedError(
                f"Honcho still holds conclusions under {sid} in {workspace!r} (or could "
                f"not say) after {poll_attempts} checks — its delete queue has not "
                "finished; no card was refreshed. Run this again later."
            )
    report = RefreshReport(old_workspace=workspace, sessions=len(sessions))
    for peer in peers:
        for observer, observed in sorted({(peer, peer), (ai_peer, peer)}):
            if await memory.refresh_card_in(
                workspace=workspace, observer=observer, observed=observed,
            ):
                report.refreshed.append((observer, observed))
            else:
                report.failed = True
    return report


def render(report: DetachReport, peers_file: Path) -> list[str]:
    head = "APPLIED" if report.applied else "dry run: nothing deleted"
    lines = [
        f"unit {report.unit!r}: detach from Honcho workspace {report.old_workspace!r} "
        f"(now bound to {report.new_workspace!r}) — {head}",
        f"  sessions: {len(report.sessions)}; peers whose cards need a rebuild: "
        f"{len(report.peers)}",
    ]
    if report.applied:
        lines.append(
            f"  deleted {report.deleted} session(s); peers recorded in {peers_file}"
            + ("; FAILED — re-run, a deleted session counts as done" if report.failed else "")
        )
        lines.append(
            "  next: `detach-unit --refresh-cards --peers-file` with the same file, once "
            "Honcho's delete queue has run"
        )
    else:
        lines.append("re-run with --apply to delete (irreversible)")
    return lines


def render_refresh(report: RefreshReport) -> list[str]:
    return [
        f"Honcho workspace {report.old_workspace!r}: {report.sessions} deleted "
        f"session(s) confirmed empty; scheduled {len(report.refreshed)} card "
        "refresh(es)" + ("; SOME FAILED — run again" if report.failed else ""),
    ]
