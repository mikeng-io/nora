"""In-memory doubles for the ports. Bound by `tests/contract/`."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID

from nora.domain.compute import (
    ComputeJob,
    ComputeJobState,
    ensure_utc_job,
    normalise_compute_job,
)
from nora.domain.media import MediaBlob, MediaBytes, MediaRef, media_json
from nora.domain.models import (
    ConversationTarget,
    DeliveryStatus,
    NormalizedMessage,
    TranscriptRow,
    UnattributedRow,
)
from nora.domain.ports import (
    AdapterHealth,
    ArtifactRegistration,
    JobResult,
    JobStatus,
    LinkPreview,
    MediaDescription,
    MemoryHealth,
    Mention,
    MessageHits,
    OutboundAttachment,
    PlatformIdentity,
    ReactionResult,
    Recollection,
    Roster,
    SendResult,
    SttResult,
    TextStyle,
)
from nora.domain.presence import haversine_distance_m
from nora.domain.tool_call_log import (
    ToolCallLogEntry,
    normalise_tool_call_log_entry,
)
from nora.domain.utf16 import utf16_len


class FakeRecordStore:
    """In-memory `RecordStore`.

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
        person_id: str | None = None,
    ) -> int:
        # Deliberately does NOT raise when closed. `SqlRecordStore.aclose`
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
            # `json.dumps`, not `None`. It used to be `None`, which was only
            # invisible while nothing read it back: `unattributed` now returns
            # `raw_json`, and the operator control plane
            # (`nora.app.pending`) correlates a sender's id forms out of that
            # envelope. A fake that dropped it would let every pending-sender
            # test pass against a store production does not have.
            "raw_json": json.dumps(message.raw),
            # The custody index, produced from the message exactly as
            # `SqlRecordStore.append` produces it — NULL when Nora holds
            # nothing, never `[]`. A fake that always stored None here would
            # let every custody test pass against a store production does not
            # have; `tests/contract/test_audit_store_contract.py` is what pins
            # the two together.
            "media_json": media_json(message.media_blobs),
        })
        return row_id

    async def recent(self, *, conversation_id: str, limit: int) -> list[TranscriptRow]:
        if self.fail_on_recent:
            raise RuntimeError("db down")
        matching = [r for r in self.rows if r["conversation_id"] == conversation_id]
        matching.sort(key=lambda r: (r["ts"], r["id"]))
        window = matching[-limit:] if limit > 0 else []
        return [self._transcript_row(r) for r in window]

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

    @staticmethod
    def _transcript_row(r: dict[str, Any]) -> TranscriptRow:
        """The shared row -> `TranscriptRow` mapping for `recent` and
        `since` — mirrors `SqlRecordStore._row` so the two stay legible
        side by side."""
        return TranscriptRow(
            id=r["id"], sender_id=r["sender_id"], sender_name=r["sender_name"],
            ts=r["ts"], body=r["body"], is_nora=r["is_nora"],
            delivery_status=r["delivery_status"], raw_json=r["raw_json"],
            quoted_ts=r["quoted_ts"], media_json=r["media_json"],
            person_id=r["person_id"],
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
                raw_json=r["raw_json"],
            )
            for r in candidates[:limit]
        ]

    async def set_person_id(self, *, row_id: int, person_id: str) -> None:
        for row in self.rows:
            if row["id"] == row_id:
                row["person_id"] = person_id
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

    def __init__(
        self,
        *,
        cards: dict[str, list[str]] | None = None,
        conclusions: dict[str, list[str]] | None = None,
        fail: bool = False,
    ) -> None:
        # (peer_id, text, conversation_id) — the conversation keys the session
        # (Honcho v2, design/24), so a test can assert a message went under its
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
        # observer/observed pair is the whole point of design/24 §4, and a fake
        # that dropped the observer could not tell Nora's model of Miles from
        # Miles's own — the exact "answers every request the same" trap.
        self.queries: list[tuple[str, str, str]] = []
        # (observer, observed, query, reasoning_level) for each `chat` call.
        self.chats: list[tuple[str, str, str, str]] = []
        # (peer_id, content) messages a test seeds for `search` to return.
        self.messages: list[tuple[str, str]] = []
        self.searches: list[str] = []
        self.fail = fail

    async def ensure_workspace(self) -> None:
        return None

    async def ensure_peer(self, *, peer_id: str) -> None:
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

    async def peer_card(self, *, peer_id: str) -> list[str]:
        if self.fail:
            return []
        return list(self.cards.get(peer_id, []))

    async def representation(
        self, *, peer_id: str, search_query: str, max_conclusions: int = 5,
        target: str | None = None,
    ) -> Recollection:
        """Mirrors `HonchoClient.representation`, INCLUDING the shape it
        returns for a peer it has never heard of.

        `peer_id` is the OBSERVER, `target` the OBSERVED (design/24 §4); the
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
        self.queries.append((peer_id, observed, search_query))
        if self.fail:
            return Recollection(reachable=False)
        return Recollection(
            reachable=True,
            conclusions=tuple(self.conclusions.get(observed, ()))[:max_conclusions],
        )

    async def chat(
        self, *, peer_id: str, query: str, reasoning_level: str = "low",
        target: str | None = None,
    ) -> str | None:
        """Mirrors `HonchoClient.chat` — the dialectic verb. Synthesises a stand-in
        answer from the observed peer's conclusions (`target or peer_id`), so a
        test that seeds conclusions gets a non-None reasoned string and an unseeded
        peer gets None (the honest «nothing to say»). `fail=True` → None."""
        observed = target if target is not None else peer_id
        self.chats.append((peer_id, observed, query, reasoning_level))
        if self.fail:
            return None
        known = self.conclusions.get(observed, ())
        if not known:
            return None
        return " ".join(known)

    async def search(
        self, *, query: str, conversation_id: str | None = None,
        limit: int = 8, workspace_id: str | None = None,
    ) -> MessageHits:
        """Mirrors `HonchoClient.search`, INCLUDING its failure shape: a
        `fail=True` adapter is UNREACHABLE, not empty. Returning `[]` for both —
        which this did — is a fake that answers every request the same way, and
        it is why `search_memory` reported a Honcho outage as «nothing was said
        here» with no test catching it."""
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
    returned to a live probe on 2026-08-06), `no_id` for a ref with neither,
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
    """

    def __init__(self, *, fail_on_put: bool = False) -> None:
        self.blobs: dict[str, bytes] = {}
        self.fail_on_put = fail_on_put
        self.puts = 0

    async def put(self, *, data: bytes) -> str:
        if self.fail_on_put:
            raise OSError("no space left on device")
        self.puts += 1
        key = hashlib.sha256(data).hexdigest()
        self.blobs.setdefault(key, data)
        return key

    async def get(self, *, key: str) -> bytes | None:
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("blob key must be a lowercase sha256 hex digest")
        return self.blobs.get(key)


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

    async def resolve(self, *, platform: str, token: str) -> MediaBlob | None:
        self.resolved.append((platform, token))
        if self.unavailable:
            return None
        return self.index.get((platform, token))

    async def describe(self, *, sha256: str) -> str | None:
        self.described.append(sha256)
        if self.unavailable:
            return None
        return self.descriptions.get(sha256)

    async def remember(
        self, *, platform: str, token: str, blob: MediaBlob,
        description: str, model: str,
    ) -> None:
        self.remembered.append((platform, token, blob.sha256))
        if self.unavailable:
            return
        self.index[(platform, token)] = blob
        self.descriptions[blob.sha256] = description


class FakeGeocodeCacheStore:
    """In-memory `GeocodeCacheStore`, bound to `SqlGeocodeCacheStore` by
    `tests/contract/test_geocode_cache_store_contract.py`. `gets`/`puts`
    record every call so a caller's read-through/write-through behaviour
    (`CachingBilingualGeocoder`) is provable, not just its end result."""

    def __init__(self) -> None:
        self._data: dict[tuple[float, float], str] = {}
        self.gets: list[tuple[float, float]] = []
        self.puts: list[tuple[float, float, str]] = []

    async def get(self, *, lat_rounded: float, lng_rounded: float) -> str | None:
        self.gets.append((lat_rounded, lng_rounded))
        return self._data.get((lat_rounded, lng_rounded))

    async def put(
        self, *, lat_rounded: float, lng_rounded: float, address: str
    ) -> None:
        self.puts.append((lat_rounded, lng_rounded, address))
        self._data[(lat_rounded, lng_rounded)] = address

    async def nearest_within(
        self, *, lat: float, lng: float, radius_m: float
    ) -> str | None:
        best: tuple[float, str] | None = None
        for (row_lat, row_lng), address in self._data.items():
            dist = haversine_distance_m(lat, lng, row_lat, row_lng)
            if dist <= radius_m and (best is None or dist < best[0]):
                best = (dist, address)
        return None if best is None else best[1]


class FakeMediaInterpreter:
    """In-memory `MediaInterpreter`. `calls` is the point of it: the invariant
    that only stickers are interpreted (spec §5.3) is proved by what this
    RECORDS, not by reading the stage and believing it.

    Mirrors `StickerInterpreter`'s own `media_type` gate (the review that
    added `tests/contract/test_media_interpreter_contract.py`, fix round 1):
    a non-image `media_type` refuses with `unsupported_format` before
    touching `available`/`text`, exactly like production refuses a lottie
    before any vision call goes out. Without this gate a test could feed a
    lottie through the fake and see "no description" even if the real
    pipeline never checked the format at all — the fake would mask the
    omission instead of proving it absent. The refusal is still RECORDED in
    `.calls`: only the result changes, because Task 14's sticker-only
    invariant test asserts on what was asked, not just what was answered.
    """

    def __init__(self, text: str = "a cartoon cat", *, available: bool = True,
                 reason: str = "") -> None:
        self.text = text
        self.available = available
        self.reason = reason
        self.calls: list[tuple[str, int]] = []
        self.model = "fake-vision"

    async def describe(self, *, data: bytes, media_type: str) -> MediaDescription:
        self.calls.append((media_type, len(data)))
        if not media_type.startswith("image/"):
            return MediaDescription(available=False, reason="unsupported_format")
        if not self.available:
            return MediaDescription(available=False, reason=self.reason or "vision")
        return MediaDescription(available=True, text=self.text)


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


class FakeArtifactIngest:
    """In-memory `ArtifactIngest` (`nora.domain.ports.ArtifactIngest`).

    **Bound by `tests/contract/test_artifact_ingest_contract.py`** alongside
    `nora.infra.drop.DropArtifactIngest`, `drop`'s real HTTP-client adapter
    (Nora Drop spec §7a) — the shared suite CLAUDE.md requires once a port
    gains a second implementation. This fake predates that adapter (it
    shipped first, so the Protocol's shape was exercised by SOMETHING rather
    than declared into a vacuum); now that the real adapter exists, both are
    held to the same contract, not just this one.

    `fail` simulates `drop`'s internal listener being unreachable — the one
    failure mode `ArtifactIngest.register` documents raising for, the same
    role `FakeBlobStore.fail_on_put`'s disk-full switch plays for `BlobStore
    .put`: a future Compute tool has to be testable turning this into R-27's
    `unavailable` rather than an uncaught exception reaching the loop.

    `artifacts` records what each call was actually given (not just the
    `ArtifactRegistration` handed back), keyed by the assigned `artifact_id`,
    so a test can assert on `title`/`media_type`/`expires_at` as well as the
    id/url pair — the same reason `FakeRecordStore.rows` keeps the full row
    rather than only what its own return value exposes.
    """

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self._next_id = 1
        self.artifacts: dict[str, dict[str, Any]] = {}

    async def register(
        self,
        *,
        data: bytes,
        title: str,
        media_type: str,
        expires_at: int | None = None,
    ) -> ArtifactRegistration:
        if self._fail:
            raise ConnectionError("drop unreachable")
        artifact_id = f"art_{self._next_id:06d}"
        self._next_id += 1
        self.artifacts[artifact_id] = {
            "data": data,
            "title": title,
            "media_type": media_type,
            "expires_at": expires_at,
        }
        return ArtifactRegistration(
            artifact_id=artifact_id,
            # A slug-shaped stand-in, not a real drop URL — nothing decodes
            # this string. It exists so a caller has something URL-shaped to
            # hand `send_reply` in a test, the same spirit as
            # `CannedSearchProvider`'s fixed `https://example.com/a`.
            #
            # Path-based, matching the real `drop` service's actual shape
            # (nora.example.net/drop/<slug>) — `drop.nora.example.net` (host-based
            # routing) was the ORIGINAL design and is no longer what ships:
            # Cloudflare's zone-level Universal SSL only covers one subdomain
            # level, so that two-level hostname never got a working
            # certificate and the cutover moved to one host, path-prefixed,
            # instead. This fake drifted out of sync with that change until
            # caught here — a real staleness bug, not cosmetic, since a
            # future caller could reasonably assume this shape is real.
            url=f"https://nora.example.net/drop/{artifact_id}",
        )

    async def update(
        self,
        *,
        artifact_id: str,
        data: bytes,
        title: str,
        media_type: str,
        expires_at: int | None = None,
    ) -> ArtifactRegistration:
        if self._fail:
            raise ConnectionError("drop unreachable")
        if artifact_id not in self.artifacts:
            raise ConnectionError("drop refused update (http 404): not found")
        existing_expires_at = self.artifacts[artifact_id]["expires_at"]
        self.artifacts[artifact_id] = {
            "data": data,
            "title": title,
            "media_type": media_type,
            "expires_at": expires_at if expires_at is not None else existing_expires_at,
        }
        return ArtifactRegistration(
            artifact_id=artifact_id,
            url=f"https://nora.example.net/drop/{artifact_id}",
        )


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
    job that reports RUNNING some number of times first, the shape Phase 4's
    fast-path/slow-path branching (Nora Compute spec §4) needs to test both
    branches deterministically, without a real timer.

    `launched` records every call's `task`/`artifact_id`/`context`/assigned
    `job_id`, the same "record what was actually asked for" pattern
    `FakeRecordStore.rows`/`FakeArtifactIngest.artifacts` already use
    elsewhere in this file.
    """

    def __init__(self) -> None:
        self._results: dict[str, JobResult] = {}
        self._polls_before_done: dict[str, int] = {}
        self.launched: list[dict[str, Any]] = []
        self._next_id = 1

    async def launch(
        self, *, task: str, artifact_id: str | None = None,
        context: list[dict[str, Any]] | None = None,
    ) -> str:
        job_id = f"job_{self._next_id:06d}"
        self._next_id += 1
        self.launched.append({
            "task": task, "artifact_id": artifact_id, "context": context,
            "job_id": job_id,
        })
        self._results.setdefault(
            job_id,
            JobResult(
                status=JobStatus.SUCCEEDED,
                url="https://nora.example.net/drop/fake",
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
        )
        return True

    async def sweep_firing(self, *, now: Any) -> list[ComputeJob]:
        return self._sweep(
            ComputeJobState.FIRING, now, "swept at boot: process crashed mid-delivery",
        )

    async def sweep_pending(self, *, now: Any) -> list[ComputeJob]:
        return self._sweep(
            ComputeJobState.PENDING, now,
            "swept at boot: lost track of this job across a restart",
        )

    def _sweep(self, state: ComputeJobState, now: Any, error: str) -> list[ComputeJob]:
        """Returns the SWEPT ROWS, matching `SqlComputeJobStore._sweep`'s
        `RETURNING`. A count would have been the easier fake and the wrong one:
        the rows are what let boot recovery tell each requester it lost track
        of their job, and a fake that answered `int` would make that
        untestable through this port."""
        stamp = ensure_utc_job(now, field="now")
        swept: list[ComputeJob] = []
        for job_id, job in list(self._jobs.items()):
            if job.state is state:
                updated = replace(
                    job, state=ComputeJobState.FAILED, completed_at=stamp, error=error,
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
