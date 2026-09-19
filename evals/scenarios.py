"""The corpus port — `evals/corpora/*.yaml` as `strands_evals.Case` objects.

`10-the-scenario-map.md` §7 (operator override, 2026-09-20): port the 150
existing cases onto the S1-S9 map. `load_corpus()` reuses the EXISTING
seeded content unchanged — the port is mechanical, not a rewrite of 150
cases' real-world transcripts, notes and policies — and carries every field
the old harness's own `Case` (`evals/rebuilt/domain.py`) read into the SDK's
`Case.metadata` under the SAME names, because `evals.backbone._drive()`
reads exactly these (the per-case room/now/policy/config port, 2026-09-20)
and `MeetsGrader` reads `expect`/is fed by the rest through `_drive()`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from strands_evals import Case, Experiment

from evals.backbone import MeetsGrader, Spec, nora_task
from evals.rebuilt.runner import substitute_trigger

_CORPORA = Path(__file__).parent / "corpora"

#: `evals/rebuilt/runner.load()`'s own fallback trigger, carried here so a
#: corpus that never states `engagement.trigger.text` renders `{trigger}`
#: the same way under either harness.
_DEFAULT_TRIGGER = "nora"


def load_corpus(name: str, *, default_trigger: str = _DEFAULT_TRIGGER) -> list[Case]:
    """`evals/corpora/<name>.yaml`, as `strands_evals.Case` objects.

    Two corpus shapes, both already in the tree (`evals/rebuilt/runner.load`'s
    own docstring): a bare list of cases, or a mapping with `cases:` and an
    optional corpus-wide `config:`/`policy:`.
    """
    doc = yaml.safe_load((_CORPORA / f"{name}.yaml").read_text(encoding="utf-8")) or []
    if isinstance(doc, dict):
        rows = doc.get("cases") or []
        top_config = dict(doc.get("config") or {})
        top_policy = str(doc.get("policy") or "")
    else:
        rows, top_config, top_policy = doc, {}, ""

    cases = []
    for row in rows:
        for key in ("name", "entry", "expect"):
            if key not in row:
                raise ValueError(f"{name}: case is missing {key!r}: {row!r}")
        merged_config = {**top_config, **(row.get("config") or {})}
        trigger = str(merged_config.get("engagement.trigger.text") or default_trigger)
        cases.append(
            Case(
                name=str(row["name"]),
                input=substitute_trigger(str(row.get("input", "")), trigger),
                expected_output=str(row.get("expected_output", "")),
                metadata={
                    **(row.get("metadata") or {}),
                    "expect": row["expect"],
                    "knows": row.get("knows") or {},
                    "room": list(row.get("room") or []),
                    "now": str(row.get("now") or ""),
                    "policy": str(row.get("policy") or top_policy),
                    "config": merged_config,
                    "category": row.get("category", ""),
                },
            )
        )
    return cases


#: Which entry point drives a corpus. ALL cases in a given corpus file share
#: one entry point (measured directly against every file in `evals/corpora/`,
#: 2026-09-20) — a corpus IS a suite about one entry point, matching
#: `evals/rebuilt/domain.py`'s own `Case.entry` being per-suite in practice.
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
#: judge is configured) matches `evals/rebuilt/runner.run_once()`'s own
#: behavior exactly rather than adding a second selection rule.
JUDGED_CORPORA = frozenset({
    "being_contradicted", "identity_grounding", "source_scope",
    "injections", "transcript_injection",
})


def experiment_for(
    name: str, *, database_url: str, judge: Any = None,
) -> tuple[Experiment, Any]:
    """A ready-to-run `(Experiment, task)` pair for one ported corpus.

    `MeetsGrader` for every corpus, uniformly — it is a strict superset of
    `AbsenceGrader`'s vocabulary (`10-the-scenario-map.md`'s own §3 table
    lists the SAME instrument, `Outcome.meets`, for every structural class),
    so there is no per-corpus grader selection to get wrong.

    `judge` is threaded through only for `JUDGED_CORPORA` — passing one for
    a corpus that never sets `expect: reply` would judge cases the corpus
    never asked to be judged, and passing `None` there silently drops S3/S9
    down to a structural-only reading (`MeetsGrader` still grades `reply`
    correctly; it just never learns whether the reply was RIGHT).
    """
    cases = load_corpus(name)
    spec = Spec(
        database_url=database_url,
        entry=CORPUS_ENTRY[name],
        judge=judge if name in JUDGED_CORPORA else None,
    )
    experiment = Experiment(cases=cases, evaluators=[MeetsGrader()])
    return experiment, nora_task(spec)
