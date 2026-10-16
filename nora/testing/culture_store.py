"""`InMemoryCultureStore` — the domain-only double for
`nora.infra.sql.culture.SqlCultureStore`, bound to it by
`tests/contract/test_culture_store_contract.py` so the two cannot drift.

`CultureReader` lives in `nora.domain.ports` (DEC-207): a port never gains a
second implementation, including a test fake, without a suite both pass.

**The chain is parent links, not insertion order** — `SqlCultureStore.chain`'s
own rule (DEC-183) — so `append` records the head it replaced as the new
revision's parent, and `chain` walks those links rather than reversing a list.
A fake that returned "the appended rows, newest first" would agree with
Postgres on every test that appends to one key and disagree the moment two
keys interleave.

It refuses what the database's CHECKs refuse (a blank key or body, a body over
`CULTURE_BODY_CEILING_CHARS`, a blank `established_by`), for
`InMemoryStandingStore`'s reason: a double more permissive than Postgres turns
a missing guarantee into a passing gate.

`reads` records every key `head` was asked for — the one thing a caller-side
test needs to assert about keying (DEC-181: a `primary_id`, never the arriving
thread's id).
"""

from __future__ import annotations

from uuid import uuid4

from nora.domain.culture import CultureRevision

#: `nora.infra.sql.schema.CULTURE_BODY_CEILING_CHARS`, restated because
#: `nora.testing` may not import `nora.infra` (the fake must stand without the
#: adapter it doubles). The contract suite pins the two equal.
CULTURE_BODY_CEILING_CHARS = 32_000


class InMemoryCultureStore:
    def __init__(self) -> None:
        self._heads: dict[str, str] = {}
        self._revisions: dict[str, CultureRevision] = {}
        self.reads: list[str] = []

    async def head(self, *, key: str) -> CultureRevision | None:
        self.reads.append(key)
        head_id = self._heads.get(key)
        return None if head_id is None else self._revisions[head_id]

    async def append(
        self,
        *,
        key: str,
        body: str,
        written_at: int,
        established_by: str,
        instruction_sha256: str | None = None,
        model: str | None = None,
        citations: tuple[str, ...] = (),
    ) -> CultureRevision:
        if not key.strip(" "):
            raise ValueError("culture_has_a_key_check")
        if not body.strip(" ") or len(body) > CULTURE_BODY_CEILING_CHARS:
            raise ValueError(
                f"culture_revision body check: {len(body)} characters")
        if not established_by.strip(" "):
            raise ValueError("culture_revision_established_by_check")
        revision = CultureRevision(
            id=str(uuid4()), culture_key=key, body=body, written_at=written_at,
            established_by=established_by,
            parent_revision_id=self._heads.get(key),
            instruction_sha256=instruction_sha256, model=model,
            citations=tuple(citations),
        )
        self._revisions[revision.id] = revision
        self._heads[key] = revision.id
        return revision

    async def chain(
        self, *, key: str, limit: int = 10,
    ) -> tuple[CultureRevision, ...]:
        out: list[CultureRevision] = []
        head_id = self._heads.get(key)
        current = None if head_id is None else self._revisions[head_id]
        while current is not None and len(out) < limit:
            out.append(current)
            parent = current.parent_revision_id
            current = None if parent is None else self._revisions.get(parent)
        return tuple(out)
