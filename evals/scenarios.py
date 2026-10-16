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

from evals import pseudonyms
from evals.backbone import SNAPSHOT_KEYS, MeetsGrader, Spec, ToolRouteGrader, nora_task
from evals.recall import evidence_problems
from nora.subject.transport import TRANSPORT_STATES

_CORPORA = Path(__file__).parent / "corpora"

#: Every key a case may carry. Anything else is REFUSED, because a corpus
#: stating something the runner ignores is a corpus asserting more than is
#: measured — which is how `media: true` was once dropped from five rows with
#: nothing reporting it.
CASE_KEYS = frozenset({
    "name", "entry", "expect", "input", "expected_output", "snapshot",
    "person", "mention", "sample", "safety", "category", "metadata", "parked",
    "counterfactual",
})
PARKED_KEYS = frozenset({"reason", "why", "returns_when"})
PARKING_REASONS = frozenset({"P-A"})

#: What a COUNTERFACTUAL case must state: which tier it varies, and why that
#: variation cannot be reconstructed from any real moment.
COUNTERFACTUAL_KEYS = frozenset({"varies", "why"})
#: The tiers a case may vary. Closed, because the whole value of this state is
#: that it names a small, argued set rather than becoming a general override.
#:
#: `transport` joined 2026-09-24 (design/self-awareness/05): whether a network's
#: connection is working when she is asked to relay to it. Health at an
#: anchor's instant is recorded nowhere — the sandbox HOLDS it at what
#: production renders on an ordinary turn (every network up,
#: `nora.subject.transport`) — so «Discord is down right now» cannot be a real
#: moment and can only be a varied one. The varied states ride in
#: `metadata.transport`, which no other case may state.
#:
#: `prior` joined 2026-09-24 (issue #445): the standing fold's position before
#: the turn, declared ABSENT for a turn older than the standing chain
#: (2026-09-20). The chain did not backfill, so no position that old is
#: recoverable, and the operator's own residue moment (a search found several
#: restaurants, the reply sent two) predates it. The harness refuses the
#: declaration wherever the chain DOES hold a revision at or before the turn.
#:
#: `lessons` joined 2026-10-09 (operator, design/self-growth/40 §8): the lessons a
#: unit held, attached at the real moment that TAUGHT them. A lesson is induced
#: from the episodes it cites, so it cannot exist before them, and the first one
#: was written 2026-10-08 17:43 UTC: no real moment holds a lesson AND the
#: situation it bears on. The varied lessons ride in `metadata.lessons` (her own
#: words, as the live lesson reads), which no other case may state.
COUNTERFACTUAL_TIERS = frozenset({"culture", "transport", "prior", "lessons"})
#: Tiers that mean something in one corpus only. `prior` is the standing
#: fold's; that corpus varies nothing else. `lessons` is the lessons corpus's.
_TIER_CORPORA = {"prior": frozenset({"standing"}), "lessons": frozenset({"lessons"})}


@dataclass(frozen=True, slots=True)
class Census:
    """How many of a corpus's cases are in each state. Reported on every run,
    so a shrinking active set is seen rather than discovered later."""

    total: int
    active: int
    parked: int
    unanchored: int
    #: Cases that are ANCHORED but vary one tier that no real moment can
    #: carry. A subset of `active`, reported separately and never folded in.
    #:
    #: **This count is what makes the state safe to have.** A counterfactual
    #: is a manipulation, not a reconstruction; an aggregate that averaged the
    #: two would let `11-the-sandbox-rebuild.md`'s rule erode by ARITHMETIC
    #: rather than by anybody deciding to relax it. Opened 2026-09-22 on the
    #: operator's ruling that the culture arm's `-chronicle` case stays: a
    #: chronicle and an account are the same facts in two representations, and
    #: no room ever held both at once.
    #:
    #: The state holds ONLY what genuinely cannot be reconstructed. The same
    #: arm's `-deviant` case is NOT in it: another real room's real account,
    #: attached here, is a declared swap of real data, so it stays anchored.
    #: Putting it here too would be the cheap economy that makes the number
    #: stop meaning anything.
    counterfactual: int

    def __str__(self) -> str:
        line = (
            f"{self.total} total, {self.active} active, {self.parked} parked, "
            f"{self.unanchored} unanchored"
        )
        # Printed only when non-zero: a corpus with no counterfactual case
        # should not carry a column implying the state is ordinary.
        return line + (
            f", {self.counterfactual} COUNTERFACTUAL" if self.counterfactual else ""
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
    counterfactual = row.get("counterfactual") or {}
    if counterfactual:
        if not snapshot:
            raise ValueError(
                f"{name}: case {row['name']!r} is counterfactual but names no "
                "`snapshot:`. A counterfactual is a real moment with ONE tier "
                "varied — without the anchor it is authored content, which is "
                "the thing this corpus has no path for"
            )
        if set(counterfactual) != COUNTERFACTUAL_KEYS:
            raise ValueError(
                f"{name}: case {row['name']!r}'s `counterfactual:` must state "
                f"exactly {sorted(COUNTERFACTUAL_KEYS)}"
            )
        varies = counterfactual.get("varies")
        allowed = _TIER_CORPORA.get(varies)
        if allowed is not None and name not in allowed:
            raise ValueError(
                f"{name}: case {row['name']!r} varies {varies!r}, which only "
                f"{sorted(allowed)} may vary"
            )
        if CORPUS_ENTRY.get(name) == "standing" and varies != "prior":
            raise ValueError(
                f"{name}: case {row['name']!r} varies {varies!r}; a standing "
                "case may vary only its prior"
            )
        if varies not in COUNTERFACTUAL_TIERS:
            raise ValueError(
                f"{name}: case {row['name']!r} varies {varies!r}; only "
                f"{sorted(COUNTERFACTUAL_TIERS)} may be varied. The set is "
                "CLOSED on purpose — a general override is what this state "
                "exists instead of"
            )
    varied = (row.get("metadata") or {}).get("transport")
    if varied is not None and (counterfactual or {}).get("varies") != "transport":
        raise ValueError(
            f"{name}: case {row['name']!r} states `metadata.transport` without "
            "being a counterfactual that varies `transport`. The tier is held "
            "at production's ordinary value for every other case; a case that "
            "changes it is a manipulation and is counted as one"
        )
    taught = (row.get("metadata") or {}).get("lessons")
    if taught is not None and (counterfactual or {}).get("varies") != "lessons":
        raise ValueError(
            f"{name}: case {row['name']!r} states `metadata.lessons` without being "
            "a counterfactual that varies `lessons`: a lesson attached to an "
            "anchored moment is a manipulation and is counted as one"
        )
    if (counterfactual or {}).get("varies") == "lessons" and (
        not isinstance(taught, list) or not taught
        or not all(isinstance(x, str) and x.strip() for x in taught)
    ):
        raise ValueError(
            f"{name}: case {row['name']!r} varies `lessons` and must state "
            "`metadata.lessons` as a non-empty list of lesson texts"
        )
    if varied is not None:
        bad = {k: v for k, v in dict(varied).items() if v not in TRANSPORT_STATES}
        if not varied or bad:
            raise ValueError(
                f"{name}: case {row['name']!r}'s `metadata.transport` must map "
                f"networks to one of {sorted(TRANSPORT_STATES)}; got {varied!r}"
            )
    surface = (row.get("metadata") or {}).get("memory_surface")
    if surface is not None and surface not in MEMORY_SURFACES:
        raise ValueError(
            f"{name}: case {row['name']!r}'s `metadata.memory_surface` must be one "
            f"of {sorted(MEMORY_SURFACES)}, or absent for a case that routes on "
            f"both; got {surface!r}"
        )
    if CORPUS_ENTRY.get(name) == "standing":
        _check_fold_case(name, row)
    evidence = evidence_problems(row.get("metadata") or {})
    if evidence:
        raise ValueError(f"{name}: case {row['name']!r}: {'; '.join(evidence)}")
    parked = row.get("parked") or {}
    if parked:
        if counterfactual:
            raise ValueError(
                f"{name}: case {row['name']!r} is parked AND counterfactual; "
                "a parked case is not run, so varying a tier in it asserts "
                "nothing"
            )
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


def _check_fold_case(name: str, row: dict[str, Any]) -> None:
    """A standing case states the turn, the kind, and the property — at LOAD.

    Refused here rather than at run time because run time is after the fold's
    model call: a typo'd kind would spend a fold and then ERROR in the judge.
    """
    from evals.rebuilt.rubric import FOLD_KINDS

    meta = row.get("metadata") or {}
    problems = []
    if row.get("expect") != "fold":
        problems.append("expect must be `fold`")
    if meta.get("kind") not in FOLD_KINDS:
        problems.append(f"metadata.kind must be one of {sorted(FOLD_KINDS)}")
    if not str(meta.get("tag_id") or "").strip():
        problems.append("metadata.tag_id must name the turn")
    if not str(row.get("expected_output") or "").strip():
        problems.append("expected_output must state the ONE property graded")
    if problems:
        raise ValueError(f"{name}: case {row['name']!r}: {'; '.join(problems)}")


def _case(row: dict[str, Any]) -> Case:
    # Resolved ONCE, here, before anything reads the case (#776): the anchor,
    # the person, the input, the expectation, every metadata string. The
    # `name` is not: it is how `--case` selects, and it names no one.
    row = {**pseudonyms.resolve(row), "name": row["name"]}
    return Case(
        name=str(row["name"]),
        input=str(row.get("input", "")),
        expected_output=str(row.get("expected_output", "")),
        metadata={
            **(row.get("metadata") or {}),
            "expect": row["expect"],
            "snapshot": dict(row.get("snapshot") or {}),
            "parked": dict(row.get("parked") or {}),
            "counterfactual": dict(row.get("counterfactual") or {}),
            "person": str(row.get("person") or pseudonyms.resolve("miles_novak")),
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
        total=len(cases), active=len(cases) - parked, parked=parked,
        unanchored=unanchored,
        counterfactual=sum(
            1 for c in cases
            if c.metadata.get("counterfactual") and not c.metadata.get("parked")
        ),
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
    "proactive_act": "proactive_act",
    "tool_selection": "tag",
    # S2 over the Cognition catalogue (design/cognition-loop/30-cognition.md
    # §2/§4; migration steps 3a-ii and 3a-iii). `entry: dreamer` with
    # `category: cognition` is driven by `evals.rebuilt.entry._cognition_probe`
    # through `CognitionSessionRunner.probe`, on the runner the subject's
    # composition builds under `[cognition] enabled = true`; with
    # sessions off in the eval config the case is an ERROR, never a pass.
    "tool_selection_cognition": "dreamer",
    # design/self-awareness/05: what she says about WHERE she is. A tag, and
    # judged — "did she reply" says nothing about whether the reply named the
    # right networks, or recited them unasked.
    "presence": "tag",
    "standing": "standing",
    "tool_reporting": "tag",
    # identity.md v12: what she says about her OWN FORM. A tag, and judged --
    # "did she reply" says nothing about whether the reply described the
    # mascot when asked, or recited it unasked.
    "appearance": "tag",
    # design/self-growth/40 §8 (L5): what a held lesson does to a reply. A tag,
    # judged on the ONE behaviour each case's lesson asks for.
    "lessons": "tag",
    # design/context-plan/12 §5's character cut: whether an ordinary tag
    # reply still sounds like her. A tag, judged on the opted-in `personality` criterion.
    "personality": "tag",
    # design/unified-memory/00-intent.md "Sequence" step 0: the conversational-
    # recall journeys on existing tools. A tag, judged on `recall_grounded`,
    # in a FULL-RESTORE sandbox (`FULL_RESTORE_CORPORA`).
    "conversational_recall": "tag",
}

#: Corpora whose sandbox is the FULL RESTORE (`nora.subject.sandbox_as_of`):
#: every restorable store as of the instant, row for row with its own ids, and
#: the subject composed against it with `evals.subject_config.
#: FULL_RESTORE_OVERRIDES` on. Every other corpus keeps the snapshot path
#: (`sandbox_from_snapshot`'s four tiers).
#:
#: Per CORPUS, never per case, for `CORPUS_ENTRY`'s reason: a corpus is a suite
#: about one assembly, and two assemblies inside one corpus would make its
#: numbers a mix nobody can read. A recall corpus needs the full restore
#: because the snapshot path seeds messages without `media_json` (she sees a
#: bare `[image …]` handle and `media_objects` is empty) and holds no events,
#: memos, presence or Rollup at all, which are exactly what recall rests on.
FULL_RESTORE_CORPORA: frozenset[str] = frozenset({"conversational_recall"})

#: S2 — graded on WHICH TOOL ran, not on whether she replied
#: (`design/tool-catalogue/05-measurement.md` §2c). One grader only:
#: `sample_read` groups every evaluator's labels under one case, so adding
#: `MeetsGrader` here would let a reply verdict blur a routing one.
ROUTED_CORPORA = frozenset({"tool_selection", "tool_selection_cognition"})

#: S3 (grounding)/S9 (injection) — "did she reply" is structural; "was the
#: reply right" needs a judge (`10-the-scenario-map.md` §3). Every case in
#: these six corpora expects `reply`, so judging unconditionally (once a
#: judge is configured) adds no second selection rule.
JUDGED_CORPORA = frozenset({
    "being_contradicted", "identity_grounding", "source_scope",
    "injections", "transcript_injection", "standing", "presence",
    # ADR 0015 §4: what she SAYS after a tool returns a status. Its opted-in
    # `tool_status_reported` binds with safety (`Judge.grade`, #449).
    "tool_reporting",
    # identity.md v12: its opted-in `appearance` criterion binds with safety.
    "appearance",
    # L5: its opted-in `lesson_applied` criterion binds with safety.
    "lessons",
    # Its opted-in `personality` criterion binds with safety.
    "personality",
    # Its opted-in `recall_grounded` criterion binds with safety.
    "conversational_recall",
})

#: Corpora with NO structural reading at all: every case's verdict IS the
#: judge's. `standing` grades a Cantonese position against a semantic property
#: (`design/standing-compact/20-verification.md` §1), and "a fold wrote
#: something" is true of every fold that did not fail. Run without a judge,
#: every case would pay for a fold and then ERROR, so `python -m evals` refuses
#: to start one instead.
#:
#: `conversational_recall` too: its only structural reading is `expect: reply`,
#: which any reply meets, including one that invents a place, and its evidence
#: is read (fail-closed, `evals.recall.recall_facts`) only inside the judge
#: branch. Run without a judge, every case would PASS on having spoken.
JUDGE_REQUIRED = frozenset({"standing", "conversational_recall"})


#: `metadata.memory_surface` (DEC-316): a case whose expected route exists on
#: ONE memory surface. `graph` (`memory_search`) runs only under
#: `--memory-index`; `honcho` (`ask_memory`, `search_memory`,
#: `search_memory_workspace`, `who_said`) only without it. A case that states
#: none runs on both. The other arm skips it rather than FAIL it by
#: construction: a verb that is not offered cannot be routed to.
MEMORY_SURFACES = frozenset({"graph", "honcho"})

#: `python -m evals --memory-index`: the subject composed with the memory
#: GRAPH's tool surface (DEC-316: `memory_search`, and none of the Honcho
#: memory verbs), the after arm of a routing measurement whose deployment
#: still runs the Honcho surface. Applied as a subject override
#: (`Spec.overrides`), never written into the generated config, so the drift
#: gate still compares that config with the deployment's own. Routing grades
#: which tools ran, so the sandbox's index may be empty for it.
MEMORY_INDEX_OVERRIDES: dict[str, object] = {"memory_index.enabled": True}


def experiment_for(
    name: str, *, source_url: str, judge: Any = None, sample: int = 1,
    only: frozenset[str] | None = None, memory_index: bool = False,
) -> tuple[Experiment, Any]:
    """A ready-to-run `(Experiment, task)` pair for one corpus.

    `MeetsGrader` for every corpus, uniformly — it is a strict superset of
    `AbsenceGrader`'s vocabulary, so there is no per-corpus grader selection
    to get wrong. `judge` is threaded through only for `JUDGED_CORPORA`.

    `sample > 1` draws each case `sample` times via `evals.backbone.sampled`
    — one draw of a stochastic process is not a reading of it. Read the
    result with `evals.backbone.sample_read`, not `read`, when `sample > 1`.

    `only` restricts the run to named cases, so re-measuring the few that
    moved does not cost the whole corpus. `sample_read`'s UNSTABLE is the
    answer that needs draws, and draws are what a corpus-sized bill makes
    people skimp on.

    `memory_index` composes the subject on the memory graph's tool surface
    (`MEMORY_INDEX_OVERRIDES`).
    """
    from evals.backbone import sampled

    cases = load_corpus(name)
    if only is not None:
        # NAMED cases only. A re-measurement asks about the cases that moved,
        # and paying for a whole corpus to re-draw four of them is how a
        # sample count gets chosen by budget instead of by the question.
        # Refused by name when a name matches nothing: a typo that silently
        # selected zero cases would report a clean corpus.
        known = {c.name for c in cases}
        missing = sorted(only - known)
        if missing:
            raise ValueError(
                f"{name}: no active case named {missing}; it may be parked, "
                f"or misspelt. Active: {sorted(known)}"
            )
        cases = [c for c in cases if c.name in only]
    # One memory surface per arm (`MEMORY_SURFACES`). A case NAMED with
    # `--case` on the other arm is refused, never silently dropped: that run
    # would report a clean corpus it never drew.
    arm = "graph" if memory_index else "honcho"
    other = sorted(c.name for c in cases if c.metadata.get("memory_surface") not in (None, arm))
    if only is not None and other:
        raise ValueError(
            f"{name}: {other} route on the other memory surface; "
            + ("drop --memory-index" if memory_index else "pass --memory-index")
        )
    cases = [c for c in cases if c.metadata.get("memory_surface") in (None, arm)]
    if sample > 1:
        cases = [draw for base in cases for draw in sampled(base, sample)]
    spec = Spec(
        source_url=source_url,
        entry=CORPUS_ENTRY[name],
        judge=judge if name in JUDGED_CORPORA else None,
        full_restore=name in FULL_RESTORE_CORPORA,
        overrides=dict(MEMORY_INDEX_OVERRIDES) if memory_index else None,
    )
    grader = ToolRouteGrader() if name in ROUTED_CORPORA else MeetsGrader()
    experiment = Experiment(cases=cases, evaluators=[grader])
    return experiment, nora_task(spec)
