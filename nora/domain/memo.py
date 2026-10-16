"""A memo: something the unit SETTLED and keeps (Register · memo, DEC-306).

The three Register kinds differ by whether the thing is settled: a note is not
(it closes on its closing condition), a memo is (it never closes; it is
revised or withdrawn), an event is dated and attended. «The wifi password is
X», «the deposit is $500, split four ways», a landlord's number, a booking
reference. A settled fact about the speaker alone is Memory's (`remember`),
never a memo.

**One memo is one chain of revisions, never overwritten and never deleted**
(the lesson's shape, `nora.domain.lessons`): `memo` is an identity in a unit
with the room it was first kept in, and every change (a revision, a
withdrawal) appends a `memo_revision`; the head is the highest `seq`. A
withdrawn memo is a revision whose `state` is `withdrawn`, so nothing about
what it said is lost.

**The body** is the memo schema, held by `parse_memo_body` with the
note body's line grammar (`nora.domain.note_body`): `## Kept` (required, one
settled claim per line), `## Conditions` (optional: when it holds, the
exceptions), every line ending in the ids it rests on, and `## Superseded`,
which code renders from the chain and a model never writes. Unlike a note, a
memo line MAY cite one of Nora's own messages: what she found or said can be
kept when the room asks her to keep it.

**What a `Kept` line may hold** (DEC-230, DEC-306): anything said,
verbatim, a password, a phone number or a booking reference included: it was
said in the group and is already shared with it. So the Cognition session's
identifier screen reads a memo's title and `## Conditions` and never its `##
Kept` lines (`nora.app.cognition_session`). What leaves in a reply is still
R-18's (`nora.agent.egress`): a key-shaped value read from a memo is refused at
the reply like any other (DEC-226).

Framework-free, per `nora/domain/`'s rule: stdlib and dataclasses only.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from nora.domain.note_body import (
    NoteBody,
    NoteBodyError,
    NoteSection,
    Shown,
    cited_line,
    split_sections,
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
    "parse_memo_body",
    "screened_parts",
    "title_carries_value",
]

#: The headings, in their only order (12-schemas §3).
MEMO_SECTIONS: tuple[str, ...] = ("Kept", "Conditions", "Superseded")

#: Written by code from the revision chain, never by a model.
MEMO_CODE_WRITTEN = "Superseded"

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


@dataclass(frozen=True, slots=True)
class Memo:
    """A memo identity and its head revision. `platform`/`conversation_id` are
    the room it was first kept in, provenance only: a memo is the UNIT's and
    renders in every room of it."""

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
        now_ms: int,
    ) -> Memo:
        """A new kept memo and its first revision, in one transaction."""
        ...

    async def append(
        self, *, unit: str, memo_id: str, base_revision_id: str, title: str, body: str,
        citations: Sequence[str], mode: str, state: str, established_by: str,
        model: str | None, now_ms: int,
    ) -> MemoRevision:
        """The next revision, compare-and-set on the head: `LookupError` when
        `unit` has no such memo, `StaleMemoError` when its head is not
        `base_revision_id`, `MemoWithdrawnError` when its head is withdrawn."""
        ...


def parse_memo_body(text: str, *, shown: Shown) -> NoteBody:
    """The body, held to the memo schema, or `NoteBodyError` naming the rule it
    broke: an unknown, misordered or model-written `## Superseded` heading
    (`code_written`), a missing or empty `## Kept`, a line that is not one `- `
    item ending in the ids it rests on, an id this write was not shown. A line
    may cite one of Nora's own messages (unlike a note's)."""
    split = split_sections(text, schema=MEMO_SECTIONS)
    present = dict(split)
    if MEMO_CODE_WRITTEN in present:
        raise NoteBodyError(
            "code_written",
            f"## {MEMO_CODE_WRITTEN} is written by code from the memo's earlier "
            "revisions; leave it out",
        )
    if not present.get("Kept"):
        raise NoteBodyError(
            "missing_section", "## Kept holds at least one line: the settled thing",
        )
    sections = tuple(
        NoteSection(heading=h, lines=tuple(
            cited_line(h, item, shown, may_cite_nora=True) for item in items
        ))
        for h, items in split
    )
    normalised = "\n".join(
        "\n".join((f"## {s.heading}", *(f"- {ln.text}" for ln in s.lines))) for s in sections
    )
    return NoteBody(text=normalised, sections=sections)


def screened_parts(title: str, body: NoteBody) -> dict[str, object]:
    """What a memo write's identifier screen reads: the title and the `##
    Conditions` lines, and NEVER a `## Kept` line (DEC-230: a Kept line
    holds what the room said, a password included). The ONE place the
    exemption is drawn, so it cannot widen by a second spelling."""
    conditions = body.section("Conditions")
    return {
        "title": title,
        "conditions": [ln.text for ln in conditions.lines] if conditions else [],
    }


_TOKEN = re.compile(r"[A-Za-z0-9]+")
_CITATION = re.compile(r"\s*\[[^\[\]]*\]$")


def title_carries_value(title: str, body: NoteBody) -> str | None:
    """The token of `title` that is also a VALUE a `## Kept` line holds, or
    None. A title renders in `<memos>` on every turn of every room, so it names
    the thing («wifi password») and never carries what is kept («wifi:
    hunter2pass»). A value is told from a word by shape: an alphanumeric run
    of 4 or more characters holding a digit (a password, a code, a booking
    reference, a number), compared case-folded against the Kept lines with
    their citation brackets removed. Ordinary words shared by both («wifi»,
    «deposit») are never refused; a value of letters only is not caught, and
    the docstring and the skill say the rule instead (DEC-306)."""
    kept = body.section("Kept")
    if kept is None:
        return None
    values = {
        t.casefold() for ln in kept.lines for t in _TOKEN.findall(_CITATION.sub("", ln.text))
        if len(t) >= 4 and any(c.isdigit() for c in t)
    }
    return next((t for t in _TOKEN.findall(title) if t.casefold() in values), None)


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
    return tuple(seen)
