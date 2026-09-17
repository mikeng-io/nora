"""`design/evaluation/02-the-rebuild.md` Step 3: the corpora are DATA, and data
that is re-homed must arrive unchanged.

"Re-homed, not rewritten" is a claim about two files, so it is checked against
both rather than asserted in a commit message. Every case's name, label, sample
count, category, safety flag, input and seed is compared field by field.

This test dies with the old corpora at Step 5, and that is correct: it exists
to make ONE move safe, not to be maintained forever.
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: `(old, new, entry)`. Only the entry points that EXIST are re-homed; the
#: remaining eleven corpora travel with their own entry point in Step 4,
#: because re-homing data for a port nothing can drive would be a move nothing
#: could check.
PAIRS = [
    ("evals/cases/relevance_gate/gate.yaml", "evals/corpora/relevance_gate.yaml", "gate"),
    ("evals/cases/participation/participation.yaml", "evals/corpora/participation.yaml",
     "participation"),
    # Step 4: the eight that were driven by `evals/run.py`'s bare Agent.
    ("evals/adversarial/injections.yaml", "evals/corpora/injections.yaml", "tag"),
    ("evals/adversarial/near_miss.yaml", "evals/corpora/near_miss.yaml", "tag"),
    ("evals/adversarial/transcript_injection.yaml",
     "evals/corpora/transcript_injection.yaml", "tag"),
    ("evals/adversarial/identity_grounding.yaml",
     "evals/corpora/identity_grounding.yaml", "tag"),
    ("evals/adversarial/source_scope.yaml", "evals/corpora/source_scope.yaml", "tag"),
    ("evals/adversarial/being_contradicted.yaml",
     "evals/corpora/being_contradicted.yaml", "tag"),
    ("evals/cases/trigger.yaml", "evals/corpora/trigger.yaml", "tag"),
    ("evals/cases/no_trigger.yaml", "evals/corpora/no_trigger.yaml", "tag"),
]


def _load(rel: str) -> list[dict]:
    """Both corpus shapes: a bare list, or a mapping with `cases:`."""
    doc = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8")) or []
    return doc.get("cases") or [] if isinstance(doc, dict) else doc


#: The rotation `eval_support._gate_records` applied to the gate corpus's bare
#: strings, and the step it spaced them by. The gate corpus now STATES both.
#:
#: Written here as well as in the corpus because this is the pin that failed to
#: catch the divergence and must be able to next time: the strings were
#: byte-identical, so "data, not rewrites" passed while the WINDOW the labels
#: were assigned against had silently changed from three speakers to one. Found
#: by running both layers live on the same case — `go` 5/5 against `no_go` 5/5.
#: Cases added to a corpus AFTER its move, by path. Declared rather than merely
#: allowed: a corpus is data, and a case that arrives without an argument is the
#: same drift as a label edited during a move.
_EXPECTED_ADDITIONS = {
    # From the 2026-09-17 live duplicate-reminder incident, via emperor-60. The
    # file's header carries the four constraints it must be ported with.
    "evals/corpora/proactive_act.yaml": ["a11_she_already_raised_this_minutes_ago"],
}

_GATE_SPEAKERS = ("Kit", "Sam", "Aaron")
_GATE_SPACING = 180


def _old_shape(raw: dict) -> dict:
    """The old shape, normalised. The gate stated its window at the top level;
    participation stated it under `seed:`. Both are `knows:` now.

    A bare transcript string is expanded to the speaker and spacing its driver
    gave it, because that is what the case MEANT. The two old drivers disagreed
    on the same syntax — the gate rotated three members 180s apart, participation
    gave every line to the one mapped member 60s apart — and neither corpus said
    so.
    """
    knows = dict(raw.get("seed") or {})
    if raw.get("transcript") and "category" in raw:
        # A gate case: `category` is the gate corpus's own field and no other
        # corpus carries it, so it is what tells the two apart here.
        knows["transcript"] = [
            {"body": b, "name": _GATE_SPEAKERS[i % 3], "person": None}
            for i, b in enumerate(raw["transcript"], 1)
        ]
        knows["spacing_seconds"] = _GATE_SPACING
    elif raw.get("transcript"):
        knows["transcript"] = list(raw["transcript"])
    if raw.get("standing"):
        knows["standing"] = raw["standing"]
    # `criteria:` -> `expected_output:`. The rename is encoded HERE, visibly,
    # rather than being waved through: two files said "what a good answer looks
    # like" under two names, and a judge should have one field to read.
    rubric = raw.get("expected_output") or raw.get("criteria") or ""
    return {
        "expect": raw.get("expect", ""),
        "sample": int(raw.get("sample", 1)),
        "category": raw.get("category", ""),
        "safety": bool(raw.get("safety", False)),
        "input": raw.get("input", ""),
        "knows": knows,
        "expected_output": rubric,
        "metadata": dict(raw.get("metadata") or {}),
    }


def _new_shape(raw: dict) -> dict:
    return {
        "expect": raw["expect"],
        "sample": int(raw.get("sample", 1)),
        "category": raw.get("category", ""),
        "safety": bool(raw.get("safety", False)),
        "input": raw.get("input", ""),
        "knows": dict(raw.get("knows") or {}),
        "expected_output": raw.get("expected_output", ""),
        "metadata": dict(raw.get("metadata") or {}),
    }


@pytest.mark.parametrize("old,new,entry", PAIRS, ids=lambda v: v if "/" not in str(v) else "")
def test_every_case_survived_unchanged(old: str, new: str, entry: str) -> None:
    before = {c["name"]: _old_shape(c) for c in _load(old)}
    after = {c["name"]: _new_shape(c) for c in _load(new)}

    assert set(before) == set(after), (
        f"{new}: case NAMES changed. added={sorted(set(after) - set(before))} "
        f"lost={sorted(set(before) - set(after))}. A case that quietly "
        "disappears takes its label with it and nothing reports the gap."
    )
    for name, shape in before.items():
        # `trigger.yaml` and `no_trigger.yaml` stated their expectation in a
        # comment and in `metadata.trigger`; the move writes it down as
        # `expect:`. That is the ONE field the move may add, and only where the
        # old file had none — a corpus whose label was never machine-readable
        # could not have been graded, which is why those two had no runner.
        if not shape["expect"]:
            shape["expect"] = after[name]["expect"]

    differing = {n: (before[n], after[n]) for n in before if before[n] != after[n]}
    assert not differing, (
        f"{new}: {len(differing)} case(s) changed content while being re-homed: "
        f"{sorted(differing)}. Data, not rewrites — a label edited during a "
        "move is a new case wearing the old name."
    )


@pytest.mark.parametrize("old,new,entry", PAIRS, ids=lambda v: v if "/" not in str(v) else "")
def test_every_case_names_its_entry_point(old: str, new: str, entry: str) -> None:
    """The one thing that IS added. It is what replaces the eight drivers."""
    for case in _load(new):
        assert case.get("entry") == entry, (
            f"{new}: {case['name']} has entry={case.get('entry')!r}, expected "
            f"{entry!r}"
        )


@pytest.mark.parametrize("old,new,entry", PAIRS, ids=lambda v: v if "/" not in str(v) else "")
def test_the_new_corpus_loads_through_the_runner(old: str, new: str, entry: str) -> None:
    """A corpus that the runner cannot parse is not data, it is a plan."""
    from evals.rebuilt.runner import load

    cases = load(ROOT / new)
    assert len(cases) == len(_load(old))


def test_the_headers_travelled() -> None:
    """The measured readings in a corpus header ARE provenance
    (`02-the-rebuild` §3), and provenance that is retyped is provenance that is
    invented. Checked as: the old header's every non-empty comment line is
    present in the new file."""
    for old, new, _ in PAIRS:
        old_text = (ROOT / old).read_text(encoding="utf-8")
        new_text = (ROOT / new).read_text(encoding="utf-8")
        old_header = []
        for ln in old_text.splitlines():
            if ln.startswith("#") or not ln.strip():
                old_header.append(ln)
            else:
                break
        missing = [ln for ln in old_header if ln.strip() and ln not in new_text]
        assert not missing, (
            f"{new} lost {len(missing)} line(s) of the provenance header, "
            f"first: {missing[0]!r}"
        )


def test_note_candidates_survived_its_deeper_reshape() -> None:
    """The one corpus whose move is not a rename.

    `batch:` -> `knows.transcript:` and `notebook:` -> `knows.notes:` change the
    SHAPE, because they stop being constructor arguments and become rows in the
    stores she reads. So the generic pin above cannot compare them field for
    field, and this checks the facts that must survive instead: every case, every
    message body in order, every message label, every seeded note title, and
    every ungraded assertion parked in `metadata`.
    """
    old = {c["name"]: c for c in _load(
        "evals/cases/note_candidates/note_candidates.yaml")}
    new = {c["name"]: c for c in _load("evals/corpora/note_candidates.yaml")}
    assert set(old) == set(new), sorted(set(old) ^ set(new))

    for name, before in old.items():
        after = new[name]
        assert after["expect"] == before["expect"], name
        assert after["entry"] == "dreamer" and after["category"] == "note-candidates"

        knows = after.get("knows") or {}
        assert [m["body"] for m in knows.get("transcript") or []] == \
            [m["body"] for m in before.get("batch") or []], name
        assert [m.get("id") for m in knows.get("transcript") or []] == \
            [m.get("id") for m in before.get("batch") or []], name
        assert [n["title"] for n in knows.get("notes") or []] == \
            [n["title"] for n in before.get("notebook") or []], name

        # The assertions the notebook diff cannot yet carry. Parked, not lost —
        # a fact recorded once and then dropped has to be re-derived by hand.
        meta = after.get("metadata") or {}
        for key in ("creates", "updates", "must_cite", "anchor_date"):
            if before.get(key) is not None:
                assert meta.get(key) == before[key], f"{name}: {key}"

        # `workspace:` became two settings because it is two facts.
        ws = before.get("workspace", "nora")
        assert after["config"]["memory.workspace_id"] == ws, name
        assert f'workspace = "{ws}"' in after["policy"], name


@pytest.mark.parametrize("old,new", [
    ("evals/cases/proactive/decide.yaml", "evals/corpora/proactive_decide.yaml"),
    ("evals/cases/proactive/act.yaml", "evals/corpora/proactive_act.yaml"),
])
def test_the_proactive_corpora_survived_their_move(old: str, new: str) -> None:
    """Re-homed, and — since the clock seam landed — runnable.

    These were REFUSED for half a day: every case states a `now:` and the
    composed graph had no way to be told the time. `build_dependencies` now
    takes a `clock` and `SubjectSpec.now` sets it, so `now` moved from
    `metadata:` to a field the runner reads.

    What this checks is that nothing was lost across either move — the parking
    or the unparking. A corpus is only worth parking if it is still the corpus
    when it comes back.
    """
    before = {c["name"]: c for c in _load(old)}
    after = {c["name"]: c for c in _load(new)}

    # NOTHING LOST is the property; nothing ADDED is not. The first draft
    # asserted equality and reddened when a new case landed, which would have
    # made the corpus unextendable to keep it faithful — the wrong trade, and
    # the opposite of what a corpus is for. A case that vanishes takes its label
    # with it and nothing reports the gap; a case that arrives is visible in the
    # file.
    assert not set(before) - set(after), (
        f"{new} LOST cases: {sorted(set(before) - set(after))}"
    )
    added = sorted(set(after) - set(before))
    assert added == _EXPECTED_ADDITIONS.get(new, []), (
        f"{new} gained {added}; declare it in _EXPECTED_ADDITIONS with where it "
        "came from, so a case nobody argued for cannot drift in."
    )

    for name, b in before.items():
        a = after[name]
        assert a["entry"] == "dreamer" and a["category"] == "proactive", name
        meta = a.get("metadata") or {}
        # `now:` is a FIRST-CLASS field now, not metadata. It sat in metadata
        # for as long as the graph had no clock seam and these cases were
        # refused; `build_dependencies(cfg, clock=...)` closed that, so the
        # field moved to where the runner reads it. The value must be identical
        # either way — it is the instant the labels were assigned against.
        assert a["now"] == b["now"], name
        assert "now" not in meta, (
            f"{name}: `now` is in metadata AND a field. One fact, one place — "
            "two is how they drift."
        )
        # The corpus's own expectation vocabulary — `remind`/`none`/`silent`/
        # `either` — is mapped onto the shared one, so the ORIGINAL is carried
        # rather than replaced. `either` in particular has no shared spelling
        # and a mapping that dropped it would silently harden a deliberately
        # permissive label.
        assert meta["expect_original"] == b["expect"], name
        for key in ("rooms", "rows", "row", "standing", "log", "max_memory_calls",
                    "must_cite", "must_not_cite", "note_moved"):
            if b.get(key) is not None:
                assert meta.get(key) == b[key], f"{name}: {key}"
        knows = a.get("knows") or {}
        assert [n["title"] for n in knows.get("notes") or []] == \
            [n["title"] for n in b.get("notes") or []], name
