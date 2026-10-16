"""The bake-off's decision rule, stated once so a verdict can be re-derived from the result
files without trusting whoever ran it.

`decide(r)` takes `r[arm][slice] -> Score`, the shape `scores_from_results`
reads out of `results/*.json`, arm names as the harness writes them.

1. **ERROR first.** Any ERROR on a journey slice in any arm, a journey slice
   with no result, a missing incumbent, or a recall that is missing or not
   finite, decides ERROR: an unmeasured slice is not a tie.
2. **The baseline is the best incumbent configuration, per slice**: the
   highest recall@5 of bge-m3 alone, trigram+bge-m3 and bm25+bge-m3
   (`INCUMBENTS`).
3. **Pass.** A candidate HOLDS cantonese (recall@5 no more than 0.05 below
   the baseline's) AND WINS text_image or names: its miss rate is at most
   half the baseline's, (1 - r) <= 0.5 * (1 - r_base). A baseline with no
   misses cannot be beaten.
4. **Several passing**: the fewest TOTAL MISSES
   summed over the journey slices, where a miss is a query whose first
   relevant item is not in the top 5 (`per_query`: a rank over 5, none in
   the top 50, or an errored query); then the lower dimension; then the arm
   name, so the pick is deterministic. A passing arm whose result carries no
   per-query ranks on a journey slice decides ERROR: its misses are unknown,
   never guessed.
5. **Media-only.** Wins text_image but does not hold cantonese: flagged for
   the operator (a second embedder doubles the index), never a REPLACE.
6. **Otherwise a tie, and a tie keeps bge-m3.**

Why halve-the-misses: an absolute recall@5 margin becomes unreachable as the
baseline nears 1.0; a relative bar on what is still missed is reachable at any
floor and still demands a large gain.

The bootstrap CIs in a result file are reported beside these numbers and
never read here.
"""

from __future__ import annotations

import enum
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "HOLD", "INCUMBENTS", "JOURNEY", "WIN_MISS_RATIO", "Decision", "Score", "Verdict",
    "decide", "rerank_ships", "scores_from_results", "slice_wins",
]

JOURNEY = ("text_image", "cantonese", "names")
#: The incumbent's configurations; the per-slice best of them is the baseline.
INCUMBENTS = ("dense:bge-m3", "fused:trigram+bge-m3", "fused:bm25+bge-m3")
HOLD = 0.05
WIN_MISS_RATIO = 0.5
#: Float slack, so a score exactly on a margin passes it.
_EPS = 1e-9
#: A reranker's p95 added latency ceiling per caller, ms.
RERANK_P95_MS = {"prefetch": 300.0, "cognition": 2000.0}
RERANK_LIFT = 0.05


class Verdict(enum.Enum):
    ERROR = "ERROR"
    KEEP_BGE_M3 = "KEEP_BGE_M3"
    REPLACE = "REPLACE"


@dataclass(frozen=True, slots=True)
class Score:
    recall5: float | None
    mrr10: float | None
    errors: int
    #: Queries whose first relevant item is not in the top 5 (`per_query`);
    #: None when the result carries no per-query ranks.
    misses: int | None = None


@dataclass(frozen=True, slots=True)
class Decision:
    verdict: Verdict
    winner: str | None = None
    wins: tuple[str, ...] = ()
    media_only: tuple[str, ...] = ()
    baseline: dict[str, float] = field(default_factory=dict)
    reason: str = ""
    #: Each passing arm's misses summed over the journey slices.
    misses: dict[str, int] = field(default_factory=dict)


def _dim(arm: str) -> int:
    """The dimension an arm SEARCHES: `dense:x@256` -> 256, and a two-stage
    arm's index, `rerank:x@256>2048/k50` -> 256 (its 2048 re-ranks only the
    top k). No dimension sorts last."""
    _, _, dim = arm.rpartition("@")
    dim = dim.partition(">")[0]
    return int(dim) if dim.isdigit() else 1 << 30


def _finite(x: float | None) -> bool:
    return x is not None and math.isfinite(x)


def _halves_the_misses(r_cand: float, r_base: float) -> bool:
    base_miss = 1 - r_base
    return base_miss > _EPS and 1 - r_cand <= WIN_MISS_RATIO * base_miss + _EPS


def _candidates(r: Mapping[str, Mapping[str, Score]]) -> list[str]:
    return sorted(a for a in r if a not in INCUMBENTS and not a.startswith("lexical:"))


def slice_wins(r: Mapping[str, Mapping[str, Score]], s: str) -> tuple[float, dict[str, bool]]:
    """One slice's baseline and whether each candidate halves its misses:
    the WIN half of the rule alone, for a slice measured before the others.
    Not a decision: `decide` still needs every journey slice. Raises on an
    ERROR, as `decide` returns one."""
    arms = (*INCUMBENTS, *_candidates(r))
    for arm in arms:
        sc = r.get(arm, {}).get(s)
        if sc is None or sc.errors or not _finite(sc.recall5):
            raise ValueError(f"ERROR: {arm} has no clean finite recall@5 on {s}")
    base = max(float(r[a][s].recall5) for a in INCUMBENTS)  # type: ignore[arg-type]
    return base, {a: _halves_the_misses(float(r[a][s].recall5), base)  # type: ignore[arg-type]
                  for a in _candidates(r)}


def decide(r: Mapping[str, Mapping[str, Score]]) -> Decision:
    missing = [a for a in INCUMBENTS if a not in r]
    if missing:
        return Decision(Verdict.ERROR, reason=f"no incumbent arm {missing}")
    candidates = _candidates(r)
    for arm in (*INCUMBENTS, *candidates):
        for s in JOURNEY:
            if s not in r[arm]:
                return Decision(Verdict.ERROR, reason=f"{arm} has no {s} result")
            if r[arm][s].errors:
                return Decision(Verdict.ERROR, reason=f"{arm} has {r[arm][s].errors} ERROR on {s}")
            if not _finite(r[arm][s].recall5):
                return Decision(Verdict.ERROR, reason=f"{arm} has no finite recall@5 on {s}")
    base = {s: max(float(r[a][s].recall5) for a in INCUMBENTS) for s in JOURNEY}  # type: ignore[arg-type]

    def recall(arm: str, s: str) -> float:
        return float(r[arm][s].recall5)  # type: ignore[arg-type]

    def wins_slice(arm: str, s: str) -> bool:
        return _halves_the_misses(recall(arm, s), base[s])

    passing: list[tuple[str, tuple[str, ...]]] = []
    media_only: list[str] = []
    for arm in candidates:
        holds = recall(arm, "cantonese") >= base["cantonese"] - HOLD - _EPS
        wins = tuple(s for s in ("text_image", "names") if wins_slice(arm, s))
        if holds and wins:
            passing.append((arm, wins))
        elif "text_image" in wins:
            media_only.append(arm)
    if not passing:
        return Decision(Verdict.KEEP_BGE_M3, media_only=tuple(media_only), baseline=base,
                        reason="no candidate passes both bars; a tie keeps the incumbent")
    if len(passing) == 1:
        arm, wins = passing[0]
        return Decision(Verdict.REPLACE, winner=arm, wins=wins, media_only=tuple(media_only),
                        baseline=base, reason=f"{arm} holds cantonese and wins {', '.join(wins)}")
    misses: dict[str, int] = {}
    for arm, _ in passing:
        per_slice = [r[arm][s].misses for s in JOURNEY]
        if any(m is None for m in per_slice):
            unknown = [s for s, m in zip(JOURNEY, per_slice, strict=True) if m is None]
            return Decision(Verdict.ERROR, media_only=tuple(media_only), baseline=base,
                            reason=f"{arm} passes but has no per_query ranks on {unknown}: "
                                   "its misses cannot be counted")
        misses[arm] = sum(m for m in per_slice if m is not None)
    arm, wins = min(passing, key=lambda p: (misses[p[0]], _dim(p[0]), p[0]))
    return Decision(Verdict.REPLACE, winner=arm, wins=wins, media_only=tuple(media_only),
                    baseline=base, misses=misses,
                    reason=f"{arm} holds cantonese, wins {', '.join(wins)}, and has the "
                           f"fewest total misses of {len(passing)} passing arms "
                           f"({misses[arm]})")


def scores_from_results(paths: Iterable[Path], *, subset: str | None = None,
                        rename: Mapping[str, str] | None = None,
                        ) -> dict[str, dict[str, Score]]:
    """`decide`'s input from result files, one slice per file, arm names as
    written. `subset` reads an arm's named subset instead of all queries
    (`in_reading_gap`, `outside_reading_gap`). ERROR is the arm's query plus
    pool errors on the slice, so either one decides ERROR.

    `rename` joins one candidate measured through two serving paths into one
    arm, explicitly: `{"wemm-fp32": "wemm"}` reads `dense:wemm-fp32@2048` as
    `dense:wemm@2048` (WeMM at fp32 on the text slices, Q4 on text_image;
    the files keep each precision in their provenance).

    A file whose arms recorded a known variant's format (`_variant_formats`)
    has them relabelled first, whatever the caller passes: arms written as
    `qwen3vl` with the default-instruction format load as `qwen3vl-defaultinst`.

    A candidate arm, after both renames, that appears twice for one slice
    raises ValueError (ERROR): the verdict would otherwise be whichever file
    was read last. The incumbents and the lexical floors, which every run
    re-measures, may recur; the last one read is kept."""
    out: dict[str, dict[str, Score]] = {}
    for path in paths:
        result = json.loads(Path(path).read_text(encoding="utf-8"))
        relabel = _variant_relabel(result)
        for written, a in result["arms"].items():
            arm = written
            for old, new in (*relabel.items(), *(rename or {}).items()):
                arm = arm.replace(old, new)
            floor = arm in INCUMBENTS or arm.startswith("lexical:")
            if not floor and result["slice"] in out.get(arm, {}):
                raise ValueError(f"ERROR: {arm} on {result['slice']} is measured twice "
                                 f"(again in {Path(path).name}); load one run per arm")
            src = a if subset is None else a[subset]
            errors = a["errors"]
            out.setdefault(arm, {})[result["slice"]] = Score(
                src.get("recall5"), src.get("mrr10"),
                int(errors.get("queries", 0)) + int(errors.get("pool", 0)),
                _misses_in(a.get("per_query"), result, subset),
            )
    return out


def _variant_formats() -> list[tuple[str, dict[str, str], str]]:
    """(label as written, the variant's recorded `text` format, the variant's
    own label) for each format the harness recorded under a reused label."""
    from evals.retrieval_bakeoff.adapters import (
        QWEN3VL_DEFAULT_INSTRUCTION_VARIANT,
        QWEN3VL_EMBEDDING_2B,
    )

    v = QWEN3VL_DEFAULT_INSTRUCTION_VARIANT
    return [(QWEN3VL_EMBEDDING_2B.label, dict(v.text), v.label)]


def _variant_relabel(result: Mapping[str, Any]) -> dict[str, str]:
    """{written label: variant label} for a file whose arms recorded a
    variant's format (only dense arms carry the format; their fused arms
    share the label and follow)."""
    out: dict[str, str] = {}
    for a in result["arms"].values():
        fmt = (a.get("provenance") or {}).get("format") or {}
        for written, text, variant in _variant_formats():
            if fmt.get("label") == written and fmt.get("text") == text:
                out[written] = variant
    return out


def _subset_queries(result: Mapping[str, Any], subset: str | None) -> set[str] | None:
    """The query ids a named subset covers (None: all queries). A subset whose
    list the result does not carry raises KeyError: its misses are unknown."""
    if subset is None:
        return None
    gap = (result.get("reading_gap") or {}).get("queries")
    subsets = dict(result.get("subsets") or {})
    named = {"in_reading_gap": gap, "outside_reading_gap": gap, **subsets,
             **{f"outside_{k}": v for k, v in subsets.items()}}
    if named.get(subset) is None:
        raise KeyError(subset)
    chosen = set(named[subset])
    if subset.startswith("outside_"):
        every = {q for a in result["arms"].values() for q in (a.get("per_query") or ())}
        return every - chosen
    return chosen


def _misses_in(per_query: Mapping[str, int | str | None] | None,
               result: Mapping[str, Any], subset: str | None) -> int | None:
    try:
        only = _subset_queries(result, subset)
    except KeyError:
        return None
    return _misses(per_query, only)


def _misses(per_query: Mapping[str, int | str | None] | None,
            only: set[str] | None) -> int | None:
    """Queries whose first relevant item is not in the top 5: a rank over 5,
    None (none in the top 50) or an `ERROR:<kind>`. None without ranks."""
    if per_query is None:
        return None
    return sum(1 for q, rank in per_query.items()
               if (only is None or q in only)
               and not (isinstance(rank, int) and not isinstance(rank, bool) and rank <= 5))


def rerank_ships(fused: Mapping[str, Score], reranked: Mapping[str, Score],
                 p95_ms: float, caller: str) -> bool:
    """Stage 4: the reranker ships on a lift of at least 0.05 recall@5 or
    MRR@10 on a journey slice, within its caller's p95 latency ceiling."""
    lift = max(
        max(float(reranked[s].recall5) - float(fused[s].recall5),  # type: ignore[arg-type]
            float(reranked[s].mrr10) - float(fused[s].mrr10))  # type: ignore[arg-type]
        for s in JOURNEY
    )
    return lift >= RERANK_LIFT - _EPS and p95_ms <= RERANK_P95_MS[caller]
