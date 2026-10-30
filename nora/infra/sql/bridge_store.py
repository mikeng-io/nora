"""`SqlBridgeStore` — Postgres truth for the platform-bridge handoff.

`bridge_event` is the canonical source mapping: uniqueness on
(platform, account_key, source_digest) is deliberately independent of the
HMAC key version that produced any `event_id` alias, so a key rotation or a
producer replay resolves to the original row instead of duplicating it.

`bridge_work` rows are the obligations a deployment must not lose. Claiming
bumps `generation`, which is also the fence every `owner_kind='work'`
bridge_action is validated against: a stale owner that wakes up after a reclaim
can no longer complete or send on behalf of work it no longer owns.

`bridge_action` is written by bridge processes (they own reservation/result);
the app reads it for recovery decisions. Writes here use the same
`engine.begin()` transaction seam as the rest of `nora.infra.sql` so callers
can join the audit transaction by passing an open connection.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from .schema import BRIDGE_ACTION, BRIDGE_EVENT, BRIDGE_EVENT_ALIAS, BRIDGE_WORK

log = logging.getLogger("nora.infra.sql.bridge_store")

WORK_KINDS = frozenset({
    "ingest", "stimulus", "memory_feed", "turn",
    "participation", "receipt", "trace_debrief",
})

#: The advisory-lock namespace `DbSpeakingLocks`
#: (`nora.app.bridge_queue`) holds per (platform, conversation) for the
#: whole turn. `expire_leases` probes `pg_locks` under the same key — one
#: value, defined beside the SQL that probes it.
SPEAK_LOCK_NS = "nora-bridge-speak"
WORK_TERMINAL = frozenset({"done", "intentional_skip", "failed_retained",
                           "uncertain", "cancelled"})
EVENT_TERMINAL = frozenset({"done", "intentional_skip", "failed_retained",
                            "uncertain"})


@dataclass(frozen=True)
class BridgeWork:
    id: int
    event_id: str | None
    kind: str
    ordinal: int
    platform: str
    account_key: str
    conversation_id: str
    generation: int
    attempts: int
    payload: dict[str, Any]
    result: dict[str, Any] | None = None


def _work(row: sa.RowMapping | dict[str, Any]) -> BridgeWork:
    return BridgeWork(
        id=row["id"], event_id=row["event_id"], kind=row["kind"],
        ordinal=row["ordinal"], platform=row["platform"],
        account_key=row["account_key"], conversation_id=row["conversation_id"],
        generation=row["generation"], attempts=row["attempts"],
        payload=dict(row["payload"]),
        result=dict(row["result"]) if row["result"] else None,
    )


def request_hash(request: dict[str, Any]) -> str:
    """Canonical request identity — the reservation-equality check that makes a
    replayed POST /v1/actions idempotent and a mutated one a conflict."""
    return hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class SqlBridgeStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    # ---- events ---------------------------------------------------------

    async def register_event(
        self, conn: AsyncConnection, *, event_id: str, platform: str,
        account_key: str, source_key: str, source_digest: str, kind: str,
        stream: str, stream_id: str, conversation_id: str,
        conversation_type: str, sender_id: str, received_ms: int,
        queued_ms: int, time_quality: str, payload: dict[str, Any],
        admission_rev: int | None,
    ) -> tuple[str, bool]:
        """Idempotent registration inside the caller's transaction.

        Returns `(event_row_id, is_new)`. A replayed alias resolves to the
        original row via the alias table, then via canonical source_digest —
        the second case is a new HMAC version of an old source, which records
        the alias instead of a duplicate event.
        """
        alias = await conn.execute(sa.select(BRIDGE_EVENT_ALIAS.c.event_ref).where(
            BRIDGE_EVENT_ALIAS.c.event_id == event_id))
        if (ref := alias.scalar_one_or_none()) is not None:
            return ref, False
        stmt = pg_insert(BRIDGE_EVENT).values(
            id=event_id, platform=platform, account_key=account_key,
            source_key=source_key, source_digest=source_digest, kind=kind,
            stream=stream, stream_id=stream_id, conversation_id=conversation_id,
            conversation_type=conversation_type, sender_id=sender_id,
            received_ms=received_ms, queued_ms=queued_ms,
            time_quality=time_quality, payload=payload,
            admission_rev=admission_rev,
        ).on_conflict_do_nothing(
            constraint="uq_bridge_event_source")
        await conn.execute(stmt)
        row = await conn.execute(
            sa.select(BRIDGE_EVENT.c.id).where(
                BRIDGE_EVENT.c.platform == platform,
                BRIDGE_EVENT.c.account_key == account_key,
                BRIDGE_EVENT.c.source_digest == source_digest))
        existing = row.scalar_one()
        if existing != event_id:
            # A different alias of the same canonical source (key rotation) —
            # map the new alias to the original id.
            await conn.execute(pg_insert(BRIDGE_EVENT_ALIAS).values(
                event_id=event_id, event_ref=existing).on_conflict_do_nothing())
            return existing, False
        return existing, True

    async def set_event_state(self, conn: AsyncConnection, event_id: str,
                              state: str, *, reason: str | None = None,
                              tag_id: str | None = None,
                              admission: dict[str, Any] | None = None) -> None:
        if state not in EVENT_TERMINAL | {"open"}:
            raise ValueError(f"bad event state {state!r}")
        values: dict[str, Any] = {"state": state,
                                  "updated_at": sa.func.now()}
        if reason is not None:
            values["outcome_reason"] = reason
        if tag_id is not None:
            values["tag_id"] = tag_id
        if admission is not None:
            values["admission"] = admission
        await conn.execute(sa.update(BRIDGE_EVENT).where(
            BRIDGE_EVENT.c.id == event_id).values(**values))

    async def event(self, event_id: str) -> sa.RowMapping | None:
        async with self._engine.connect() as conn:
            row = await conn.execute(sa.select(BRIDGE_EVENT).where(
                BRIDGE_EVENT.c.id == event_id))
            return row.mappings().one_or_none()

    async def event_open_work(self, conn: AsyncConnection,
                              event_id: str) -> list[BridgeWork]:
        rows = await conn.execute(sa.select(BRIDGE_WORK).where(
            BRIDGE_WORK.c.event_id == event_id,
            ~BRIDGE_WORK.c.state.in_(tuple(WORK_TERMINAL))).order_by(BRIDGE_WORK.c.ordinal))
        return [_work(r) for r in rows.mappings()]

    # ---- work obligations -----------------------------------------------

    async def enqueue_work(
        self, conn: AsyncConnection, *, event_id: str | None, kind: str,
        ordinal: int, platform: str, account_key: str, conversation_id: str,
        payload: dict[str, Any], due_at: Any | None = None,
    ) -> None:
        """Persist an obligation inside the caller's transaction — the call
        that makes an intent durable BEFORE any detached execution can be
        lost by a deploy."""
        if kind not in WORK_KINDS:
            raise ValueError(f"unknown work kind {kind!r}")
        values: dict[str, Any] = {
            "event_id": event_id, "kind": kind, "ordinal": ordinal,
            "platform": platform, "account_key": account_key,
            "conversation_id": conversation_id, "payload": payload,
        }
        if due_at is not None:
            values["due_at"] = due_at
        await conn.execute(pg_insert(BRIDGE_WORK).values(
            **values).on_conflict_do_nothing(constraint="uq_bridge_work_obligation"))

    async def claim_next(self, *, owner: str, lease_seconds: int = 300,
                         platform: str | None = None,
                         conversation_id: str | None = None,
                         kinds: frozenset[str] | tuple[str, ...] | None = None
                         ) -> BridgeWork | None:
        """Claim the earliest due non-terminal work row, skipping locked rows.

        The claim bumps `generation` — every later completion write must carry
        it, and every bridge_action the work sends is fenced by it. `kinds`
        scopes the claim to one lane's kinds — the WorkRunner's comprehend/
        speak lanes claim independently so no lane can take another's row.
        """
        cond = [BRIDGE_WORK.c.state.in_(("pending", "retryable_failure")),
                BRIDGE_WORK.c.due_at <= sa.func.now()]
        if platform is not None:
            cond += [BRIDGE_WORK.c.platform == platform]
        if conversation_id is not None:
            cond += [BRIDGE_WORK.c.conversation_id == conversation_id]
        if kinds is not None:
            cond += [BRIDGE_WORK.c.kind.in_(tuple(kinds))]
        async with self._engine.begin() as conn:
            row = (await conn.execute(
                sa.select(BRIDGE_WORK).where(*cond)
                .order_by(BRIDGE_WORK.c.id)
                .limit(1).with_for_update(skip_locked=True))).mappings().one_or_none()
            if row is None:
                return None
            new_generation = row["generation"] + 1
            await conn.execute(sa.update(BRIDGE_WORK).where(
                BRIDGE_WORK.c.id == row["id"]).values(
                state="running", generation=new_generation,
                attempts=BRIDGE_WORK.c.attempts + 1, lease_owner=owner,
                leased_until=sa.func.now() + sa.text(
                    f"interval '{int(lease_seconds)} seconds'"),
                updated_at=sa.func.now()))
            return _work({**row, "generation": new_generation})

    async def finish_work(self, conn: AsyncConnection, work_id: int, *,
                          generation: int, state: str,
                          result: dict[str, Any] | None = None) -> bool:
        """Fenced completion — a stale generation writes nothing and answers
        False so the caller knows ownership was lost rather than completed."""
        # "retryable_failure" is a named non-terminal state (claimable again
        # after `due_at`), "pending" the plain re-park; both leave
        # `completed_at` unset, which is what non-terminal means on the row.
        if state not in WORK_TERMINAL | {"pending", "retryable_failure"}:
            raise ValueError(f"bad work state {state!r}")
        # `result` MERGES into the existing JSONB — a persisted admission
        # verdict or an earlier failure's record must survive the final
        # disposition write, not be clobbered by it.
        res = await conn.execute(sa.update(BRIDGE_WORK).where(
            BRIDGE_WORK.c.id == work_id,
            BRIDGE_WORK.c.generation == generation).values(
            state=state,
            result=sa.func.coalesce(BRIDGE_WORK.c.result, sa.cast({}, pg.JSONB))
            .concat(sa.cast(result or {}, pg.JSONB)),
            lease_owner=None, leased_until=None,
            updated_at=sa.func.now(),
            completed_at=sa.func.now() if state in WORK_TERMINAL else None))
        return res.rowcount == 1

    async def record_admission(self, conn: AsyncConnection, work_id: int, *,
                               generation: int, admission: dict[str, Any]) -> bool:
        """Persist the admission verdict on the turn row — a recovered tag
        reuses it instead of re-running debounce against itself."""
        res = await conn.execute(sa.update(BRIDGE_WORK).where(
            BRIDGE_WORK.c.id == work_id,
            BRIDGE_WORK.c.generation == generation).values(
            result=sa.func.coalesce(BRIDGE_WORK.c.result, sa.cast({}, pg.JSONB))
            .concat(sa.cast({"admission": admission}, pg.JSONB)),
            updated_at=sa.func.now()))
        return res.rowcount == 1

    async def nonterminal_work(self, *, limit: int = 500) -> list[BridgeWork]:
        """Boot-scan + sweep source: work a dead owner left non-terminal.
        `running` rows whose lease expired are re-claimable."""
        async with self._engine.connect() as conn:
            rows = await conn.execute(sa.select(BRIDGE_WORK).where(
                ~BRIDGE_WORK.c.state.in_(tuple(WORK_TERMINAL))).order_by(
                BRIDGE_WORK.c.id).limit(limit))
            return [_work(r) for r in rows.mappings()]

    async def expire_leases(self) -> int:
        """Re-park `running` rows whose lease is dead AND whose holder is
        gone. An expired `leased_until` alone does NOT prove the owner is
        dead — a long turn's heartbeat may simply lag — so a row whose
        speaking-session advisory lock (`nora-bridge-speak`, the same key
        `DbSpeakingLocks` takes) is still GRANTED in `pg_locks` keeps its
        claim. `pg_locks` is the server-side liveness truth: a dead backend
        cannot hold one."""
        async with self._engine.begin() as conn:
            res = await conn.execute(sa.text(
                "UPDATE bridge_work SET state='pending',"
                " generation = generation + 1,"
                " lease_owner=NULL, leased_until=NULL, updated_at=now()"
                " WHERE state='running' AND leased_until < now()"
                " AND NOT EXISTS ("
                "   SELECT 1 FROM pg_locks l"
                "   WHERE l.locktype = 'advisory' AND l.granted"
                "     AND l.objsubid = 2"  # the two-int advisory form
                "     AND l.classid = hashtext(:ns)::oid"
                "     AND l.objid = hashtext("
                "       bridge_work.platform || ':' ||"
                "       bridge_work.conversation_id)::oid"
                " )"), {"ns": SPEAK_LOCK_NS})
            return res.rowcount or 0

    async def renew_lease(self, work_id: int, *, generation: int,
                          owner: str, lease_seconds: int) -> bool:
        """Fenced heartbeat — the RUNNING owner extends `leased_until` so a
        legitimately long turn keeps signing actions. Every predicate is the
        same fence `claim_next` established: wrong generation, wrong holder,
        re-parked state, or an already-dead lease writes nothing and answers
        False so the caller knows ownership is gone, not briefly delayed."""
        async with self._engine.begin() as conn:
            res = await conn.execute(sa.update(BRIDGE_WORK).where(
                BRIDGE_WORK.c.id == work_id,
                BRIDGE_WORK.c.generation == generation,
                BRIDGE_WORK.c.lease_owner == owner,
                BRIDGE_WORK.c.state == "running",
                BRIDGE_WORK.c.leased_until > sa.func.now()).values(
                leased_until=sa.func.now() + sa.text(
                    f"interval '{int(lease_seconds)} seconds'"),
                updated_at=sa.func.now()))
        return res.rowcount == 1

    # ---- actions (bridge-side writes, app-side reads) ---------------------

    async def reserve_action(
        self, conn: AsyncConnection, *, action_id: str, platform: str,
        account_key: str, owner_kind: str, owner_id: str,
        owner_generation: int | None, op: str, request: dict[str, Any],
        message_id: int | None = None,
        request_stored: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        """`("execute", None)` ONLY to the caller that atomically transitions
        prepared→started in this statement — a read-then-decide under a second
        transaction would let two callers both execute. `("replay", row)` for
        an identical already-spent request; `("conflict", row)` when the id
        was spent on a different request or a different owner/generation.

        `request_stored` is what the JSONB keeps when it must differ from the
        hashed request — the OTP send, whose `text` is a live code that never
        rests. `request_hash` is ALWAYS over the full `request`, so two
        requests that differ only in the code still fence each other."""
        digest = request_hash(request)
        await conn.execute(pg_insert(BRIDGE_ACTION).values(
            action_id=action_id, platform=platform, account_key=account_key,
            owner_kind=owner_kind, owner_id=owner_id,
            owner_generation=owner_generation, op=op,
            request=request if request_stored is None else request_stored,
            request_hash=digest, message_id=message_id,
        ).on_conflict_do_nothing())
        # The reservation IS the start: immutable binding fields ride the CAS
        # predicate so a stale owner or a different account/op/request can
        # never take a prepared row the first caller still owns.
        taken = (await conn.execute(sa.update(BRIDGE_ACTION).where(
            BRIDGE_ACTION.c.action_id == action_id,
            BRIDGE_ACTION.c.state == "prepared",
            BRIDGE_ACTION.c.request_hash == digest,
            BRIDGE_ACTION.c.platform == platform,
            BRIDGE_ACTION.c.account_key == account_key,
            BRIDGE_ACTION.c.owner_kind == owner_kind,
            BRIDGE_ACTION.c.owner_id == owner_id,
            BRIDGE_ACTION.c.owner_generation == owner_generation,
            BRIDGE_ACTION.c.op == op,
        ).values(state="started", started_at=sa.func.now()))).rowcount
        if taken == 1:
            return "execute", None
        row = (await conn.execute(sa.select(BRIDGE_ACTION).where(
            BRIDGE_ACTION.c.action_id == action_id))).mappings().one()
        if (row["request_hash"] == digest and row["owner_kind"] == owner_kind
                and row["owner_id"] == owner_id):
            return "replay", dict(row)
        return "conflict", dict(row)

    async def action_state(self, conn: AsyncConnection, action_id: str,
                           state: str, *, expected: str | None = "started",
                           platform_ref: str | None = None,
                           platform_ms: int | None = None,
                           error: dict[str, Any] | None = None) -> str:
        """Terminal CAS: moves `expected → state` in one update. An already-
        terminal row is never overwritten — its persisted state wins and is
        returned, so a late/duplicate completion cannot rewrite the outcome."""
        if state not in {"started", "confirmed", "rejected", "uncertain"}:
            raise ValueError(f"bad action state {state!r}")
        pred = BRIDGE_ACTION.c.action_id == action_id
        if expected is not None:
            pred &= BRIDGE_ACTION.c.state == expected
        res = await conn.execute(sa.update(BRIDGE_ACTION).where(pred).values(
            state=state, platform_ref=platform_ref, platform_ms=platform_ms,
            error=error, started_at=sa.func.coalesce(
                BRIDGE_ACTION.c.started_at, sa.func.now()),
            completed_at=sa.func.now() if state != "started" else None))
        if res.rowcount:
            return state
        row = (await conn.execute(sa.select(BRIDGE_ACTION.c.state).where(
            BRIDGE_ACTION.c.action_id == action_id))).one_or_none()
        if row is None:
            raise LookupError(action_id)
        current: str = row[0]
        return current

    async def action(self, action_id: str) -> dict[str, Any] | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(sa.select(BRIDGE_ACTION).where(
                BRIDGE_ACTION.c.action_id == action_id))).mappings().one_or_none()
            return dict(row) if row else None

    async def owner_actions(self, owner_kind: str,
                            owner_id: str) -> list[dict[str, Any]]:
        async with self._engine.connect() as conn:
            rows = await conn.execute(sa.select(BRIDGE_ACTION).where(
                BRIDGE_ACTION.c.owner_kind == owner_kind,
                BRIDGE_ACTION.c.owner_id == owner_id))
            return [dict(r) for r in rows.mappings()]

    async def work_generation(self, conn: AsyncConnection,
                              work_id: str) -> int | None:
        row = (await conn.execute(sa.select(BRIDGE_WORK.c.generation).where(
            BRIDGE_WORK.c.id == int(work_id)))).one_or_none()
        return row[0] if row else None

    # ---- advisory locks --------------------------------------------------

    @asynccontextmanager
    async def advisory_lock(self, namespace: str, *key_parts: str
                            ) -> AsyncIterator[bool]:
        """`pg_try_advisory_lock` scoped to a held connection. Yields whether
        the lock was taken; the caller decides what 'not taken' means."""
        conn = await self._engine.connect()
        try:
            acquired = (await conn.execute(sa.text(
                "SELECT pg_try_advisory_lock("
                "hashtext(:ns), hashtext(:k))"),
                {"ns": namespace, "k": ":".join(key_parts)})).scalar_one()
            yield bool(acquired)
            if acquired:
                await conn.execute(sa.text(
                    "SELECT pg_advisory_unlock(hashtext(:ns), hashtext(:k))"),
                    {"ns": namespace, "k": ":".join(key_parts)})
        finally:
            await conn.close()
