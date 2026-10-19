"""`SqlPersonaStore`: Persona accounts and their lines (design/unified-memory/45).

**One write path.** `write` opens one transaction, locks the account row,
reads every line of the account, asks `nora.domain.persona.plan_write`
whether the ops may stand, and inserts exactly the rows it returns. A write
that any op fails inserts nothing and returns every refusal; there is no
other way into `persona_line`, so the reasoning-mode rules hold for every
row the tables carry.

`persona_line` is append-only (`nora.infra.sql.history.APPEND_ONLY`): an
account is its chain heads, the rows nothing supersedes. `persona` changes in
place and keeps `row_history`.

**The index (45 §1, the switch).** With `index` on (`[persona] render`),
every write projects the rows it inserted and the rows they supersede into
the memory index in the write's own `begin()` block (25 §7). Off is the
shadow: the write lands, no node does, and no prompt changes. Lines written
during the shadow join the index through the flip's `nora memory rebuild`
(45 §8) — the projector converges on the source's current row, so a rebuild
equals live projection.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.persona import (
    DISPUTE_KINDS,
    VERDICTS,
    Account,
    DisputeResult,
    PersonaCitable,
    PersonaDispute,
    PersonaLine,
    PersonaOp,
    PersonaRefusal,
    PersonaWriteResult,
    match_disputed_line,
    plan_write,
)
from nora.infra.sql import memory_index
from nora.infra.sql.schema import PERSONA, PERSONA_DISPUTE, PERSONA_LINE

__all__ = ["SqlPersonaStore", "chain_heads"]


def _line(row: sa.Row[Any]) -> PersonaLine:
    return PersonaLine(
        id=str(row.id), persona_key=row.persona_key, part=row.part, kind=row.kind,
        text=row.text, mode=row.mode, citations=tuple(row.citations), state=row.state,
        unit=row.unit, written_at=row.written_at, written_by_session=row.written_by_session,
        draws_on=tuple(str(d) for d in row.draws_on), told_by=row.told_by,
        prediction=row.prediction, prediction_observable=row.prediction_observable,
        prediction_window_end=row.prediction_window_end, verdict=row.verdict,
        verdict_by=row.verdict_by, verdict_at=row.verdict_at,
        supersedes=None if row.supersedes is None else str(row.supersedes),
        support_count=row.support_count, last_supported_at=row.last_supported_at,
        exclude_persons=tuple(row.exclude_persons), as_of=row.as_of,
    )


def _values(line: PersonaLine) -> dict[str, Any]:
    return {
        "id": line.id, "persona_key": line.persona_key, "part": line.part,
        "kind": line.kind, "text": line.text, "mode": line.mode,
        "citations": list(line.citations), "draws_on": list(line.draws_on),
        "told_by": line.told_by, "prediction": line.prediction,
        "prediction_observable": line.prediction_observable,
        "prediction_window_end": line.prediction_window_end, "state": line.state,
        "verdict": line.verdict, "verdict_by": line.verdict_by, "verdict_at": line.verdict_at,
        "supersedes": line.supersedes, "support_count": line.support_count,
        "last_supported_at": line.last_supported_at, "unit": line.unit,
        "exclude_persons": list(line.exclude_persons), "as_of": line.as_of,
        "written_at": line.written_at, "written_by_session": line.written_by_session,
    }


def chain_heads(lines: Sequence[PersonaLine]) -> tuple[tuple[PersonaLine, ...], dict[str, str]]:
    """Every row of one account reduced to its chain heads (the rows nothing
    supersedes), and each row id's head id (`plan_write`'s `aliases`)."""
    successor = {line.supersedes: line.id for line in lines if line.supersedes is not None}
    heads = tuple(line for line in lines if line.id not in successor)
    aliases: dict[str, str] = {}
    for line in lines:
        head = line.id
        while head in successor:
            head = successor[head]
        aliases[line.id] = head
    return heads, aliases


class SqlPersonaStore:
    def __init__(
        self, engine: AsyncEngine, *, screen: Callable[[str], str | None],
        index: bool = False,
    ) -> None:
        """`screen` is the R-18/R-28 egress screen every line's texts pass
        (`nora.agent.dream_screen.text_offence` in production): held here so
        every writer is screened the same way, and none can forget it.

        `index` is the switch (45 §5): on, every write projects its lines
        into the memory index; off (the shadow), no write does."""
        self._engine = engine
        self._screen = screen
        self._index = index

    async def account_heads(self, *, key: str) -> tuple[PersonaLine, ...]:
        """The account as it stands: every chain head, in every state."""
        stmt = (sa.select(PERSONA_LINE).where(PERSONA_LINE.c.persona_key == key)
                .order_by(PERSONA_LINE.c.written_at, PERSONA_LINE.c.id))
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return chain_heads([_line(r) for r in rows])[0]

    async def account_state(self, *, key: str) -> tuple[int, int | None]:
        """The account's revision (0 when it has never been written: what a
        session's write is compared against, `persona_write`'s
        `expected_revision`) and when its session last settled it (`None`:
        never, so its first session is the backfill, 35 §4)."""
        async with self._engine.connect() as conn:
            found = (await conn.execute(
                sa.select(PERSONA.c.revision, PERSONA.c.settled_at).where(PERSONA.c.key == key)
            )).one_or_none()
        return (0, None) if found is None else (int(found.revision), found.settled_at)

    async def pending_told(self, *, key: str) -> int:
        """How many `told` lines a turn (`written_by_session = 'tag'`) wrote
        into this account since its session last settled it
        (`persona.settled_at`): the stimulus that wakes the session at once
        (45 §4)."""
        settled = (
            sa.select(PERSONA.c.settled_at).where(PERSONA.c.key == key).scalar_subquery()
        )
        stmt = sa.select(sa.func.count()).select_from(PERSONA_LINE).where(
            PERSONA_LINE.c.persona_key == key,
            PERSONA_LINE.c.written_by_session == "tag",
            PERSONA_LINE.c.written_at > sa.func.coalesce(settled, -1),
        )
        async with self._engine.connect() as conn:
            return int((await conn.execute(stmt)).scalar_one())

    # -- disputes (35 §2) ---------------------------------------------------

    async def record_dispute(
        self, *, account: Account, disputed_by: str, claim: str, kind: str,
        message_id: int, now_ms: int,
    ) -> DisputeResult:
        """Record a dispute of this account's line and do what 35 §2 rules at
        once: the subject's own dispute or «forget that» HIDES the matched
        line (a new row, state `hidden`), anyone else's marks it `disputed`
        (awaiting weighing), and an unmatched claim lands as the row alone
        for the next session to weigh. One transaction; a racing write to the
        same line is a `conflict` and nothing lands."""
        claim = claim.strip()
        offence = self._screen(claim)
        if offence is not None:
            return DisputeResult(status="refused", refusal=PersonaRefusal(
                offence, "the claim carries an identifier or a place's coordinates"))
        if kind not in DISPUTE_KINDS:
            return DisputeResult(status="refused", refusal=PersonaRefusal(
                "malformed", f"kind is one of {DISPUTE_KINDS}"))
        if kind == "forget" and disputed_by != account.subject:
            return DisputeResult(status="refused", refusal=PersonaRefusal(
                "forget_not_subject", "only the person themselves can ask to forget"))
        dispute_id = str(uuid.uuid4())
        try:
            async with self._engine.connect() as conn:
                trans = await conn.begin()
                head_row = (await conn.execute(
                    sa.select(PERSONA.c.revision).where(PERSONA.c.key == account.key)
                    .with_for_update()
                )).one_or_none()
                heads: tuple[PersonaLine, ...] = ()
                if head_row is not None:
                    rows = (await conn.execute(
                        sa.select(PERSONA_LINE)
                        .where(PERSONA_LINE.c.persona_key == account.key)
                        .order_by(PERSONA_LINE.c.written_at, PERSONA_LINE.c.id)
                    )).all()
                    heads = chain_heads([_line(r) for r in rows])[0]
                matched = match_disputed_line(claim, heads)
                effect: Literal["hidden", "disputed", "recorded"] = "recorded"
                if matched is not None:
                    effect = ("hidden" if disputed_by == account.subject
                              else "disputed")
                    if matched.state != effect and head_row is not None:
                        new_line = replace(
                            matched, id=str(uuid.uuid4()), supersedes=matched.id,
                            state=effect, written_at=now_ms,
                            written_by_session="tag",
                        )
                        await conn.execute(sa.insert(PERSONA_LINE).values(_values(new_line)))
                        if self._index:
                            # 25 §7, in the write's own begin(): the new row,
                            # and the row it supersedes (hidden drops both).
                            await memory_index.project(conn, "persona_line", new_line.id)
                            await memory_index.project(conn, "persona_line", matched.id)
                        await conn.execute(
                            sa.update(PERSONA).where(PERSONA.c.key == account.key)
                            .values(revision=head_row.revision + 1, updated_at=now_ms))
                await conn.execute(sa.insert(PERSONA_DISPUTE).values(
                    id=dispute_id, persona_key=account.key,
                    line_id=None if matched is None else matched.id,
                    disputed_by=disputed_by, kind=kind, claim=claim,
                    message_id=message_id, unit=account.unit, written_at=now_ms,
                ))
                await trans.commit()
        except sa.exc.IntegrityError as exc:
            if "uq_persona_line_supersedes" not in str(exc):
                raise
            return DisputeResult(status="conflict")
        return DisputeResult(status="ok", dispute_id=dispute_id, effect=effect)

    async def pending_disputes(self, *, key: str) -> int:
        """How many disputes of this account landed since its session last
        settled it: a stimulus that wakes the session at once, beside
        `pending_told`."""
        settled = (
            sa.select(PERSONA.c.settled_at).where(PERSONA.c.key == key).scalar_subquery()
        )
        stmt = sa.select(sa.func.count()).select_from(PERSONA_DISPUTE).where(
            PERSONA_DISPUTE.c.persona_key == key,
            PERSONA_DISPUTE.c.written_at > sa.func.coalesce(settled, -1),
        )
        async with self._engine.connect() as conn:
            return int((await conn.execute(stmt)).scalar_one())

    async def open_disputes(self, *, key: str, limit: int) -> tuple[PersonaDispute, ...]:
        """The account's pending disputes, oldest first: what the session
        weighs in its identity phase (45 §4 phase 1)."""
        settled = (
            sa.select(PERSONA.c.settled_at).where(PERSONA.c.key == key).scalar_subquery()
        )
        stmt = (sa.select(PERSONA_DISPUTE)
                .where(PERSONA_DISPUTE.c.persona_key == key,
                       PERSONA_DISPUTE.c.written_at > sa.func.coalesce(settled, -1))
                .order_by(PERSONA_DISPUTE.c.written_at, PERSONA_DISPUTE.c.id)
                .limit(limit))
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return tuple(PersonaDispute(
            id=str(r.id), persona_key=r.persona_key,
            line_id=None if r.line_id is None else str(r.line_id),
            disputed_by=r.disputed_by, kind=r.kind, claim=r.claim,
            message_id=int(r.message_id), unit=r.unit, written_at=int(r.written_at),
        ) for r in rows)

    # -- hypothesis verdicts (45 §3) -----------------------------------------

    async def due_hypotheses(
        self, *, unit: str, now_ms: int, limit: int,
    ) -> tuple[PersonaLine, ...]:
        """The unit's open hypotheses whose prediction window has closed, the
        judge's docket (45 §3: the verdict is set when `prediction_window_end`
        passes, by a judge other than the writing adaptor). Chain heads only:
        a superseded row is judged through its successor."""
        superseded = (sa.select(PERSONA_LINE.c.supersedes)
                      .where(PERSONA_LINE.c.supersedes.isnot(None)).scalar_subquery())
        stmt = (sa.select(PERSONA_LINE)
                .where(PERSONA_LINE.c.unit == unit,
                       PERSONA_LINE.c.part == "hypothesis",
                       PERSONA_LINE.c.verdict == "open",
                       PERSONA_LINE.c.prediction_window_end <= now_ms,
                       PERSONA_LINE.c.id.notin_(superseded))
                .order_by(PERSONA_LINE.c.prediction_window_end, PERSONA_LINE.c.id)
                .limit(limit))
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return tuple(_line(r) for r in rows)

    async def record_verdict(
        self, *, key: str, line_id: str, verdict: str, verdict_by: str, now_ms: int,
    ) -> Literal["ok", "conflict", "refused"]:
        """Set one hypothesis's verdict: a new row superseding its chain head,
        carrying `verdict`/`verdict_by`/`verdict_at` and nothing else changed.
        This is the judge's path, deliberately NOT a `persona_write` op — a
        hypothesis is never graded by its writer (45 §3). `conflict` when the
        line moved meanwhile or was judged already; `refused` when it is not
        an open, window-closed hypothesis of this account."""
        if verdict not in VERDICTS or verdict == "open":
            return "refused"
        try:
            async with self._engine.connect() as conn:
                trans = await conn.begin()
                head_row = (await conn.execute(
                    sa.select(PERSONA.c.revision).where(PERSONA.c.key == key)
                    .with_for_update()
                )).one_or_none()
                if head_row is None:
                    await trans.rollback()
                    return "refused"
                rows = (await conn.execute(
                    sa.select(PERSONA_LINE).where(PERSONA_LINE.c.persona_key == key)
                    .order_by(PERSONA_LINE.c.written_at, PERSONA_LINE.c.id)
                )).all()
                heads, aliases = chain_heads([_line(r) for r in rows])
                by_id = {h.id: h for h in heads}
                head = by_id.get(aliases.get(line_id, ""))
                if head is None or head.part != "hypothesis":
                    await trans.rollback()
                    return "refused"
                if head.verdict != "open":
                    await trans.rollback()
                    return "conflict"
                if (head.prediction_window_end or 0) > now_ms:
                    await trans.rollback()
                    return "refused"
                new_line = replace(
                    head, id=str(uuid.uuid4()), supersedes=head.id, verdict=verdict,
                    verdict_by=verdict_by, verdict_at=now_ms, written_at=now_ms,
                    written_by_session="persona-verdict",
                )
                await conn.execute(sa.insert(PERSONA_LINE).values(_values(new_line)))
                if self._index:
                    # 25 §7, in the write's own begin(): the new row, and the
                    # chain head it supersedes.
                    await memory_index.project(conn, "persona_line", new_line.id)
                    await memory_index.project(conn, "persona_line", head.id)
                await conn.execute(
                    sa.update(PERSONA).where(PERSONA.c.key == key)
                    .values(revision=head_row.revision + 1, updated_at=now_ms))
                await trans.commit()
        except sa.exc.IntegrityError as exc:
            if "uq_persona_line_supersedes" not in str(exc):
                raise
            return "conflict"
        return "ok"

    async def persona_write(
        self, *, account: Account, ops: Sequence[PersonaOp], citable: PersonaCitable,
        now_ms: int, written_by_session: str,
        apply_decay: bool, expected_revision: int | None = None,
    ) -> PersonaWriteResult:
        """Judge and apply one write atomically (45 §4). `expected_revision`
        is the revision the writer read its handles at; a write against a
        newer one is a `conflict` and changes nothing."""
        try:
            return await self._write(
                account=account, ops=ops, citable=citable, now_ms=now_ms,
                written_by_session=written_by_session,
                apply_decay=apply_decay, expected_revision=expected_revision,
            )
        except sa.exc.IntegrityError as exc:
            if "uq_persona_line_supersedes" not in str(exc):
                raise
            return PersonaWriteResult(status="conflict", revision=None)

    async def _write(
        self, *, account: Account, ops: Sequence[PersonaOp], citable: PersonaCitable,
        now_ms: int, written_by_session: str,
        apply_decay: bool, expected_revision: int | None,
    ) -> PersonaWriteResult:
        async with self._engine.connect() as conn:
            trans = await conn.begin()
            await conn.execute(
                pg_insert(PERSONA).values(
                    key=account.key, entity_kind=account.entity_kind, unit=account.unit,
                    revision=0, updated_at=now_ms,
                ).on_conflict_do_nothing(index_elements=[PERSONA.c.key])
            )
            head = (await conn.execute(
                sa.select(PERSONA.c.entity_kind, PERSONA.c.revision)
                .where(PERSONA.c.key == account.key).with_for_update()
            )).one()
            if head.entity_kind != account.entity_kind:
                await trans.rollback()
                return PersonaWriteResult(status="refused", revision=head.revision, refusals=(
                    PersonaRefusal("malformed", f"this account is a {head.entity_kind}'s"),))
            if expected_revision is not None and head.revision != expected_revision:
                await trans.rollback()
                return PersonaWriteResult(status="conflict", revision=head.revision)
            rows = (await conn.execute(
                sa.select(PERSONA_LINE).where(PERSONA_LINE.c.persona_key == account.key)
                .order_by(PERSONA_LINE.c.written_at, PERSONA_LINE.c.id)
            )).all()
            heads, aliases = chain_heads([_line(r) for r in rows])
            plan = plan_write(
                account=account, heads=heads, ops=ops, citable=citable, now_ms=now_ms,
                written_by_session=written_by_session, screen=self._screen,
                apply_decay=apply_decay, aliases=aliases,
            )
            if plan.refused:
                await trans.rollback()
                return PersonaWriteResult(
                    status="refused", revision=head.revision, refusals=plan.refusals)
            revision = head.revision
            changed: dict[str, Any] = {}
            if plan.inserts:
                await conn.execute(sa.insert(PERSONA_LINE), [_values(x) for x in plan.inserts])
                if self._index:
                    # 25 §7, in the write's own begin(): each inserted row, and
                    # each row one supersedes (its node turns `superseded`).
                    for line in plan.inserts:
                        await memory_index.project(conn, "persona_line", line.id)
                        if line.supersedes is not None:
                            await memory_index.project(conn, "persona_line", line.supersedes)
                revision += 1
                changed = {"revision": revision, "updated_at": now_ms}
            if apply_decay:
                # A decaying write is a session settling the account; a turn's
                # never decays (45 §4), so its lines stay pending.
                changed["settled_at"] = now_ms
            if changed:
                await conn.execute(
                    sa.update(PERSONA).where(PERSONA.c.key == account.key).values(**changed))
            await trans.commit()
        return PersonaWriteResult(
            status="ok", revision=revision, written=tuple(x.id for x in plan.inserts))
