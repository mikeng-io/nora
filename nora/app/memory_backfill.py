"""`nora memory backfill --unit <unit>` — replay one unit's recorded human
messages into that unit's Honcho workspace (DEC-214).

WHY IT EXISTS. When a unit moves to a Nora workspace of its own, it moves to
a Honcho workspace of its own, and that workspace starts EMPTY: every person
model Nora had of the unit's people was derived in the old one. Honcho keeps
conclusions, not text, so nothing can be moved across; what can be done is to
feed the unit's own history again, from the transcript Nora already records
(`messages`, read through `RecordStore.since`), so the person models in the
new workspace are built from that unit's rooms and nothing else.

THE SAME FEED LIVE INGESTION MAKES, NOT A LOOK-ALIKE. Each row goes through
`MemoryStore.ensure_peer` + `MemoryStore.feed` on the production
`HonchoClient` with the production router, keyed by the row's own
conversation id — so the same session id (`nora-conv-<id>`), the same peer
id (the person's current `person_id`), the same observer configuration (Nora
observes, the human is observable) and the same scope membership as the live
path (`nora.ingest.stages.memory_feed`). The metadata is the live path's too:
`{"ts": <ts>}`, and an edited row is fed as its first version followed by the
edit, framed by the same `edit_text` the live edit path uses. One thing is
NOT the live path's: each message is sent with its own `created_at` (the
row's `ts`, the edit's for an edit), so Honcho dates it when it was said. The
TEXT is the
row joined through the process's `MediaJoin`, as lazy adoption joins it
(`nora.app.adoption`, the other replay into memory): live ingestion fed a
photo's marker and, later and separately, its comprehension; a replay has
only the row, so the comprehension rides in it or it does not reach memory.

WHAT IS SKIPPED, exactly as the live feed skips it: Nora's own rows (`since`
excludes `is_nora` in the query — her replies reach Honcho through the reply
path, not ingest — so in the new workspace a message that answered her
arrives without the reply it answered), and rows whose sender mapped to
nobody (`person_id` NULL, never fed). Also skipped, and counted: a row
with no text at all, and a person_id `identity.toml` does not know. The transcript store keeps at
most `store.max_per_conversation` rows per conversation, so «history» here is
what it still holds.

SAFE TO RUN TWICE. Dry run by default: it reads everything and writes
nothing, and prints what `--apply` would feed. Before writing it reads what
each session already holds (`HonchoClient.session_messages`), and a session
that is not empty refuses the whole run unless `--resume`, which then skips
every row the session already has — matched by speaker and `ts`, the metadata
the live feed stamps — so a half-finished run, or live traffic fed since the
move, is never fed twice. The run stops at its first failed feed, so a
resume continues in order rather than around a hole.

REFUSED OUTRIGHT: a Honcho workspace that belongs to another application
(it holds sessions and lacks Nora's owner marker, `HonchoClient._owned`), a
policy with no `[units]` (every unit is then in the one
implicit workspace, so there is nowhere new to write), a unit the policy does
not route, and a unit whose Honcho workspace is `honcho.workspace_id` — the
implicit default's, which already holds this history, so a replay there
would feed every message a second time.

IT DELETES NOTHING, anywhere, and in particular nothing from the Honcho
workspace the unit left. Every room or unit read for the units that stayed is
made through their NAMED SCOPES, and the rooms of the unit that left are
members of none of them, so what was derived from those rooms is not in
those reads. It IS still in the old workspace's UNSCOPED reads, and two of
Nora's reads are unscoped: the peer card (its endpoint takes no scope) and
the portal's own-memory page (a whole-workspace read). Removing it from
there is `nora memory detach-unit` (`nora.app.memory_detach`), run after
this has landed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from nora.domain.external_files import render_file_links
from nora.domain.media_join import MediaJoin
from nora.domain.models import TranscriptRow
from nora.domain.ports import RecordStore
from nora.infra.honcho import WorkspaceRouter
from nora.ingest.stages.memory_feed import edit_text
from nora.persons import Persons
from nora.policy import Policy

#: Rows read per `since` call. The store caps a conversation far below any
#: sane multiple of this; the loop is there so the command never depends on
#: that cap staying where it is.
PAGE = 500


class BackfillRefusedError(Exception):
    """The run cannot be made safely as asked. Printed and exit 1; nothing
    has been written when this is raised."""


class BackfillMemory(Protocol):
    """What the backfill needs of `HonchoClient`: the two `MemoryStore`
    verbs the live feed uses, and the one read that is not on the port."""

    async def ensure_peer(
        self, *, peer_id: str, conversation_id: str | None = None,
        unit: str | None = None,
    ) -> None: ...

    async def feed(
        self, *, peer_id: str, text: str, conversation_id: str,
        metadata: dict[str, object] | None = None,
        created_at_ms: int | None = None,
    ) -> bool: ...

    async def ensure_workspace(self) -> None: ...

    async def session_messages(
        self, *, conversation_id: str,
    ) -> list[dict[str, Any]] | None: ...

    async def inspect_workspace(self, workspace: str) -> str | None: ...


@dataclass
class RoomCounts:
    """One conversation id's run. `fed` counts what was WRITTEN (or, dry,
    what would be); every other counter is a reason a row was not."""

    conversation_id: str
    label: str
    rows: int = 0
    held: int = 0
    unmapped: int = 0
    empty: int = 0
    fed: int = 0
    failed: int = 0
    held_before: int = 0


@dataclass
class BackfillReport:
    unit: str
    honcho_workspace: str
    applied: bool
    rooms: list[RoomCounts] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return sum(room.failed for room in self.rooms)


def _items(row: TranscriptRow) -> list[tuple[str, dict[str, object], int]]:
    """What the live path fed for this row: the message, and — when it was
    edited — the edit as a second message framed as a correction
    (`nora.ingest.stages.memory_feed.run`). Only the LATEST edit survives in
    the transcript, so an edited-twice row replays as its first version plus
    its last."""
    # The file-link handle, never the URL, as the live feed sends it.
    body = render_file_links(row.body or "").strip()
    if row.edit is None:
        return [(body, {"ts": row.ts}, row.ts)] if body else []
    was = render_file_links(row.edit.was or "")
    first = was.strip()
    out: list[tuple[str, dict[str, object], int]] = []
    if first:
        out.append((first, {"ts": row.ts}, row.ts))
    if body:
        out.append((
            edit_text(body=body, first_body=was),
            {"ts": row.edit.ts, "edit_of_ts": row.ts},
            row.edit.ts,
        ))
    return out


def _held_key(peer_id: object, metadata: dict[str, Any]) -> tuple[object, ...] | None:
    """The identity of a fed message, as the live feed stamps it. A media
    comprehension carries `media` and is a different message about the same
    `ts`, so it never matches a text row."""
    if "media" in metadata or "ts" not in metadata:
        return None
    return (peer_id, metadata.get("ts"), metadata.get("edit_of_ts"))


async def _rows(
    records: RecordStore, conversation_id: str, media_join: MediaJoin | None,
) -> list[TranscriptRow]:
    out: list[TranscriptRow] = []
    after = 0
    while True:
        page = await records.since(conversation_id=conversation_id, after_id=after, limit=PAGE)
        out.extend(page)
        if len(page) < PAGE:
            break
        after = page[-1].id
    if media_join is not None:
        out = list(await media_join.enrich(out, path="memory_backfill"))
    # TIMESTAMP order, the order the room said it in; the id breaks ties so
    # two runs replay identically.
    return sorted(out, key=lambda r: (r.ts, r.id))


async def backfill_unit(
    *,
    unit: str,
    policy: Policy,
    persons: Persons,
    router: WorkspaceRouter,
    records: RecordStore,
    memory: BackfillMemory,
    default_workspace: str,
    media_join: MediaJoin | None = None,
    apply: bool = False,
    resume: bool = False,
) -> BackfillReport:
    """Replay `unit`'s human rows into its Honcho workspace. See the module
    docstring for what is skipped, refused and why."""
    if not policy.units_declared:
        raise BackfillRefusedError(
            "policy.toml declares no [units], so every unit is in the one implicit "
            "workspace, bound to honcho.workspace_id — there is no workspace of "
            f"{unit!r}'s own to replay into"
        )
    route = router.for_unit(unit)
    if route is None:
        raise BackfillRefusedError(f"policy.toml routes no unit named {unit!r}")
    if route.workspace == default_workspace:
        raise BackfillRefusedError(
            f"unit {unit!r} is bound to Honcho workspace {route.workspace!r}, which is "
            "honcho.workspace_id — the workspace that already holds this history. "
            "Replaying there would feed every message twice."
        )
    # OURS, or nothing. A Honcho workspace another application uses can carry
    # the same name as a Nora workspace, and a replay into it would write
    # people's words into someone else's peers.
    ownership = await memory.inspect_workspace(route.workspace)
    if ownership is None:
        raise BackfillRefusedError(
            f"could not read who Honcho workspace {route.workspace!r} belongs to — "
            "nothing was written"
        )
    if ownership == "foreign":
        raise BackfillRefusedError(
            f"Honcho workspace {route.workspace!r} holds sessions and is not marked "
            "as this Nora's: another application uses it. Nothing was written. Bind "
            "the workspace to a different honcho_workspace in identity.toml."
        )
    report = BackfillReport(unit=unit, honcho_workspace=route.workspace, applied=apply)
    targets: list[tuple[str, str]] = sorted(
        (cid, convo.label or convo.primary_id)
        for convo in policy.rooms() if convo.unit == unit
        for cid in convo.ids
    )

    # READ EVERYTHING FIRST. A refusal must come before the first write, not
    # halfway through the rooms.
    #
    # The ROWS are read here too, and BEFORE what the session holds: the rows
    # are the fixed set this run replays, so a message said while the run is
    # writing — which live ingestion feeds itself — is not in it and is never
    # fed twice. Reading the rows first and the session second means a row
    # that live ingestion fed before the pre-pass is already in `held`. The
    # window left is a message recorded between the two reads whose live feed
    # (spawned off the listener path) lands after the second; it would be fed
    # twice, once each way, and cost one duplicate line in Honcho.
    held: dict[str, set[tuple[object, ...]]] = {}
    rows: dict[str, list[TranscriptRow]] = {}
    for cid, label in targets:
        rows[cid] = await _rows(records, cid, media_join)
        messages = await memory.session_messages(conversation_id=cid)
        if messages is None:
            raise BackfillRefusedError(
                f"could not read what Honcho already holds for {label!r} — nothing "
                "was written; is Honcho up, and does it run v3.1 or later?"
            )
        keys = {
            key for m in messages
            if (key := _held_key(m.get("peer_id"), m.get("metadata") or {})) is not None
        }
        held[cid] = keys
        if messages and not resume:
            raise BackfillRefusedError(
                f"{label!r}'s session in Honcho workspace {route.workspace!r} already "
                f"holds {len(messages)} message(s). Nothing was written. Re-run with "
                "--resume to skip what it holds and feed the rest."
            )

    # Only now, past every refusal and the read-only pre-pass: provision the
    # workspace (Nora's peer, the unit scopes, the rooms joined to them).
    if apply:
        await memory.ensure_workspace()
    for cid, label in targets:
        counts = RoomCounts(conversation_id=cid, label=label, held_before=len(held[cid]))
        report.rooms.append(counts)
        for row in rows[cid]:
            counts.rows += 1
            if row.person_id is None:
                counts.unmapped += 1
                continue
            person = persons.by_person.get(persons.subject_id_for(row.person_id))
            if person is None:
                counts.unmapped += 1
                continue
            items = _items(row)
            if not items:
                counts.empty += 1
                continue
            for text, metadata, said_at in items:
                key = _held_key(person.honcho_peer_id, metadata)
                if key in held[cid]:
                    counts.held += 1
                    continue
                if not apply:
                    counts.fed += 1
                    continue
                await memory.ensure_peer(peer_id=person.honcho_peer_id, conversation_id=cid)
                if not await memory.feed(
                    peer_id=person.honcho_peer_id, text=text, conversation_id=cid,
                    metadata=metadata, created_at_ms=said_at,
                ):
                    counts.failed += 1
                    break
                counts.fed += 1
            if counts.failed:
                break
        if counts.failed:
            break
    return report


def render(report: BackfillReport) -> list[str]:
    verb = "fed" if report.applied else "would feed"
    lines = [
        f"unit {report.unit!r} -> Honcho workspace {report.honcho_workspace!r}"
        f" ({'APPLIED' if report.applied else 'dry run: nothing written'})",
    ]
    for room in report.rooms:
        lines.append(
            f"  {room.label} [{room.conversation_id[:12]}…]: {room.rows} human row(s); "
            f"{verb} {room.fed}, already held {room.held}, unmapped {room.unmapped}, "
            f"no text {room.empty}, failed {room.failed}"
            + (" — STOPPED at the first failure; re-run with --resume to "
               "continue from it" if room.failed else "")
        )
    total = sum(r.fed for r in report.rooms)
    lines.append(f"total: {verb} {total} message(s) across {len(report.rooms)} conversation id(s)")
    if not report.applied:
        lines.append("re-run with --apply to write")
    return lines
