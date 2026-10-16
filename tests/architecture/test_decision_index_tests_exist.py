"""A decision row's enforcing test must exist, or the row must say it doesn't.

`design/09-decision-index.md`'s last column names the test that enforces each
decision. That column is the whole point of the index — `AGENTS.md` sends you
here to answer "which test guards this?" — so the named test has to be real.

`RULE_TESTS`'s own gate cannot catch a row naming a missing test: it asserts
that every entry IN the registry collects, never that a rule claiming a test
HAS one. A row can therefore name a test that was deleted and stay green — the
decision-index form of "a declared metric with no call site is a lie".

**Rows that legitimately cite deleted tests are marked, not exempted by name.**
A row whose decision was deprecated with its code deleted carries a `DEPRECATED`
banner. That marking is what this test reads — a banner is a deliberate
statement that the row is a record rather than a live guarantee, which is
exactly the distinction the index's own deprecation convention makes. So the
check is not "the test exists" but "the test exists OR the row says it does
not", and adding a banner to silence a real regression would be a visible,
reviewable edit rather than a quiet one.

## The `::selector` extension

The file-level check matches only the **file path** and stops at `.py`. The
`::selector` half of a `file.py::test_name` citation is read separately, so a
row whose file exists but whose named `def`/`class` does not (a test renamed or
moved WITHIN a file that still exists) is caught too. A reproducible DERIVATION
is the deliverable here, not a headline count, because the count moves
depending on what a re-run counts as a citation at all.

`_resolved_selectors()` below is that derivation. It parses every
backtick-quoted `` `file.py::name` `` citation AND the bare `` `::name` ``
continuation form the index uses to cite several tests in one file without
repeating the path (the LAST file path named earlier in the same row), then
resolves `name` against the target file's AST — every `def`/`class` at any
nesting depth, so a bare method name still resolves even though this index
never uses the two-segment `Class::method` form, and a bare class name
(`` `file.py::SomeClass` ``) is legal on the same footing as a function.

Two citation shapes are NOT pytest node ids and are named here, explicitly, as
an accepted blind spot rather than silently matched or silently failed:

- **The registry form**, `` `tests/test_acceptance_rules.py::RULE_TESTS` ``:
  `RULE_TESTS` is a module-level dict, not a `def`/`class`, so an exact-name
  AST match can never see it. This gate accepts any selector that resolves to
  a module-level **assignment** target as this form and does not attempt to
  verify the DICT KEY it may also carry (`RULE_TESTS["R-29"]`) — that would
  need evaluating the dict, which is exactly the kind of thing a noisy gate
  warns against building a second version of.
- **The `-k`-style partial name**, e.g. `` `::test_resolve_identity_` `` (a
  trailing-underscore prefix of several real tests in `test_signal_adapter.py`)
  and `` `::test_obligation_7_` `` (a prefix of one long test name in
  `test_strands_api_contract.py`). A selector that is a proper PREFIX of at
  least one real `def`/`class` name in the file is accepted rather than
  flagged: it cannot resolve to nothing, because at least one real test starts
  with it, and pytest's own `-k` matches it the same way.

What is left after both blind spots and the file-level DEPRECATED/REVERSED/NOT
ENFORCED marking is a real regression list. The entries that need a product
judgement call this gate cannot make on its own (does a doctrine rewrite's
new, differently-shaped test still enforce an old row's exact clause; does a
narrowed successor test satisfy an old port-level claim) are pinned in
`_KNOWN_UNRESOLVED`, exactly like `_KNOWN_DANGLING` in
`test_doc_references_resolve.py`: a NEW unresolved selector fails the gate, a
fixed one must be dropped from the pin (checked below), and the pin itself is
this gate's reported finding rather than a guessed rewrite.

Positive control, same shape as the file-level test below: a selector that
really does resolve must still be seen to resolve, so a regex or AST change
that quietly stops matching anything cannot pass by finding nothing wrong.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "design" / "09-decision-index.md"

#: A row carrying one of these is asserting that it does NOT describe a live
#: guarantee. `DEPRECATED` is the index's own established convention (rows are
#: marked, never renumbered and never removed); `REVERSED` marks a decision
#: reversed by evidence and `NOT ENFORCED` one whose stated enforcement
#: mechanism never existed.
_UNENFORCED_MARKERS = ("DEPRECATED", "NOT ENFORCED", "REVERSED")

#: Matches a backticked path into `tests/`, stopping at the first character that
#: cannot appear in one. Deliberately narrow: the column also carries `::node`
#: selectors and prose, and over-matching here would produce a noisy gate that
#: cries wolf.
_TEST_PATH = re.compile(r"`(tests/[A-Za-z0-9_/]+\.py)")

#: Every backtick-quoted span, read whole so the selector logic below can
#: classify each one (a `file.py::name` citation, a bare `::name` continuation,
#: or something else entirely — prose, a config path, a migration id — which is
#: simply not a selector citation and is ignored).
_CITATION = re.compile(r"`([^`]+)`")

#: `file.py`, optionally followed by `::name` in the SAME backtick span.
_FILE_AND_SELECTOR = re.compile(r"^(tests/[A-Za-z0-9_/]+\.py)(::[A-Za-z0-9_]+)?")

#: A bare `::name` continuation — the index's own shorthand for "same file as
#: the last one named in this row".
_BARE_SELECTOR = re.compile(r"^(::[A-Za-z0-9_]+)")

_ROW_ID = re.compile(r"\|\s*((?:DEC|R|D|A)-?[0-9]+[a-z]?)\s*\|")

#: Rows whose stale selector needs a product judgement call this gate cannot
#: make from the code history alone — repointing or marking would be a guess
#: about which of several plausible successor tests (or none) now carries the
#: clause.
_KNOWN_UNRESOLVED: dict[tuple[str, str, str], str] = {
    (
        "R-33", "tests/test_vision.py",
        "test_the_vision_timeout_is_always_shorter_than_the_loop_budget",
    ): (
        "replaced (a5e0d747) by a parametrised AGGREGATE-budget test "
        "(`test_the_budget_of_vision_calls_together_stays_under_the_loop`) "
        "covering several calls together, not one — whether that subsumes "
        "R-33's single-call claim or narrows it is a product call"
    ),
    (
        "DEC-23", "tests/infra/test_alembic.py",
        "test_0003_adds_quoted_ts_without_disturbing_the_ts_desc_index",
    ): (
        "the SQLite-specific migration-trap test; deleted in e5baa538 "
        "('Postgres test isolation + rebuilt parity gate'). No 1:1 Postgres "
        "successor asserts this exact index-shape trap"
    ),
    (
        "DEC-23", "tests/test_prefill.py",
        "test_the_arrow_name_is_the_literal_label_the_parents_own_line_uses",
    ): (
        "deleted in e4fe9dfe when the ↳-arrow transcript format was replaced "
        "by XML tags (DEC-80 era); no successor names this specific "
        "byte-identical-label claim"
    ),
    (
        "DEC-29", "tests/test_doctrine_assembly.py",
        "test_the_capability_section_is_generated_and_sits_between_conduct_and_constraints",
    ): (
        "DEC-150 deleted the generated capability section itself (d799c027, "
        "'both hand-written catalogues die'); whether DEC-29's own claim "
        "still stands under a different mechanism is DEC-150's call, not "
        "this gate's"
    ),
    (
        "DEC-29", "tests/test_doctrine_content.py",
        "test_the_negative_space_split_by_kind_and_the_capability_claim_died",
    ): (
        "same DEC-150 capability-block removal (2254452e); the claim this "
        "test pinned (a negative-space split) has no confirmed successor"
    ),
    (
        "DEC-29", "tests/test_prefill.py",
        "test_the_generated_capability_block_names_exactly_the_tools_that_exist",
    ): (
        "same DEC-150 capability-block removal (d799c027)"
    ),
    (
        "DEC-29", "tests/test_composition.py",
        "test_the_doctrine_the_root_builds_carries_the_generated_capability",
    ): (
        "same DEC-150 capability-block removal (d799c027); the file's "
        "replacement test asserts the OPPOSITE "
        "('carries_no_capability_section'), which is DEC-150's finding to "
        "cite, not this row's to be silently repointed to"
    ),
    (
        "DEC-31", "tests/test_prefill.py",
        "test_the_arrow_quotes_the_parent_so_two_lines_from_one_author_are_told_apart",
    ): (
        "deleted with the ↳-arrow transcript format (e4fe9dfe); superseded "
        "by the XML-tag rendering DEC-80 introduced, with no confirmed "
        "successor test for this exact ambiguity claim"
    ),
    (
        "DEC-31", "tests/test_prefill.py",
        "test_the_snippet_cannot_forge_a_transcript_line_out_of_a_newline",
    ): (
        "same ↳-arrow deletion (e4fe9dfe); the newline-forgery claim may or "
        "may not still hold against the XML-tag renderer — unverified"
    ),
    (
        "DEC-36", "tests/unit/test_engagement_feedback.py",
        "test_noras_reply_never_reaches_any_peers_stream",
    ): (
        "deleted during the Honcho v2 rewrite (5120019c); the row's OTHER "
        "citation on the same line (`test_only_the_taggers_own_words_are_fed_"
        "under_the_taggers_peer`) was renamed forward in the same commit and "
        "is repointed in this index, but this one's replacement is a "
        "differently-scoped test (multi-message feed completeness), not a "
        "rename of this claim"
    ),
    (
        "DEC-43", "tests/infra/test_alembic.py",
        "test_0004_adds_media_json_without_disturbing_the_ts_desc_index",
    ): (
        "same SQLite-to-Postgres migration-test rebuild as DEC-23 (e5baa538)"
    ),
    (
        "DEC-43", "tests/infra/test_schema_parity.py",
        "test_media_json_is_the_last_declared_column",
    ): (
        "same rebuild (e5baa538); replaced by a parametrised "
        "`test_every_table_in_the_metadata_exists`, which checks a different "
        "property (table existence, not column order)"
    ),
    (
        "DEC-80", "tests/test_prefill.py",
        "test_the_arrow_uses_the_identity_name_on_both_lines",
    ): (
        "deleted with the ↳-arrow format itself (e4fe9dfe) — DEC-80's own "
        "row is about that format, and the format it pinned no longer exists "
        "in the renderer (XML tags replaced it)"
    ),
    (
        "DEC-88", "tests/test_doctrine_content.py",
        "test_conduct_says_what_earns_a_second_message_and_what_does_not",
    ): (
        "deleted, no replacement in the same hunk, when DEC-96 rewrote "
        "voice/conduct/constraints 'from scratch' as a portrait (2254452e); "
        "DEC-96's row documents the rewrite but does not enumerate which "
        "earlier clause each deleted test's claim maps to now"
    ),
    (
        "DEC-91", "tests/test_doctrine_content.py",
        "test_voice_names_the_generated_text_tells_it_exists_to_remove",
    ): (
        "same portrait rewrite (2254452e), same no-enumerated-successor gap"
    ),
    (
        "DEC-91", "tests/test_doctrine_content.py",
        "test_the_anti_hedge_rule_does_not_collide_with_saying_you_dont_know",
    ): (
        "same portrait rewrite (2254452e)"
    ),
    (
        "DEC-92", "tests/test_doctrine_content.py",
        "test_conduct_refuses_to_record_a_contradiction_as_a_fact",
    ): (
        "same portrait rewrite (2254452e); diff shows pure deletion (three "
        "blank lines), no replacement test in the same commit"
    ),
    (
        "DEC-94", "tests/test_doctrine_content.py",
        "test_voice_carries_the_groups_own_register_not_a_polite_gesture_at_it",
    ): (
        "same portrait rewrite (2254452e)"
    ),
    (
        "DEC-94", "tests/test_doctrine_content.py",
        "test_voice_separates_a_fixed_expletive_from_targeting_a_real_person",
    ): (
        "same portrait rewrite (2254452e)"
    ),
    (
        "DEC-95", "tests/test_doctrine_content.py",
        "test_voice_teaches_the_inversion_particle_not_just_a_vocabulary_list",
    ): (
        "same portrait rewrite (2254452e); no history hit at all for this "
        "exact name, suggesting it may never have resolved even when DEC-95 "
        "landed — needs the operator's memory, not just git log"
    ),
    (
        "DEC-95", "tests/test_doctrine_content.py",
        "test_voice_gates_the_target_not_the_topic",
    ): (
        "same as the sibling DEC-95 entries above"
    ),
    (
        "DEC-95", "tests/test_doctrine_content.py",
        "test_voice_keeps_the_in_trouble_limit_as_a_limit_not_an_aside",
    ): (
        "same as the sibling DEC-95 entries above"
    ),
    (
        "DEC-95", "tests/test_doctrine_content.py",
        "test_conduct_says_what_earns_a_second_message_and_what_does_not",
    ): (
        "duplicate of DEC-88's citation above (same deleted test, cited by "
        "two rows); same portrait-rewrite gap"
    ),
    (
        "DEC-96", "tests/test_doctrine_content.py",
        "test_voice_keeps_the_two_limits_that_are_about_aim_not_volume",
    ): (
        "DEC-96 is the row THAT DESCRIBES the portrait rewrite, and its own "
        "enforcing-test citation did not survive it — the most pointed "
        "instance of this whole gap"
    ),
    (
        "R-73", "tests/unit/test_debrief.py",
        "test_an_identifier_never_reaches_the_collector",
    ): (
        "file extensively rewritten when tracing moved to strands.telemetry "
        "(7f93934b / cd600f2f); no confirmed 1:1 successor found"
    ),
    (
        "R-73", "tests/unit/test_debrief.py",
        "test_a_coordinate_in_the_answer_never_reaches_the_collector",
    ): (
        "same strands.telemetry rewrite (7f93934b)"
    ),
    (
        "R-73", "tests/unit/test_debrief.py",
        "test_the_traced_prompt_is_the_prompt_the_model_was_actually_given",
    ): (
        "same rewrite (7f93934b); a similarly-named test survives with a "
        "changed signature (`store_verdict=None` argument added), so this "
        "may be a rename rather than a deletion — left pinned rather than "
        "guessed because the behavioural diff is nontrivial"
    ),
    **{
        ("DEC-142", "tests/unit/test_ingest_pipeline.py", name): (
            "the ~10 per-outcome tests DEC-142 named were consolidated into "
            "a table-driven suite (68539447): `_OUTCOMES` + `_all_drivers` "
            "plus generic tests (`test_no_ingest_exit_reaches_the_collector`, "
            "`test_every_exit_is_counted_with_its_outcome`, "
            "`test_every_declared_outcome_is_reachable`, "
            "`test_the_outcome_counter_carries_no_identifier`). Which "
            "generic test now carries which of the 10 original named claims "
            "is a mapping call, not a rename"
        )
        for name in (
            "test_an_ordinary_ambient_message_says_so_on_the_root",
            "test_a_tagged_message_is_recorded_as_engaged",
            "test_a_failed_audit_write_leaves_a_root_that_says_it_was_refused",
            "test_a_refused_sender_keeps_the_trigger_it_matched",
            "test_a_gate_that_degraded_closed_does_not_read_as_an_untagged_message",
            "test_the_root_says_what_arrived_and_whether_the_sender_is_known",
            "test_a_known_sender_is_reported_as_known",
            "test_every_exit_stamps_an_outcome_on_the_root",
            "test_an_ingest_that_crashes_ends_on_the_root_as_an_error",
            "test_no_identifier_reaches_the_collector",
            "test_one_ambient_message_is_exactly_one_trace",
        )
    },
}


def _rows() -> list[tuple[str, int, str]]:
    """Every table row of the index, as (identifier, line number, text)."""
    rows = []
    for number, line in enumerate(INDEX.read_text().splitlines(), start=1):
        if not line.startswith("|"):
            continue
        match = _ROW_ID.match(line)
        rows.append((match.group(1) if match else "?", number, line))
    return rows


def _is_marked(line: str) -> bool:
    """Whether a row asserts its own guarantee is not live.

    «DEPRECATED IN PART» retires ONE clause; the rest of the row still stands
    and still names its enforcing tests, so it must not excuse the whole row
    the way a bare DEPRECATED does.
    """
    unexcused = line.replace("DEPRECATED IN PART", "")
    return any(marker in unexcused for marker in _UNENFORCED_MARKERS)


def _unmarked_rows_citing_a_missing_test() -> list[str]:
    """Rows naming a test that is not on disk, without saying the guarantee is gone."""
    found = []
    for identifier, number, line in _rows():
        missing = sorted(
            {path for path in _TEST_PATH.findall(line) if not (ROOT / path).exists()}
        )
        if missing and not _is_marked(line):
            found.append(f"{identifier} (line {number}): {', '.join(missing)}")
    return found


def test_every_row_naming_an_enforcing_test_either_has_it_or_says_it_does_not() -> None:
    unmarked = _unmarked_rows_citing_a_missing_test()
    assert not unmarked, (
        "decision-index rows name an enforcing test that does not exist, and do "
        "not say so. Either repoint the row at where the test actually lives, or "
        "mark it DEPRECATED/REVERSED/NOT ENFORCED if the guarantee is genuinely "
        "gone:\n  " + "\n  ".join(unmarked)
    )


def test_the_index_still_cites_tests_at_all() -> None:
    """Positive control for the test above.

    `test_every_row_naming_an_enforcing_test...` passes trivially if the regex
    stops matching — a column reformat, a renamed directory, an index that no
    longer names tests at all. This asserts the scan still sees a large body of
    citations, so a zero-finding result means "nothing is broken" rather than
    "nothing was looked at".
    """
    cited = {path for _, _, line in _rows() for path in _TEST_PATH.findall(line)}
    assert len(cited) > 150, (
        f"only {len(cited)} test citations found in the decision index — the "
        "matcher has probably stopped seeing the column it is meant to read"
    )


def test_a_row_citing_a_deleted_test_is_caught() -> None:
    """The detector fires on a genuine regression."""
    row = (
        "| DEC-999 | A decision nobody enforces. | `design/00-overview.md` | "
        "`tests/test_this_file_was_deleted_long_ago.py` |"
    )
    missing = [path for path in _TEST_PATH.findall(row) if not (ROOT / path).exists()]
    assert missing == ["tests/test_this_file_was_deleted_long_ago.py"]
    assert not any(marker in row for marker in _UNENFORCED_MARKERS)


def test_a_marked_row_citing_a_deleted_test_is_allowed() -> None:
    """The deprecation convention is honoured rather than special-cased by name.

    This is the shape of a deprecated tier's rows: the test is genuinely
    gone, and the row says so.
    """
    row = (
        "| DEC-998 | **DEPRECATED 2026-09-07 — the tier this decides was deleted "
        "(issue #115).** | `design/00-overview.md` | `tests/infra/test_fact_schema.py` |"
    )
    missing = [path for path in _TEST_PATH.findall(row) if not (ROOT / path).exists()]
    assert missing, "the fixture must cite a genuinely absent test to be meaningful"
    assert any(marker in row for marker in _UNENFORCED_MARKERS)


def test_a_sub_lettered_row_is_read_as_its_own_identifier() -> None:
    """`R-6b` is not `R-6`.

    The same false positive that made the duplicate-number check untrustworthy
    would misattribute a finding here to the wrong rule, which is worse than
    useless in a message a reader is meant to act on.
    """
    row = "| R-6b | Something. | `design/00-overview.md` | `tests/test_guards.py` |"
    match = _ROW_ID.match(row)
    assert match is not None
    assert match.group(1) == "R-6b"


def test_a_row_deprecated_only_in_part_is_still_held_to_its_tests() -> None:
    """The partial marker must not switch the row off: the clauses that stand
    are still enforced by the tests the row names."""
    row = (
        "| DEC-999 | **DEPRECATED IN PART 2026-09-24 — clause (3) superseded.** "
        "The rest stands. | `tests/test_this_file_was_deleted_long_ago.py` |"
    )
    unexcused = row.replace("DEPRECATED IN PART", "")
    assert not any(marker in unexcused for marker in _UNENFORCED_MARKERS)


# ---------------------------------------------------------------------------
# The `::selector` extension.
# ---------------------------------------------------------------------------


_defs_cache: dict[Path, tuple[set[str], set[str]] | None] = {}


def _defs_and_assigned(path: Path) -> tuple[set[str], set[str]] | None:
    """`(def/class names at any depth, module-level assignment targets)`.

    `None` if the file does not exist or is not valid Python — either way,
    nothing here can resolve, and the caller treats that as "no selector in
    this file resolves" rather than crashing the gate on a syntax error in a
    file this test does not own.
    """
    if path in _defs_cache:
        return _defs_cache[path]
    if not path.exists():
        _defs_cache[path] = None
        return None
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        _defs_cache[path] = (set(), set())
        return _defs_cache[path]
    names: set[str] = set()
    assigned: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            assigned.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assigned.add(node.target.id)
    _defs_cache[path] = (names, assigned)
    return _defs_cache[path]


def _selector_citations(line: str) -> list[tuple[str, str]]:
    """Every `(file, selector)` pair a row's `::` citations name.

    A bare `` `::name` `` continues the LAST file path named earlier in the
    same row — the index's own shorthand for citing several tests in one file
    without repeating the path.
    """
    out: list[tuple[str, str]] = []
    last_file: str | None = None
    for raw in _CITATION.findall(line):
        m = _FILE_AND_SELECTOR.match(raw)
        if m:
            last_file = m.group(1)
            if m.group(2):
                out.append((last_file, m.group(2)))
            continue
        m2 = _BARE_SELECTOR.match(raw)
        if m2 and last_file:
            out.append((last_file, m2.group(1)))
    return out


def _resolve(file_part: str, selector: str) -> str:
    """`"resolved"` / `"registry"` / `"partial"` / `"missing_file"` / `"unresolved"`."""
    name = selector[2:]  # strip leading '::'
    d = _defs_and_assigned(ROOT / file_part)
    if d is None:
        return "missing_file"
    names, assigned = d
    if name in names:
        return "resolved"
    if name in assigned:
        return "registry"
    if any(n.startswith(name) for n in names):
        return "partial"
    return "unresolved"


#: Every bucket a `::selector` citation can land in, in the order the census
#: line reports them. `total` is not a bucket a citation lands in — it is the
#: sum of the other seven, asserted below so the census can never silently
#: stop accounting for a citation.
_CENSUS_BUCKETS = (
    "resolved", "registry", "partial", "missing_file",
    "marked_skip", "pinned", "unresolved",
)


def _census() -> tuple[dict[str, int], list[str]]:
    """Every `::selector` citation, sorted into exactly one bucket.

    - **resolved** — a real `def`/`class` in the cited file.
    - **registry** — a module-level assignment (`RULE_TESTS`), the named
      registry-form blind spot.
    - **partial** — a proper prefix of a real `def`/`class`, the named
      `-k`-style blind spot.
    - **missing_file** — the cited FILE does not exist; this is
      `test_every_row_naming_an_enforcing_test_either_has_it_or_says_it_does_not`'s
      finding, not this census's, so it is counted but not re-reported below.
    - **marked_skip** — the selector resolves to nothing in an existing file,
      but the ROW says its guarantee is not live (DEPRECATED/REVERSED/NOT
      ENFORCED), so the gap is a declared record, not a regression.
    - **pinned** — unresolved, the row is not marked, but the triple is in
      `_KNOWN_UNRESOLVED` with a stated reason.
    - **unresolved** — none of the above: a real, unreported regression.

    Every citation lands in exactly one bucket, so `sum(counts.values()) ==
    total` always holds — asserted by the caller, not trusted here.
    """
    counts = dict.fromkeys(_CENSUS_BUCKETS, 0)
    unresolved: list[str] = []
    for identifier, number, line in _rows():
        marked = _is_marked(line)
        for file_part, selector in _selector_citations(line):
            outcome = _resolve(file_part, selector)
            if outcome in ("resolved", "registry", "partial", "missing_file"):
                counts[outcome] += 1
                continue
            # outcome == "unresolved" from here down.
            if marked:
                counts["marked_skip"] += 1
                continue
            key = (identifier, file_part, selector[2:])
            if key in _KNOWN_UNRESOLVED:
                counts["pinned"] += 1
                continue
            counts["unresolved"] += 1
            unresolved.append(f"{identifier} (line {number}): {file_part}::{selector[2:]}")
    return counts, unresolved


def _census_line(counts: dict[str, int]) -> str:
    total = sum(counts.values())
    parts = " ".join(f"{bucket}={counts[bucket]}" for bucket in _CENSUS_BUCKETS)
    return f"total={total} {parts}"


def test_every_selector_a_row_names_resolves_to_a_def_or_class_in_its_file() -> None:
    counts, unresolved = _census()
    print(_census_line(counts))
    assert sum(counts.values()) == sum(
        len(_selector_citations(line)) for _, _, line in _rows()
    ), "a `::selector` citation was not sorted into exactly one census bucket"
    assert not unresolved, (
        "decision-index rows name a `::selector` with no matching def/class in "
        "the cited file (the file itself exists — this is a rename or move "
        "WITHIN it, or a genuinely deleted test). Repoint the row, or add it to "
        "_KNOWN_UNRESOLVED with the reason if it needs a judgement call:\n  "
        + "\n  ".join(unresolved) + "\n  " + _census_line(counts)
    )


def test_the_selector_scan_still_resolves_real_selectors() -> None:
    """Positive control, same shape as `test_the_index_still_cites_tests_at_all`.

    The unresolved-selector test above passes trivially if the parser or the
    AST match stops working — this asserts it is still actually resolving a
    large body of real citations, not finding zero because it looked at
    nothing.
    """
    counts, _ = _census()
    assert counts["resolved"] > 400, (
        f"only {counts['resolved']} `::selector` citations resolved to a real "
        "def/class — the parser or the AST match has probably broken "
        f"({_census_line(counts)})"
    )


def _stale_pins(
    rows: list[tuple[str, int, str]], known: dict[tuple[str, str, str], str]
) -> dict[tuple[str, str, str], str]:
    """`_KNOWN_UNRESOLVED` entries that no longer need pinning.

    A pin goes stale TWO ways, and the first one is not "dropped from the
    row" — `test_doc_references_resolve.py`'s `_KNOWN_DANGLING` only has the
    second, because a dangling PATH cannot itself start resolving without the
    citation changing. A `::selector` can: the row citing it is untouched, but
    the test it names gets rewritten back under that name (re-added, restored
    from a revert, an independent rename that happens to land on it) — the
    citation is still LIVE, but `_resolve` now says "resolved". Missing
    either check reopens the exact hole `test_known_unresolved_selectors_
    are_still_unresolved` exists to close: a pin that never re-checks
    `_resolve` stays pinned forever, even after the work it recorded is done.

    - **No longer cited**: the (row, file, selector) triple is not among
      today's citations at all — the row was fixed or renumbered.
    - **Now resolves**: still cited, but `_resolve` returns `"resolved"` —
      the def/class the pin said was missing exists again.
    """
    live: dict[tuple[str, str, str], tuple[str, str]] = {}
    for identifier, _number, line in rows:
        for file_part, selector in _selector_citations(line):
            live[(identifier, file_part, selector[2:])] = (file_part, selector)
    stale = {}
    for key, reason in known.items():
        if key not in live:
            stale[key] = reason
            continue
        file_part, selector = live[key]
        if _resolve(file_part, selector) == "resolved":
            stale[key] = reason
    return stale


def test_known_unresolved_selectors_are_still_unresolved() -> None:
    """Every `_KNOWN_UNRESOLVED` pin must still reproduce, or be dropped.

    Mirrors `test_doc_references_resolve.py`'s stale-pin check: a pin that
    silently stopped reproducing (the row was fixed, or the selector now
    resolves) is exactly the kind of finding this gate exists to surface, and
    leaving it pinned would hide that the work is done.
    """
    stale = _stale_pins(_rows(), _KNOWN_UNRESOLVED)
    assert not stale, (
        "_KNOWN_UNRESOLVED entr(y/ies) that no longer need pinning — either "
        "the (row, file, selector) no longer appears in the index, or the "
        f"selector now resolves. Drop them: {sorted(stale)}"
    )


def test_a_pin_whose_selector_now_resolves_is_flagged_stale(tmp_path, monkeypatch) -> None:
    """A pin that never re-runs `_resolve` stays pinned even after its function
    is re-added, restored, or renamed back onto the exact name the pin recorded
    as missing."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_fixture_module.py").write_text(
        "def test_the_thing_that_came_back():\n    pass\n"
    )
    monkeypatch.setattr(
        "tests.architecture.test_decision_index_tests_exist.ROOT", tmp_path
    )
    row = (
        "| DEC-999 | Statement. | "
        "`tests/test_fixture_module.py::test_the_thing_that_came_back` |"
    )
    rows = [("DEC-999", 1, row)]
    known = {
        ("DEC-999", "tests/test_fixture_module.py", "test_the_thing_that_came_back"): (
            "was deleted; pinned 2026-01-01"
        ),
    }
    stale = _stale_pins(rows, known)
    assert list(stale) == [
        ("DEC-999", "tests/test_fixture_module.py", "test_the_thing_that_came_back")
    ]


def test_a_pin_still_unresolved_and_still_cited_is_not_flagged_stale(
    tmp_path, monkeypatch
) -> None:
    """The negative case: a pin whose selector is still cited and still does
    NOT resolve must not be reported as stale."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_fixture_module.py").write_text(
        "def test_something_unrelated():\n    pass\n"
    )
    monkeypatch.setattr(
        "tests.architecture.test_decision_index_tests_exist.ROOT", tmp_path
    )
    row = "| DEC-999 | Statement. | `tests/test_fixture_module.py::test_still_gone` |"
    rows = [("DEC-999", 1, row)]
    known = {
        ("DEC-999", "tests/test_fixture_module.py", "test_still_gone"): (
            "still deleted; pinned 2026-01-01"
        ),
    }
    assert _stale_pins(rows, known) == {}


def test_a_bare_selector_continues_the_last_file_named_on_the_row() -> None:
    row = "| R-1 | Statement. | `tests/test_a.py::test_one`, `::test_two` |"
    assert _selector_citations(row) == [
        ("tests/test_a.py", "::test_one"),
        ("tests/test_a.py", "::test_two"),
    ]


def test_a_class_selector_resolves_like_a_function_selector(tmp_path, monkeypatch) -> None:
    target = tmp_path / "test_fixture_module.py"
    target.write_text("class SomeClass:\n    def test_method(self):\n        pass\n")
    monkeypatch.setattr(
        "tests.architecture.test_decision_index_tests_exist.ROOT", tmp_path
    )
    assert _resolve("test_fixture_module.py", "::SomeClass") == "resolved"
    assert _resolve("test_fixture_module.py", "::test_method") == "resolved"


def test_a_registry_form_selector_is_a_named_blind_spot_not_a_failure(
    tmp_path, monkeypatch
) -> None:
    target = tmp_path / "test_fixture_module.py"
    target.write_text("RULE_TESTS = {'R-1': 'test_something'}\n")
    monkeypatch.setattr(
        "tests.architecture.test_decision_index_tests_exist.ROOT", tmp_path
    )
    assert _resolve("test_fixture_module.py", "::RULE_TESTS") == "registry"


def test_a_k_style_partial_name_is_a_named_blind_spot_not_a_failure(tmp_path, monkeypatch) -> None:
    target = tmp_path / "test_fixture_module.py"
    target.write_text("def test_resolve_identity_always_includes_the_primary():\n    pass\n")
    monkeypatch.setattr(
        "tests.architecture.test_decision_index_tests_exist.ROOT", tmp_path
    )
    assert _resolve("test_fixture_module.py", "::test_resolve_identity_") == "partial"


def test_a_selector_naming_nothing_in_the_file_is_unresolved(tmp_path, monkeypatch) -> None:
    target = tmp_path / "test_fixture_module.py"
    target.write_text("def test_something_else():\n    pass\n")
    monkeypatch.setattr(
        "tests.architecture.test_decision_index_tests_exist.ROOT", tmp_path
    )
    assert _resolve("test_fixture_module.py", "::test_nothing_named_this") == "unresolved"
