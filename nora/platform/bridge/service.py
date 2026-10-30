"""Shared durable capture + action services for the Python bridge runtimes.

Two real users — Signal (`deploy/signal-bridge/wrapper.py`) and Discord
(`deploy/discord-bridge/entry.py`) — justify one implementation of the
contract instead of two drift-prone copies. The pieces and their seams:

- `JournalPublisher`: the ONE journal→stream writer per account. Capture
  never enqueues directly; it appends to the fsync journal and wakes this
  loop, which drains the head FIFO and BLOCKS on a Redis failure without
  skipping — a recovered outage can never let a new event outrun an older
  journaled one (Signal's Fix-A). `on_enqueued` is the platform's
  post-publish receipt step; `retry_pending`/`has_pending` let parked
  receipt work retry on the short cadence instead of the idle poll.
- `JournalCaptureSink`: the `CaptureSink` collaborator — freezes the
  receipt intent into the envelope, appends the journal record, wakes the
  publisher. `intent_for` is the platform's eligibility rule.
- `DurableActionService`: the `ActionService` collaborator — the closed
  typed owner gate (`validate_owner`) runs under the SAME transaction as
  the `bridge_action` reservation CAS, before any upstream effect; the
  platform's executor (`DiscordSender`/`DiscordReactor`,
  `SignalSender`/`SignalReactor` — SDK semantics stay theirs) runs only
  for a verdict of `execute`; a terminal write that fails never reports a
  state the ledger doesn't hold.
- `receipt_via`: the deterministic addressed half of receipt eligibility —
  the same rule IG's `receiptAddressed` and Signal's `_receipt_eligible`
  implement: DM receipts need `dm_allowed_senders`; group receipts need
  `room_receipt_senders[room]` membership or `"*"` AND a mention of a self
  alias, a quote of a self-authored message, or a deterministic trigger in
  the body. Rooms alone grant capture, never a receipt.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import Any, Protocol

from nora.domain.actions import ActionOwner
from nora.infra.sql.bridge_store import SqlBridgeStore
from nora.platform.bridge import telemetry as bt
from nora.platform.bridge.actions import stored_request, validate_owner
from nora.platform.bridge.envelope import Envelope, canonical_digest
from nora.platform.bridge.journal import Journal
from nora.platform.bridge.queue import StreamProducer

log = logging.getLogger("nora.bridge.service")

#: Blocked-head and parked-receipt cadence — short enough that a recovered
#: outage drains promptly, long enough not to spin (Signal's REPLAY_RETRY).
REPLAY_RETRY_SECONDS = 2.0


def resolve_clockless_transition(
    journal: Journal, key: str, state: str,
) -> tuple[str | None, None]:
    """The ledger answer for a clockless observation that carries NO upstream
    revision — frame lost, buffer overflow, or a delayed callback after a
    restart (design 02 §wire semantics: "a reconnect replay without a source
    revision that can distinguish an old transition from a new one is
    ambiguous, retained for diagnosis without fabricating a new
    transition").

    - a repeated SAME-state observation resolves to the identity the
      recorded transition committed under — the identical redelivery
      dedupes downstream against the event it replays;
    - a differing or unrecorded state resolves `(None, None)` — ambiguous:
      nothing here can tell a new transition from a replayed old one, so
      the caller retains the event without publishing.

    A ledger that cannot be read raises `JournalError` out of
    `journal.transition_ledger()` — never a guessed sequence.
    """
    last = journal.transition_ledger().get(key)
    if last is not None and last[0] == state and last[1] > 0:
        upstream = last[2] if last[2] else f"l:{key}:{state}:{last[1]}"
        return upstream, None
    return None, None


def commit_transition(
    journal: Journal, key: str, state: str, upstream: str,
) -> dict[str, Any]:
    """The ledger step for a transition whose upstream revision IS proven
    (`g:{session}:{s}` from the dispatch frame). The record rides ON the
    event's journal line: the ledger advance and the event commit in one
    fsync, and the committed `upstream` is what a later frameless
    same-state redelivery resolves to. Sequence semantics are unchanged —
    same-state replays the recorded seq, a state change increments it —
    so `add/remove/add` stays three distinct events and the seq remains a
    stable fingerprint of the state path, never a fabricated identity.
    """
    last = journal.transition_ledger().get(key)
    last_state, last_seq = (last[0], last[1]) if last is not None else ("", 0)
    seq = last_seq if last_state == state and last_seq > 0 else last_seq + 1
    return {"key": key, "state": state, "seq": seq, "upstream": upstream}


class ActionReply:
    """One wire action's answer — the frozen `state`/`outcome` vocabulary:
    `confirmed` only when the platform call provably landed, `rejected` a
    definitive refusal, `unsupported` the op doesn't exist, `unavailable`
    the ledger/executor could not be reached BEFORE an effect, `conflict`
    the id was spent on a different request or immutable context, and
    `uncertain` when the upstream call was made (or may have been) and the
    result cannot be proven — never a stubbed success."""

    def __init__(
        self, state: str, *, kind: str = "", message: str = "",
        platform_ref: str | None = None, platform_ms: int | None = None,
        message_id: int | None = None,
    ) -> None:
        self.state = state
        self.kind = kind
        self.message = message
        self.platform_ref = platform_ref
        self.platform_ms = platform_ms
        self.message_id = message_id


class _Executor(Protocol):
    """The platform's real effect — SDK semantics (mention resolution,
    rate-limit honesty, reaction endpoints) live behind it, never in this
    module. Answers the same `ActionReply` vocabulary."""

    async def __call__(self, action: Mapping[str, Any]) -> ActionReply: ...


def action_http_status(reply: ActionReply) -> int:
    """The wire status for an `ActionReply` — Signal's contract: a malformed
    body is 400, a denied owner 403, a spent id 409, a ledger unreachable
    BEFORE the effect 503, every executed outcome 200 — including a
    terminal-write failure, which is an `uncertain` the client must see,
    not a transport failure."""
    if reply.state == "unavailable":
        return 503
    if reply.state == "conflict":
        return 409
    if reply.kind == "bad_request":
        return 400
    if reply.kind == "owner":
        return 403
    return 200


def action_response(action_id: str, reply: ActionReply) -> dict[str, Any]:
    """The frozen wire body — Signal and Discord render the identical
    shape so `BridgeAdapter._action` parses one contract. `outcome` is
    the terminal vocabulary (`confirmed|rejected|unsupported|uncertain`)
    or null while a state is still in flight; `state` is the row's own
    word — `conflict`/`unavailable` only ever ride non-2xx bodies."""
    if reply.state in ("confirmed", "uncertain"):
        outcome: str | None = reply.state
    elif reply.state == "rejected":
        outcome = ("unsupported" if reply.kind == "unsupported"
                   else "rejected")
    elif reply.state in ("conflict", "unavailable"):
        outcome = reply.state
    else:
        outcome = None
    return {
        "action_id": action_id, "state": reply.state, "outcome": outcome,
        "platform_ref": reply.platform_ref,
        "platform_ms": reply.platform_ms,
        "message_id": reply.message_id,
        "error": (
            {"kind": reply.kind, "message": reply.message}
            if reply.kind else None),
    }


def _reply_from_row(row: Mapping[str, Any]) -> ActionReply:
    error = row.get("error") or {}
    return ActionReply(
        str(row["state"]), kind=str(error.get("kind") or ""),
        message=str(error.get("message") or ""),
        platform_ref=row.get("platform_ref"),
        platform_ms=row.get("platform_ms"),
        message_id=row.get("message_id"))


def receipt_via(
    proj: Mapping[str, Any], *, conversation_id: str, conversation_type: str,
    sender_id: str, mentions: set[str], quote_author_id: str | None,
    body: str, self_ids: set[str],
) -> str:
    """The deterministic addressed half of receipt eligibility — re-checked
    against CURRENT projection before every effect so a revocation
    suppresses a queued receipt. Returns the channel
    (`"dm"`/`"mention"`/`"reply"`/`"trigger"`) or `""` — a miss is a deny,
    never an inference. `self_ids` is the bridge's own alias set (account
    floor plus `self_aliases`); an empty set denies the group half."""
    if not proj.get("receipt_emoji"):
        return ""
    if conversation_type == "dm":
        return ("dm" if sender_id in set(proj.get("dm_allowed_senders") or ())
                else "")
    senders = (proj.get("room_receipt_senders") or {}).get(conversation_id)
    if not senders:
        return ""  # no map entry → no group receipts for this room
    if "*" not in senders and sender_id not in senders:
        return ""
    own = ({str(s) for s in self_ids} | set(
        proj.get("self_aliases") or ())) - {""}
    if not own:
        return ""
    if own & mentions:
        return "mention"
    if quote_author_id and quote_author_id in own:
        return "reply"
    if any(t and t in (body or "")
           for t in proj.get("deterministic_triggers") or ()):
        return "trigger"
    return ""


def admitted(proj: Mapping[str, Any], *, conversation_id: str,
             sender_id: str, conversation_type: str) -> bool:
    """The CAPTURE decision — `enabled` + room membership; a DM additionally
    needs `dm_allowed_senders`. Admission never implies a receipt grant."""
    if not proj.get("enabled"):
        return False
    if conversation_id in set(proj.get("rooms") or ()):
        return True
    return (conversation_type == "dm"
            and sender_id in set(proj.get("dm_allowed_senders") or ()))


def receipt_intent_ids(journal: Journal) -> frozenset[str]:
    """The durable evidence an `event_receipt` owner must name: every id
    under which a journaled envelope's FROZEN receipt intent can be
    referenced — the canonical event alias AND the rotation-stable source
    digest. Records that fail to replay count as no intent."""
    ids: set[str] = set()
    for record in journal.recover():
        fields = record.get("envelope")
        if not isinstance(fields, dict):
            continue
        try:
            env = Envelope.from_fields(fields)
        except (KeyError, TypeError, ValueError):
            continue
        if not env.receipt:
            continue
        ids.add(env.event_id)
        ids.add(canonical_digest(env.source_key))
    return frozenset(ids)


class JournalPublisher:
    """The ONE writer from journal to Redis. `capture` only appends and
    calls `wake()`; every enqueue — live or replayed — flows through this
    head in FIFO order, so a recovered outage can never let a new event
    outrun an older journaled one. A Redis failure stops the head and
    retries; the head is never skipped."""

    def __init__(
        self, *, journal: Journal, producer: StreamProducer,
        on_enqueued: Callable[[Envelope, str], Awaitable[None]] | None = None,
        retry_pending: Callable[[], Awaitable[None]] | None = None,
        has_pending: Callable[[], bool] = lambda: False,
        metrics: Any | None = None,
    ) -> None:
        self._journal = journal
        self._producer = producer
        self._on_enqueued = on_enqueued
        self._retry_pending = retry_pending
        self._has_pending = has_pending
        self._metrics = metrics
        self._offset = 0
        self._stop = asyncio.Event()
        self._dirty = asyncio.Event()

    @property
    def offset(self) -> int:
        return self._offset

    def wake(self) -> None:
        self._dirty.set()

    def stop(self) -> None:
        self._stop.set()
        self._dirty.set()

    async def run(self) -> None:
        # A fresh stop flag per run: `stop()` halts the CURRENT loop only,
        # so a supervised respawn after `close()` is a real restart, not a
        # permanently-poisoned event.
        self._stop = asyncio.Event()
        while not self._stop.is_set():
            records = self._journal.recover()
            blocked = False
            while self._offset < len(records):
                rec = records[self._offset]
                if "envelope" not in rec:
                    self._offset += 1
                    continue
                env = Envelope.from_fields(rec["envelope"])
                try:
                    # A journaled envelope's publication is a NEW bounded
                    # root — never ambient — LINKED to the producer context
                    # the envelope froze at capture (design 04 §producer).
                    with bt.producer_span(
                        env.platform,
                        links=bt.carrier_links(env.trace),
                        event_id=env.event_id, kind=env.kind,
                        queued_ms=env.queued_ms,
                    ):
                        _sid, canonical_id = await self._producer.enqueue(env)
                    bt.lifecycle(
                        self._metrics,
                        "enqueued" if canonical_id == env.event_id
                        else "duplicate_suppressed",
                        platform=env.platform, event_id=env.event_id,
                        stream_id=_sid, queued_ms=env.queued_ms)
                except Exception:  # noqa: BLE001 — any enqueue failure stops the head
                    blocked = True
                    break
                self._offset += 1
                if self._on_enqueued is not None:
                    await self._on_enqueued(env, canonical_id)
            if self._retry_pending is not None:
                await self._retry_pending()
            if blocked or self._has_pending():
                # Blocked head OR parked receipts: short cadence — pending
                # work retries without a restart, never on the idle poll.
                await asyncio.sleep(REPLAY_RETRY_SECONDS)
                continue
            # Drained. Re-check before clearing the wake flag: an append that
            # landed during the drain is already in the journal.
            if self._offset < len(self._journal.recover()):
                continue
            self._dirty.clear()
            if self._offset < len(self._journal.recover()):
                continue
            # The periodic re-check is the safety net; the flag is the wake.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._dirty.wait(), timeout=REPLAY_RETRY_SECONDS * 15)


class JournalCaptureSink:
    """`CaptureSink`: freeze the receipt intent → journal append → wake the
    publisher. The intent is computed at CAPTURE (`intent_for`) and rides
    the envelope — replay never re-derives eligibility from the current
    emoji flag, so an event that was never receipt-eligible cannot grow a
    reaction on re-enqueue."""

    def __init__(
        self, *, journal: Journal, publisher: JournalPublisher,
        intent_for: Callable[[Envelope], dict[str, str] | None] | None = None,
        metrics: Any | None = None,
    ) -> None:
        self._journal = journal
        self._publisher = publisher
        self._intent_for = intent_for
        self._metrics = metrics

    def preflight(self) -> None:
        """Prove append+fsync works BEFORE upstream starts receiving —
        a disk-full or unwritable journal refuses the receive window
        rather than capturing events it cannot spool."""
        self._journal.probe()

    async def capture(self, envelope: Envelope, *,
                      transition: dict[str, Any] | None = None) -> bool:
        if self._intent_for is not None:
            # Envelope is frozen — the frozen intent rides a replacement.
            envelope = replace(
                envelope, receipt=self._intent_for(envelope))
        record = {"envelope": envelope.to_fields()}
        # The clockless-transition ledger step commits ON the event's own
        # record — one append+fsync, never a separate row.
        if transition is not None:
            record["transition"] = transition
        self._journal.append(record)
        bt.lifecycle(self._metrics, "captured", platform=envelope.platform,
                     event_id=envelope.event_id, kind=envelope.kind,
                     queued_ms=envelope.queued_ms)
        self._publisher.wake()
        return True

    def note_ambiguous(self, record: dict[str, Any]) -> None:
        """A clockless event whose identity could NOT be established is
        retained as diagnostic evidence in the same journal — never
        guessed an identity, never silently dropped (design 00: "the bridge
        either identifies it durably or retains it as ambiguous evidence").
        """
        self._journal.append({"ambiguous": record})
        log.warning("bridge capture retained an ambiguous event")


class DurableActionService:
    """`ActionService`: the shared ledger behind `/v1/actions` and the
    bridge's own internal effects (the receipt lane).

    `run` is the HTTP path — parse/closed-kind owner → `validate_owner`
    + `reserve_action` under ONE transaction → replay/conflict honesty →
    the platform executor → an honest terminal write. `run_owned` is the
    internal path — the caller's authority is its own durable evidence
    (a journaled receipt intent), so it reserves under `event_receipt`
    without the client-facing gate."""

    def __init__(
        self, *, store: SqlBridgeStore, platform: str, account_key: str,
        execute: _Executor,
        receipt_intents: Callable[[], frozenset[str]],
        ops: frozenset[str] | None = None,
        metrics: Any | None = None,
    ) -> None:
        self._store = store
        self._platform = platform
        self._account_key = account_key
        self._execute = execute
        self._receipt_intents = receipt_intents
        self._ops = ops
        self._metrics = metrics

    # ---- HTTP path ---------------------------------------------------------

    async def run(self, action: Mapping[str, Any], *,
                  links: list[Any] | None = None) -> ActionReply:
        """`links` are the OTel `Link`s the deliver span holds to the
        caller's lineage — the W3C `traceparent`/`tracestate` headers the
        action POST carried, parsed through `bt.carrier_links`. Absent or
        invalid context means no link, never a fabricated one."""
        aid = str(action.get("action_id") or "")
        op = str(action.get("op") or "")
        r = action.get("request")
        if self._ops is not None and op not in self._ops:
            return ActionReply("rejected", kind="unsupported", message=op)
        try:
            owner = ActionOwner.from_dict(action.get("owner") or {})
        except (KeyError, TypeError, ValueError):
            return ActionReply(
                "rejected", kind="bad_request", message="bad owner")
        if not isinstance(r, dict) or not aid or not op:
            return ActionReply(
                "rejected", kind="bad_request", message="missing field")
        target_id = str((r.get("target") or {}).get("id") or "")
        if not target_id:
            return ActionReply(
                "rejected", kind="bad_request", message="missing target")
        # Refuse BEFORE any effect when the ledger is unreachable — an
        # unrecorded effect is the one thing this API must never produce.
        try:
            async with self._store._engine.begin() as conn:
                verdict_owner = await validate_owner(
                    conn, platform=self._platform,
                    account_key=self._account_key, owner=owner,
                    target_id=target_id,
                    receipt_intents=self._receipt_intents())
                if not verdict_owner.ok:
                    return ActionReply(
                        "rejected", kind="owner",
                        message=verdict_owner.reason)
                verdict, prior = await self._store.reserve_action(
                    conn, action_id=aid, platform=self._platform,
                    account_key=self._account_key,
                    owner_kind=owner.kind, owner_id=owner.id,
                    owner_generation=owner.generation, op=op, request=r,
                    message_id=verdict_owner.message_id,
                    request_stored=stored_request(owner, r))
        except Exception as exc:  # noqa: BLE001 — boundary catch; logged by type, outcome stays honest
            log.error("action store unavailable: %s", type(exc).__name__)
            return ActionReply(
                "unavailable", kind="store", message="db unreachable")
        if verdict == "replay":
            # Immutable binding on replay: same id + same request + same
            # owner — return the persisted outcome, no second effect.
            return _reply_from_row(prior)  # type: ignore[arg-type]
        if verdict == "conflict":
            return ActionReply(
                "conflict", kind="id_reused", message="action id spent")
        # The outbound effect is its own bounded root — never a child of an
        # ambient span (design 04 §delivery): it links to the producer
        # context only when the caller carries one (the receipt lane).
        bt.lifecycle(self._metrics, "delivery_started",
                     platform=self._platform, action_id=aid)
        with bt.operation_span("deliver", platform=self._platform,
                               links=list(links or []),
                               action_id=aid):
            reply = await self._execute(action)
            final = await self._terminal(
                aid, reply.state, platform_ref=reply.platform_ref,
                platform_ms=reply.platform_ms,
                error=({"kind": reply.kind, "message": reply.message}
                       if reply.kind else None))
        self._delivery_outcome(aid, op, final)
        if final is None:
            # The effect ran but the outcome was NOT persisted — report the
            # retained ambiguity, never a terminal the ledger doesn't hold.
            return ActionReply(
                "uncertain", kind="store",
                message="terminal write failed")
        return ActionReply(
            final, kind=reply.kind, message=reply.message,
            platform_ref=reply.platform_ref, platform_ms=reply.platform_ms)

    async def get(self, action_id: str) -> dict[str, Any] | None:
        """`GET /v1/actions/{id}` — the persisted truth, scoped: another
        platform's or account's action answers `None`, same as absent —
        a scoped 404, never a cross-tenant read."""
        row = await self._store.action(action_id)
        if (row is None or row["platform"] != self._platform
                or row["account_key"] != self._account_key):
            return None
        return dict(row)

    # ---- internal path (receipt lane) ----------------------------------------

    async def run_owned(
        self, *, action_id: str, owner: ActionOwner, op: str,
        request: dict[str, Any],
        links: list[Any] | None = None,
    ) -> str:
        """Reserve → execute → terminal for a bridge-initiated action whose
        authority is the bridge's own durable evidence (a journaled receipt
        intent), not an HTTP body — the client-facing gate stays closed to
        internal owners minted here. Returns the persisted phase:
        `confirmed`/`uncertain`/`rejected`/`conflict`/`unavailable` — the
        last means the ledger was unreachable and the caller must retry.

        `links` are the OTel `Link`s this bounded root holds to the
        producer lineage (the envelope's frozen trace via
        `bt.carrier_links`) — the bridge passes the carrier's links, never
        an ambient parent."""
        try:
            async with self._store._engine.begin() as conn:
                verdict, prior = await self._store.reserve_action(
                    conn, action_id=action_id, platform=self._platform,
                    account_key=self._account_key,
                    owner_kind=owner.kind, owner_id=owner.id,
                    owner_generation=owner.generation, op=op,
                    request=request)
        except Exception as exc:  # noqa: BLE001 — boundary catch; logged by type
            log.error("action store unavailable: %s", type(exc).__name__)
            return "unavailable"
        if verdict == "execute":
            bt.lifecycle(self._metrics, "delivery_started",
                         platform=self._platform, action_id=action_id)
            with bt.operation_span(
                    "deliver", platform=self._platform, links=links,
                    action_id=action_id):
                reply = await self._execute({
                    "action_id": action_id, "op": op, "request": request,
                    "owner": {"kind": owner.kind, "id": owner.id,
                              "generation": owner.generation}})
                final = await self._terminal(
                    action_id, reply.state, platform_ref=reply.platform_ref,
                    platform_ms=reply.platform_ms,
                    error=({"kind": reply.kind, "message": reply.message}
                           if reply.kind else None))
            self._delivery_outcome(action_id, op, final)
            return final or "unavailable"
        if verdict == "replay" and prior is not None:
            if prior["state"] == "started":
                # Crash between reserve and terminal: outcome unknowable —
                # retain `uncertain`, never resend.
                final = await self._terminal(
                    action_id, "uncertain", error={
                        "kind": "recovery",
                        "message": "outcome unknown after restart"})
                return final or "unavailable"
            return str(prior["state"])
        return verdict  # 'conflict' | 'unavailable'

    def _delivery_outcome(
            self, action_id: str, op: str, final: str | None) -> None:
        """The closed vocabulary of persisted outcomes — logged and counted
        at the durable result boundary, never before it lands."""
        if final == "confirmed":
            bt.lifecycle(self._metrics, "delivery_confirmed",
                         platform=self._platform, action_id=action_id)
        elif final != "rejected":
            # `None` (terminal write failed) and `uncertain` BOTH mean the
            # outcome is not provably delivered — the same WARNING event.
            # A persisted `rejected` IS provable: the counter carries it.
            bt.lifecycle(self._metrics, "delivery_uncertain",
                         platform=self._platform, action_id=action_id,
                         reason=final or "terminal_write_failed")
        # `unavailable`/`conflict` persisted terminals and a failed write
        # all mean "not provably delivered" — the counter's vocabulary is
        # the narrower one, so they land on `uncertain`, never a raise.
        bt.action_metrics(
            self._metrics, platform=self._platform, op=op,
            outcome=final if final in bt.ACTION_OUTCOMES else "uncertain")

    # ---- shared terminal CAS -------------------------------------------------

    async def _terminal(
        self, action_id: str, state: str, *,
        platform_ref: str | None = None, platform_ms: int | None = None,
        error: dict[str, Any] | None = None,
    ) -> str | None:
        """started→terminal CAS. Returns the persisted state — an already-
        terminal row keeps its truth instead of being overwritten. `None`
        means the write did NOT land: the caller must not report a terminal
        state the ledger doesn't hold."""
        try:
            async with self._store._engine.begin() as conn:
                return await self._store.action_state(
                    conn, action_id, state, platform_ref=platform_ref,
                    platform_ms=platform_ms, error=error)
        except Exception as exc:  # noqa: BLE001 — boundary catch; logged by type
            log.error("terminal write failed: %s", type(exc).__name__)
            return None


__all__ = [
    "REPLAY_RETRY_SECONDS",
    "ActionReply",
    "DurableActionService",
    "JournalCaptureSink",
    "JournalPublisher",
    "action_http_status",
    "action_response",
    "admitted",
    "receipt_intent_ids",
    "receipt_via",
]
