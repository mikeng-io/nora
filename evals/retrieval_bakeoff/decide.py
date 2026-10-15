"""The bake-off's decision rule (design/unified-memory/20 «Slices and margins»,
«The target is one multimodal embedder»), stated once so a verdict can be
re-derived from a result file without trusting whoever ran it.

`decide(r)` takes `r[arm][slice] -> Score`, where an arm is a fused arm:
`BASELINE` (the best lexical arm fused with bge-m3 over the stored readings)
and each candidate as `fused:<model>@<dim>`.

1. **ERROR first.** Any ERROR on a journey slice, in any arm, or a journey
   slice with no result, decides ERROR: an unmeasured slice is not a tie.
2. **Pass.** A candidate holds the Cantonese slice (recall@5 no more than
   0.05 below the baseline's) AND wins another journey slice (recall@5 at
   least 0.10 above it). Several passing: the best text-to-image recall@5,
   then the lower dimension.
3. **Media-only.** Wins text-to-image but does not hold Cantonese: flagged
   for the operator (a second embedder doubles the index), never a REPLACE.
4. **Otherwise a tie, and a tie keeps bge-m3.**

The bootstrap CI in a result file is reported beside these numbers and is
never read here: with ~40 queries a slice, one query moves recall by about
0.025, so a 0.10 margin is four queries, and the margin is the rule.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass

__all__ = [
    "BASELINE", "HOLD", "JOURNEY", "WIN", "Decision", "Score", "Verdict", "decide",
    "rerank_ships",
]

JOURNEY = ("text_image", "cantonese", "names")
BASELINE = "fused:bge-m3"
HOLD = 0.05
WIN = 0.10
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
    recall5: float
    mrr10: float
    errors: int


@dataclass(frozen=True, slots=True)
class Decision:
    verdict: Verdict
    winner: str | None = None
    wins: tuple[str, ...] = ()
    media_only: tuple[str, ...] = ()
    reason: str = ""


def _dim(arm: str) -> int:
    _, _, dim = arm.rpartition("@")
    return int(dim) if dim.isdigit() else 1 << 30


def decide(r: Mapping[str, Mapping[str, Score]]) -> Decision:
    if BASELINE not in r:
        return Decision(Verdict.ERROR, reason=f"no {BASELINE} arm")
    for arm, slices in r.items():
        for s in JOURNEY:
            if s not in slices:
                return Decision(Verdict.ERROR, reason=f"{arm} has no {s} result")
            if slices[s].errors:
                return Decision(Verdict.ERROR, reason=f"{arm} has {slices[s].errors} ERROR on {s}")
    base = r[BASELINE]
    passing: list[tuple[str, tuple[str, ...]]] = []
    media_only: list[str] = []
    for arm in sorted(a for a in r if a.startswith("fused:") and a != BASELINE):
        holds = r[arm]["cantonese"].recall5 >= base["cantonese"].recall5 - HOLD - _EPS
        wins = tuple(s for s in ("text_image", "names")
                     if r[arm][s].recall5 >= base[s].recall5 + WIN - _EPS)
        if holds and wins:
            passing.append((arm, wins))
        elif "text_image" in wins:
            media_only.append(arm)
    if not passing:
        return Decision(Verdict.KEEP_BGE_M3, media_only=tuple(media_only),
                        reason="no candidate passes both margins; a tie keeps the incumbent")
    arm, wins = min(passing, key=lambda p: (-r[p[0]]["text_image"].recall5, _dim(p[0]), p[0]))
    return Decision(Verdict.REPLACE, winner=arm, wins=wins, media_only=tuple(media_only),
                    reason=f"{arm} holds cantonese and wins {', '.join(wins)}")


def rerank_ships(fused: Mapping[str, Score], reranked: Mapping[str, Score],
                 p95_ms: float, caller: str) -> bool:
    """Stage 4: the reranker ships on a lift of at least 0.05 recall@5 or
    MRR@10 on a journey slice, within its caller's p95 latency ceiling."""
    lift = max(max(reranked[s].recall5 - fused[s].recall5, reranked[s].mrr10 - fused[s].mrr10)
               for s in JOURNEY)
    return lift >= RERANK_LIFT - _EPS and p95_ms <= RERANK_P95_MS[caller]
