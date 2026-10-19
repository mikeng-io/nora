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

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from strands_evals import Case, Experiment

from evals import pseudonyms
from evals.backbone import SNAPSHOT_KEYS, MeetsGrader, Spec, ToolRouteGrader, nora_task
from evals.recall import evidence_problems
from nora.subject.sandbox_index import EMBEDDER_FROM_HOST
from nora.subject.transport import TRANSPORT_STATES

_CORPORA = Path(__file__).parent / "corpora"

#: Every key a case may carry. Anything else is REFUSED, because a corpus
#: stating something the runner ignores is a corpus asserting more than is
#: measured.
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
#: `transport`: whether a network's
#: connection is working when she is asked to relay to it. Health at an
#: anchor's instant is recorded nowhere — the sandbox HOLDS it at what
#: production renders on an ordinary turn (every network up,
#: `nora.subject.transport`) — so «Discord is down right now» cannot be a real
#: moment and can only be a varied one. The varied states ride in
#: `metadata.transport`, which no other case may state.
#:
#: `prior`: the standing fold's position before the turn, declared ABSENT for
#: a turn older than the standing chain. The chain did not backfill, so no
#: position that old is recoverable. The harness refuses the declaration
#: wherever the chain DOES hold a revision at or before the turn.
#:
#: `lessons`: the lessons a unit held, attached at the real moment that
#: TAUGHT them. A lesson is induced from the episodes it cites, so it cannot
#: exist before them: no real moment holds a lesson AND the situation it
#: bears on. The varied lessons ride in `metadata.lessons` (her own
#: words, as the live lesson reads), which no other case may state.
COUNTERFACTUAL_TIERS = frozenset({
    "culture", "transport", "prior", "lessons", "persona_sources",
})
#: Tiers that mean something in one corpus only. `prior` is the standing
#: fold's; that corpus varies nothing else. `lessons` is the lessons corpus's.
_TIER_CORPORA = {
    "prior": frozenset({"standing"}), "lessons": frozenset({"lessons"}),
    "persona_sources": frozenset({"persona_identity"}),
}


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
    #: rather than by anybody deciding to relax it. Example: the culture arm's
    #: `-chronicle` case, where a chronicle and an account are the same facts
    #: in two representations and no room ever held both at once.
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
    rows = list(doc)
    if name == "persona_identity" and os.environ.get("NORA_EVAL_PERSONA_CASES"):
        private = yaml.safe_load(Path(os.environ["NORA_EVAL_PERSONA_CASES"]).read_text())
        if not isinstance(private, dict) or set(private) != {r["name"] for r in rows}:
            raise ValueError("private persona file must supply exactly the six registered vectors")
        for row in rows:
            value = private[row["name"]]
            if (not isinstance(value, dict)
                    or set(value) != {"snapshot", "person", "sources", "clock_ms"}):
                raise ValueError("private persona vector requires snapshot, roster person, sources")
            row.update(snapshot=value["snapshot"], person=value["person"], counterfactual={
                "varies": "persona_sources",
                "why": "Private invented source arrivals and held birthday, at a declared clock; "
                       "the real anchor is background only, not evidence for these facts.",
            })
            row["metadata"] = {**row.get("metadata", {}),
                               "persona_input": {"sources": value["sources"],
                                                 "clock_ms": value["clock_ms"]},
                               "private_persona_file": True}
    return rows


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
    # Resolved ONCE, here, before anything reads the case: the anchor,
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
    # S2 over the Cognition catalogue. `entry: dreamer` with
    # `category: cognition` is driven by `evals.rebuilt.entry._cognition_probe`
    # through `CognitionSessionRunner.probe`, on the runner the subject's
    # composition builds under `[cognition] enabled = true`; with
    # sessions off in the eval config the case is an ERROR, never a pass.
    "tool_selection_cognition": "dreamer",
    # What she says about WHERE she is. A tag, and judged — "did she reply"
    # says nothing about whether the reply named the right networks, or recited
    # them unasked.
    "presence": "tag",
    "standing": "standing",
    "persona_identity": "persona_identity",
    "tool_reporting": "tag",
    # What she says about her OWN FORM. A tag, and judged — "did she reply"
    # says nothing about whether the reply described the mascot when asked, or
    # recited it unasked.
    "appearance": "tag",
    # What a held lesson does to a reply. A tag, judged on the ONE behaviour
    # each case's lesson asks for.
    "lessons": "tag",
    # The character cut: whether an ordinary tag reply still sounds like her.
    # A tag, judged on the opted-in `personality` criterion.
    "personality": "tag",
    # The conversational-recall journeys on existing tools. A tag, judged on
    # `recall_grounded`, in a FULL-RESTORE sandbox (`FULL_RESTORE_CORPORA`).
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

#: S2 — graded on WHICH TOOL ran, not on whether she replied. One grader only:
#: `sample_read` groups every evaluator's labels under one case, so adding
#: `MeetsGrader` here would let a reply verdict blur a routing one.
ROUTED_CORPORA = frozenset({"tool_selection", "tool_selection_cognition"})

#: S3 (grounding)/S9 (injection) — "did she reply" is structural; "was the
#: reply right" needs a judge. Every case in these corpora expects `reply`, so
#: judging unconditionally (once a judge is configured) adds no second
#: selection rule.
JUDGED_CORPORA = frozenset({
    "being_contradicted", "identity_grounding", "source_scope",
    "injections", "transcript_injection", "standing", "presence",
    # ADR 0015 §4: what she SAYS after a tool returns a status. Its opted-in
    # `tool_status_reported` binds with safety (`Judge.grade`).
    "tool_reporting",
    # Its opted-in `appearance` criterion binds with safety.
    "appearance",
    # Its opted-in `lesson_applied` criterion binds with safety.
    "lessons",
    # Its opted-in `personality` criterion binds with safety.
    "personality",
    # Its opted-in `recall_grounded` criterion binds with safety.
    "conversational_recall",
})

#: Corpora with NO structural reading at all: every case's verdict IS the
#: judge's. `standing` grades a Cantonese position against a semantic property,
#: and "a fold wrote something" is true of every fold that did not fail. Run
#: without a judge, every case would pay for a fold and then ERROR, so
#: `python -m evals` refuses to start one instead.
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
#: gate still compares that config with the deployment's own. Applied on the
#: graph arm whatever the deployment's flag, so a deployment that turned the
#: surface on is composed the same way.
#:
#: `embedder_url`: the query embedder, reached from the host where the suite
#: runs (`nora.subject.EMBEDDER_FROM_HOST`); the deployment's `embedder:8080`
#: resolves only on the compose network. Never on the honcho arm, which builds
#: no `memory_search`. The worker is not the subject's (`nora.subject.obtain`
#: composes none), so nothing writes through it, and every graph-surface
#: sandbox gets its index and the source's vectors before the drive
#: (`nora.subject.index_sandbox`).
MEMORY_INDEX_OVERRIDES: dict[str, object] = {
    "memory_index.enabled": True,
    "memory_index.embedder_url": EMBEDDER_FROM_HOST,
}


def selected_cases(
    name: str, *, only: frozenset[str] | None = None, memory_index: bool = False,
) -> list[Case]:
    """The cases of corpus `name` a run draws, before sampling: `only`'s, on
    the resolved memory surface (`experiment_for` says why each filter).
    Refused (ValueError) when `only` names a case the run cannot draw."""
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
    return cases


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

    `memory_index` is the RESOLVED arm (`python -m evals` takes it from the
    generated config's `[memory_index] enabled`, or `--memory-index`): it
    selects the cases, composes the subject on the graph surface
    (`MEMORY_INDEX_OVERRIDES`), and is checked against the composed config
    per case (`Spec.memory_surface`).
    """
    from evals.backbone import sampled

    cases = selected_cases(name, only=only, memory_index=memory_index)
    if sample > 1:
        cases = [draw for base in cases for draw in sampled(base, sample)]
    spec = Spec(
        source_url=source_url,
        entry=CORPUS_ENTRY[name],
        judge=judge if name in JUDGED_CORPORA else None,
        full_restore=name in FULL_RESTORE_CORPORA,
        overrides=dict(MEMORY_INDEX_OVERRIDES) if memory_index else None,
        memory_surface="graph" if memory_index else "honcho",
    )
    grader = ToolRouteGrader() if name in ROUTED_CORPORA else MeetsGrader()
    experiment = Experiment(cases=cases, evaluators=[grader])
    return experiment, nora_task(spec)
