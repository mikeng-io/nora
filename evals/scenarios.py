"""The corpus port — `evals/corpora/*.yaml` as `strands_evals.Case` objects.

A corpus is a set of cases and nothing else (`01-boundaries.md` §2). Every
case names a REAL room at a REAL instant (`snapshot:`), and everything she
knows when it starts is what that room held then — read from the deployment's
clone and written into the case's own sandbox by `evals.backbone._drive`.
There is no other way to state what she knows: no `knows:`, no `config:`, no
`policy:`, no `room:`, no `now:`. Each of those was a case compensating by
hand for a subject that was not the deployment's (`11-the-sandbox-rebuild.md`
§1.2), and under one path they have nothing left to say.

THREE STATES, and the middle one is the point (`11-the-sandbox-rebuild.md`
§7.1):

- **active** — anchored; runs.
- **unanchored** — no `snapshot:` and not parked; is loaded, is driven, and
  ERRORS. Work to do, visible in the report rather than skipped out of it.
- **parked** — `parked:` with a reason and a return condition; never run,
  always counted. `load_corpus` keeps these out of the active set and
  `census()` reports them beside it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from strands_evals import Case, Experiment

from evals.backbone import SNAPSHOT_KEYS, MeetsGrader, Spec, nora_task

_CORPORA = Path(__file__).parent / "corpora"

#: Every key a case may carry. Anything else is REFUSED, because a corpus
#: stating something the runner ignores is a corpus asserting more than is
#: measured — which is how `media: true` was once dropped from five rows with
#: nothing reporting it.
CASE_KEYS = frozenset({
    "name", "entry", "expect", "input", "expected_output", "snapshot",
    "person", "mention", "sample", "safety", "category", "metadata", "parked",
})
PARKED_KEYS = frozenset({"reason", "why", "returns_when"})
PARKING_REASONS = frozenset({"P-A"})


@dataclass(frozen=True, slots=True)
class Census:
    """How many of a corpus's cases are in each state. Reported on every run,
    so a shrinking active set is seen rather than discovered later."""

    total: int
    active: int
    parked: int
    unanchored: int

    def __str__(self) -> str:
        return (
            f"{self.total} total, {self.active} active, {self.parked} parked, "
            f"{self.unanchored} unanchored"
        )


def _rows(name: str) -> list[dict[str, Any]]:
    doc = yaml.safe_load((_CORPORA / f"{name}.yaml").read_text(encoding="utf-8")) or []
    if isinstance(doc, dict):
        stale = sorted(set(doc) - {"cases"})
        if stale:
            raise ValueError(
                f"{name}: corpus-level {stale} is not a thing a corpus states; "
                "the subject is the deployment's, and a corpus cannot "
                "configure it (11-the-sandbox-rebuild.md §1.2)"
            )
        return list(doc.get("cases") or [])
    return list(doc)


def _check(name: str, row: dict[str, Any]) -> None:
    for key in ("name", "entry", "expect"):
        if key not in row:
            raise ValueError(f"{name}: case is missing {key!r}: {row!r}")
    unknown = sorted(set(row) - CASE_KEYS)
    if unknown:
        raise ValueError(
            f"{name}: case {row['name']!r} states {unknown}; a case may state "
            f"only {sorted(CASE_KEYS)}"
        )
    snapshot = row.get("snapshot") or {}
    if snapshot:
        missing = sorted(SNAPSHOT_KEYS - set(snapshot))
        extra = sorted(set(snapshot) - SNAPSHOT_KEYS)
        if missing or extra:
            raise ValueError(
                f"{name}: case {row['name']!r}'s `snapshot:` is missing "
                f"{missing} and states {extra}; it names exactly "
                f"{sorted(SNAPSHOT_KEYS)} — the room's culture key and unit "
                "come from the deployment's policy entry, and its window from "
                "production's config (R7)"
            )
    parked = row.get("parked") or {}
    if parked:
        if snapshot:
            raise ValueError(
                f"{name}: case {row['name']!r} is parked AND anchored; a "
                "parked case is one with no moment to anchor to"
            )
        if set(parked) != PARKED_KEYS or parked.get("reason") not in PARKING_REASONS:
            raise ValueError(
                f"{name}: case {row['name']!r}'s `parked:` must state exactly "
                f"{sorted(PARKED_KEYS)} with reason in {sorted(PARKING_REASONS)} "
                "— P-B (a seam this rebuild owes) is NOT a parking reason; "
                "such a case stays unanchored and ERRORs (§7.1)"
            )


def _case(row: dict[str, Any]) -> Case:
    return Case(
        name=str(row["name"]),
        input=str(row.get("input", "")),
        expected_output=str(row.get("expected_output", "")),
        metadata={
            **(row.get("metadata") or {}),
            "expect": row["expect"],
            "snapshot": dict(row.get("snapshot") or {}),
            "parked": dict(row.get("parked") or {}),
            "person": str(row.get("person") or "miles_novak"),
            "mention": bool(row.get("mention", False)),
            "category": row.get("category", ""),
        },
    )


def load_all(name: str) -> list[Case]:
    """Every case in `evals/corpora/<name>.yaml`, parked ones included."""
    rows = _rows(name)
    for row in rows:
        _check(name, row)
    return [_case(row) for row in rows]


def load_corpus(name: str) -> list[Case]:
    """The ACTIVE set: every case that is not parked. Unanchored cases are
    included — they ERROR when driven, which is the point."""
    return [c for c in load_all(name) if not c.metadata.get("parked")]


def census(name: str) -> Census:
    cases = load_all(name)
    parked = sum(1 for c in cases if c.metadata.get("parked"))
    unanchored = sum(
        1 for c in cases if not c.metadata.get("parked") and not c.metadata.get("snapshot")
    )
    return Census(
        total=len(cases), active=len(cases) - parked, parked=parked, unanchored=unanchored,
    )


#: Which entry point drives a corpus. ALL cases in a given corpus file share
#: one entry point — a corpus IS a suite about one entry point.
CORPUS_ENTRY: dict[str, str] = {
    "no_trigger": "tag",
    "near_miss": "tag",
    "trigger": "tag",
    "injections": "tag",
    "transcript_injection": "tag",
    "being_contradicted": "tag",
    "identity_grounding": "tag",
    "source_scope": "tag",
    "participation": "participation",
    "relevance_gate": "gate",
    "note_candidates": "dreamer",
    "proactive_decide": "dreamer",
    "proactive_act": "proactive_act",
}

#: S3 (grounding)/S9 (injection) — "did she reply" is structural; "was the
#: reply right" needs a judge (`10-the-scenario-map.md` §3). Every case in
#: these five corpora expects `reply`, so judging unconditionally (once a
#: judge is configured) adds no second selection rule.
JUDGED_CORPORA = frozenset({
    "being_contradicted", "identity_grounding", "source_scope",
    "injections", "transcript_injection",
})


def experiment_for(
    name: str, *, source_url: str, judge: Any = None, sample: int = 1,
) -> tuple[Experiment, Any]:
    """A ready-to-run `(Experiment, task)` pair for one corpus.

    `MeetsGrader` for every corpus, uniformly — it is a strict superset of
    `AbsenceGrader`'s vocabulary, so there is no per-corpus grader selection
    to get wrong. `judge` is threaded through only for `JUDGED_CORPORA`.

    `sample > 1` draws each case `sample` times via `evals.backbone.sampled`
    — one draw of a stochastic process is not a reading of it. Read the
    result with `evals.backbone.sample_read`, not `read`, when `sample > 1`.
    """
    from evals.backbone import sampled

    cases = load_corpus(name)
    if sample > 1:
        cases = [draw for base in cases for draw in sampled(base, sample)]
    spec = Spec(
        source_url=source_url,
        entry=CORPUS_ENTRY[name],
        judge=judge if name in JUDGED_CORPORA else None,
    )
    experiment = Experiment(cases=cases, evaluators=[MeetsGrader()])
    return experiment, nora_task(spec)
