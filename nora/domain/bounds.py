"""Bounded reads that say when the bound actually bit.

THE SHAPE THIS EXISTS FOR
-------------------------
A caller passes `limit=N`; the callee returns `tuple[T, ...]`. That return type
has room for the rows and no room for the cut, so the one fact Postgres knew —
whether an N+1th row existed — is destroyed at the exact point it was known.
Everything downstream then cannot tell «there were N» from «there were N and I
stopped counting», and a reader that was shown a clipped slice and found
nothing reports `nothing_found` — «there is nothing» — when the honest answer
is `unavailable`, «I could not see all of it». That substitution is the one
R-27 exists to forbid, and a bound is a quiet way to commit it.

The distribution of this repo's HONEST bounds is what says this is one defect
and not seventeen. Every bound counted correctly here is one where the cap was
applied in the CALLER, while the total was still in scope: `NoteRevisitor`
reads `standing()` unbounded, compares its length to its own maximum, and
increments `nora_note_revisit_deferred_total` by the difference *before*
slicing. Nobody in this repo has ever failed to count a bound they could see.
They fail exactly when the bound is one layer down, in SQL.

**Counting the post-cut length is not a weaker version of this — it is a
different measurement wearing its clothes.** `count=len(hits)` after a `LIMIT
5` is bounded by 5 by construction, so 5-of-5 and 5-of-500 emit the identical
event. Several call sites already do this and read as though they measure
truncation.

THE PROBE: OVER-FETCH BY ONE
----------------------------
Ask for `limit + 1`, keep `limit`, and the extra row's existence IS the answer.
One row, no `COUNT(*)`, no second statement — and unlike a companion count it
cannot disagree with the rows it accompanies, because it is the same snapshot
of the same query. It works identically on a SQL `LIMIT` and on a Python slice.

**A BIT, NOT A MAGNITUDE, and that is a decision.** The counter moves by one,
never by "how many were left". The remainder is only truthfully available where
the caller already holds the unbounded set, and over-fetching by one is the
only probe that is cheap and correct everywhere. A series carrying "1" from one
site and "30" from another would be lying about its own unit. Sites that can
cheaply know the depth keep their own magnitude counters unchanged — this does
not replace `nora_note_revisit_deferred_total`.

WHAT THIS CLOSES, AND WHAT IT DOES NOT
--------------------------------------
It closes **operator** visibility, with no caller cooperation: the counter
fires inside the store, so a bound that bites is visible whether or not anyone
downstream thought about it.

It does NOT close **model** calibration. Turning "there was more" into text the
model reads is per-site render work — `fact_render`'s extent line is the model
of it — and follows separately. One change, two audiences, and only one of them
is served class-wide. Saying so here rather than letting the next reader assume
both: a `search_knowledge` result is still handed to the model as `ok` with no
hint that its list was a ceiling.

THE REVERSAL CONDITION
----------------------
This design is wrong if `nora_bound_truncated_total{site=...}` sits at or near
the call rate for a site, sustained. A bit that is always 1 carries no
information — the operator learns "there is a backlog" and never "it is
growing" — and that site needs a queue-DEPTH gauge instead, an unbounded
`COUNT(*)` of eligible rows. `fact_candidates.pending` is the one most likely
to read that way, and it is deliberately the first site wired so that reading
arrives early rather than after sixteen more.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Literal, Protocol, TypeVar, get_args

log = logging.getLogger(__name__)

T = TypeVar("T")

# Every bounded read wired to this helper, named for where the bound is
# APPLIED — `store.method` — and never for the caller. `facts.standing_facts`
# would be ONE series shared by its two callers, because the caller is not
# knowable at the point of the cut and adding it would multiply cardinality to
# buy a distinction the store cannot make.
#
# This list GROWS as sites are wired, and it is deliberately not the full
# inventory of bounded reads in the repo. A member with no increment site is
# the label-level form of "a declared metric with no call site is a lie", and
# `tests/architecture/test_bounded_reads_are_counted.py` fails on one.
BoundSite = Literal[
    "audit.recent",
    "fact_candidates.pending",
]

BOUND_SITES: frozenset[str] = frozenset(get_args(BoundSite))

# The one counter. Declared in `nora.obs.metric_specs`, whose help text points
# HERE for the vocabulary rather than restating it — a hand-copied vocabulary
# is exactly what drifts, and `nora_ingest_outcomes_total` already sets that
# precedent verbatim.
TRUNCATED_METRIC = "nora_bound_truncated_total"


class _Counter(Protocol):
    """`nora.obs.metrics.Metrics`' recording surface, as much of it as this
    module uses. Declared structurally because `nora.domain` is framework-free
    and may import nothing above itself (`lint-imports`' first contract)."""

    def inc(self, name: str, value: float = ..., **labels: object) -> None: ...


def over_fetch(limit: int) -> int:
    """The row count to actually ask the database for.

    Paired with `cut`: a site that calls one without the other is the bug this
    module exists to prevent, so both take the same `limit` and
    `tests/architecture/test_bounded_reads_are_counted.py` checks that every
    wired site uses both.
    """
    return limit + 1


def cut(
    rows: Sequence[T],
    *,
    limit: int,
    site: str,
    metrics: _Counter | None = None,
) -> tuple[T, ...]:
    """Trim an over-fetched read back to `limit`, counting when it truncated.

    `rows` must have been fetched with `over_fetch(limit)`. `len(rows) > limit`
    is then exactly "at least one more row existed", which is the whole datum.

    The site name is checked at RUNTIME, not only by mypy: `make gate`'s mypy
    is scoped and does not cover `nora/language` or `nora/agent`, where most of
    the remaining bounded reads live, so a static-only guard there would be a
    claim rather than a check. It LOGS and still records rather than raising —
    a mistyped label must not take down a read path, and the wrong-label series
    is visible in the same place the right one would have been.
    """
    if site not in BOUND_SITES:
        log.warning(
            "bounded read recorded under an undeclared site %r — add it to "
            "BoundSite or fix the caller; counting it anyway", site,
        )
    kept = tuple(rows[:limit])
    if metrics is not None and len(rows) > limit:
        metrics.inc(TRUNCATED_METRIC, site=site)
    return kept
