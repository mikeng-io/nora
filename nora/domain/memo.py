"""Types and bounds for the legacy memo evidence kept in SQL."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from nora.domain.lineage import Lineage
from nora.domain.note_body import (
    NoteBody,
)

__all__ = [
    "MEMO_BODY_MAX_CHARS",
    "MEMO_MODES",
    "MEMO_SECTIONS",
    "MEMO_TITLE_MAX_CHARS",
    "Memo",
    "MemoMode",
    "MemoRevision",
    "MemoState",
    "MemoStore",
    "MemoWithdrawnError",
    "StaleMemoError",
    "citations_of",
]

#: The headings, in their only order (12-schemas §3).
MEMO_SECTIONS: tuple[str, ...] = ("Kept", "Conditions", "Superseded")

#: A title is rendered on every turn of every room of the unit (`<memos>`), so
#: it is held to a line; the note title's cap with room for a booking name.
MEMO_TITLE_MAX_CHARS = 80

#: A body's ceiling as written. Nothing measures a memo yet (none exists); a
#: memo is a handful of settled lines, and this is a refusal threshold, never a
#: reservation. The database's own ceiling sits above it (`schema.MEMO_*`).
MEMO_BODY_MAX_CHARS = 2000

MemoMode = Literal["told", "deduced", "abduced"]
MemoState = Literal["kept", "withdrawn"]
MEMO_MODES: frozenset[str] = frozenset({"told", "deduced", "abduced"})


class MemoWithdrawnError(Exception):
    """The head is withdrawn: a withdrawal is final, so the store refuses any
    revision on top of it, a second withdrawal included."""


class StaleMemoError(Exception):
    """The head the writer judged against is no longer the head: another
    revision landed in between (compare-and-set on `base_revision_id`)."""


@dataclass(frozen=True, slots=True)
class MemoRevision:
    id: str
    memo_id: str
    seq: int
    title: str
    body: str
    citations: tuple[str, ...]
    mode: str
    state: str
    written_at: int
    established_by: str
    model: str | None = None
    lineage: Lineage = field(default_factory=Lineage)


@dataclass(frozen=True, slots=True)
class Memo:
    """A memo identity and its head revision. `platform`/`conversation_id` are
    the room it was first kept in, provenance only: a memo is the UNIT's and
    renders where its cited disclosure requirements permit."""

    id: str
    unit: str
    platform: str
    conversation_id: str
    created_at: int
    head: MemoRevision

    @property
    def title(self) -> str:
        return self.head.title

    @property
    def is_kept(self) -> bool:
        return self.head.state == "kept"


class MemoStore(Protocol):
    """The memo tier's store: `nora.infra.sql.memo.SqlMemoStore` in production,
    `nora.testing.memo_store.InMemoryMemoStore` in tests, both held to
    `tests/contract/test_memo_store_contract.py`. Every read is scoped by unit:
    another unit's memo is not found."""

    async def kept(self, *, unit: str) -> tuple[Memo, ...]:
        """The unit's kept memos with their heads, oldest first."""
        ...

    async def get(self, *, unit: str, memo_id: str) -> Memo | None:
        """One memo of `unit` (kept or withdrawn) with its head, or None."""
        ...

    async def revisions(self, *, unit: str, memo_id: str) -> tuple[MemoRevision, ...]:
        """Every revision of one memo of `unit`, newest first."""
        ...

    async def create(
        self, *, unit: str, platform: str, conversation_id: str, title: str, body: str,
        citations: Sequence[str], mode: str, established_by: str, model: str | None,
        now_ms: int, receipt: Any = None,
    ) -> Memo:
        """A new kept memo and its first revision, in one transaction."""
        ...

    async def append(
        self, *, unit: str, memo_id: str, base_revision_id: str, title: str, body: str,
        citations: Sequence[str], mode: str, state: str, established_by: str,
        model: str | None, now_ms: int, receipt: Any = None,
    ) -> MemoRevision:
        """The next revision, compare-and-set on the head: `LookupError` when
        `unit` has no such memo, `StaleMemoError` when its head is not
        `base_revision_id`, `MemoWithdrawnError` when its head is withdrawn."""
        ...


def citations_of(body: NoteBody) -> tuple[str, ...]:
    """Every id and Rollup key a body cites, as the revision's `citations`
    column holds them: ids as digits, keys as `rollup(...)`, each once, in the
    body's order."""
    seen: dict[str, None] = {}
    for section in body.sections:
        for line in section.lines:
            for i in line.ids:
                seen.setdefault(str(i), None)
            for key in line.rollup_keys:
                seen.setdefault(f"rollup({key})", None)
            for handle in line.turn_handles:
                seen.setdefault(f"turn({handle})", None)
    return tuple(seen)
