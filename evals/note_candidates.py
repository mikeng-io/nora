"""The note-candidates scenario suite — cases, structural grading, summary
(design/dreamer-agent-loop/37 §7, issue #118).

Why a suite and not a probe. The first live fire of `note-candidates` on a
real, dated, two-open-corners arrangement returned `{"candidates": []}`; a
five-variant probe of the same single message at temperature 0.7 and n=3
showed the PROMPT was the bottleneck and nothing else, and showed nothing
about which variant is right — one message and three samples cannot. This
suite is the instrument that can: ten batches (five that must produce a note,
five that must not), each run N times through the REAL runtime and adaptor
against the live model, graded on what the notebook holds afterwards.

**Structural grading, no judge.** The adaptor's output is a notebook state,
and a notebook state is a fact: a new note exists or it does not; a head
moved or it did not. Grading that with a second model would be a stochastic
instrument measuring a stochastic one. The one thing a structural grade
cannot see — is the BODY any good — is exactly what 37 §7 says to read by
eye once a note exists at all.

**Fail-closed.** `unavailable` is ERROR for every case: a timeout that looks
like a correct `none` is R-27's forbidden substitution, and the summary
reports errors apart from failures so an outage during a run cannot read as
a quiet model.

This module holds the parts that need no model: the case schema and its
validation, the grade over a finished run, and the per-variant summary.
`evals.run._run_note_candidates_live` drives the model.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml

Expect = Literal["create", "update", "none"]
EXPECTS: tuple[str, ...] = ("create", "update", "none")

Verdict = Literal["PASS", "FAIL", "ERROR"]


@dataclass(frozen=True)
class BatchMessage:
    id: int
    ts_ms: int
    person: str
    name: str
    body: str | None
    media: bool = False


@dataclass(frozen=True)
class NotebookEntry:
    title: str
    description: str
    anchor_date: int
    body: str


@dataclass(frozen=True)
class NoteCandidatesCase:
    name: str
    expect: str
    workspace: str
    batch: tuple[BatchMessage, ...]
    notebook: tuple[NotebookEntry, ...] = ()
    anchor_date: int | None = None
    must_cite: tuple[int, ...] = ()
    updates: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> NoteCandidatesCase:
        batch = tuple(
            BatchMessage(
                id=int(m["id"]), ts_ms=_iso_to_ms(m["ts"]), person=str(m["person"]),
                name=str(m["name"]), body=m.get("body"), media=bool(m.get("media", False)),
            )
            for m in raw["batch"]
        )
        notebook = tuple(
            NotebookEntry(
                title=str(n["title"]), description=str(n["description"]),
                anchor_date=int(n["anchor_date"]), body=str(n["body"]),
            )
            for n in raw.get("notebook", ())
        )
        case = cls(
            name=str(raw["name"]), expect=str(raw["expect"]),
            workspace=str(raw["workspace"]), batch=batch, notebook=notebook,
            anchor_date=(int(raw["anchor_date"]) if raw.get("anchor_date") is not None else None),
            must_cite=tuple(int(i) for i in raw.get("must_cite", ())),
            updates=raw.get("updates"),
        )
        case.validate()
        return case

    def validate(self) -> None:
        """Refuse a case that cannot be graded — at LOAD time, so a typo in the
        corpus fails the deterministic tier rather than burning a live run."""
        if self.expect not in EXPECTS:
            raise ValueError(f"{self.name}: expect must be one of {EXPECTS}, got {self.expect!r}")
        if not self.batch:
            raise ValueError(f"{self.name}: a case needs at least one message")
        ids = [m.id for m in self.batch]
        if len(set(ids)) != len(ids):
            raise ValueError(
                f"{self.name}: message ids must be distinct within the batch"
            )
        if any(c not in ids for c in self.must_cite):
            raise ValueError(f"{self.name}: must_cite names an id outside the batch")
        titles = [n.title for n in self.notebook]
        if len(set(titles)) != len(titles):
            raise ValueError(f"{self.name}: notebook titles must be distinct")
        if self.expect == "update" and (self.updates is None or self.updates not in titles):
            raise ValueError(
                f"{self.name}: an update case must name a notebook title in `updates`"
            )
        if self.expect != "update" and self.updates is not None:
            raise ValueError(f"{self.name}: `updates` only makes sense with expect: update")
        if self.expect == "none" and (self.anchor_date is not None or self.must_cite):
            raise ValueError(f"{self.name}: a `none` case has no anchor_date or must_cite")


def _iso_to_ms(value: str) -> int:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def load_note_candidates_cases(path: Path | str) -> list[NoteCandidatesCase]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    cases = [NoteCandidatesCase.from_dict(item) for item in raw]
    names = [c.name for c in cases]
    if len(set(names)) != len(names):
        raise ValueError("case names must be distinct")
    return cases


# ---------------------------------------------------------------------------
# what one run left behind, and the grade over it
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StagedCandidate:
    title: str
    anchor_date: int
    message_ids: tuple[int, ...]
    status: str


@dataclass(frozen=True)
class NoteSnapshot:
    id: str
    title: str
    anchor_date: int | None
    head_revision_id: str


@dataclass(frozen=True)
class RunResult:
    """The runtime's outcome plus the notebook before and after — everything
    a grade reads, and nothing a grade would have to re-derive."""

    status: str
    stop_reason: str | None
    instruction_sha256: str
    before: tuple[NoteSnapshot, ...]
    after: tuple[NoteSnapshot, ...]
    staged: tuple[StagedCandidate, ...]
    raw_items: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class Grade:
    verdict: Verdict
    reason: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict == "PASS"


def grade(case: NoteCandidatesCase, result: RunResult) -> Grade:
    if result.status == "unavailable":
        return Grade("ERROR", f"runtime unavailable (stop_reason={result.stop_reason})")
    before = {n.id: n for n in result.before}
    after = {n.id: n for n in result.after}
    new_notes = [n for i, n in after.items() if i not in before]
    moved = [
        n for i, n in after.items()
        if i in before and n.head_revision_id != before[i].head_revision_id
    ]

    if case.expect == "none":
        if new_notes:
            return Grade("FAIL", f"a note was created: {[n.title for n in new_notes]}")
        if moved:
            return Grade("FAIL", f"a note was revised: {[n.title for n in moved]}")
        return Grade("PASS")

    if case.expect == "create":
        if not new_notes:
            return Grade("FAIL", "no note was created")
        if len(new_notes) > 1:
            return Grade("FAIL", f"{len(new_notes)} notes created, expected one")
        note = new_notes[0]
        if case.anchor_date is not None and note.anchor_date != case.anchor_date:
            return Grade("FAIL", f"anchor_date {note.anchor_date} != expected {case.anchor_date}")
        if case.must_cite:
            created = [
                s for s in result.staged
                if s.status == "created" and s.anchor_date == note.anchor_date
            ]
            cited = set().union(*(set(s.message_ids) for s in created)) if created else set()
            missing = [c for c in case.must_cite if c not in cited]
            if missing:
                return Grade("FAIL", f"candidate did not cite {missing}")
        return Grade("PASS")

    # update
    if new_notes:
        return Grade(
            "FAIL",
            f"a second note was created instead of an update: "
            f"{[n.title for n in new_notes]}",
        )
    target = next((n for n in before.values() if n.title == case.updates), None)
    if target is None:
        return Grade("ERROR", f"the notebook has no note titled {case.updates!r}")
    if target.id not in {n.id for n in moved}:
        return Grade("FAIL", f"{case.updates!r} was not revised")
    return Grade("PASS")


# ---------------------------------------------------------------------------
# summary over samples
# ---------------------------------------------------------------------------


@dataclass
class CaseTally:
    name: str
    expect: str
    verdicts: list[Verdict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def passes(self) -> int:
        return sum(1 for v in self.verdicts if v == "PASS")

    @property
    def errors(self) -> int:
        return sum(1 for v in self.verdicts if v == "ERROR")

    @property
    def majority_pass(self) -> bool:
        graded = [v for v in self.verdicts if v != "ERROR"]
        return bool(graded) and self.passes * 2 > len(graded)


def summarise(tallies: Sequence[CaseTally]) -> dict[str, Any]:
    """Precision/recall over SAMPLES of the positive cases (create/update):
    recall = positives that produced the expected write; precision = of every
    sample that wrote anything, how many were supposed to. Errors are
    excluded from both and reported on their own — an outage is not a miss."""
    tp = fp = fn = 0
    for t in tallies:
        for v in t.verdicts:
            if v == "ERROR":
                continue
            wrote = (t.expect != "none" and v == "PASS") or (
                t.expect == "none" and v == "FAIL"
            )
            if t.expect != "none":
                if v == "PASS":
                    tp += 1
                else:
                    fn += 1
            elif wrote:
                fp += 1
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    return {
        "cases": len(tallies),
        "majority_pass": sum(1 for t in tallies if t.majority_pass),
        "samples": sum(len(t.verdicts) for t in tallies),
        "errors": sum(t.errors for t in tallies),
        "tp": tp, "fp": fp, "fn": fn,
        "precision": precision, "recall": recall,
        "verdicts": dict(Counter(v for t in tallies for v in t.verdicts)),
    }
