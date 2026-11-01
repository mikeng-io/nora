"""In-memory `ProfileWriter`: the domain judge (`plan_write`) and chain
reduction (`chain_heads`) run for real; only persistence and the revision
counter are faked, so refusals, precedence and conflicts are production
semantics."""
from __future__ import annotations

from typing import Any

from nora.domain.profile import (
    Account,
    ProfileCitable,
    ProfileWriteResult,
    plan_write,
)
from nora.infra.sql.profile import chain_heads


class InMemoryProfileStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.lines: dict[str, list[Any]] = {}
        self.revisions: dict[str, int] = {}
        self.writes = 0
        self.fail = fail
        self.now_ms_seen: int | None = None
        self.by_session: str | None = None

    async def account_state(self, *, key: str) -> tuple[int, int | None]:
        return self.revisions.get(key, 0), None

    async def account_heads(self, *, key: str) -> tuple[Any, ...]:
        return chain_heads(self.lines.get(key, []))[0]

    async def open_disputes(self, *, key: str, limit: int) -> tuple[Any, ...]:
        return ()

    async def profile_write(
        self, *, account: Account, ops: Any, citable: ProfileCitable,
        now_ms: int, written_by_session: str, apply_decay: bool,
        expected_revision: int | None = None, receipt: Any = None,
    ) -> ProfileWriteResult:
        self.writes += 1
        if self.fail:
            raise RuntimeError("profile store down")
        revision = self.revisions.get(account.key, 0)
        self.now_ms_seen, self.by_session = now_ms, written_by_session
        if expected_revision is not None and revision != expected_revision:
            return ProfileWriteResult(status="conflict", revision=revision)
        heads, aliases = chain_heads(self.lines.get(account.key, []))
        plan = plan_write(
            account=account, heads=heads, ops=ops, citable=citable,
            now_ms=now_ms, written_by_session=written_by_session,
            screen=lambda _: None, apply_decay=apply_decay, aliases=aliases)
        if plan.refused:
            return ProfileWriteResult(
                status="refused", revision=revision, refusals=plan.refusals)
        self.lines.setdefault(account.key, []).extend(plan.inserts)
        self.revisions[account.key] = revision + 1
        if receipt is not None:
            await receipt(None, {
                "status": "ok", "revision": self.revisions[account.key],
                "written": [x.id for x in plan.inserts]})
        return ProfileWriteResult(
            status="ok", revision=self.revisions[account.key],
            written=tuple(x.id for x in plan.inserts))
