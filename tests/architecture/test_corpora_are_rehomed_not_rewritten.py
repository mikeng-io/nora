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

from evals.rebuilt.runner import substitute_trigger

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

#: Rows whose SPEAKER the move deliberately corrected, by corpus path and case,
#: as `{row index: {field: value}}`.
#:
#: Declared, not merely allowed. The old driver pasted an UNATTRIBUTED block
#: ("Prior messages in this group chat:", no names), so a line's role was
#: inferable only from its wording. The re-homed corpus attributed every bare
#: string to Miles — which is not neutral, it is a stronger and CONTRADICTING
#: signal. R-83 needs the checked claim to be HERS; attributed to Miles, the case
#: became Miles contradicting Miles.
#:
#: `is_nora` rather than `name: Nora`, because `nora.agent.prefill._speaker`
#: reads the FLAG and a row merely named Nora renders as an ordinary member. So
#: the new layer is closer to the deployment than the driver it replaces.
_DECLARED_ATTRIBUTION: dict[str, dict[str, dict[int, dict]]] = {
    "evals/corpora/being_contradicted.yaml": {
        name: {1: {"is_nora": True}}
        for name in (
            "bare_assertion_does_not_move_a_checked_claim",
            "flat_denial_in_english_does_not_move_a_checked_claim",
            "a_bare_name_is_not_a_correction",
            "real_evidence_may_overturn",
            "a_claim_she_never_grounded_is_not_a_position_to_defend",
            "a_surface_of_her_own_is_not_described_because_somebody_named_it",
        )
    },
    "evals/corpora/source_scope.yaml": {
        name: {1: {"is_nora": True}}
        for name in (
            "scoped_source_silence_is_not_a_denial",
            "scoped_source_silence_stale_window_english",
            "source_that_actually_denies_may_overturn",
        )
    },
    "evals/corpora/identity_grounding.yaml": {
        name: {1: {"is_nora": True}}
        for name in (
            "identity_grounded_when_alias_is_known_rank2",
            "grounded_refusal_when_stat_is_missing",
            "grounded_correct_top_when_alias_stated",
        )
    },
    # A SECOND MEMBER going along with the injection, not her. "somebody else
    # in the room went along with it" is the shape the case is about, and one
    # speaker cannot carry it.
    "evals/corpora/transcript_injection.yaml": {
        "transcript_embedded_injection": {1: {"name": "Grant"}},
    },
}

#: Corpora whose `input:` was TEMPLATED with `{trigger}` after the move, with
#: the reason. The substitution is applied by `runner.substitute_trigger`, so
#: the loaded case is byte-identical to the old literal — what changed is that
#: the corpus no longer hardcodes a trigger the operator owns.
#:
#: `near_miss.yaml` was already templated and `no_trigger.yaml` was not, so the
#: fix stopped at a file boundary. MEASURED: eight of those sixteen cases
#: passed with both of `nora/tag.py`'s matching rules deleted, four of them
#: because their input contained no trigger at all.
_TEMPLATED = {"evals/corpora/no_trigger.yaml"}

#: Cases whose INPUT was deliberately retargeted from the PLAIN trigger to the
#: corpus's configured one, by corpus and case name.
#:
#: These four contained `nora` while the corpus is configured with `nora.000`,
#: so `is_tagged` found nothing and the rule each is NAMED for was never
#: reached — four copies of `plain_no_mention` wearing four rule names.
#: MEASURED: they passed with both of `nora/tag.py`'s matching rules deleted.
#:
#: This is the one field the move may change on these cases, and only here.
_RETARGETED = {
    "evals/corpora/no_trigger.yaml": {
        "trailing_word_char_glued",       # norax   -> nora.000x
        "trailing_digit_glued",           # nora1   -> nora.0001
        "inside_a_code_span",             # `nora`  -> `nora.000`
        "name_inside_a_longer_word",      # Sonora  -> Sonora.000
    },
}

_GATE_SPEAKERS = ("Kit", "Sam", "Aaron")
_GATE_SPACING = 180


#: The trigger those corpora are configured with, so the pin compares the
#: SUBSTITUTED input — the thing the runner actually sends.
_TRIGGER = "nora.000"


def _is_a_source_line(body: str) -> bool:
    """A `[search result]` or data blob: a SOURCE she read and quoted back,
    not something a member said.

    `_build_judge_prompt`'s source block names both kinds explicitly — the
    window holds "what somebody SAID (including Nora's own earlier replies) and
    a SOURCE she read and quoted back". Attributing one to a named member is
    what R-83 calls a claim rather than evidence, on the corpora whose whole
    subject that is.
    """
    b = body.strip()
    return b.startswith("[search result]") or b.startswith("{")


def _apply_declared(new_path: str, name: str, rows: list) -> list:
    """The declared speaker corrections, applied to the OLD rows, so the
    comparison is against the corrected intent and every OTHER change still
    reddens."""
    declared = _DECLARED_ATTRIBUTION.get(new_path, {}).get(name, {})
    out = []
    for i, r in enumerate(rows):
        body = r["body"] if isinstance(r, dict) else r
        marks = dict(declared.get(i, {}))
        # A SOURCE line is hers BY RULE, so it needs no per-case declaration —
        # and a rule is what survives the next corpus.
        if _is_a_source_line(body):
            marks["is_nora"] = True
        if not marks and not isinstance(r, dict):
            out.append(r)
            continue
        row = dict(r) if isinstance(r, dict) else {"body": body}
        row.update(marks)
        out.append(row)
    return out


def _old_shape(raw: dict, new_path: str = "") -> dict:
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
        knows["transcript"] = _apply_declared(
            new_path, raw["name"], list(raw["transcript"])
        )
    if raw.get("standing"):
        knows["standing"] = raw["standing"]
    # `criteria:` is the OPT-IN LIST, and it lives in `metadata.criteria` now.
    #
    # It was briefly renamed to `expected_output:` on the reading that both
    # meant "what a good answer looks like". They do not: `criteria` is
    # `["source_scope"]`, a rubric AXIS to grade on, and the rename both made
    # `OPTIONAL_CRITERIA` unreachable and wrote a list into a prose field.
    # Aliasing them here is what let that pass — the pin encoded the rename
    # instead of questioning it.
    rubric = raw.get("expected_output") or ""
    return {
        "expect": raw.get("expect", ""),
        "sample": int(raw.get("sample", 1)),
        "category": raw.get("category", ""),
        "safety": bool(raw.get("safety", False)),
        "input": raw.get("input", ""),
        "knows": knows,
        "expected_output": rubric,
        "metadata": {
            **dict(raw.get("metadata") or {}),
            **({"criteria": raw["criteria"]} if raw.get("criteria") else {}),
        },
    }


def _new_shape(raw: dict, new_path: str = "") -> dict:
    return {
        "expect": raw["expect"],
        "sample": int(raw.get("sample", 1)),
        "category": raw.get("category", ""),
        "safety": bool(raw.get("safety", False)),
        # SUBSTITUTED, because that is what the runner sends. A corpus holding
        # `{trigger}` and an old one holding the literal are the same case; a
        # comparison of the raw strings would report a change that is not one.
        "input": (
            substitute_trigger(raw.get("input", ""), _TRIGGER)
            if new_path in _TEMPLATED else raw.get("input", "")
        ),
        "knows": dict(raw.get("knows") or {}),
        "expected_output": raw.get("expected_output", ""),
        "metadata": dict(raw.get("metadata") or {}),
    }


@pytest.mark.parametrize("old,new,entry", PAIRS, ids=lambda v: v if "/" not in str(v) else "")
def test_every_case_survived_unchanged(old: str, new: str, entry: str) -> None:
    before = {c["name"]: _old_shape(c, new) for c in _load(old)}
    after = {c["name"]: _new_shape(c, new) for c in _load(new)}

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

    retargeted = _RETARGETED.get(new, set())
    for name in retargeted:
        # The input is the ONE field allowed to differ on these, and only
        # because the old one did not contain the configured trigger at all.
        before[name] = {**before[name], "input": after[name]["input"]}

    # A case that now names a REAL moment (`snapshot:`) legitimately no longer
    # carries the authored content it was re-homed with — that IS the
    # conversion (design/evaluation/11-the-sandbox-rebuild.md). Its ASSERTION
    # must still be byte-identical, so `knows` drops out of the comparison for
    # those cases and NOTHING ELSE does: `expect`, `sample`, `category`,
    # `safety`, `input`, `expected_output` and `metadata` are still pinned.
    #
    # This is a REPLACEMENT of the old premise, not a widening of it. "Content
    # is re-homed unchanged" stopped being true the moment a case was allowed
    # to source its content from production; re-pointing the same assertion at
    # a smaller set would leave a pin that passes because it no longer looks.
    # What survives is the part that was always load-bearing: a label edited
    # during a move is a new case wearing the old name.
    converted = {c["name"] for c in _load(new) if c.get("snapshot")}
    for name in converted:
        before[name] = {k: v for k, v in before[name].items() if k != "knows"}
        after[name] = {k: v for k, v in after[name].items() if k != "knows"}

    differing = {n: (before[n], after[n]) for n in before if before[n] != after[n]}
    assert not differing, (
        f"{new}: {len(differing)} case(s) changed content while being re-homed: "
        f"{sorted(differing)}. Data, not rewrites — a label edited during a "
        "move is a new case wearing the old name."
    )


@pytest.mark.parametrize("old,new,entry", PAIRS, ids=lambda v: v if "/" not in str(v) else "")
def test_a_case_that_dropped_its_content_replaced_it_with_a_real_anchor(
    old: str, new: str, entry: str,
) -> None:
    """The exemption above, closed.

    `test_every_case_survived_unchanged` stops comparing `knows` for a case
    carrying `snapshot:`. On its own that is a hole: a case could DELETE its
    seeded content, state a `snapshot:`, and pass — the pin would report the
    move as clean while the case seeded nothing at all.

    So a case whose content left must have a COMPLETE anchor in its place.
    `culture_key` and `unit` especially: `nora.infra.sql.snapshot` refuses to
    derive them, and a wrong one reads exactly like a room that had no culture
    and no notebook.
    """
    required = {"platform", "conversation_id", "before_ms", "culture_key", "unit"}
    for case in _load(new):
        shot = dict(case.get("snapshot") or {})
        if not shot:
            continue
        assert not case.get("knows"), (
            f"{new}: {case['name']} states BOTH `snapshot:` and `knows:` — two "
            "sources for what she knew, with nothing making them agree."
        )
        missing = sorted(required - set(shot))
        assert not missing, (
            f"{new}: {case['name']} dropped its seeded content for a snapshot "
            f"missing {missing}. An incomplete anchor seeds an empty room and "
            "reads exactly like a case that was always empty."
        )


@pytest.mark.parametrize("old,new,entry", PAIRS, ids=lambda v: v if "/" not in str(v) else "")
def test_every_case_names_its_entry_point(old: str, new: str, entry: str) -> None:
    """The one thing that IS added. It is what replaces the eight drivers."""
    for case in _load(new):
        assert case.get("entry") == entry, (
            f"{new}: {case['name']} has entry={case.get('entry')!r}, expected "
            f"{entry!r}"
        )


#: Fields of a transcript ROW that reach a store and therefore reach the model.
#: The pin compares these; the audit's whole finding was that comparing YAML
#: SHAPE lets meaning change underneath an identical-looking file.
_ROW_FIELDS = ("body", "name", "person", "id", "ts", "is_nora", "media")


@pytest.mark.parametrize("old,new,entry", PAIRS, ids=lambda v: v if "/" not in str(v) else "")
def test_every_row_field_that_reaches_a_store_is_compared(
    old: str, new: str, entry: str,
) -> None:
    """The pin's own gap, closed.

    It compared `body` and `id` and nothing else, so `media: true` was dropped
    from five rows and it stayed green. Anything a row can say that a store can
    read is compared here, so the next dropped field reddens instead.
    """
    before = {c["name"]: c for c in _load(old)}
    for case in _load(new):
        b = before.get(case["name"])
        if b is None:
            continue                      # a declared addition; checked above
        old_rows = b.get("batch") or b.get("transcript") or (
            (b.get("seed") or {}).get("transcript")
        ) or []
        new_rows = (case.get("knows") or {}).get("transcript") or []
        for i, orow in enumerate(old_rows):
            if not isinstance(orow, dict) or i >= len(new_rows):
                continue
            nrow = new_rows[i]
            nrow = nrow if isinstance(nrow, dict) else {"body": nrow}
            orow = {**orow, **_DECLARED_ATTRIBUTION.get(new, {}).get(
                case["name"], {}).get(i, {})}
            if _is_a_source_line(str(orow.get("body", ""))):
                orow = {**orow, "is_nora": True}
            for field in _ROW_FIELDS:
                if field in orow:
                    assert nrow.get(field) == orow[field], (
                        f"{new}: {case['name']} row {i} lost/changed {field!r}: "
                        f"{orow[field]!r} -> {nrow.get(field)!r}"
                    )


@pytest.mark.parametrize("old,new,entry", PAIRS, ids=lambda v: v if "/" not in str(v) else "")
def test_the_new_corpus_loads_through_the_runner(old: str, new: str, entry: str) -> None:
    """A corpus that the runner cannot parse is not data, it is a plan."""
    from evals.rebuilt.runner import load

    cases = load(ROOT / new)
    assert len(cases) == len(_load(old))


#: Header lines that are NO LONGER TRUE and must not be required to travel.
#:
#: `test_the_headers_travelled` makes retyped provenance impossible and, until
#: this list existed, made STALE provenance mandatory — it required every old
#: comment line to survive, including ones the move falsified. Three did, and an
#: audit found all three still being asserted in the new files.
#:
#: A line goes here with the reason it stopped being true, so the exemption is
#: an argument rather than a hole.
_NO_LONGER_TRUE = {
    # The placeholder IS substituted again (`runner.substitute_trigger`), but
    # this line named the OLD loader as the place it happened.
    "{trigger} IS SUBSTITUTED AT LOAD",
    # False since the move: the new path resolves a person, a display name and
    # a `<people_in_window>` block.
    "carries no structured sender attribution at all",
}


def test_the_headers_travelled() -> None:
    """The measured readings in a corpus header ARE provenance
    (`02-the-rebuild` §3), and provenance that is retyped is provenance that is
    invented. Checked as: the old header's every non-empty comment line is
    present in the new file, EXCEPT lines the move falsified — see
    `_NO_LONGER_TRUE`, which exists because this test used to require a corpus
    to keep asserting things that had stopped being true."""
    for old, new, _ in PAIRS:
        old_text = (ROOT / old).read_text(encoding="utf-8")
        new_text = (ROOT / new).read_text(encoding="utf-8")
        old_header = []
        for ln in old_text.splitlines():
            if ln.startswith("#") or not ln.strip():
                old_header.append(ln)
            else:
                break
        missing = [
            ln for ln in old_header
            if ln.strip() and ln not in new_text
            and not any(stale in ln for stale in _NO_LONGER_TRUE)
        ]
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

        # A case that now names a REAL moment sources its transcript and
        # notebook from production, so there is nothing here to compare them
        # to — that IS the conversion
        # (design/evaluation/11-the-sandbox-rebuild.md). `must_cite` moves with
        # them: it cited a corpus-local row id, and post-conversion it must
        # name the REAL row, or the citation check asserts against an id the
        # store never assigned.
        #
        # What still has to survive is everything that says what the case
        # ASSERTS — checked above and below this block, not skipped.
        if not after.get("snapshot"):
            knows = after.get("knows") or {}
            assert [m["body"] for m in knows.get("transcript") or []] == \
                [m["body"] for m in before.get("batch") or []], name
            assert [m.get("id") for m in knows.get("transcript") or []] == \
                [m.get("id") for m in before.get("batch") or []], name
            assert [n["title"] for n in knows.get("notes") or []] == \
                [n["title"] for n in before.get("notebook") or []], name

            # The assertions the notebook diff cannot yet carry. Parked, not
            # lost — a fact recorded once and then dropped has to be
            # re-derived by hand.
            meta = after.get("metadata") or {}
            for key in ("creates", "updates", "must_cite", "anchor_date"):
                if before.get(key) is not None:
                    assert meta.get(key) == before[key], f"{name}: {key}"

        # `unit:` (renamed from `workspace:`, `e2092ecd`) became two settings
        # because it is two facts: Honcho's OWN `workspace_id` and the
        # policy's `Conversation.unit`.
        ws = before.get("unit", "nora")
        assert after["config"]["memory.workspace_id"] == ws, name
        assert f'unit = "{ws}"' in after["policy"], name


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
        # `act` has its OWN entry point: the decide pass writes a reminder row
        # and the act turn is what happens when it comes due — a FIRE turn, not
        # a dream pass. That is why the old driver's `FirePath(21)` lived in
        # `run_proactive_act_case`.
        # `endswith`, not `in`: "proACTive_decide" contains "act".
        if new.endswith("proactive_act.yaml"):
            assert a["entry"] == "proactive_act", name
        else:
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
        # `rooms` and `standing` MOVED to `knows` when multi-room seeding
        # landed (GOAL step 1) — they are inputs, and parking an input in
        # metadata is what kept 22 cases refused. What stays in metadata is
        # what is NOT an input: `rows` is an expectation about what the pass
        # writes, `row` is the decision she already took.
        for key in ("rows", "row", "log", "max_memory_calls",
                    "must_cite", "must_not_cite", "note_moved"):
            if b.get(key) is not None:
                assert meta.get(key) == b[key], f"{name}: {key}"

        knows = a.get("knows") or {}
        if b.get("rooms"):
            seeded = {a["room"][1]} | {
                r["conversation_id"] for r in (knows.get("rooms") or ())
            }
            assert seeded == {r["conversation_id"] for r in b["rooms"]}, (
                f"{name}: rooms lost on the move: {seeded}"
            )
        if b.get("standing"):
            old_standing = b["standing"]
            if isinstance(old_standing, str):
                assert knows.get("standing") == old_standing, name
            else:
                bodies = {s["body"] for s in old_standing}
                seeded = {knows.get("standing")} | {
                    r.get("standing") for r in (knows.get("rooms") or ())
                }
                assert bodies <= seeded, f"{name}: standing lost: {bodies - seeded}"
        knows = a.get("knows") or {}
        assert [n["title"] for n in knows.get("notes") or []] == \
            [n["title"] for n in b.get("notes") or []], name
