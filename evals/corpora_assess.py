"""The Assess adaptor's labelled corpora (DEC-240): event derivation and note
derivation, each at least 40 labelled cases including negatives (a joke, a
hypothetical, another unit's plan, a plan already cancelled/settled).

**Fully synthetic, unlike `evals/scenarios.py`'s "every case is a real
moment".** The tag-path corpora that module loads name a real room at a real
instant (`snapshot:`) because they measure what Nora does with what a real room
really held. Assess's derivation corpora have no real moment to anchor to, and
must never carry a real member name or a real room message.
`evals/corpora/assess_event.yaml` and `assess_note.yaml` use placeholder
speakers (`P1`..`P5`) and invented places and topics throughout.

**Loading and collection only, on purpose.** `load_assess_corpus` parses and
validates the YAML; nothing here calls a model or drives a sandbox. Running
these cases against a live model (precision/recall, false-derivation and
unasked-act measurement) is `nora-eval`'s job, and is deliberately NOT wired
into `python -m evals`'s `CORPUS_ENTRY` (`evals/scenarios.py`): that dict is
model calls, and gating `make gate` on a live run would break the boundary
`AGENTS.md`'s `nora-eval` skill draws ("never against live models").
`tests/unit/test_assess_corpora.py` is what "the runner collects it" means: it
loads both corpora and asserts the shape, counts and negative coverage.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

__all__ = [
    "GATES",
    "NEGATIVE_KINDS",
    "AssessCase",
    "AssessCorpusCensus",
    "census",
    "load_assess_corpus",
]

_CORPORA = Path(__file__).parent / "corpora"

#: Every key a case may carry. A corpus stating something this loader ignores
#: is a corpus asserting more than is checked (`evals/scenarios.py`'s own
#: `CASE_KEYS` rule, carried here).
_CASE_KEYS = frozenset({"name", "series", "category", "negative_kind", "input", "expect"})
_INPUT_ROW_KEYS = frozenset({"t", "speaker", "text"})

#: The five event/note negative kinds, then the memo corpus's own (DEC-306,
#: `assess_memo.yaml`): a memo already withdrawn or superseded, and the three
#: things that look settled but are not a memo: about the speaker alone
#: (`remember`), still open (a note), dated and attended (an event).
NEGATIVE_KINDS: tuple[str, ...] = (
    "joke", "hypothetical", "other_unit", "already_cancelled", "already_settled",
    "already_withdrawn", "about_the_speaker", "still_open", "dated",
)

#: Pass thresholds for a live run, which this module does not perform (see the
#: module docstring). Read by `nora-eval`.
GATES: dict[str, float] = {
    "precision_min": 0.90,
    "recall_min": 0.80,
    "false_derivation_rate_max": 0.05,
    "unasked_act_rate_max": 0.0,
}


@dataclass(frozen=True, slots=True)
class AssessCase:
    name: str
    category: Literal["positive", "negative"]
    input: tuple[dict[str, str], ...]
    expect: dict[str, Any]
    series: str | None = None
    negative_kind: str | None = None


@dataclass(frozen=True, slots=True)
class AssessCorpusCensus:
    total: int
    positive: int
    negative: int
    negative_kinds: frozenset[str]

    def __str__(self) -> str:
        return (
            f"{self.total} total, {self.positive} positive, {self.negative} negative "
            f"({', '.join(sorted(self.negative_kinds))})"
        )


def _validate_row(name: str, i: int, row: dict[str, Any]) -> None:
    stale = set(row) - _INPUT_ROW_KEYS
    if stale:
        raise ValueError(f"{name}: input[{i}] has unknown keys {sorted(stale)}")
    missing = _INPUT_ROW_KEYS - set(row)
    if missing:
        raise ValueError(f"{name}: input[{i}] is missing {sorted(missing)}")


def _case_from_row(row: dict[str, Any]) -> AssessCase:
    name = row.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"a case with no (or empty) name: {row!r}")
    stale = set(row) - _CASE_KEYS
    if stale:
        raise ValueError(f"{name}: unknown case keys {sorted(stale)}")
    category = row.get("category")
    if category not in ("positive", "negative"):
        raise ValueError(f"{name}: category must be 'positive' or 'negative', got {category!r}")
    negative_kind = row.get("negative_kind")
    if category == "negative":
        if negative_kind not in NEGATIVE_KINDS:
            raise ValueError(
                f"{name}: negative case needs negative_kind in {NEGATIVE_KINDS}, "
                f"got {negative_kind!r}"
            )
    elif negative_kind is not None:
        raise ValueError(f"{name}: negative_kind is only for category=negative")
    input_rows = row.get("input")
    if not isinstance(input_rows, list) or not input_rows:
        raise ValueError(f"{name}: input must be a non-empty list")
    for i, r in enumerate(input_rows):
        _validate_row(name, i, r)
    expect = row.get("expect")
    if not isinstance(expect, dict) or "outcome" not in expect:
        raise ValueError(f"{name}: expect must be a dict naming 'outcome'")
    if category == "negative" and expect.get("outcome") != "none":
        raise ValueError(f"{name}: a negative case must expect outcome: none")
    return AssessCase(
        name=name, category=category, input=tuple(input_rows), expect=expect,
        series=row.get("series"), negative_kind=negative_kind,
    )


def load_assess_corpus(name: str) -> tuple[AssessCase, ...]:
    """`evals/corpora/<name>.yaml` as `AssessCase`s, fully validated. Raises
    on anything the schema does not expect — a corpus asserting more than
    this loader checks is refused, `evals/scenarios.py`'s own rule."""
    path = _CORPORA / f"{name}.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(doc, dict) or set(doc) != {"cases"}:
        raise ValueError(f"{name}: corpus-level keys must be exactly {{'cases'}}")
    rows = doc["cases"]
    if not isinstance(rows, list):
        raise ValueError(f"{name}: 'cases' must be a list")
    cases = tuple(_case_from_row(r) for r in rows)
    names = [c.name for c in cases]
    if len(names) != len(set(names)):
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise ValueError(f"{name}: duplicate case names {dupes}")
    return cases


def census(cases: tuple[AssessCase, ...]) -> AssessCorpusCensus:
    positive = sum(1 for c in cases if c.category == "positive")
    negative = sum(1 for c in cases if c.category == "negative")
    kinds = frozenset(c.negative_kind for c in cases if c.negative_kind)
    return AssessCorpusCensus(
        total=len(cases), positive=positive, negative=negative, negative_kinds=kinds,
    )
