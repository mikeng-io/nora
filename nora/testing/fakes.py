"""In-memory doubles for the ports. Bound by `tests/contract/`."""

from __future__ import annotations

from typing import Any

from nora.domain.models import ConversationTarget, DeliveryStatus, NormalizedMessage, TranscriptRow
from nora.domain.ports import AdapterHealth, MemoryHealth, PlatformIdentity, SendResult


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


class FakeMemoryStore:
    """In-memory `MemoryStore`, keyword-only to match the port and
    `nora.infra.honcho.client.HonchoClient` exactly (unlike `tests/fakes.py`'s
    older `FakeHoncho`, which predates this file and takes `peer_id`/`text`
    positionally — a drift mypy never catches because `nora.testing` sits
    outside its strict scope). `fail=True` simulates the service being down,
    the same "log and return a benign value" contract `HonchoClient` documents.
    """

    def __init__(self, *, cards: dict[str, list[str]] | None = None, fail: bool = False) -> None:
        self.fed: list[tuple[str, str]] = []
        self.peers: list[str] = []
        self.cards = cards or {}
        self.fail = fail

    async def ensure_workspace(self) -> None:
        return None

    async def ensure_peer(self, *, peer_id: str) -> None:
        if not self.fail:
            self.peers.append(peer_id)

    async def feed(self, *, peer_id: str, text: str) -> bool:
        if self.fail:
            return False
        self.fed.append((peer_id, text))
        return True

    async def peer_card(self, *, peer_id: str) -> list[str]:
        if self.fail:
            return []
        return list(self.cards.get(peer_id, []))

    async def health(self) -> MemoryHealth:
        return MemoryHealth(reachable=not self.fail)

    async def aclose(self) -> None:
        return None


class FakeAdapter:
    """Records sends instead of performing them; parses signal-cli-shaped envelopes
    inline (spec E.3) rather than importing `nora.platform.signal.parser` — `nora.testing`
    sits below `nora.platform` in the layers contract (`nora.agent | nora.platform` may
    import `nora.testing`, not the other way around), so this reimplements just the
    group-message subset of `parse_event` it needs.

    `PlatformAdapter` is a documented, still-not-fully-conformed-to port. The platforms
    phase closed the `resolve_identity()`, `send()` and `health()` gaps
    (`tests/contract/test_port_conformance.py` no longer pins any of the three
    absent), so this fake mirrors all three the same way `SignalAdapter` does
    (`resolve_identity()`, a `SendResult`-returning `send()`, an
    `AdapterHealth`-returning `health()`). `listen()`/`parse()` still mirror
    `SignalAdapter`'s pre-phase shape (positional `on_event`, a `dict`-shaped
    `parse()`) — that gap
    (`tests/contract/test_port_conformance.py::test_signal_adapter_listen_gap_to_on_message_is_known`)
    is still open and closes in a later phase, so this fake staying on the old
    shape for those two does not newly trigger the "second port implementation
    needs a contract suite" rule either.

    `_account` (not `own_account`) deliberately matches `SignalAdapter._account`'s
    name: `nora.engagement.stages.admission`'s R-12 self-tag check reads
    `adapter._account` directly (its own `_AccountHolder` narrow `Protocol`), so
    any adapter double must carry that exact attribute name to be admitted through
    the pipeline at all.
    """

    platform = "signal"

    def __init__(self, *, succeed: bool = True, account: str = "") -> None:
        self.sent: list[tuple[ConversationTarget, str]] = []
        self._succeed = succeed
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

    async def send(self, target: ConversationTarget, text: str) -> SendResult:
        self.sent.append((target, text))
        return SendResult(ok=self._succeed)

    async def health(self) -> AdapterHealth:
        return AdapterHealth(platform=self.platform, connected=True, last_event_age_seconds=0.0)

    @property
    def texts(self) -> list[str]:
        return [text for _, text in self.sent]
