"""The bake-off's decision rule (design/unified-memory/20 «Slices and margins»,
operator ruling 2026-10-15), stated once so a verdict can be re-derived from
the result files without trusting whoever ran it.

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
   misses cannot be beaten. Several passing: the best text_image recall@5,
   then the lower dimension.
4. **Media-only.** Wins text_image but does not hold cantonese: flagged for
   the operator (a second embedder doubles the index), never a REPLACE.
5. **Otherwise a tie, and a tie keeps bge-m3.**

Why halve-the-misses (operator, 2026-10-15): the plan's absolute +0.10
recall@5 margin was unreachable once bge-m3 over the readings measured 0.928
on text_image; a relative bar on what is still missed is reachable at any
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
#: A reranker's p95 added latency ceiling per caller, ms (20 «When a reranker earns its place»).
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


@dataclass(frozen=True, slots=True)
class Decision:
    verdict: Verdict
    winner: str | None = None
    wins: tuple[str, ...] = ()
    media_only: tuple[str, ...] = ()
    baseline: dict[str, float] = field(default_factory=dict)
    reason: str = ""


def _dim(arm: str) -> int:
    _, _, dim = arm.rpartition("@")
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
    arm, wins = min(passing, key=lambda p: (-recall(p[0], "text_image"), _dim(p[0]), p[0]))
    return Decision(Verdict.REPLACE, winner=arm, wins=wins, media_only=tuple(media_only),
                    baseline=base, reason=f"{arm} holds cantonese and wins {', '.join(wins)}")


def scores_from_results(paths: Iterable[Path], *, subset: str | None = None,
                        ) -> dict[str, dict[str, Score]]:
    """`decide`'s input from result files, one slice per file, arm names as
    written. `subset` reads an arm's named subset instead of all queries
    (`in_reading_gap`, `outside_reading_gap`). ERROR is the arm's query plus
    pool errors on the slice, so either one decides ERROR."""
    out: dict[str, dict[str, Score]] = {}
    for path in paths:
        result = json.loads(Path(path).read_text(encoding="utf-8"))
        for arm, a in result["arms"].items():
            src = a if subset is None else a[subset]
            errors = a["errors"]
            out.setdefault(arm, {})[result["slice"]] = Score(
                src.get("recall5"), src.get("mrr10"),
                int(errors.get("queries", 0)) + int(errors.get("pool", 0)),
            )
    return out


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
