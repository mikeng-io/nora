"""In-memory doubles for the ports. Bound by `tests/contract/`."""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from nora.domain.compute import (
    ComputeJob,
    ComputeJobState,
    ensure_utc_job,
    normalise_compute_job,
)
from nora.domain.computer import (
    BrowseOutcome,
    BytesResult,
    FileEntry,
    FileResult,
    ListResult,
    OpResult,
    RunResult,
    SandboxStatus,
    ScopeProcess,
    StateFile,
    StateResult,
    TmuxSession,
)
from nora.domain.computer_events import ComputerEvent, normalise_computer_event
from nora.domain.computer_exports import (
    SHARED_DESTINATIONS,
    ComputerExport,
    SharedPage,
    normalise_export,
)
from nora.domain.computer_sessions import (
    EXPIRED,
    OPEN,
    ComputerSession,
    ensure_utc_event,
    normalise_session,
)
from nora.domain.documents import (
    DocumentExistsError,
    DocumentState,
    DocumentSummary,
    ListedState,
    RevisionIn,
    RevisionOutcome,
    RevisionRecord,
    RoomKey,
    StoredDocument,
    StoredRevision,
    VisiblePage,
    is_handle,
    normalize_query,
)
from nora.domain.external_files import external_files_json
from nora.domain.media import (
    MediaBlob,
    MediaBytes,
    MediaObject,
    MediaRef,
    VideoReadingKey,
    media_json,
)
from nora.domain.models import (
    ConversationTarget,
    DeliveryStatus,
    EditedMessage,
    EditTarget,
    NormalizedMessage,
    RowEdit,
    TranscriptRow,
    UnattributedRow,
    mentions_json,
    parse_mentions_json,
)
from nora.domain.ports import (
    AdapterHealth,
    BlobDeletion,
    JobResult,
    JobStatus,
    LaunchRequest,
    LinkPreview,
    MediaDescription,
    MediaLookup,
    MemoryHealth,
    Mention,
    MessageHits,
    OutboundAttachment,
    PlaceArea,
    PlatformIdentity,
    ReactionResult,
    Recollection,
    RoomActivity,
    Roster,
    SendResult,
    SttResult,
    TextStyle,
    UsernameLookup,
)
from nora.domain.presence import haversine_distance_m
from nora.domain.tool_call_log import (
    ToolCallLogEntry,
    normalise_tool_call_log_entry,
)
from nora.domain.utf16 import utf16_len


def transcript_rows(
    bodies: Sequence[str],
    *,
    sender_id: str,
    sender_name: str,
    person_id: str | None,
    ts_base: int,
    ts_step: int = 60_000,
    id_start: int = 1000,
    is_nora: bool = False,
) -> tuple[TranscriptRow, ...]:
    """Build transcript rows from plain message bodies.

    The builder `evals/` reaches this type through — `evals/` may import
    `nora.app` and `nora.testing` and nothing else (`tests/test_boundaries.py`),
    so it cannot name `TranscriptRow` itself. The shape mirrors what a real
    `RecordStore.recent` read returns (see `_transcript_row` below): oldest
    first as written, `ts` ticking forward by `ts_step`, every row SENT — a
    row the sender's own message produced, not a pending outbound one.

    **`is_nora` marks HER OWN earlier speech**, the input a whole class of
    judgement turns on — «have I already said this?». A harness that cannot
    seed a speaker cannot measure a judgement about that speaker.
    `nora.agent.prefill._speaker` reads THIS flag to attribute a row, so a row
    that merely carries the name "Nora" renders as a member with that display
    name instead — which is why the flag and not a `sender_name` convention.
    """
    return tuple(
        TranscriptRow(
            id=id_start + i, sender_id=sender_id, sender_name=sender_name,
            ts=ts_base + i * ts_step, body=body, is_nora=is_nora,
            delivery_status=DeliveryStatus.SENT,
            raw_json=None, quoted_ts=None, media_json=None,
            person_id=person_id,
        )
        for i, body in enumerate(bodies)
    )


class FakeRecordStore:
    """In-memory `RecordStore`.

    The two failure switches exist because the integration harness must inject
    audit failures, and the old mechanism — `monkeypatch.setattr(db, "append")`
    — silently became a no-op the moment the store was injected rather than
    imported. A switch on the injected object cannot be bypassed that way.
    """

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        # `message_edit`, as dicts: {"id", "message_id", "ts", "body",
        # "raw_json", "mentions_json"}.
        self.edits: list[dict[str, Any]] = []
        # `message_reaction` and `message_delete`, as dicts, mirroring the
        # columns `SqlRecordStore.append_reaction`/`append_delete` write.
        self.reactions: list[dict[str, Any]] = []
        self.deletes: list[dict[str, Any]] = []
        # Row ids `hide_message` hid.
        self.hidden_ids: set[int] = set()
        self.fail_on_nora = False
        self.fail_on_recent = False
        self.closed = False

    async def append(
        self,
        *,
        message: NormalizedMessage,
        is_nora: bool = False,
        delivery_status: DeliveryStatus = DeliveryStatus.PENDING,
        person_id: str | None = None,
        tx_hook: Any = None,
    ) -> int:
        # Deliberately does NOT raise when closed. `SqlRecordStore.aclose`
        # delegates to `AsyncEngine.dispose()`, which releases the pool but
        # leaves the engine usable. A fake that raised
        # here would be stricter than production, which is precisely the
        # drift the contract suite exists to prevent; `closed` is recorded for
        # assertions, not enforced.
        if is_nora and self.fail_on_nora:
            raise RuntimeError("disk full")
        row_id = len(self.rows) + 1
        self.rows.append({
            "id": row_id,
            # `platform` is recorded because `unattributed` has to return it:
            # re-resolving a sender is keyed (platform, sender_id), and a row
            # without its platform cannot be re-resolved at all.
            "platform": message.platform,
            "conversation_id": message.conversation_id,
            "sender_id": message.sender_id,
            "sender_name": message.sender_name,
            "person_id": person_id,
            "ts": message.timestamp,
            "body": message.body,
            "is_nora": is_nora,
            "delivery_status": delivery_status,
            # The parent pointer rides the message, exactly as it does in
            # `SqlRecordStore.append` — no extra parameter on either store.
            "quoted_ts": message.quote.quoted_ts if message.quote else None,
            # `json.dumps`, not `None`: `unattributed` returns `raw_json`, and
            # the operator control plane (`nora.app.pending`) correlates a
            # sender's id forms out of that envelope. A fake that dropped it would
            # let every pending-sender test pass against a store production does
            # not have.
            "raw_json": json.dumps(message.raw),
            # The custody index, produced from the message exactly as
            # `SqlRecordStore.append` produces it — NULL when Nora holds
            # nothing, never `[]`. A fake that always stored None here would
            # let every custody test pass against a store production does not
            # have; `tests/contract/test_audit_store_contract.py` is what pins
            # the two together.
            "media_json": media_json(message.media_blobs),
            # Mirrors `SqlRecordStore.append`, through the same domain
            # function, so the two stores cannot write different text.
            "external_files_json": external_files_json(message.external_files),
            # Both mirror `SqlRecordStore.append`.
            "platform_message_id": message.message_id or None,
            "mentions_json": mentions_json(message.mentions),
        })
        # `tx_hook(conn, row_id)` commits atomically with the row in
        # `SqlRecordStore.append`. The fake has no transaction to hand over,
        # so the hook runs against `None` and an explosion takes the staged
        # row back — the same all-or-nothing a real rollback gives.
        if tx_hook is not None:
            try:
                await tx_hook(None, row_id)
            except Exception:
                self.rows.pop()
                raise
        return row_id

    def _latest_edit(self, row_id: int) -> dict[str, Any] | None:
        """Mirrors `audit._latest_edit`: newest by arrival (`id`)."""
        mine = [e for e in self.edits if e["message_id"] == row_id]
        return max(mine, key=lambda e: e["id"]) if mine else None

    async def find_edited(
        self, *, conversation_id: str, target: EditTarget, sender_id: str,
    ) -> EditedMessage | None:
        """Mirrors `SqlRecordStore.find_edited`, earliest-id tie-break
        included; pinned for both by the contract suite."""
        if target.message_id:
            def match(r: dict[str, Any]) -> bool:
                return r["platform_message_id"] == target.message_id
        elif target.sent_ts is not None and sender_id:
            def match(r: dict[str, Any]) -> bool:
                # Mirrors the SQL: the original's ts, or one of its edits'.
                return (
                    r["ts"] == target.sent_ts
                    or any(e["message_id"] == r["id"] and e["ts"] == target.sent_ts
                           for e in self.edits)
                )
        else:
            return None
        # The sender PREFERRED, then the earliest — mirrors the SQL ordering.
        found = sorted(
            (r for r in self.rows
             if r["conversation_id"] == conversation_id and match(r)),
            key=lambda r: (r["sender_id"] != sender_id, r["id"]),
        )
        if not found:
            return None
        r = found[0]
        latest = self._latest_edit(r["id"])
        return EditedMessage(
            id=r["id"], ts=r["ts"], sender_id=r["sender_id"],
            person_id=r["person_id"], is_nora=bool(r["is_nora"]),
            body=latest["body"] if latest else r["body"],
            mentions=parse_mentions_json(
                latest["mentions_json"] if latest else r["mentions_json"]
            ),
            first_body=r["body"],
            platform_message_id=r["platform_message_id"] or "",
            replies_to_nora=any(
                q["conversation_id"] == r["conversation_id"]
                and r["quoted_ts"] is not None and q["ts"] == r["quoted_ts"]
                and q["is_nora"]
                for q in self.rows
            ),
            edited_ts=latest["ts"] if latest else None,
            media_json=r["media_json"],
            hidden=r["id"] in self.hidden_ids,
            edit_timestamps=frozenset(
                e["ts"] for e in self.edits if e["message_id"] == r["id"]
            ),
        )

    async def quoted_ts_of(self, *, conversation_id: str, ts: int) -> int:
        """Mirrors `SqlRecordStore.quoted_ts_of`."""
        rows = [r for r in self.rows if r["conversation_id"] == conversation_id]
        if any(r["ts"] == ts for r in rows):
            return ts
        for r in sorted(rows, key=lambda r: r["id"]):
            if any(e["message_id"] == r["id"] and e["ts"] == ts for e in self.edits):
                return int(r["ts"])
        return ts

    async def append_edit(self, *, message_id: int, message: NormalizedMessage) -> None:
        self.edits.append({
            "id": len(self.edits) + 1,
            "message_id": message_id,
            "ts": message.timestamp,
            "body": message.body,
            "raw_json": json.dumps(message.raw),
            # Mirrors `audit._edit_mentions_json`.
            "mentions_json": (
                None if message.edit_of is not None and not message.edit_of.mentions_known
                else mentions_json(message.mentions)
            ),
        })

    async def append_reaction(
        self, *, message_id: int, message: NormalizedMessage, person_id: str | None,
    ) -> int:
        """Mirrors `SqlRecordStore.append_reaction`."""
        touch = message.touch
        self.reactions.append({
            "id": len(self.reactions) + 1, "message_id": message_id,
            "sender_id": message.sender_id, "person_id": person_id,
            "emoji": touch.emoji if touch is not None else "",
            "removed": bool(touch.removed) if touch is not None else False,
            "ts": message.timestamp, "raw_json": json.dumps(message.raw),
        })
        return len(self.reactions)

    async def append_delete(
        self, *, message_id: int, message: NormalizedMessage, person_id: str | None,
    ) -> int:
        """Mirrors `SqlRecordStore.append_delete`."""
        self.deletes.append({
            "id": len(self.deletes) + 1, "message_id": message_id,
            "sender_id": message.sender_id, "person_id": person_id,
            "ts": message.timestamp, "raw_json": json.dumps(message.raw),
        })
        return len(self.deletes)

    async def hide_message(self, *, message_id: int, now_ms: int) -> bool:
        """Mirrors `SqlRecordStore.hide_message`: True when this call hid a stored
        row, False when it was already hidden or no such row is held."""
        if message_id in self.hidden_ids or not any(r["id"] == message_id for r in self.rows):
            return False
        self.hidden_ids.add(message_id)
        return True

    async def recent(self, *, conversation_id: str, limit: int) -> list[TranscriptRow]:
        if self.fail_on_recent:
            raise RuntimeError("db down")
        matching = [r for r in self.rows if r["conversation_id"] == conversation_id]
        matching.sort(key=lambda r: (r["ts"], r["id"]))
        window = matching[-limit:] if limit > 0 else []
        return [self._transcript_row(r) for r in window]

    async def media_rows(
        self, *, conversation_id: str, since_ts: int, limit: int,
    ) -> list[TranscriptRow]:
        """Mirrors `SqlRecordStore.media_rows`: this conversation's human rows
        that hold media, `ts >= since_ts`, newest first. Raises when
        `fail_on_recent` is set, as the SQL store raises on a database error."""
        if self.fail_on_recent:
            raise RuntimeError("db down")
        if limit <= 0:
            return []
        matching = [
            r for r in self.rows
            if r["conversation_id"] == conversation_id and r["ts"] >= since_ts
            and not r["is_nora"] and r["media_json"] is not None
        ]
        matching.sort(key=lambda r: (r["ts"], r["id"]), reverse=True)
        return [self._transcript_row(r) for r in matching[:limit]]

    async def external_file_rows(
        self, *, conversation_id: str, since_ts: int, limit: int,
    ) -> list[TranscriptRow]:
        """Mirrors `SqlRecordStore.external_file_rows`, hidden rows excluded;
        raises when `fail_on_recent` is set."""
        if self.fail_on_recent:
            raise RuntimeError("db down")
        if limit <= 0:
            return []
        matching = [
            r for r in self.rows
            if r["conversation_id"] == conversation_id and r["ts"] >= since_ts
            and not r["is_nora"] and r.get("external_files_json") is not None
            and r["id"] not in self.hidden_ids
        ]
        matching.sort(key=lambda r: (r["ts"], r["id"]), reverse=True)
        return [self._transcript_row(r) for r in matching[:limit]]

    async def recent_before(
        self, *, conversation_id: str, before_ts: int, limit: int,
    ) -> list[TranscriptRow]:
        """Mirrors `SqlRecordStore.recent_before`: `recent` over the rows with
        `ts < before_ts` only."""
        if self.fail_on_recent:
            raise RuntimeError("db down")
        matching = [r for r in self.rows
                    if r["conversation_id"] == conversation_id and r["ts"] < before_ts]
        matching.sort(key=lambda r: (r["ts"], r["id"]))
        window = matching[-limit:] if limit > 0 else []
        return [self._transcript_row(r) for r in window]

    async def depth_to(self, *, conversation_id: str, ts: int) -> int | None:
        """Mirrors `SqlRecordStore.depth_to`: `None` when no row in this
        conversation carries `ts`, otherwise the `recent(limit=...)` size that
        would just reach it."""
        matching = [r for r in self.rows if r["conversation_id"] == conversation_id]
        if not any(r["ts"] == ts for r in matching):
            return None
        return sum(1 for r in matching if r["ts"] >= ts)

    async def since(
        self, *, conversation_id: str, after_id: int, limit: int
    ) -> list[TranscriptRow]:
        """Mirrors `SqlRecordStore.since`: ascending by id, `is_nora` rows
        excluded IN THIS METHOD (not left to the caller), `[]` for a
        non-positive limit. A fake that returned everything would make the
        contract test decorative."""
        if limit <= 0:
            return []
        rows = [
            r for r in self.rows
            if r["conversation_id"] == conversation_id
            and r["id"] > after_id
            and not r["is_nora"]
        ]
        rows.sort(key=lambda r: r["id"])
        return [self._transcript_row(r) for r in rows[:limit]]

    async def since_including_nora(
        self, *, conversation_id: str, after_id: int, limit: int
    ) -> list[TranscriptRow]:
        """Mirrors `SqlRecordStore.since_including_nora`: ascending by id,
        `is_nora` rows INCLUDED (unlike `since`), `[]` for a non-positive
        limit. A fake that returned everything would make the contract test
        decorative."""
        if limit <= 0:
            return []
        rows = [
            r for r in self.rows
            if r["conversation_id"] == conversation_id
            and r["id"] > after_id
        ]
        rows.sort(key=lambda r: r["id"])
        return [self._transcript_row(r) for r in rows[:limit]]

    def _transcript_row(self, r: dict[str, Any]) -> TranscriptRow:
        """The shared row -> `TranscriptRow` mapping for `recent` and
        `since` — mirrors `SqlRecordStore._row` so the two stay legible
        side by side, the edit fold included."""
        latest = self._latest_edit(r["id"])
        return TranscriptRow(
            id=r["id"], sender_id=r["sender_id"], sender_name=r["sender_name"],
            ts=r["ts"], body=latest["body"] if latest else r["body"],
            is_nora=r["is_nora"],
            delivery_status=r["delivery_status"], raw_json=r["raw_json"],
            quoted_ts=r["quoted_ts"], media_json=r["media_json"],
            external_files_json=r.get("external_files_json"),
            person_id=r["person_id"],
            edit=(
                RowEdit(ts=latest["ts"], was=r["body"], raw_json=latest["raw_json"])
                if latest else None
            ),
        )

    async def set_delivery_outcome(
        self, *, row_id: int, status: DeliveryStatus, ts: int | None = None
    ) -> None:
        """Mirrors `SqlRecordStore.set_delivery_outcome`, `ts=None` included:
        a platform that returned no timestamp must leave the row's own alone,
        not blank it. Both halves are pinned for both stores by
        `tests/contract/test_audit_store_contract.py`."""
        for row in self.rows:
            if row["id"] == row_id:
                row["delivery_status"] = status
                if ts is not None:
                    row["ts"] = ts
                return

    async def unattributed(self, *, limit: int) -> list[UnattributedRow]:
        """Mirrors `SqlRecordStore.unattributed`: person_id IS NULL, never
        Nora's own rows, oldest first with `id` only as a tie-break, and `[]`
        for a non-positive limit. Every one of those is pinned for BOTH stores
        by `tests/contract/test_audit_store_contract.py`."""
        if limit <= 0:
            return []
        candidates = [r for r in self.rows if r["person_id"] is None and not r["is_nora"]]
        candidates.sort(key=lambda r: (r["ts"], r["id"]))
        return [
            UnattributedRow(
                id=r["id"], platform=r["platform"], sender_id=r["sender_id"],
                ts=r["ts"], body=r["body"], conversation_id=r["conversation_id"],
                raw_json=r["raw_json"], media_json=r["media_json"],
            )
            for r in candidates[:limit]
        ]

    async def set_person_id(self, *, row_id: int, person_id: str) -> None:
        for row in self.rows:
            if row["id"] == row_id:
                row["person_id"] = person_id
                return

    async def room_activity(self, *, conversation_id: str, since_ts: int) -> RoomActivity:
        """Mirrors `SqlRecordStore.room_activity`: human rows only, the count from
        `since_ts` on, the newest `ts` at any time, `None` when there is none.
        `fail_on_recent` covers this read too: it is the switch for «the
        message store is down», and a store that is down for one read is down
        for both."""
        if self.fail_on_recent:
            raise RuntimeError("db down")
        human = [
            r for r in self.rows
            if r["conversation_id"] == conversation_id and not r["is_nora"]
        ]
        window = [r for r in human if r["ts"] >= since_ts]
        return RoomActivity(
            count=len(window),
            last_ts=max((r["ts"] for r in human), default=None),
            # Mirrors the SQL's coalesce(person_id, sender_id) exactly: only a
            # NULL falls through, so an empty-string person_id counts as itself
            # here as it does there.
            speakers=len({
                r["sender_id"] if r["person_id"] is None else r["person_id"]
                for r in window
            }),
        )

    async def stats(self) -> dict[str, Any]:
        return {
            "messages": len(self.rows),
            "conversations": len({r["conversation_id"] for r in self.rows}),
            "nora_messages": sum(1 for r in self.rows if r["is_nora"]),
            "latest_ts": max((r["ts"] for r in self.rows), default=0),
            "schema_revision": "fake",
        }

    async def aclose(self) -> None:
        self.closed = True


class FakeMemoryStore:
    """In-memory `MemoryStore`, keyword-only to match the port and
    `nora.infra.honcho.client.HonchoClient` exactly, and bound to the client by
    `tests/contract/test_memory_store_contract.py`. It is the suite's ONLY
    memory fake.
    `fail=True` simulates the service being down, the same "log and return a
    benign value" contract `HonchoClient` documents.
    """

    def __init__(
        self,
        *,
        cards: dict[str, list[str]] | None = None,
        conclusions: dict[str, list[str]] | None = None,
        fail: bool = False,
    ) -> None:
        # (peer_id, text, conversation_id) — the conversation keys the session
        # (Honcho v2), so a test can assert a message went under its
        # own speaker's peer into the right conversation's session.
        self.fed: list[tuple[str, str, str]] = []
        self.peers: list[str] = []
        self.cards = cards or {}
        # Separate from `cards` on purpose. `peer_card` and `representation`
        # are different endpoints with different failure shapes on the live
        # instance — `GET /peers/{id}/card` answers `{"peer_card": null}` even
        # for peers that demonstrably have conclusions — and a fake that
        # served one dict for both would make them look interchangeable, which
        # is exactly the "fake answers every request the same way" trap that
        # hid three separate no-ops in this codebase.
        self.conclusions = conclusions or {}
        # (observer, observed, search_query) — the observed is `target or peer_id`,
        # so a test can assert a recall ran as `(observer=nora, observed=person)`
        # rather than the person's self-model. Kept a 3-tuple ON PURPOSE: the
        # observer/observed pair is the whole point of the pair, and a fake
        # that dropped the observer could not tell Nora's model of Miles from
        # Miles's own — the exact "answers every request the same" trap.
        self.queries: list[tuple[str, str, str]] = []
        # (observer, observed, query, reasoning_level) for each `chat` call.
        self.chats: list[tuple[str, str, str, str]] = []
        # (peer_id, content) messages a test seeds for `search` to return.
        self.messages: list[tuple[str, str]] = []
        self.searches: list[str] = []
        # (observer, observed, content) landed conclusion writes — the read
        # side serves these back out of `conclusions` (see `conclude`), and a
        # test asserts the PAIR, not just the text: a write under the wrong
        # peer is a memory about nobody, which is the exact bug the `remember`
        # lane's identity refusal exists to prevent.
        self.writes: list[tuple[str, str, str]] = []
        # (verb, conversation_id, unit) for every routed call — the ROUTE key
        # each call carried (DEC-214). The real client sends a call for a room
        # or unit to that unit's Honcho workspace and scope; a caller that
        # forgets the key reads the wrong workspace (or, with `[units]`,
        # none), so a test of a caller asserts the key here, not just the
        # payload.
        self.routes: list[tuple[str, str | None, str | None]] = []
        # (verb, Nora workspace) for the reads keyed on a whole WORKSPACE (the
        # portal's own-memory page), which name neither a room nor a unit.
        self.workspace_reads: list[tuple[str, str]] = []
        self.fail = fail

    async def ensure_workspace(self) -> None:
        return None

    async def ensure_peer(
        self, *, peer_id: str, conversation_id: str | None = None,
        unit: str | None = None,
    ) -> None:
        self.routes.append(("ensure_peer", conversation_id, unit))
        if not self.fail:
            self.peers.append(peer_id)

    async def feed(
        self,
        *,
        peer_id: str,
        text: str,
        conversation_id: str,
        metadata: dict[str, object] | None = None,
    ) -> bool:
        if self.fail:
            return False
        self.fed.append((peer_id, text, conversation_id))
        return True

    async def conclude(
        self, *, peer_id: str, content: str, target: str,
        conversation_id: str | None = None, unit: str | None = None,
    ) -> bool:
        """Mirrors `HonchoClient.conclude` — the conclusion WRITE lane behind
        the `remember` tool, keyword-only exactly like the port.

        `fail=True` is the down store: False, nothing lands, no raised error —
        the exact never-load-bearing shape the client shows the reply path. A
        landed write records the (observer, observed, content) tuple in
        `writes` AND APPENDS into `conclusions` under the observed peer, so the
        read lane (`representation`/`chat`) serves it right back — the
        remember→about loop, closed in memory, which is the whole point of the
        write lane and the reason it must not write anywhere the readers
        cannot see."""
        self.routes.append(("conclude", conversation_id, unit))
        if self.fail:
            return False
        self.writes.append((peer_id, target, content))
        self.conclusions.setdefault(target, []).append(content)
        return True

    async def peer_card(
        self, *, peer_id: str, conversation_id: str | None = None,
        unit: str | None = None, workspace: str | None = None,
    ) -> list[str]:
        self.routes.append(("peer_card", conversation_id, unit))
        if workspace is not None:
            self.workspace_reads.append(("peer_card", workspace))
        if self.fail:
            return []
        return list(self.cards.get(peer_id, []))

    async def representation(
        self, *, peer_id: str, search_query: str, max_conclusions: int = 5,
        target: str | None = None, conversation_id: str | None = None,
        unit: str | None = None, workspace: str | None = None,
    ) -> Recollection:
        """Mirrors `HonchoClient.representation`, INCLUDING the shape it
        returns for a peer it has never heard of.

        `peer_id` is the OBSERVER, `target` the OBSERVED; the
        conclusions are keyed by the observed (`target or peer_id`), so a test
        sets what is known ABOUT a person and reaches it as Nora's model of them
        (`peer_id=nora, target=person`) or the person's self-model (`target=None`).
        The observer is recorded in `queries` either way.

        The live instance answers **200 with an empty string** for an unknown
        peer rather than 404, so an unknown peer here is
        `Recollection(reachable=True, conclusions=())` — reachable, knowing
        nothing — and only `fail=True` produces `reachable=False`. A fake that
        collapsed those two into one value would let every test asserting the
        distinction pass against a client that had lost it, which is precisely
        how `peer_card` shipped dead.
        """
        observed = target if target is not None else peer_id
        self.routes.append(("representation", conversation_id, unit))
        if workspace is not None:
            self.workspace_reads.append(("representation", workspace))
        self.queries.append((peer_id, observed, search_query))
        if self.fail:
            return Recollection(reachable=False)
        return Recollection(
            reachable=True,
            conclusions=tuple(self.conclusions.get(observed, ()))[:max_conclusions],
        )

    async def chat(
        self, *, peer_id: str, query: str, reasoning_level: str = "low",
        target: str | None = None, conversation_id: str | None = None,
        unit: str | None = None,
    ) -> str | None:
        """Mirrors `HonchoClient.chat` — the dialectic verb. Synthesises a stand-in
        answer from the observed peer's conclusions (`target or peer_id`), so a
        test that seeds conclusions gets a non-None reasoned string and an unseeded
        peer gets None (the honest «nothing to say»). `fail=True` → None."""
        observed = target if target is not None else peer_id
        self.routes.append(("chat", conversation_id, unit))
        self.chats.append((peer_id, observed, query, reasoning_level))
        if self.fail:
            return None
        known = self.conclusions.get(observed, ())
        if not known:
            return None
        return " ".join(known)

    async def search(
        self, *, query: str, conversation_id: str | None = None,
        limit: int = 8, unit: str | None = None,
    ) -> MessageHits:
        """Mirrors `HonchoClient.search`, INCLUDING its failure shape: a
        `fail=True` adapter is UNREACHABLE, not empty. Returning `[]` for both —
        which this did — is a fake that answers every request the same way, and
        it is why `search_memory` reported a Honcho outage as «nothing was said
        here» with no test catching it."""
        self.routes.append(("search", conversation_id, unit))
        self.searches.append(query)
        if self.fail:
            return MessageHits(reachable=False)
        return MessageHits(reachable=True, hits=tuple(list(self.messages)[:limit]))

    async def health(self) -> MemoryHealth:
        return MemoryHealth(reachable=not self.fail)

    async def aclose(self) -> None:
        return None


class FakeRosterSource:
    """In-memory `nora.domain.ports.RosterSource`.

    Bound to `nora.platform.signal.roster.SignalRoster` by
    `tests/contract/test_roster_source_contract.py` — a port never gains a
    second implementation, test fakes included, without a suite both pass.

    `available=False` is reachable two ways here because it is reachable two
    ways in production: the daemon is unreadable (`fail=True`), or the daemon
    answered but does not list this conversation (an unknown id — stale group
    state on a linked device, which this deployment has actually hit). Neither
    is an empty roster, and the contract suite pins that both stores agree.
    """

    platform = "signal"

    def __init__(
        self,
        *,
        groups: dict[str, tuple[frozenset[str], ...]] | None = None,
        fail: bool = False,
    ) -> None:
        self.groups = groups or {}
        self.fail = fail
        self.calls: list[str] = []

    async def roster(self, *, conversation_id: str) -> Roster:
        self.calls.append(conversation_id)
        if self.fail or conversation_id not in self.groups:
            return Roster(available=False)
        return Roster(available=True, members=tuple(self.groups[conversation_id]))


class FakeAdapter:
    """Records sends instead of performing them; parses signal-cli-shaped envelopes
    inline (spec E.3) rather than importing `nora.platform.signal.parser` — `nora.testing`
    sits below `nora.platform` in the layers contract (`nora.agent | nora.platform` may
    import `nora.testing`, not the other way around), so this reimplements just the
    group-message subset of `parse_event` it needs.

    `SignalAdapter` now satisfies `nora.domain.ports.PlatformAdapter` in full — the
    platforms phase closed every gap, including `isinstance(SignalAdapter(...),
    PlatformAdapter)` (`tests/contract/test_port_conformance.py`). This fake keeps
    `resolve_identity()`/`send()`/`health()` in the port's shape, but `.listen()` stays
    a no-op and `.parse()` stays public and dict-in rather than mirroring
    `SignalAdapter.listen(*, on_message=...)`'s internal parse-and-drop: nothing here
    drives this fake through a real SSE loop — `evals/harness.py` and
    `tests/integration/harness.py`'s `_HandlerView` both call `.parse()` directly, the
    same parse-then-dispatch shape `SignalAdapter.listen` uses internally, to turn one
    raw `group_event(...)` into a `NormalizedMessage` without a socket. Keeping `.parse()`
    public here does not reintroduce the "second port implementation needs a contract
    suite" rule either: `parse()` is not on the port at all (`nora.domain.ports`'s
    module docstring).

    `_account` is retained (not `own_account`) for parsing and for
    `resolve_identity()`'s `primary` — `nora.engagement.stages.admission`'s R-12 no
    longer reads it directly (it takes a resolved `PlatformIdentity` now), but
    `resolve_identity()` still needs *some* account value to publish.
    """

    platform = "signal"

    def __init__(
        self, *, succeed: bool = True, account: str = "", react_fails: bool = False,
    ) -> None:
        self.sent: list[tuple[ConversationTarget, str]] = []
        # Parallel to `sent`, one entry per send — see `send`'s docstring for
        # why the decoration is beside the list rather than inside its tuples.
        self.sent_mentions: list[tuple[Mention, ...]] = []
        self.sent_styles: list[tuple[TextStyle, ...]] = []
        self.sent_previews: list[LinkPreview | None] = []
        self.reactions: list[tuple[ConversationTarget, str, str, int, str, bool]] = []
        self._succeed = succeed
        self._react_fails = react_fails
        self._account = account

    async def resolve_identity(self) -> PlatformIdentity:
        return PlatformIdentity(platform=self.platform, primary=self._account or "nora")

    async def listen(self, on_event) -> None:
        return None

    async def parse(self, raw_event: dict) -> NormalizedMessage | None:
        if (raw_event or {}).get("method") != "receive":
            return None
        env = ((raw_event.get("params") or {}).get("envelope")) or {}
        data = env.get("dataMessage")
        if not isinstance(data, dict):
            return None
        group_id = (data.get("groupInfo") or {}).get("groupId")
        if not group_id:
            return None
        source = env.get("source") or ""
        if self._account and source == self._account:
            return None
        text = (data.get("message") or "").strip()
        if not text:
            return None
        return NormalizedMessage(
            platform="signal", conversation_id=group_id, conversation_type="group",
            sender_id=source, sender_name=env.get("sourceName") or None,
            timestamp=int(env.get("timestamp") or data.get("timestamp") or 0),
            body=text, raw=raw_event,
        )

    async def send(
        self,
        target: ConversationTarget,
        text: str,
        mentions: tuple[Mention, ...] = (),
        styles: tuple[TextStyle, ...] = (),
        preview: LinkPreview | None = None,
        attachments: tuple[OutboundAttachment, ...] = (),
        quote: object | None = None,
    ) -> SendResult:
        """Records the send instead of performing it, DECORATION AND ALL.

        `mentions`/`styles`/`preview` are recorded, not dropped, and that is the
        point rather than bookkeeping: this is `PlatformAdapter.send`'s second
        implementation, so `tests/contract/test_send_contract.py` runs one suite
        over it and over `SignalAdapter` to keep the two in agreement — a fake
        that accepted the arguments and forgot them would let every pipeline
        test prove a mention was composed while proving nothing about whether it
        reached the wire.

        **It also drops the ranges the real transport drops**, which is not
        symmetry for its own sake: `SignalSender._text_style_params` refuses a
        style that does not fit the text because signal-cli fails the whole SEND
        on one, and a fake that recorded it anyway would let a test assert a
        range was «sent» that the daemon would have answered with «Invalid
        textStyle syntax» and no message. The fake has to be wrong in the same
        places the server is, or it is not measuring the server.

        `self.sent` keeps its two-tuple shape (target, text) because dozens of
        assertions read it and a message's TEXT is what almost all of them are
        about; the decoration lands on `self.sent_mentions`/`self.sent_styles`/
        `self.sent_previews` in step, one entry per send, so an index into any
        of them matches `sent`.
        """
        self.sent.append((target, text))
        self.sent_mentions.append(tuple(mentions))
        limit = utf16_len(text)
        self.sent_styles.append(tuple(
            style for style in styles
            if style.start >= 0 and style.length > 0
            and style.start + style.length <= limit
        ))
        self.sent_previews.append(preview)
        return SendResult(ok=self._succeed)

    async def react(
        self,
        *,
        target: ConversationTarget,
        emoji: str,
        to_author: str,
        to_timestamp: int,
        to_message_id: str = "",
        remove: bool = False,
    ) -> ReactionResult:
        """Records reactions instead of performing them.

        Keyword-only and `ReactionResult`-returning, matching the port exactly —
        unlike `send` above, whose positional drift predates the port. This is
        `PlatformAdapter.react`'s second implementation, so per CLAUDE.md it is
        bound to `SignalAdapter.react` by a shared contract suite both pass
        (`tests/contract/test_reaction_contract.py`) rather than being free to
        drift. `FakeAdapter`'s documented exemption does not stretch to cover it:
        that exemption is about `parse()`/`listen()`, which are not on the port
        at all, whereas `react()` is.

        `react_fails=True` simulates a working platform whose reaction call is
        rejected — the R-5 path where the reply must still go out.
        """
        self.reactions.append(
            (target, emoji, to_author, to_timestamp, to_message_id, remove)
        )
        return ReactionResult(ok=not self._react_fails)

    async def health(self) -> AdapterHealth:
        return AdapterHealth(platform=self.platform, connected=True, last_event_age_seconds=0.0)

    @property
    def texts(self) -> list[str]:
        return [text for _, text in self.sent]


class FakeMediaSource:
    """In-memory `MediaSource`, bound to `SignalMediaSource` by
    `tests/contract/test_media_source_contract.py`.

    **It does not answer every request the same way, and that is the whole
    reason it is written like this.** A fake that returned bytes for any ref
    would let the custody stage pass while the real adapter asked signal-cli
    for the wrong thing — the exact shape of the three no-ops CLAUDE.md
    records. So it behaves like the daemon does: it holds a dict keyed by
    `ref.fetch_token or ref.platform_id` — stickers are addressed by TOKEN,
    attachments by id, the same split the real adapters make — answers
    `not_found` for a key it does not hold (which is what `getAttachment`
    returns), `no_id` for a ref with neither,
    and `too_large` against the same ceiling. `unavailable` is a switch,
    because "the daemon is down" is a state the ingest stage has to degrade
    through and there is no id that means it.
    """

    platform = "signal"

    def __init__(
        self,
        blobs: dict[str, bytes] | None = None,
        *,
        unavailable: bool = False,
        max_bytes: int = 100 * 1024 * 1024,
    ) -> None:
        self.blobs = dict(blobs or {})
        self.unavailable = unavailable
        self.fetched: list[str] = []
        self._max_bytes = max_bytes

    async def fetch(self, *, ref: MediaRef) -> MediaBytes:
        # Stickers are addressed by TOKEN, attachments by id — the same split
        # the real adapters make. A fake that answered both off `platform_id`
        # would let a stage that routes a sticker to `getAttachment` pass.
        # Lookup and recording share this one key so "no RPC happened"
        # assertions in a caller's tests stay readable.
        key = ref.fetch_token or ref.platform_id
        if not key:
            return MediaBytes(available=False, reason="no_id")
        if ref.size is not None and ref.size > self._max_bytes:
            return MediaBytes(available=False, reason="too_large")
        self.fetched.append(key)
        if self.unavailable:
            return MediaBytes(available=False, reason="unreachable")
        data = self.blobs.get(key)
        if data is None:
            return MediaBytes(available=False, reason="not_found")
        if len(data) > self._max_bytes:
            return MediaBytes(available=False, reason="too_large")
        return MediaBytes(available=True, data=data)


class FakeBlobStore:
    """In-memory `BlobStore`, bound to `FilesystemBlobStore` by
    `tests/contract/test_blob_store_contract.py`.

    Content-addressed for real — it hashes the bytes rather than handing out a
    counter — because the key is the thing every caller reasons about: dedup,
    verifiability and the rendered handle all come out of it, and a fake that
    invented keys would make all three untestable against production.

    `fail_on_put` is the disk refusing. The custody stage must record the
    message without a handle and count the failure, and the only way to test
    that is a store that can fail on demand.

    Every `put`, of new bytes or held ones, is stamped with `clock` (seconds),
    which `delete`'s `idle_seconds` reads, as the real stores read a file's
    modification time or an object's `LastModified`. `before_delete` runs
    inside `delete` before its check: what a concurrent writer does between
    a caller's decision and the deletion.
    """

    def __init__(
        self, *, fail_on_put: bool = False, clock: Callable[[], float] = time.time,
        before_delete: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.blobs: dict[str, bytes] = {}
        self.put_at: dict[str, float] = {}
        self.fail_on_put = fail_on_put
        self.puts = 0
        self.deletes = 0
        self._clock = clock
        self._before_delete = before_delete

    async def put(self, *, data: bytes) -> str:
        if self.fail_on_put:
            raise OSError("no space left on device")
        self.puts += 1
        key = hashlib.sha256(data).hexdigest()
        self.blobs.setdefault(key, data)
        self.put_at[key] = self._clock()
        return key

    async def get(self, *, key: str) -> bytes | None:
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("blob key must be a lowercase sha256 hex digest")
        return self.blobs.get(key)

    async def delete(self, *, key: str, idle_seconds: float) -> BlobDeletion:
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("blob key must be a lowercase sha256 hex digest")
        if self._before_delete is not None:
            await self._before_delete(key)
        if key not in self.blobs:
            return "absent"
        if self._clock() - self.put_at.get(key, float("-inf")) < idle_seconds:
            return "kept"
        self.deletes += 1
        del self.blobs[key]
        return "deleted"


class FakeMediaDescriptionStore:
    """In-memory `MediaDescriptionStore`, bound to `SqlMediaDescriptionStore` by
    `tests/contract/test_media_description_store_contract.py`.

    **It does not answer every request the same way.** Both levels are real
    dicts keyed the way production keys them, so a stage that looks up the
    wrong key misses here exactly as it would against Postgres. A fake that
    always hit would make every dedup test decorative — and dedup is the half
    of this feature that is supposed to save the calls.

    `unavailable` is the store being down. Both reads must degrade to a miss
    and `remember` must swallow, which is the behaviour the ingest stage has to
    be testable against.
    """

    def __init__(self, *, unavailable: bool = False) -> None:
        self.unavailable = unavailable
        self.index: dict[tuple[str, str], MediaBlob] = {}
        self.descriptions: dict[str, str] = {}
        self.resolved: list[tuple[str, str]] = []
        self.described: list[str] = []
        self.remembered: list[tuple[str, str, str]] = []
        # The `model` each `remember` carried, in call order. `media_fetch` must
        # thread the REAL configured describer name into the durable row rather
        # than hardcode one, and only a store that records it can prove that.
        self.remembered_models: list[str] = []

    async def resolve(self, *, platform: str, token: str) -> MediaBlob | None:
        self.resolved.append((platform, token))
        if self.unavailable:
            return None
        return self.index.get((platform, token))

    async def describe(self, *, sha256: str) -> str | None:
        self.described.append(sha256)
        if self.unavailable:
            return None
        description = self.descriptions.get(sha256)
        # EMPTY description is a MISS, matching `SqlMediaDescriptionStore`: the
        # DEC-138 M4 inbound row carries `description=""` as an index
        # placeholder before any vision model has run, and the cache boundary
        # must treat that as "not described yet" — never as a cached empty
        # description that short-circuits the describer.
        return description if description else None

    async def remember(
        self, *, platform: str, token: str, blob: MediaBlob,
        description: str, model: str,
    ) -> None:
        self.remembered.append((platform, token, blob.sha256))
        self.remembered_models.append(model)
        if self.unavailable:
            return
        self.index[(platform, token)] = blob
        self.descriptions[blob.sha256] = description


class FakeVideoReadingStore:
    """In-memory `VideoReadingStore`, bound to `SqlVideoReadingStore` by
    `tests/contract/test_video_reading_store_contract.py`.

    Keyed on the WHOLE `VideoReadingKey`, exactly as the table is, so a caller
    that builds the key from the wrong geometry or model misses here as it
    would against Postgres. `unavailable` is the store being down: `get`
    misses and `put` swallows.
    """

    def __init__(self, *, unavailable: bool = False) -> None:
        self.unavailable = unavailable
        self.readings: dict[VideoReadingKey, str] = {}
        self.gets: list[VideoReadingKey] = []
        self.puts: list[tuple[VideoReadingKey, str]] = []

    async def get(self, *, key: VideoReadingKey) -> str | None:
        self.gets.append(key)
        if self.unavailable:
            return None
        return self.readings.get(key) or None

    async def put(self, *, key: VideoReadingKey, reading: str) -> None:
        self.puts.append((key, reading))
        if self.unavailable or not reading:
            return
        self.readings[key] = reading


class FakeMediaObjectStore:
    """In-memory `MediaObjectStore`, bound to `SqlMediaObjectStore` by
    `tests/contract/test_media_object_store_contract.py`.

    Mirrors the SQL store's OBSERVABLE semantics rather than a convenient
    subset, because two copies that disagree (one substring-matching
    `description`/`prompt`/`title`, the other returning every row for any query)
    certify nothing. Here, as in Postgres:

    - `save` is first-source-wins (`on_conflict_do_nothing` on `sha256`) and
      answers True whether or not the row was new; every call is recorded in
      `saved` so a write path can assert WHAT it wrote.
    - `search` ANDs each whitespace-separated word as a case-insensitive
      substring of `description` OR of the provenance rendered as JSON text
      (the `provenance::text` cast — keys included, which is what production
      matches against). Empty query → newest `limit` rows. `sources` filters.
    - `by_prefix` matches `sha256` by prefix over the WHOLE index and returns
      every hit up to `limit`, newest first — the caller decides what a
      collision means.
    - `fail=True` is the database being unreachable: `save` answers False and
      every read answers empty, exactly as the SQL store's `except Exception`
      arms do. The tools above it must read that as `unavailable`.
    """

    def __init__(self, objs: Sequence[MediaObject] = (), *, fail: bool = False) -> None:
        self.objs: dict[str, MediaObject] = {}
        self.saved: list[MediaObject] = []
        self.comprehended: list[tuple[str, str, str, str, str]] = []
        self.fail = fail
        for obj in objs:
            self.objs.setdefault(obj.sha256, obj)

    def _newest_first(self, rows: Sequence[MediaObject]) -> list[MediaObject]:
        return sorted(rows, key=lambda o: o.created_at_ms, reverse=True)

    async def save(self, *, obj: MediaObject) -> bool:
        """First-source-wins, EXCEPT that it repairs a comprehension shell —
        the row `comprehend` inserts when a spawned read beats the inline
        custody write (`media_type=""`, `created_at_ms=0`). Mirrors the SQL
        store's `where` clause exactly: the comprehension FIELDS are never
        rolled back by custody catching up, and a real row is untouched."""
        if self.fail:
            return False
        self.saved.append(obj)
        row = self.objs.get(obj.sha256)
        if row is None:
            self.objs[obj.sha256] = obj
            return True
        if row.media_type == "" and row.created_at_ms == 0:
            merged = dict(row.provenance or {})
            merged.update(obj.provenance or {})
            self.objs[obj.sha256] = replace(
                obj, title=row.title, description=row.description,
                content=row.content, describe_model=row.describe_model,
                provenance=merged,
            )
        return True

    async def comprehend(
        self, *, sha256: str, title: str, description: str, content: str,
        model: str, provenance: dict[str, object] | None = None,
    ) -> bool:
        """The read landing on a row, matching the SQL store's upsert exactly:
        it creates the row when custody has not written one yet (the two
        writers race by design — custody is inline, the read is spawned), it
        replaces the three comprehension fields and `describe_model`, and it
        MERGES `provenance` rather than replacing it so a generated image's
        birth record survives a later read of the same bytes. `source` and
        `created_at_ms` are never touched on the conflict arm."""
        if self.fail:
            return False
        self.comprehended.append((sha256, title, description, content, model))
        row = self.objs.get(sha256)
        merged = dict((row.provenance if row else None) or {})
        merged.update(provenance or {})
        if row is None:
            self.objs[sha256] = MediaObject(
                sha256=sha256, media_type="", size=0, source="inbound",
                title=title, description=description, content=content,
                describe_model=model or None, provenance=merged, created_at_ms=0,
            )
            return True
        # An EMPTY incoming field keeps what is stored, matching the SQL
        # store's `coalesce(nullif(...))` exactly: the custody stage's inline
        # voice-note write sends `content` alone, and unconditional SETs had a
        # RESENT voice note wiping the title the job had written.
        self.objs[sha256] = replace(
            row,
            title=title or row.title,
            description=description or row.description,
            content=content or row.content,
            # Keyed on the MODEL, mirroring the SQL store's `coalesce`: a
            # writer that names none is custody, which wrote no text and must
            # leave this column alone. Keyed on the description it was NULL for
            # every video on the shipped config; keyed on "any of the three" a
            # resent voice note wiped it, because custody always sends
            # `content`.
            describe_model=(model or None) or row.describe_model,
            provenance=merged,
        )
        return True

    async def search(
        self, *, query: str, limit: int = 20, sources: tuple[str, ...] = (),
        since_ms: int = 0,
    ) -> tuple[MediaObject, ...]:
        found = await self.lookup_search(
            query=query, limit=limit, sources=sources, since_ms=since_ms,
        )
        return tuple(found.objects.values())

    async def lookup_search(
        self, *, query: str, limit: int = 20, sources: tuple[str, ...] = (),
        since_ms: int = 0,
    ) -> MediaLookup:
        """Mirrors `SqlMediaObjectStore.lookup_search`: `fail=True` is
        `available=False` with no objects, never an empty success."""
        if self.fail:
            return MediaLookup(available=False, reason="store")
        words = [w.casefold() for w in query.split() if w]
        pool = self._newest_first(list(self.objs.values()))
        if sources:
            pool = [o for o in pool if o.source in sources]
        if since_ms > 0:
            pool = [o for o in pool if o.created_at_ms >= since_ms]
        if words:
            def matches(o: MediaObject) -> bool:
                # All THREE comprehension fields plus the provenance text,
                # matching `SqlMediaObjectStore.search` exactly:
                # one model pass writes the title and the legible content too,
                # and a fake that searched only `description` would let a query
                # that works in Postgres come back empty here.
                haystacks = (
                    o.title.casefold(), o.description.casefold(),
                    o.content.casefold(),
                    json.dumps(o.provenance or {}).casefold(),
                )
                return all(any(w in h for h in haystacks) for w in words)
            pool = [o for o in pool if matches(o)]
        return MediaLookup(available=True, objects={o.sha256: o for o in pool[:limit]})

    async def get(self, *, sha256: str) -> MediaObject | None:
        if self.fail:
            return None
        return self.objs.get(sha256)

    async def lookup(self, *, sha256s: tuple[str, ...]) -> MediaLookup:
        """Mirrors `SqlMediaObjectStore.lookup`, INCLUDING the axis `get` does
        not have: `fail=True` answers `available=False` with no objects, where
        `get` answers `None` and is indistinguishable from a miss. A fake that
        collapsed the two would let the render join's `unavailable` branch —
        the one an operator is paged by — go untested."""
        if not sha256s:
            # BEFORE the failure check, matching the SQL store exactly: no
            # shas is no query, and a query nobody made cannot have failed.
            # The fake checked `fail` first and answered `unavailable` for an
            # empty tuple where production answers `available=True` — the
            # wrong-call shape the contract suite exists to keep in step, and
            # the healthy fixture was the only one testing that argument.
            return MediaLookup(available=True, objects={})
        if self.fail:
            return MediaLookup(available=False, reason="store")
        return MediaLookup(
            available=True,
            objects={s: self.objs[s] for s in sha256s if s in self.objs},
        )

    async def by_prefix(self, *, handle: str, limit: int = 100) -> tuple[MediaObject, ...]:
        if self.fail:
            return ()
        hits = [o for o in self.objs.values() if o.sha256.startswith(handle)]
        return tuple(self._newest_first(hits)[:limit])

    async def lookup_prefix(self, *, handle: str, limit: int = 100) -> MediaLookup:
        """Mirrors `SqlMediaObjectStore.lookup_prefix`: `fail=True` is
        `available=False` with no objects, never an empty success."""
        if self.fail:
            return MediaLookup(available=False, reason="store")
        hits = [o for o in self.objs.values() if o.sha256.startswith(handle)]
        return MediaLookup(available=True,
                           objects={o.sha256: o for o in self._newest_first(hits)[:limit]})


class FakeReverseGeocoder:
    """In-memory `ReverseGeocoder`, bound to the Google and HK-gov backends by
    `tests/contract/test_geocoder_contract.py`.

    One address for every coordinate, and every call recorded in `calls` —
    the assertion that matters most in the tools above this port is HOW MANY
    times it was reached (a screened coordinate must reach nothing, an
    on-demand lookup exactly once), so a fake that did not record would make
    those assertions vacuous. `address=None` is an honest miss; `fail=True` is
    the backend down, which the port says is ALSO None and never a raise —
    the fake keeps them as two switches because a test wants to reach each on
    purpose, the way the contract suite's `break_it()` does.
    """

    def __init__(self, address: str | None = "Yoho Mall", *, fail: bool = False) -> None:
        self.address = address
        self.fail = fail
        self.calls: list[tuple[float, float]] = []

    async def address_for(self, *, lat: float, lng: float) -> str | None:
        self.calls.append((lat, lng))
        if self.fail:
            return None
        return self.address


class FakeGeocodeCacheStore:
    """In-memory `GeocodeCacheStore`, bound to `SqlGeocodeCacheStore` by
    `tests/contract/test_geocode_cache_store_contract.py`. `gets`/`puts`
    record every call so a caller's read-through/write-through behaviour
    (`CachingBilingualGeocoder`) is provable, not just its end result."""

    def __init__(self) -> None:
        self._data: dict[tuple[float, float], str] = {}
        self._areas: dict[tuple[float, float], PlaceArea] = {}
        self.gets: list[tuple[float, float]] = []
        self.puts: list[tuple[float, float, str]] = []

    async def get(self, *, lat_rounded: float, lng_rounded: float) -> str | None:
        self.gets.append((lat_rounded, lng_rounded))
        return self._data.get((lat_rounded, lng_rounded))

    async def put(
        self, *, lat_rounded: float, lng_rounded: float, address: str,
        area: PlaceArea | None = None,
    ) -> None:
        self.puts.append((lat_rounded, lng_rounded, address))
        self._data[(lat_rounded, lng_rounded)] = address
        self._areas[(lat_rounded, lng_rounded)] = area or PlaceArea()

    def _near(self, lat: float, lng: float, radius_m: float) -> list[tuple[float, float]]:
        """Keys within `radius_m`, nearest first (ties in insertion order)."""
        found = [
            (haversine_distance_m(lat, lng, k[0], k[1]), i, k)
            for i, k in enumerate(self._data)
        ]
        return [k for d, _, k in sorted(found, key=lambda t: (t[0], t[1])) if d <= radius_m]

    async def nearest_within(
        self, *, lat: float, lng: float, radius_m: float
    ) -> str | None:
        near = self._near(lat, lng, radius_m)
        return self._data[near[0]] if near else None

    async def area_within(
        self, *, lat: float, lng: float, radius_m: float
    ) -> PlaceArea | None:
        for key in self._near(lat, lng, radius_m):
            if self._areas[key]:
                return self._areas[key]
        return None


class FakeMediaInterpreter:
    """In-memory `MediaInterpreter`, bound to `MediaComprehender` by
    `tests/contract/test_media_interpreter_contract.py`. `calls` is the point
    of it: which media actually reached a model is proved by what this
    RECORDS, not by reading the stage and believing it.

    Mirrors `MediaComprehender`'s own gates rather than a convenient subset,
    because a fake that answered every request the same way would make every
    test above it decorative:

    - a media type outside image/video/audio refuses with
      `unsupported_format` before touching `available`, exactly like
      production refuses a lottie before any model call goes out — and the
      refusal is still RECORDED, because the invariant tests assert on what
      was ASKED, not only on what was answered;
    - EMPTY bytes refuse the same way (there is nothing to read);
    - `summarize` refuses `empty` for blank text, so the voice-note lane's
      "no transcript, no claim" branch is reachable here too.

    THREE FIELDS: `title`/`text`/`content` come back together,
    because one model pass produces all three and a fake returning only a
    description would let a caller forget to store the other two.
    """

    def __init__(self, text: str = "a cartoon cat", *, available: bool = True,
                 reason: str = "", title: str = "a cat",
                 content: str = "") -> None:
        self.text = text
        self.title = title
        self.content = content
        self.available = available
        self.reason = reason
        self.calls: list[tuple[str, int]] = []
        self.labels: list[str] = []
        self.contexts: list[str] = []
        self.summarized: list[str] = []
        self.model = "fake-vision"
        self.text_model = "fake-text"

    async def describe(
        self, *, data: bytes, media_type: str, label: str = "", context: str = "",
    ) -> MediaDescription:
        self.calls.append((media_type, len(data)))
        self.labels.append(label)
        self.contexts.append(context)
        top = media_type.partition("/")[0].lower()
        if top not in ("image", "video", "audio") or not data:
            return MediaDescription(available=False, reason="unsupported_format")
        if not self.available:
            return MediaDescription(available=False, reason=self.reason or "vision")
        if not (self.title or self.text or self.content):
            # A 200 with nothing readable in it is a FAILURE TO ANSWER, never
            # "the image was blank" — the same call `MediaComprehender` makes,
            # and the same call `VisionClient` makes one level below it. A fake
            # that returned an available-but-empty triple here would let a
            # caller store a blank comprehension and stay green (R-51).
            return MediaDescription(available=False, reason="empty")
        return MediaDescription(
            available=True, title=self.title, text=self.text,
            content=self.content, model=self.model,
        )

    async def summarize(self, *, text: str) -> MediaDescription:
        self.summarized.append(text)
        if not text.strip():
            return MediaDescription(available=False, reason="empty")
        if not self.available:
            return MediaDescription(available=False, reason=self.reason or "vision")
        if not (self.title or self.text):
            return MediaDescription(available=False, reason="empty")
        # A DIFFERENT model from `describe`'s, mirroring production: a voice
        # note's headline is written by the text model, not by the describer
        # that would have read its bytes. A fake reporting one model for both
        # would let a caller conflate them and stay green.
        return MediaDescription(
            available=True, title=self.title, text=self.text,
            model=self.text_model,
        )


class FakeTranscriber:
    """In-memory `Transcriber`, bound to `SenseVoiceTranscriber` by
    `tests/contract/test_transcriber_contract.py`. `calls` is the point of
    it: the ingest stage's voice-note-only gate (`nora/ingest/stages/media.py`)
    is proved by what this RECORDS, not by reading the stage and believing it.

    Mirrors `SenseVoiceTranscriber.transcribe`'s own `media_type` belt gate:
    a `media_type` that is KNOWN and is not `audio/*` refuses with
    `unsupported` before touching `available`/`text`; an UNRESOLVED type
    (`""`) is let through, exactly like production. Without this gate a test
    could feed a non-audio type through the fake and see it refused even if
    the real client never checked at all — the fake would mask the omission
    instead of proving it absent.
    """

    def __init__(self, text: str = "喂你今日得唔得閒啊", *, available: bool = True,
                 reason: str = "") -> None:
        self.text = text
        self.available = available
        self.reason = reason
        self.calls: list[tuple[bytes, str, str]] = []

    async def transcribe(
        self, *, data: bytes, media_type: str, language: str = ""
    ) -> SttResult:
        if media_type and not media_type.startswith("audio/"):
            return SttResult(available=False, reason="unsupported")
        self.calls.append((data, media_type, language))
        if not self.available:
            return SttResult(available=False, reason=self.reason or "error")
        return SttResult(available=True, text=self.text)


class FakeLinkPreviewSource:
    """In-memory `LinkPreviewSource`, bound to `nora.infra.preview.
    OpenGraphPreviewSource` by `tests/contract/test_link_preview_contract.py`.

    **It does not answer every request the same way, and that is the whole
    reason it is written like this** (CLAUDE.md; `FakeMediaSource` makes the
    same argument at length). A fake that returned a card for any URL would let
    the composer's tests pass while production refused the same URL at its SSRF
    check, or dropped it for having no `og:title` — the two most likely real
    outcomes. So it reproduces the production source's decision table rather
    than its transport:

    * a URL it holds a page for → that page's card;
    * a page with no title → `None`, because `--preview-title` is mandatory and
      `OpenGraphPreviewSource` refuses a titleless card;
    * a non-`http(s)` scheme, or a host that is not globally routable →
      `None`, the SSRF refusal, keyed off the same two properties the real
      guard checks so a test can exercise the branch without a network;
    * a URL it has never heard of → `None`, which is what a 404 or a dead host
      produces;
    * `unavailable=True` → `None` for everything, the "the whole capability is
      having a bad day" state that no single URL can express.

    `requested` records what was asked for, so a test can prove the composer
    only fetches when there IS a link and only once per message.
    """

    def __init__(
        self,
        pages: dict[str, LinkPreview] | None = None,
        *,
        unavailable: bool = False,
    ) -> None:
        self.pages = dict(pages or {})
        self.unavailable = unavailable
        self.requested: list[str] = []

    async def preview_for(self, *, url: str) -> LinkPreview | None:
        self.requested.append(url)
        if self.unavailable:
            return None
        if not _fetchable(url):
            return None
        preview = self.pages.get(url)
        if preview is None or not preview.title:
            return None
        return preview


def _fetchable(url: str) -> bool:
    """The two refusals `nora.infra.preview._safe` makes, without a resolver.

    Deliberately NOT a copy of the real guard — it cannot be, since the real
    one calls `getaddrinfo` and this must run offline. It reproduces the two
    properties a test can state literally (scheme, and a host that is obviously
    not global), which is enough for the contract suite to assert that BOTH
    implementations refuse `http://127.0.0.1:9100/metrics` and `file:///etc/
    passwd`. The full address check is production's, and the contract suite
    says so where it parametrizes.
    """
    scheme, _, rest = url.partition("://")
    if scheme not in ("http", "https"):
        return False
    host = rest.split("/")[0].split(":")[0].lower()
    private = ("localhost", "127.", "10.", "192.168.", "169.254.", "0.")
    return not (host.startswith(private) or host.endswith(".local"))


class FakeComputeBackend:
    """In-memory `ComputeBackend`. Every `launch`ed job defaults to immediate
    success (a caller that doesn't care about timing gets a working job with
    no setup) — call `set_result` with `polls_before_done > 0` to arrange a
    job that reports RUNNING some number of times first, the shape the
    fast-path/slow-path branching needs to test both
    branches deterministically, without a real timer.

    `launched` records every call's `task`/`artifact_id`/`context`/assigned
    `job_id`, the same "record what was actually asked for" pattern
    `FakeRecordStore.rows` already uses
    elsewhere in this file.
    """

    def __init__(self) -> None:
        self._results: dict[str, JobResult] = {}
        self._polls_before_done: dict[str, int] = {}
        self.launched: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self._next_id = 1

    async def launch(self, *, request: LaunchRequest) -> str:
        job_id = f"job_{self._next_id:06d}"
        self._next_id += 1
        self.launched.append({
            "task": request.task, "artifact_id": request.artifact_id,
            "context": request.context, "job_id": job_id, "request": request,
        })
        self._results.setdefault(
            job_id,
            JobResult(
                status=JobStatus.SUCCEEDED,
                url="https://portal.example.net/artifacts/fake",
                summary="done",
            ),
        )
        self._polls_before_done.setdefault(job_id, 0)
        return job_id

    def set_result(
        self, *, job_id: str, result: JobResult, polls_before_done: int = 0,
    ) -> None:
        self._results[job_id] = result
        self._polls_before_done[job_id] = polls_before_done

    async def cancel(self, *, job_id: str) -> None:
        self.cancelled.append(job_id)

    async def poll(self, *, job_id: str) -> JobResult:
        if job_id not in self._results:
            return JobResult(status=JobStatus.FAILED, error="unknown job_id")
        if self._polls_before_done[job_id] > 0:
            self._polls_before_done[job_id] -= 1
            return JobResult(status=JobStatus.RUNNING)
        return self._results[job_id]


class FakeComputeJobStore:
    """In-memory `ComputeJobStore`. Bound to
    `nora.infra.sql.compute_jobs.SqlComputeJobStore` by
    `tests/contract/test_compute_job_store_contract.py`."""

    def __init__(self) -> None:
        self._jobs: dict[UUID, ComputeJob] = {}

    async def create(self, job: ComputeJob) -> None:
        self._jobs[job.id] = normalise_compute_job(job)

    async def get(self, job_id: UUID) -> ComputeJob | None:
        return self._jobs.get(job_id)

    async def pending(self, *, limit: int) -> list[ComputeJob]:
        rows = [j for j in self._jobs.values() if j.state is ComputeJobState.PENDING]
        rows.sort(key=lambda j: (j.created_at, str(j.id)))
        return rows[:limit]

    async def room_jobs(
        self, *, platform: str, conversation_id: str, kind: str, limit: int,
    ) -> list[ComputeJob]:
        rows = [
            j for j in self._jobs.values()
            if (j.platform, j.conversation_id, j.kind) == (platform, conversation_id, kind)
        ]
        rows.sort(key=lambda j: (j.created_at, str(j.id)), reverse=True)
        return rows[:limit]

    async def kind_since(self, *, kind: str, since: Any) -> list[ComputeJob]:
        floor = ensure_utc_job(since, field="since")
        rows = [j for j in self._jobs.values() if j.kind == kind and j.created_at >= floor]
        rows.sort(key=lambda j: (j.created_at, str(j.id)))
        return rows

    async def set_checkpoint(self, backend_job_id: str, checkpoint: Any, at: Any) -> bool:
        stamp = ensure_utc_job(at, field="checkpoint_at")
        for job_id, job in self._jobs.items():
            if job.backend_job_id == backend_job_id and job.state is ComputeJobState.PENDING:
                self._jobs[job_id] = replace(
                    job, checkpoint={k: list(v) if isinstance(v, (list, tuple)) else v
                                     for k, v in checkpoint.items()},
                    checkpoint_at=stamp,
                )
                return True
        return False

    async def claim(self, job_id: UUID) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.state is not ComputeJobState.PENDING:
            return False
        self._jobs[job_id] = replace(job, state=ComputeJobState.FIRING)
        return True

    async def mark(
        self, job_id: UUID, state: ComputeJobState, *,
        completed_at: Any = None, result_url: str | None = None,
        result_summary: str | None = None, error: str | None = None,
        failure_reason: str | None = None, result_report: str | None = None,
        result_document_ids: tuple[str, ...] | None = None,
        trace_id: str | None = None,
        checkpoint: Any = None, checkpoint_at: Any = None,
    ) -> bool:
        job = self._jobs.get(job_id)
        if job is None:
            return False
        validated_completed_at = (
            None if completed_at is None
            else ensure_utc_job(completed_at, field="completed_at")
        )
        self._jobs[job_id] = replace(
            job, state=state,
            completed_at=validated_completed_at if completed_at is not None else job.completed_at,
            result_url=result_url if result_url is not None else job.result_url,
            result_summary=result_summary if result_summary is not None else job.result_summary,
            error=error if error is not None else job.error,
            failure_reason=(
                failure_reason if failure_reason is not None else job.failure_reason
            ),
            result_report=result_report if result_report is not None else job.result_report,
            result_document_ids=(
                tuple(result_document_ids) if result_document_ids
                else job.result_document_ids
            ),
            trace_id=trace_id if trace_id is not None else job.trace_id,
            checkpoint=(
                {k: list(v) if isinstance(v, (list, tuple)) else v
                 for k, v in checkpoint.items()}
                if checkpoint is not None else job.checkpoint
            ),
            checkpoint_at=(
                ensure_utc_job(checkpoint_at, field="checkpoint_at")
                if checkpoint is not None and checkpoint_at is not None else job.checkpoint_at
            ),
        )
        return True

    async def sweep_firing(self, *, now: Any) -> list[ComputeJob]:
        return self._sweep(
            ComputeJobState.FIRING, now, "swept at boot: process crashed mid-delivery", "lost",
        )

    async def sweep_pending(
        self, *, now: Any, keep_kinds: frozenset[str] = frozenset(),
    ) -> list[ComputeJob]:
        return self._sweep(
            ComputeJobState.PENDING, now,
            "swept at boot: lost track of this job across a restart", "lost",
            keep_kinds=keep_kinds,
        )

    def _sweep(
        self, state: ComputeJobState, now: Any, error: str, reason: str,
        *, keep_kinds: frozenset[str] = frozenset(),
    ) -> list[ComputeJob]:
        """Returns the SWEPT ROWS, matching `SqlComputeJobStore._sweep`'s
        `RETURNING`. A count would have been the easier fake and the wrong one:
        the rows are what let boot recovery tell each requester it lost track
        of their job, and a fake that answered `int` would make that
        untestable through this port."""
        stamp = ensure_utc_job(now, field="now")
        swept: list[ComputeJob] = []
        for job_id, job in list(self._jobs.items()):
            if job.state is state and job.kind not in keep_kinds:
                updated = replace(
                    job, state=ComputeJobState.FAILED, completed_at=stamp, error=error,
                    failure_reason=reason,
                )
                self._jobs[job_id] = updated
                swept.append(updated)
        return swept

    async def aclose(self) -> None:
        pass


class FakeToolCallLogStore:
    """In-memory `ToolCallLogStore`. Bound to
    `nora.infra.sql.tool_call_log.SqlToolCallLogStore` by
    `tests/contract/test_tool_call_log_store_contract.py`."""

    def __init__(self) -> None:
        self._entries: list[ToolCallLogEntry] = []

    async def append(self, entry: ToolCallLogEntry) -> None:
        self._entries.append(normalise_tool_call_log_entry(entry))

    async def recent(
        self, *, conversation_id: str, since: datetime, limit: int,
    ) -> list[ToolCallLogEntry]:
        rows = [
            e for e in self._entries
            if e.conversation_id == conversation_id and e.created_at >= since
        ]
        rows.sort(key=lambda e: e.created_at, reverse=True)
        return rows[:limit]


class FakeComputerExportStore:
    """In-memory `ComputerExportStore`. Bound to
    `nora.infra.sql.computer_exports.SqlComputerExportStore` by
    `tests/contract/test_computer_export_store_contract.py`.

    `fail_record` makes `record` raise (the export already happened: the
    tool must still answer); `fail_read` makes `for_artifact` raise (a
    revision whose owner cannot be read must not go ahead).
    """

    def __init__(self) -> None:
        self.exports: list[ComputerExport] = []
        self.fail_record = False
        self.fail_read = False

    async def record(self, export: ComputerExport) -> None:
        if self.fail_record:
            raise RuntimeError("fake computer_exports record failure")
        if any(e.id == export.id for e in self.exports):
            raise RuntimeError("duplicate id")
        self.exports.append(normalise_export(export))

    async def for_artifact(self, *, artifact_id: str) -> ComputerExport | None:
        if self.fail_read:
            raise RuntimeError("fake computer_exports read failure")
        rows = [e for e in self.exports if e.artifact_id == artifact_id]
        rows.sort(key=lambda e: (e.created_at, str(e.id)))
        return rows[0] if rows else None

    def _shared(self, rooms: frozenset[tuple[str, str]]) -> list[ComputerExport]:
        if self.fail_read:
            raise RuntimeError("fake computer_exports read failure")
        return [e for e in self.exports if e.destination in SHARED_DESTINATIONS
                and (e.platform, e.conversation_id) in rooms]

    async def list_shared(
        self, *, rooms: frozenset[tuple[str, str]], limit: int, offset: int, query: str = "",
    ) -> SharedPage:
        newest: dict[str, ComputerExport] = {}
        for e in sorted(self._shared(rooms), key=lambda e: (e.created_at, e.id.int)):
            newest[e.artifact_id or str(e.id)] = e
        needle = normalize_query(query).lower()
        rows = [e for e in newest.values() if needle in e.title.lower()]
        rows.sort(key=lambda e: (-e.created_at.timestamp(), str(e.id)))
        limit, offset = max(limit, 0), max(offset, 0)
        return SharedPage(items=tuple(rows[offset:offset + limit]), total=len(rows))

    async def shared_link(
        self, *, artifact_id: str, rooms: frozenset[tuple[str, str]],
    ) -> ComputerExport | None:
        rows = [e for e in self._shared(rooms)
                if e.destination == "link" and e.artifact_id == artifact_id]
        return max(rows, key=lambda e: (e.created_at, e.id.int)) if rows else None

    async def shared_attachment(
        self, *, export_id: UUID, rooms: frozenset[tuple[str, str]],
    ) -> ComputerExport | None:
        return next((e for e in self._shared(rooms)
                     if e.destination == "chat" and e.id == export_id), None)


class FakeComputerEventStore:
    """In-memory `ComputerEventStore`. Bound to
    `nora.infra.sql.computer_events.SqlComputerEventStore` by
    `tests/contract/test_computer_event_store_contract.py`.

    `fail` names the phases whose append raises: `{"started"}` is the case
    where a tool must refuse to run, `{"finished"}` the one where it must
    still answer. `(operation_id, phase)` is unique, as in the table.
    """

    def __init__(self, *, sessions: FakeComputerSessionStore | None = None) -> None:
        self._sessions = sessions
        self.events: list[ComputerEvent] = []
        self.fail: set[str] = set()
        # Set to an unset `asyncio.Event` to make a `finished` append wait on
        # it: a slow write a second cancellation can land on.
        self.hold_finished: asyncio.Event | None = None

    async def append(self, event: ComputerEvent) -> None:
        if event.phase == "finished" and self.hold_finished is not None:
            await self.hold_finished.wait()
        if event.phase in self.fail:
            raise RuntimeError("fake computer_events append failure")
        if any((e.operation_id, e.phase) == (event.operation_id, event.phase)
               for e in self.events):
            raise RuntimeError("duplicate (operation_id, phase)")
        self.events.append(normalise_computer_event(event))

    async def orphaned_starts(self, *, older_than: datetime, limit: int) -> list[ComputerEvent]:
        done = {e.operation_id for e in self.events if e.phase == "finished"}
        rows = [e for e in self.events if e.phase == "started"
                and e.operation_id not in done and e.created_at < older_than]
        rows.sort(key=lambda e: e.created_at)
        return rows[:limit]

    def finished(self) -> list[ComputerEvent]:
        """The `finished` rows, in order: what a test asserts outcomes on."""
        return [e for e in self.events if e.phase == "finished"]

    async def for_scope(
        self, *, scope: str, since: datetime, limit: int,
    ) -> list[ComputerEvent]:
        scopes = {scope}
        if self._sessions is not None:
            scopes.update(s.scope for s in self._sessions.sessions.values()
                          if s.workspace_scope == scope)
        rows = [e for e in self.events if e.scope in scopes and e.created_at >= since]
        return sorted(rows, key=lambda e: e.created_at, reverse=True)[:limit]

    async def for_session(self, *, session_id: UUID, limit: int) -> list[ComputerEvent]:
        rows = [e for e in self.events if e.session_id == session_id]
        rows.sort(key=lambda e: e.created_at, reverse=True)
        return rows[:limit]

    async def recent(
        self, *, conversation_id: str, since: datetime, limit: int,
        platform: str | None = None,
    ) -> list[ComputerEvent]:
        rows = [
            e for e in self.events
            if e.conversation_id == conversation_id and e.created_at >= since
            and (platform is None or e.platform == platform)
        ]
        rows.sort(key=lambda e: e.created_at, reverse=True)
        return rows[:limit]


class FakeComputerSessionStore:
    """In-memory `ComputerSessionStore`, bound to
    `nora.infra.sql.computer_sessions.SqlComputerSessionStore` by
    `tests/contract/test_computer_session_store_contract.py`: one open
    session per scope, `expire` a compare-and-set on `last_activity_at`,
    nothing ever deleted. `fail = True` makes every call raise (a database
    that is down)."""

    def __init__(self) -> None:
        self.sessions: dict[UUID, ComputerSession] = {}
        self.fail = False

    def _check(self) -> None:
        if self.fail:
            raise RuntimeError("fake computer_sessions failure")

    async def continue_or_open(
        self, *, platform: str, conversation_id: str, scope: str, objective: str,
        actor: str, tag_id: str, now: datetime, open_new: bool = True,
    ) -> tuple[ComputerSession, bool] | None:
        self._check()
        now = ensure_utc_event(now, field="now")
        for sid, s in sorted(self.sessions.items(),
                             key=lambda item: (item[1].last_activity_at, item[0].int),
                             reverse=True):
            if s.workspace_scope == scope and s.status == OPEN:
                touched = replace(s, updated_at=max(s.updated_at, now),
                                  last_activity_at=max(s.last_activity_at, now))
                self.sessions[sid] = touched
                return touched, False
        if not open_new:
            return None
        opened = normalise_session(ComputerSession(
            id=uuid4(), platform=platform, conversation_id=conversation_id, scope=scope,
            objective=objective, status=OPEN, created_at=now, updated_at=now,
            last_activity_at=now, actor=actor, created_by_tag=tag_id,
        ))
        self.sessions[opened.id] = opened
        return opened, True

    async def open_sessions(self, *, limit: int) -> list[ComputerSession]:
        self._check()
        rows = sorted((s for s in self.sessions.values() if s.status == OPEN),
                      key=lambda s: s.last_activity_at)
        return rows[:limit]

    async def for_scope(
        self, *, scope: str, limit: int,
    ) -> list[ComputerSession]:
        self._check()
        rows = sorted(
            (s for s in self.sessions.values()
             if s.workspace_scope == scope),
            key=lambda s: s.last_activity_at, reverse=True,
        )
        return rows[:limit]

    async def note_resources(
        self, session_id: UUID, *, tmux: Sequence[str], now: datetime,
    ) -> None:
        self._check()
        s = self.sessions.get(session_id)
        if s is None or s.status != OPEN:
            return
        held = list(s.held_tmux)
        merged = held + [n for n in dict.fromkeys(tmux) if n not in held]
        if merged == held:
            return
        self.sessions[session_id] = replace(
            s, stopped_at_detail={**s.stopped_at_detail, "tmux": merged[:20]},
            updated_at=max(s.updated_at, ensure_utc_event(now, field="now")),
        )

    async def expire(
        self, session_id: UUID, *, seen_last_activity_at: datetime, reason: str,
        now: datetime,
    ) -> ComputerSession | None:
        self._check()
        s = self.sessions.get(session_id)
        seen = ensure_utc_event(seen_last_activity_at, field="seen_last_activity_at")
        if s is None or s.status != OPEN or s.last_activity_at != seen:
            return None
        now = ensure_utc_event(now, field="now")
        expired = replace(s, status=EXPIRED, expired_at=now, expired_reason=reason,
                          updated_at=max(s.updated_at, now))
        self.sessions[session_id] = expired
        return expired


class FakeSocialProvider:
    """In-memory `SocialProvider`, bound to `OpenCliBridgeClient`,
    `InstagramSocialClient` and `SocialRouter` by
    `tests/contract/test_social_provider_contract.py`.

    **It does not answer every request the same way** (CLAUDE.md), and the
    decision table it reproduces is the production one rather than its
    transport:

    * a `(platform, what)` it serves, with an answer → that answer;
    * a `(platform, what)` it serves, with nothing behind it →
      `nothing_found`, the honest miss;
    * a `(platform, what)` it does NOT serve → `unavailable`, because being
      asked for a pair outside its reach is a routing gap, not «there is
      nothing there». This is the branch that matters: a fake that returned
      `nothing_found` for an unrouted verb would let a mis-wired router pass
      every test while production said «he has no story up» about a backend
      that was never asked;
    * `unavailable=True` → `unavailable` for everything, the "this whole
      backend is having a bad day" state that no single pair can express.

    `calls` records `(platform, what, target, limit)` in order, which is what
    lets a router test prove not just what came back but WHICH backend was
    asked — and, for the no-failover rule, that the other one was not.
    """

    def __init__(
        self,
        answers: dict[tuple[str, str], dict[str, Any]] | None = None,
        *,
        serves: frozenset[tuple[str, str]] | None = None,
        unavailable: bool = False,
    ) -> None:
        self.answers = dict(answers or {})
        # Defaults to whatever it has an answer for, so the common case needs
        # one argument; pass `serves` explicitly to model a backend that
        # legitimately has nothing to return for a verb it does serve.
        self.serves = serves if serves is not None else frozenset(self.answers)
        self.unavailable = unavailable
        self.calls: list[tuple[str, str, str, int]] = []

    async def read(
        self, *, platform: str, what: str, target: str, limit: int,
    ) -> dict[str, Any]:
        self.calls.append((platform, what, target, limit))
        if self.unavailable or (platform, what) not in self.serves:
            return {
                "status": "unavailable",
                "note": "I couldn't read that just now. Say so — don't answer "
                        "as if you'd checked.",
            }
        answer = self.answers.get((platform, what))
        if answer is None:
            return {"status": "nothing_found"}
        return dict(answer)


class FakeSocialSubjectIndex:
    """In-memory `SocialSubjectIndex`, exercised by
    `tests/contract/test_social_provider_contract.py` through `SocialRouter`,
    which is where the tier ladder lives, and held to the SAME answers
    as the production `nora.infra.social.PersonsSubjectIndex` by
    `tests/contract/test_social_subject_index_contract.py`.

    **It does not answer every request the same way.** The three outcomes it
    can produce are the three the tier ladder produces, and collapsing any two
    of them is the defect the port exists to prevent:

    * a person it holds one igid for, in reach → `{igid}`, Tier 0 true;
    * a person it holds one igid for, out of reach → `{igid}`, Tier 0 false,
      which is `refused` at the router — a boundary, not an outage;
    * a person it holds several igids for (a `frozenset` value) → all of
      them, which is `ambiguous` at the router — never a pick;
    * a person it has never heard of → the EMPTY set, which is «no account on
      file» (`nothing_found`) — and NEVER a handle derived from the id,
      because a wrong binding does not degrade the answer, it reads a
      different human's stories silently.

    Every method fails CLOSED and none raises, exactly as the port requires:
    `unavailable=True` models an index that cannot be read at all and answers
    `None` / `False`, which costs a read that should have happened rather than
    granting one that should not have.

    `asked` records the person ids, so a test can prove a refusal happened
    BEFORE any network call rather than after one.
    """

    def __init__(
        self,
        igids: dict[str, str | frozenset[str]] | None = None,
        *,
        out_of_reach: frozenset[str] = frozenset(),
        unavailable: bool = False,
        social_off: frozenset[str] = frozenset(),
    ) -> None:
        self.social_off = social_off
        # One igid (a str) or several (a frozenset) per person, normalised to
        # sets so every method reads one shape.
        self.igids: dict[str, frozenset[str]] = {
            pid: frozenset({held}) if isinstance(held, str) else frozenset(held)
            for pid, held in (igids or {}).items()
        }
        self.out_of_reach = out_of_reach
        self.unavailable = unavailable
        self.asked: list[str] = []

    async def igids_for(self, *, person_id: str) -> frozenset[str] | None:
        self.asked.append(person_id)
        if self.unavailable:
            return None
        return self.igids.get(person_id, frozenset())

    async def is_tier_zero(self, *, person_id: str) -> bool:
        if self.unavailable:
            return False
        return person_id in self.igids and person_id not in self.out_of_reach

    async def person_for_igid(self, *, igid: str) -> str | None:
        """The reverse of `igids`: the person holding exactly this igid, or
        `None` for one nobody holds (and for an unreadable index — fail
        closed)."""
        if self.unavailable:
            return None
        return next((pid for pid, held in self.igids.items() if igid in held), None)

    async def members(self) -> frozenset[str] | None:
        """Every person `is_tier_zero` answers True for, less `social_off`."""
        if self.unavailable:
            return None
        return frozenset(self.igids) - self.out_of_reach - self.social_off


class FakeInstagramAccountLookup:
    """In-memory `InstagramAccountLookup`, held to the SAME answers as the production
    `nora.infra.social.InstagramSocialClient` by
    `tests/contract/test_instagram_account_lookup_contract.py`.

    **It does not answer every request the same way.** Its three answers are
    the port's three:

    * a username it holds, matched exactly and ASCII case-insensitively, with
      one leading `@` ignored (the bridge's own rule) → `found` with its igid;
    * a username it does not hold → `none`, «no such account»;
    * `unavailable=True` → `unavailable` for everything, the bridge that
      cannot be reached or answered a body nobody can trust.

    It never picks a near match: `example_friend2` is not `example_friend`.
    `asked` records every username, so a test can prove a refusal happened
    before any lookup, or that no lookup was made at all.
    """

    def __init__(
        self, accounts: dict[str, str] | None = None, *, unavailable: bool = False,
    ) -> None:
        self.accounts = {name.lower(): igid for name, igid in (accounts or {}).items()}
        self.unavailable = unavailable
        self.asked: list[str] = []

    async def igid_for_username(self, *, username: str) -> UsernameLookup:
        self.asked.append(username)
        if self.unavailable:
            return UsernameLookup("unavailable")
        igid = self.accounts.get(username.strip().removeprefix("@").lower())
        return UsernameLookup("found", igid) if igid else UsernameLookup("none")


class FakeBrowserRunner:
    """`BrowserRunner` that answers a set outcome and records every call
    (scope, objective, start_url), for the `computer_browse` tool's tests."""

    def __init__(self, outcome: BrowseOutcome | None = None) -> None:
        self.outcome = outcome or BrowseOutcome(status="ok", answer="done", steps=1)
        self.calls: list[tuple[str, str, str | None]] = []

    async def browse(self, *, scope: str, objective: str,
                     start_url: str | None = None) -> BrowseOutcome:
        self.calls.append((scope, objective, start_url))
        return self.outcome


class FakeSandboxRuntime:
    """In-memory `SandboxRuntime` (`nora.domain.ports.SandboxRuntime`).

    **Bound by `tests/contract/test_sandbox_runtime_contract.py`** alongside
    `nora.infra.computer.client.ComputerdRuntime` talking to a real computerd
    (`deploy/computer/computerd.py`) started in-process. The suite holds both
    to the same file semantics: scope keys, escaping paths, missing files,
    ambiguous edits, caps.

    It cannot execute anything, so `run` answers what a test scripted with
    `script(command, result)`, and `ok` with exit 0 and no output otherwise.
    Set `down = True` for the computer-unreachable case.

    The observed state (`state`) is what was written, with the mtime `clock`
    gave each write (strictly increasing, as a filesystem's would be for
    writes in sequence), dot-paths hidden as computerd hides them. tmux
    sessions exist only when a test starts one with `start_tmux`: the fake
    runs nothing, so a `run("tmux new ...")` starts none. `tmux_unknown =
    True` is computerd without a tmux it can list (`tmux` None). There is
    deliberately no delete and no kill of any kind: nothing on Nora's side
    removes a file or ends a job.
    """

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self.files_by_scope: dict[str, dict[str, bytes]] = {}
        self.mtimes: dict[str, dict[str, datetime]] = {}
        self.tmux_by_scope: dict[str, dict[str, TmuxSession]] = {}
        self.tmux_unknown = False
        # Processes a scope's user owns, as computerd reads them from /proc:
        # only what a test starts with `start_process` (the fake runs
        # nothing). `processes_unknown = True` is a /proc it cannot read.
        self.processes_by_scope: dict[str, list[ScopeProcess]] = {}
        self.processes_unknown = False
        self._clock = clock or (lambda: datetime.now(UTC))
        self._last_mtime: datetime | None = None
        self.runs: list[tuple[str, str]] = []
        # Every `run` call as it arrived, refused ones included: what a test
        # reads to see the timeout and the reserved `limits` slot.
        self.run_calls: list[dict[str, object]] = []
        self.down = False
        # Set to an unset `asyncio.Event` to make `run` wait on it: a slow
        # command a test can cancel mid-call.
        self.hold: asyncio.Event | None = None
        self._scripted: dict[str, RunResult] = {}

    def script(self, command: str, result: RunResult) -> None:
        self._scripted[command] = result

    def _refusal(self, scope: str, path: str | None = None) -> SandboxStatus | None:
        if self.down:
            return SandboxStatus.UNAVAILABLE
        if not re.fullmatch(r"[0-9a-f]{16,64}", scope):
            return SandboxStatus.DENIED
        if path is not None and _fake_path(path) is None:
            return SandboxStatus.DENIED
        return None

    async def run(
        self, *, scope: str, command: str, timeout_s: float,
        limits: Mapping[str, int] | None = None,
    ) -> RunResult:
        self.run_calls.append({"scope": scope, "command": command,
                               "timeout_s": timeout_s, "limits": limits})
        if (refusal := self._refusal(scope)) is not None:
            return RunResult(status=refusal, reason="fake refusal")
        self.runs.append((scope, command))
        if self.hold is not None:
            await self.hold.wait()
        return self._scripted.get(command, RunResult(status=SandboxStatus.OK, exit_code=0))

    async def read(
        self, *, scope: str, path: str, offset: int = 0, limit: int | None = None,
    ) -> FileResult:
        if (refusal := self._refusal(scope, path)) is not None:
            return FileResult(status=refusal, reason="fake refusal")
        if (conflict := self._conflict(scope, path, reading=True)) is not None:
            return FileResult(status=conflict, reason="fake conflict")
        data = self.files_by_scope.get(scope, {}).get(_fake_path(path) or "")
        if data is None:
            return FileResult(status=SandboxStatus.NOTHING_FOUND, reason="no such file")
        digest = hashlib.sha256(data).hexdigest()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return FileResult(
                status=SandboxStatus.OK, binary=True, size=len(data), sha256=digest,
            )
        lines = text.splitlines(keepends=True)
        chosen = "".join(lines[max(offset, 0):])
        # computerd's rule: absent or not positive means its ceiling.
        cap = limit if limit is not None and limit > 0 else FAKE_MAX_READ_CHARS
        cap = min(cap, FAKE_MAX_READ_CHARS)
        return FileResult(
            status=SandboxStatus.OK, content=chosen[:cap], size=len(data), sha256=digest,
            truncated=len(chosen) > cap, lines_total=len(lines),
        )

    async def write_text(self, *, scope: str, path: str, content: str) -> OpResult:
        return await self.put(scope=scope, path=path, data=content.encode("utf-8"))

    async def edit(self, *, scope: str, path: str, old: str, new: str) -> OpResult:
        if (refusal := self._refusal(scope, path)) is not None:
            return OpResult(status=refusal, reason="fake refusal")
        if not old:
            return OpResult(status=SandboxStatus.DENIED, reason="old must be non-empty")
        key = _fake_path(path) or ""
        data = self.files_by_scope.get(scope, {}).get(key)
        if data is None:
            return OpResult(status=SandboxStatus.NOTHING_FOUND, reason="no such file")
        text = data.decode("utf-8")
        count = text.count(old)
        if count == 0:
            return OpResult(status=SandboxStatus.NOTHING_FOUND, reason="old text not found")
        if count > 1:
            return OpResult(
                status=SandboxStatus.DENIED, reason=f"old text matches {count} places",
            )
        return await self.put(scope=scope, path=path, data=text.replace(old, new, 1).encode())

    async def files(self, *, scope: str, pattern: str = "**/*") -> ListResult:
        if (refusal := self._refusal(scope)) is not None:
            return ListResult(status=refusal, reason="fake refusal")
        entries = tuple(
            FileEntry(path=p, size=len(d))
            for p, d in sorted(self.files_by_scope.get(scope, {}).items())
            if _fake_matches(p, pattern)
        )
        status = SandboxStatus.OK if entries else SandboxStatus.NOTHING_FOUND
        return ListResult(status=status, entries=entries)

    async def fetch(self, *, scope: str, path: str, max_bytes: int) -> BytesResult:
        if (refusal := self._refusal(scope, path)) is not None:
            return BytesResult(status=refusal, reason="fake refusal")
        data = self.files_by_scope.get(scope, {}).get(_fake_path(path) or "")
        if data is None:
            return BytesResult(status=SandboxStatus.NOTHING_FOUND, reason="no such file")
        if len(data) > max_bytes:
            return BytesResult(status=SandboxStatus.DENIED, reason="file is over the cap")
        digest = hashlib.sha256(data).hexdigest()
        return BytesResult(status=SandboxStatus.OK, data=data, sha256=digest)

    async def put(self, *, scope: str, path: str, data: bytes) -> OpResult:
        if (refusal := self._refusal(scope, path)) is not None:
            return OpResult(status=refusal, reason="fake refusal")
        if (conflict := self._conflict(scope, path, reading=False)) is not None:
            return OpResult(status=conflict, reason="fake conflict")
        stored = self.files_by_scope.setdefault(scope, {})
        key = _fake_path(path) or ""
        before = stored.get(key)
        stored[key] = data
        self.mtimes.setdefault(scope, {})[key] = self._stamp()
        digest = hashlib.sha256(data).hexdigest()
        return OpResult(
            status=SandboxStatus.OK, size=len(data), sha256=digest,
            previous_sha256=hashlib.sha256(before).hexdigest() if before is not None else None,
        )

    def start_tmux(
        self, scope: str, name: str, *, running: bool = True,
        created: datetime | None = None,
    ) -> None:
        self.tmux_by_scope.setdefault(scope, {})[name] = TmuxSession(
            name=name, created=created or self._clock(), attached=False, running=running,
        )

    def start_process(
        self, scope: str, command: str, *, pid: int | None = None,
        started: datetime | None = None,
    ) -> None:
        """A process owned by `scope`'s user, running until `stop_process`
        (a `nohup … &` job: no tmux session)."""
        procs = self.processes_by_scope.setdefault(scope, [])
        at = started or self._clock()
        procs.append(ScopeProcess(
            pid=pid if pid is not None else 1000 + len(procs), command=command[:80],
            started=at, elapsed_seconds=max(0, int((self._clock() - at).total_seconds())),
        ))

    def stop_process(self, scope: str, pid: int) -> None:
        self.processes_by_scope[scope] = [
            p for p in self.processes_by_scope.get(scope, []) if p.pid != pid
        ]

    async def state(self, *, scope: str) -> StateResult:
        if (refusal := self._refusal(scope)) is not None:
            return StateResult(status=refusal, reason="fake refusal")
        stamped = self.mtimes.get(scope, {})
        visible = [
            StateFile(path=p, size=len(d), mtime=stamped[p])
            for p, d in self.files_by_scope.get(scope, {}).items()
            if not any(part.startswith(".") for part in p.split("/"))
            and not any(part in FAKE_STATE_SKIP_DIRS for part in p.split("/")[:-1])
        ]
        visible.sort(key=lambda f: (f.mtime, f.path), reverse=True)
        tmux = None if self.tmux_unknown else tuple(
            sorted(self.tmux_by_scope.get(scope, {}).values(), key=lambda t: t.created)
        )
        procs = sorted(self.processes_by_scope.get(scope, []), key=lambda p: p.started,
                       reverse=True)
        return StateResult(
            status=SandboxStatus.OK, files=tuple(visible[:FAKE_MAX_STATE_FILES]),
            tmux=tmux, tmux_reason="tmux is not installed" if tmux is None else None,
            processes=None if self.processes_unknown else tuple(
                procs[:FAKE_MAX_STATE_PROCESSES]),
            processes_truncated=(not self.processes_unknown
                                 and len(procs) > FAKE_MAX_STATE_PROCESSES),
            processes_reason="/proc could not be read" if self.processes_unknown else None,
        )

    def _stamp(self) -> datetime:
        now = self._clock()
        if self._last_mtime is not None and now <= self._last_mtime:
            now = self._last_mtime + timedelta(microseconds=1)
        self._last_mtime = now
        return now

    def _conflict(self, scope: str, path: str, *, reading: bool) -> SandboxStatus | None:
        """A file and a directory cannot share a path, as on a filesystem:
        a file under a file is refused like computerd's walk refuses it, and
        a directory where a file is asked for is not a regular file."""
        key = _fake_path(path) or ""
        stored = self.files_by_scope.get(scope, {})
        parts = key.split("/")
        if any("/".join(parts[:i]) in stored for i in range(1, len(parts))):
            return SandboxStatus.DENIED
        if any(other.startswith(key + "/") for other in stored):
            return SandboxStatus.DENIED
        return None

def _fake_matches(rel: str, pattern: str) -> bool:
    """computerd's `_matches`: no pattern, `*`, `**` or `**/*` list everything."""
    if pattern in ("", "*", "**", "**/*"):
        return True
    name = rel.rsplit("/", 1)[-1]
    if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(name, pattern):
        return True
    return pattern.startswith("**/") and fnmatch.fnmatch(rel, pattern[3:])

FAKE_MAX_READ_CHARS = 200_000  # computerd's MAX_READ_CHARS
FAKE_MAX_STATE_FILES = 50  # computerd's MAX_STATE_FILES
FAKE_MAX_STATE_PROCESSES = 20  # computerd's MAX_STATE_PROCESSES
FAKE_STATE_SKIP_DIRS = frozenset({"node_modules", "__pycache__"})  # STATE_SKIP_DIRS


def _fake_path(path: str) -> str | None:
    """computerd's confinement rule without a filesystem: relative only, and
    no `..` that climbs out after normalisation."""
    if not path or path.startswith("/") or "\x00" in path:
        return None
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return None
    return "/".join(parts)


class FakeDocumentStore:
    """In-memory `DocumentStore`. Bound to `nora.infra.sql.documents.SqlDocumentStore`
    by `tests/contract/test_document_store_contract.py`: the same visibility rule,
    the same compare-and-swap on the revision number, the same ordering.

    Time is a counter, not the wall clock, so two writes never share an instant
    and "most recently updated first" is decidable in a test."""

    def __init__(self) -> None:
        self._rooms: dict[UUID, tuple[str, str, str, datetime]] = {}
        self._revisions: dict[UUID, list[tuple[RevisionIn, datetime]]] = {}
        # Append-only, like `document_state`: the latest entry is the state, none is active.
        self._states: dict[UUID, list[tuple[DocumentState, str]]] = {}
        self._tick = 0

    def _state(self, document_id: UUID) -> DocumentState:
        changes = self._states.get(document_id)
        return changes[-1][0] if changes else "active"

    def _state_by(self, document_id: UUID) -> str:
        changes = self._states.get(document_id)
        return changes[-1][1] if changes else ""

    def _now(self) -> datetime:
        self._tick += 1
        return datetime(2026, 10, 1, tzinfo=UTC) + timedelta(microseconds=self._tick)

    def _stored(self, document_id: UUID) -> StoredDocument | None:
        head = self._rooms.get(document_id)
        if head is None:
            return None
        platform, conversation_id, created_by, created_at = head
        revision, recorded_at = self._revisions[document_id][-1]
        number = len(self._revisions[document_id])
        return StoredDocument(
            summary=DocumentSummary(
                id=document_id, platform=platform, conversation_id=conversation_id,
                created_by=created_by, title=revision.title, revision=number,
                created_at=created_at, updated_at=recorded_at,
                state=self._state(document_id),
                state_by=self._state_by(document_id),
            ),
            spec=json.loads(json.dumps(revision.spec)), text=revision.text,
            sha256=revision.sha256, media=tuple(revision.media),
            written_by=revision.written_by,
        )

    @staticmethod
    def _sees(summary: DocumentSummary, person_id: str, rooms: frozenset[RoomKey]) -> bool:
        return summary.created_by == person_id or (
            summary.platform, summary.conversation_id
        ) in rooms

    async def create(
        self, *, document_id: UUID, platform: str, conversation_id: str, created_by: str,
        revision: RevisionIn,
    ) -> StoredDocument:
        if document_id in self._rooms:
            raise DocumentExistsError(str(document_id))
        now = self._now()
        self._rooms[document_id] = (platform, conversation_id, created_by, now)
        self._revisions[document_id] = [(revision, now)]
        stored = self._stored(document_id)
        if stored is None:
            raise RuntimeError("unreachable: the document was just written")
        return stored

    async def add_revision(
        self, *, document_id: UUID, base_revision: int, revision: RevisionIn
    ) -> RevisionOutcome:
        if document_id not in self._rooms:
            return RevisionOutcome("not_found")
        if self._state(document_id) != "active":
            return RevisionOutcome("not_active")
        if len(self._revisions[document_id]) != base_revision:
            return RevisionOutcome("conflict")
        self._revisions[document_id].append((revision, self._now()))
        return RevisionOutcome("ok", self._stored(document_id))

    async def set_state(
        self, *, document_id: UUID, state: DocumentState, set_by: str,
    ) -> bool:
        if document_id not in self._rooms:
            return False
        self._states.setdefault(document_id, []).append((state, set_by))
        return True

    async def get(self, *, document_id: UUID) -> StoredDocument | None:
        return self._stored(document_id)

    def set_by_of(self, document_id: UUID) -> list[str]:
        """Who made each change of state, oldest first: what a test reads to show a
        change was the forget's, not a person's (the SQL store's `document_state.set_by`)."""
        return [by for _, by in self._states.get(document_id, [])]

    async def history(self, *, document_id: UUID) -> tuple[RevisionRecord, ...]:
        return tuple(
            RevisionRecord(
                revision=i, title=r.title, sha256=r.sha256, written_by=r.written_by,
                recorded_at=at, media=tuple(r.media),
            )
            for i, (r, at) in enumerate(self._revisions.get(document_id, []), start=1)
        )

    async def revision(self, *, document_id: UUID, revision: int) -> StoredRevision | None:
        revisions = self._revisions.get(document_id, [])
        if not 1 <= revision <= len(revisions):
            return None
        r, at = revisions[revision - 1]
        return StoredRevision(
            revision=revision, title=r.title, spec=json.loads(json.dumps(r.spec)), text=r.text,
            sha256=r.sha256, media=tuple(r.media), written_by=r.written_by, recorded_at=at,
        )

    async def list_visible(
        self, *, person_id: str, rooms: frozenset[RoomKey], limit: int, offset: int,
        state: ListedState = "active", query: str = "",
    ) -> VisiblePage:
        listed = "archived" if state == "archived" else "active"
        # `lower()`, not `casefold()`: Postgres ILIKE folds with lower(), and casefold maps
        # "ß" to "ss" where lower does not, so a query would match here and not there.
        needle = normalize_query(query).lower()
        seen = [
            d.summary for doc_id in self._rooms
            if (d := self._stored(doc_id)) is not None
            and self._sees(d.summary, person_id, rooms)
            and d.summary.state == listed
            and (not needle or needle in normalize_query(d.summary.title).lower()
                 or needle in normalize_query(d.text).lower())
        ]
        seen.sort(key=lambda s: (-s.updated_at.timestamp(), str(s.id)))
        # The same bounds as `SqlDocumentStore.list_visible`.
        limit = min(max(limit, 0), 1_000)
        offset = min(max(offset, 0), 2**31 - 1)
        return VisiblePage(items=tuple(seen[offset : offset + limit] if limit > 0 else ()),
                           total=len(seen))

    async def list_room(
        self, *, platform: str, conversation_ids: frozenset[str], limit: int,
        person_id: str, rooms: frozenset[RoomKey],
        states: tuple[DocumentState, ...] = ("active",),
    ) -> tuple[DocumentSummary, ...]:
        seen = [
            d.summary for doc_id in self._rooms
            if (d := self._stored(doc_id)) is not None
            and d.summary.platform == platform
            and d.summary.conversation_id in conversation_ids
            and d.summary.state in states
            and self._sees(d.summary, person_id, rooms)
        ]
        seen.sort(key=lambda s: (-s.updated_at.timestamp(), str(s.id)))
        limit = min(max(limit, 0), 1_000)
        return tuple(seen[:limit])

    async def get_visible(
        self, *, document_id: UUID, person_id: str, rooms: frozenset[RoomKey]
    ) -> StoredDocument | None:
        stored = self._stored(document_id)
        if (
            stored is None
            or stored.summary.state == "deleted"
            or not self._sees(stored.summary, person_id, rooms)
        ):
            return None
        return stored

    async def find_by_handle(self, *, handle: str, limit: int = 10) -> tuple[StoredDocument, ...]:
        if not is_handle(handle):
            return ()
        found = sorted(
            (s for doc_id in self._rooms if doc_id.hex.startswith(handle)
             if (s := self._stored(doc_id)) is not None),
            key=lambda s: str(s.summary.id),
        )
        return tuple(found[: min(max(limit, 0), 1_000)])

    async def get_visible_by_handle(
        self, *, handle: str, person_id: str, rooms: frozenset[RoomKey]
    ) -> StoredDocument | None:
        if not is_handle(handle):
            return None
        seen = [
            stored
            for document_id in self._rooms
            if document_id.hex.startswith(handle)
            and (stored := self._stored(document_id)) is not None
            and stored.summary.state != "deleted"
            and self._sees(stored.summary, person_id, rooms)
        ]
        return seen[0] if len(seen) == 1 else None

    async def aclose(self) -> None:
        return None
