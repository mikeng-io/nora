"""In-memory doubles for the ports. Bound by `tests/contract/`."""

from __future__ import annotations

from typing import Any

from nora.domain.models import DeliveryStatus, NormalizedMessage, TranscriptRow


class FakeAuditStore:
    """In-memory `AuditStore`.

    The two failure switches exist because the integration harness must inject
    audit failures, and the old mechanism — `monkeypatch.setattr(db, "append")`
    — silently became a no-op the moment the store was injected rather than
    imported. A switch on the injected object cannot be bypassed that way.
    """

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.fail_on_nora = False
        self.fail_on_recent = False
        self.closed = False

    async def append(
        self,
        *,
        message: NormalizedMessage,
        is_nora: bool = False,
        delivery_status: DeliveryStatus = DeliveryStatus.PENDING,
    ) -> int:
        # Deliberately does NOT raise when closed. `SqlAuditStore.aclose`
        # delegates to `AsyncEngine.dispose()`, which releases the pool but
        # leaves the engine usable — verified 2026-08-05. A fake that raised
        # here would be stricter than production, which is precisely the
        # drift the contract suite exists to prevent; `closed` is recorded for
        # assertions, not enforced.
        if is_nora and self.fail_on_nora:
            raise RuntimeError("disk full")
        row_id = len(self.rows) + 1
        self.rows.append({
            "id": row_id,
            "conversation_id": message.conversation_id,
            "sender_id": message.sender_id,
            "sender_name": message.sender_name,
            "ts": message.timestamp,
            "body": message.body,
            "is_nora": is_nora,
            "delivery_status": delivery_status,
            "raw_json": None,
        })
        return row_id

    async def recent(self, *, conversation_id: str, limit: int) -> list[TranscriptRow]:
        if self.fail_on_recent:
            raise RuntimeError("db down")
        matching = [r for r in self.rows if r["conversation_id"] == conversation_id]
        matching.sort(key=lambda r: (r["ts"], r["id"]))
        window = matching[-limit:] if limit > 0 else []
        return [
            TranscriptRow(
                id=r["id"], sender_id=r["sender_id"], sender_name=r["sender_name"],
                ts=r["ts"], body=r["body"], is_nora=r["is_nora"],
                delivery_status=r["delivery_status"], raw_json=r["raw_json"],
            )
            for r in window
        ]

    async def set_delivery_status(
        self, *, row_id: int, status: DeliveryStatus
    ) -> None:
        for row in self.rows:
            if row["id"] == row_id:
                row["delivery_status"] = status
                return

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
