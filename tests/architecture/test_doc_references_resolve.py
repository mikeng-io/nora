"""Docs cite repo paths inline in backticks — `docs/design/decisions.md`,
`nora/agent/tools.py` — and a doc move (such as the ADRs moving from
`docs/design/adr/` to `docs/adr/`) leaves every backticked citation of the old
location dangling silently, because a doc move is not a `grep` anyone runs by
habit. This gate checks that each cited path still exists.

**The one deliberate, large-scale exception is `docs/superpowers/` (and
`.superpowers/`).** AGENTS.md's reading-order section states that the
superpowers process was removed and its historical citations "resolve only in
git history" — they are kept ON PURPOSE as provenance for *why*, not as live
pointers, and rewriting them would erase the history the reading order says to
go read. This gate exempts that tree by name, in both directions: a citation
FROM a file under it, and a citation OF a path under it from anywhere else.

**Conservative on purpose.** A noisy gate is a disabled gate, and an unscoped
sweep of every backticked, slash-containing token in every tracked `.md` file
produces mostly non-defects: version-like numbers and IPs (no extension), bare
relative mentions inside a file already scoped to that subtree
(`agent/guards.py` inside a doc under `docs/design/platform/whatsapp.md`), and — the
largest category — prose that talks ABOUT a deleted or not-yet-built file
rather than pointing at a live one. Each tightening below is a STRUCTURAL
signal already present in this repo's own conventions, not a per-path guess:

  - Only a reference anchored at a real top-level directory (`nora/`,
    `docs/design/`, `docs/`, `tests/`, `evals/`, `deploy/`, `scripts/`,
    `.github/`, `.agents/`) or a known root file counts as a path
    CLAIM. A bare `doctrine/identity.md` is prose shorthand for a concept
    ("her doctrine"), not a root-relative path — `doctrine/` is not itself a
    top-level directory (the real files live under `nora/doctrine/` and
    `nora/dreamer/doctrine/`), so nothing anchored there is checked.
  - A path that is itself under `docs/superpowers/` or `.superpowers/` is
    never checked, from anywhere (the exemption above).
  - A file whose own first ~30 lines carry a `> **DEPRECATED` banner is a
    self-declared historical record — CLAUDE.md states this is how a deprecated
    design node is marked — and is skipped whole, the same reasoning as the
    superpowers exemption one level down from a directory to a single file. A
    `PARTLY DEPRECATED` banner does not qualify (see `_DEPRECATED_BANNER`).
  - A PARAGRAPH (not just the one line the path sits on) containing the word
    "RETIRED", "DEPRECATED", "SUPERSEDED", or "deleted", or the phrase
    "resolves in git history", is read as the same kind of deliberate
    historical citation the superpowers exemption already covers; an ADR is
    such a record and is never rewritten.
  - A path that is a LITERAL (non-glob) entry in `.gitignore` is a generated
    artifact, not a moved one — `nora/version.json` is built at image time and
    is `unavailable` by design in a source checkout.

What survives all of that and still does not resolve is pinned in
`_KNOWN_DANGLING`, exactly like `_ALLOWED` / `_UNRESOLVABLE` elsewhere in this
directory: a NEW dangling reference fails the gate, a fixed one must be
dropped from the pin (checked below), and the pinned list itself is this
gate's actual finding — drift reported to the operator rather than
mass-rewritten (some of it is a design brief predating the code it describes,
not a broken link to fix by editing prose).

Positive control: a synthetic reference to a path that does not exist must be
flagged; the same reference once the file exists must not be; a reference
under `docs/superpowers/` must never be flagged even though nothing there
resolves; and a paragraph carrying "DEPRECATED" must suppress a reference that
would otherwise be flagged.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from scripts.check_documents import STAGED_TARGETS

_PRE_MOVE = (
    "design/", "docs/maintainers/", "docs/runbook.md", "docs/configuration.md",
    "docs/observability.md", "docs/code-standards.md", "docs/eval-sop.md",
    "ORCHESTRATION.md", "REVIEW.md", "SECURITY_MODEL.md", "DESIGN.md",
)

ROOT = Path(__file__).resolve().parents[2]


# Only these anchor a backticked token as a repo-root-relative path CLAIM.
# `doctrine/` is deliberately absent — see the module docstring.
_CHECKED_PREFIXES = (
    "design/",
    "docs/",
    "nora/",
    "tests/",
    "evals/",
    "deploy/",
    "scripts/",
    ".github/",
    ".agents/",
)
#
# `config.example.toml` is deliberately NOT in this set even though it is a
# real, checkable root file: naming it here as a module-level string constant
# would itself be flagged by test_config_comes_from_the_template.py's R-B gate
# ("a module-level constant naming config.toml or config.example.toml"),
# which exists precisely so that string is never written at module scope
# outside the fixtures it governs. It always exists in this repo regardless.
_CHECKED_ROOT_FILES = frozenset(
    {
        "AGENTS.md",
        "CLAUDE.md",
        "README.md",
        "Makefile",
        "pyproject.toml",
        "identity.example.toml",
        "policy.example.toml",
    }
)

# A path under either of these is never checked, from anywhere: the
# superpowers exemption AGENTS.md names explicitly.
_EXEMPT_TARGET_PREFIXES = ("docs/superpowers/", ".superpowers/")

# A DOC under any of these is never scanned — it is itself historical
# provenance, or it is explicitly describing something not yet built.
_EXEMPT_SOURCE_PREFIXES = ("docs/superpowers/", ".superpowers/")

# A paragraph carrying any of these is read as a deliberate historical
# citation (a RETIRED/DEPRECATED/SUPERSEDED decision-index row, or prose
# stating outright that the file "deleted" / "resolves in git history only").
_HISTORICAL_MARKERS = ("RETIRED", "DEPRECATED", "SUPERSEDED", "deleted", "resolves in git history")

#: A whole-file historical record. **`PARTLY DEPRECATED` is deliberately NOT
#: here.** A "partly" banner says one tier or section of the file is history and
#: the rest still stands, so a whole-file exemption would leave the live
#: sections unguarded. Paragraph-level markers (`_HISTORICAL_MARKERS`) carry the
#: partly-deprecated case instead, which is the right grain: the banner scopes
#: itself in prose, so the check should too.
_DEPRECATED_BANNER = re.compile(r">\s*\*\*DEPRECATED")

_LIST_ITEM = re.compile(r"\s*(?:[-*+]|\d+[.)])\s")

_PATH_TOKEN = re.compile(
    r"`([A-Za-z0-9_./-]+/[A-Za-z0-9_./-]+\.[A-Za-z0-9]+)"
    r"(?:::[\w.]+(?:::[\w.]+)*|:\d+(?:[-,:]\d+)*)?`"
)


def _is_checked_path(path: str) -> bool:
    if path.startswith(_EXEMPT_TARGET_PREFIXES):
        return False
    return path.startswith(_CHECKED_PREFIXES) or path in _CHECKED_ROOT_FILES


def _paragraphs(markdown: str) -> list[tuple[int, list[str]]]:
    """`(first line number, lines)` for each blank-line-delimited paragraph —
    and each TABLE ROW on its own.

    A table row is one record, not a sentence of a paragraph. Treated as one
    blank-line-delimited block, a whole table would be one «paragraph», and a
    historical marker on ONE row would exempt every other row in it.

    A LIST ITEM starts a new paragraph for the same reason: an unbroken
    bulleted list would be one block, and "deleted" in one bullet would exempt
    the whole list. Continuation lines (indented, not themselves a bullet) stay
    with their item."""
    paragraphs: list[tuple[int, list[str]]] = []
    current: list[str] = []
    start = 1
    for lineno, line in enumerate(markdown.splitlines(), 1):
        if _LIST_ITEM.match(line) and current:
            paragraphs.append((start, current))
            current = []
            start = lineno
        if line.lstrip().startswith("|"):
            if current:
                paragraphs.append((start, current))
            paragraphs.append((lineno, [line]))
            current = []
            start = lineno + 1
            continue
        if line.strip() == "":
            if current:
                paragraphs.append((start, current))
            current = []
            start = lineno + 1
        else:
            current.append(line)
    if current:
        paragraphs.append((start, current))
    return paragraphs


def is_deprecated_file(markdown: str) -> bool:
    """A `> **DEPRECATED` banner in the file's first 30 lines marks the WHOLE
    file as a self-declared historical record — the repo's own convention for a
    deprecated design node ("never renumbered and never removed").

    A `PARTLY DEPRECATED` banner does not qualify: it scopes itself to one tier
    or section, so the rest of the file is live and must stay guarded. Those
    files rely on the paragraph-level `_HISTORICAL_MARKERS` instead."""
    head = "\n".join(markdown.splitlines()[:30])
    return bool(_DEPRECATED_BANNER.search(head))


def candidate_references(markdown: str) -> list[tuple[int, str]]:
    """`(line number, path)` for every backticked, root-anchored path
    reference in `markdown` that is not inside a historically-marked
    paragraph and is not itself under the superpowers exemption."""
    found: list[tuple[int, str]] = []
    for start, lines in _paragraphs(markdown):
        paragraph_text = "\n".join(lines)
        if any(marker in paragraph_text for marker in _HISTORICAL_MARKERS):
            continue
        for offset, line in enumerate(lines):
            for match in _PATH_TOKEN.finditer(line):
                path = match.group(1)
                if _is_checked_path(path):
                    found.append((start + offset, path))
    return found


def gitignore_literals(gitignore_path: Path) -> frozenset[str]:
    """Non-glob `.gitignore` entries — a generated artifact's exact path, not
    a pattern. `nora/version.json` (build-time stamp, gitignored) is the
    motivating case: absent in a checkout by design, not by drift."""
    literals: set[str] = set()
    for raw in gitignore_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "!")) or "*" in line or line.endswith("/"):
            continue
        literals.add(line.lstrip("/"))
    return frozenset(literals)


def unresolved(
    root: Path, candidates: list[tuple[int, str]], ignored: frozenset[str]
) -> list[tuple[int, str]]:
    """Candidates that resolve to nothing under `root` and are not a known
    generated (gitignored) artifact."""
    return [
        (lineno, path)
        for lineno, path in candidates
        if path not in ignored and not (root / path).exists()
    ]


def _tracked_markdown_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "*.md"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return [
        line
        for line in out.stdout.splitlines()
        if not line.startswith(_EXEMPT_SOURCE_PREFIXES) and not line.startswith(".claude/")
    ]


# Every dangling reference this gate still finds after the tightening above,
# pinned with why it is not (yet) a fixed link — this list IS the report to
# the operator; do not mass-rewrite the docs to clear it, and do not add to it
# for a NEW reference (fix the doc, or the code, instead).
_KNOWN_DANGLING: dict[tuple[str, str], str] = {
    (
        "docs/adr/0002-honcho-session-binding-deferred.md",
        "nora/ingest/stages/memory_feed.py",
    ): (
        "The historical ADR names the Honcho feed the Cognitive Graph replaced; "
        "resolves in git history."
    ),

    (
        "docs/adr/0002-honcho-session-binding-deferred.md",
        "nora/infra/honcho/client.py",
    ): "The accepted ADR records the Honcho client at the revision it describes.",
    (
        'docs/adr/0028-pull-requests-are-labelled-and-assigned-by-a-workflow-and-a-visible-commit-has-a-body.md',
        '.github/workflows/pr-triage.yml',
    ): (
        "Issue #385 folds triage into contribution-content.yml. The historical ADR "
        "records the earlier workflow and remains historical; resolves in git history."
    ),

    (
        "docs/adr/0020-documents-are-json-specs-the-portal-renders.md",
        "deploy/drop/rendering.py",
    ): (
        "deleted 2026-10-08 with the whole Drop service (ADR 0023). ADR 0020 is "
        "accepted, so only its Status line may change; its Context text keeps "
        "naming the file as it was. checked 2026-10-08 (`git log -- deploy/drop`)"
    ),
    # --- found once table rows were split out of their table ---
    #
    # `_paragraphs` treats each table row as its own record, so a marker on one
    # row no longer exempts its neighbours. A row whose cited code was deleted
    # carries its own `_HISTORICAL_MARKERS` hit (the decision-index header says a
    # row is MARKED, not rewritten) and needs no entry here.
    # --- found once list items were split out of their list ---
    #
    # `_paragraphs` treats each list item as its own record; each of these is a
    # design record naming the code of its day.
    # --- the self-reference bundle, DEC-193 ---
    #
    # `scripts/ingest_self_asset.py` exists. What remains is history.
    # --- the one-path rebuild ---
    #
    # --- the old eval layer ---
    #
    # These are HISTORY, not broken links: each describes what was true when it
    # was written, and `evals/run.py` was the CLI at the time. Pinned rather
    # than rewritten, for this gate's own stated reason — a design brief that
    # names the code of its day is not drift, and mass-rewriting prose to chase
    # a delete loses the record of what the delete removed.
    #
    # The OPERATOR-facing docs (`docs/operations/runbook.md`, `docs/operations/eval-sop.md`,
    # `docs/engineering/conventions-rationale.md`, the `make eval-*` targets)
    # name `python -m evals`, because those tell somebody to run a command.
    # The eval-layer modules live in `tests/`; these references name where the
    # code was before.
    ("docs/operations/trace-redaction.md", "evals/judge.py"): (
        "re-homed into `tests/` 2026-09-18; the reference records where "
        "it lived (docs/engineering/evaluation Step 5)"
    ),
    (
        "docs/adr/0011-the-relevance-gate-stays-off-the-sdk-transport.md",
        "nora/app/eval_support.py",
    ): (
        "re-homed to `tests/offline_eval_support.py` 2026-09-18 "
        "(docs/engineering/evaluation Step 5); "
        "the "
        "reference records where it lived"
    ),
    # The ROUTING tier, which has no counterpart — DEC-76 is NOT ENFORCED.
    # These two lines NAME that absence, so the reference is the point.
    ("voice-lab/docs/audit-brief-kimi-integration.md", "nora/platform/discord/voice_session.py"): (
        "a 2026-09 audit brief moved in from the voice-lab repo; it proposes where "
        "stage-1 TTS+ducking WOULD live. The file was never created (the voice "
        "channel went the sidecar route, docs/design/voice/README.md) — the brief is a "
        "historical record of the proposal, not a link to fix"
    ),
    ("docs/adr/0020-documents-are-json-specs-the-portal-renders.md",
     "nora/compute/smith/page_shell.py"): (
        "an accepted ADR names the file whose choice it reverses; the file was deleted "
        "in S3b (DEC-254), and an accepted ADR changes only by a new ADR"
    ),
}


def test_every_doc_reference_resolves_or_is_a_known_finding() -> None:
    found: dict[tuple[str, str], int] = {}
    ignored = gitignore_literals(ROOT / ".gitignore")
    total_candidates = 0
    for rel in _tracked_markdown_files():
        path = ROOT / rel
        markdown = path.read_text(encoding="utf-8")
        if is_deprecated_file(markdown):
            continue
        candidates = candidate_references(markdown)
        total_candidates += len(candidates)
        for lineno, ref in unresolved(ROOT, candidates, ignored):
            found[(rel, ref)] = lineno

    # Positive control: a walk that stopped matching anything would report an
    # empty offender set too — indistinguishable from "checked and clean"
    # without this.
    assert total_candidates >= 350, (
        f"only matched {total_candidates} root-anchored doc path references — "
        "the extraction likely stopped matching; fix candidate_references "
        "before trusting a green run"
    )

    # Staged paths are owned by the document gate; an ADR is history, so it may
    # cite a path as it stood before the docs/ move.
    unpinned = {
        k: v for k, v in found.items()
        if k not in _KNOWN_DANGLING and k[1] not in STAGED_TARGETS
        and not (k[0].startswith("docs/adr/") and k[1].startswith(_PRE_MOVE))
    }
    assert not unpinned, (
        "new dangling documentation reference(s) — a backticked path that no "
        "longer resolves and is not in _KNOWN_DANGLING. Report to the "
        "operator; do not rewrite the doc without checking whether the "
        "referenced code moved, was renamed, or was never built:\n  "
        + "\n  ".join(f"{f}:{ln} -> {p}" for (f, p), ln in sorted(unpinned.items()))
    )

    stale = {k: v for k, v in _KNOWN_DANGLING.items() if k not in found}
    assert not stale, (
        "_KNOWN_DANGLING entr(y/ies) that now resolve (or the doc/reference no "
        f"longer exists) — drop them: {sorted(stale)}"
    )


def test_an_unresolved_reference_is_flagged(tmp_path: Path) -> None:
    """Positive control: a reference to a path that does not exist is
    flagged, and the identical reference once the path exists is not."""
    markdown = "See `nora/totally_made_up_module_for_this_test.py` for details.\n"
    candidates = candidate_references(markdown)
    assert candidates == [(1, "nora/totally_made_up_module_for_this_test.py")]

    assert unresolved(tmp_path, candidates, frozenset()) == candidates

    (tmp_path / "nora").mkdir()
    (tmp_path / "nora" / "totally_made_up_module_for_this_test.py").write_text("")
    assert unresolved(tmp_path, candidates, frozenset()) == []


def test_a_symbol_or_line_suffix_cannot_hide_a_dangling_path(tmp_path: Path) -> None:
    markdown = (
        "See `nora/missing_symbol.py::Class::method` and `nora/missing_line.py:24-26`.\n"
        "See `nora/existing.py::function`.\n"
    )
    (tmp_path / "nora").mkdir()
    (tmp_path / "nora" / "existing.py").touch()

    assert unresolved(tmp_path, candidate_references(markdown), frozenset()) == [
        (1, "nora/missing_symbol.py"),
        (1, "nora/missing_line.py"),
    ]


def test_a_superpowers_target_is_never_flagged_even_though_it_cannot_resolve() -> None:
    """The mandatory exemption: nothing under docs/superpowers/ (or
    .superpowers/) is ever checked, no matter how the citing doc reads."""
    markdown = (
        "See `docs/superpowers/specs/2026-08-03-nora-strands-spec.md` for the "
        "original design, and `.superpowers/plugin.json` for the old wiring.\n"
    )
    assert candidate_references(markdown) == []


def test_a_deprecated_paragraph_suppresses_the_reference_it_contains() -> None:
    """A paragraph naming RETIRED/DEPRECATED/SUPERSEDED/deleted, or stating a
    file "resolves in git history", is read as deliberate history, the same
    way the decision index's own DEPRECATED rows are — without this, the
    positive control above would also have to flag every such row."""
    markdown = (
        "**DEPRECATED 2026-09-07** — `nora/language/knowledge_search.py` "
        "described a tier whose code has since been deleted.\n"
    )
    assert candidate_references(markdown) == []


def test_a_deprecated_table_row_does_not_exempt_the_live_row_beside_it() -> None:
    """A table row is its own record: a historical marker on one row must not
    unguard its neighbours' live references in the decision index. Two adjacent
    rows: the deprecated one's path is suppressed, the live one's is still
    reported — each assertion is the other's control."""
    markdown = (
        "| id | decision |\n"
        "|---|---|\n"
        "| DEC-1 | DEPRECATED — was `nora/deprecated_row_module_for_this_test.py` |\n"
        "| DEC-2 | lives in `nora/live_row_module_for_this_test.py` |\n"
    )
    found = candidate_references(markdown)
    assert (4, "nora/live_row_module_for_this_test.py") in found, found
    assert all(
        path != "nora/deprecated_row_module_for_this_test.py" for _, path in found
    ), found


def test_a_historical_list_item_does_not_exempt_the_live_item_beside_it() -> None:
    """A list item is its own record, the same way a table row is: the word
    "deleted" in one bullet must not exempt the rest of an unbroken bulleted
    list. Two adjacent bullets: the historical one's path is suppressed, the
    live one's is still reported."""
    markdown = (
        "- Never trust an attribute that was deleted: "
        "`nora/historical_item_module_for_this_test.py`.\n"
        "- Never skip the gate: `nora/live_item_module_for_this_test.py` is\n"
        "  the check, and this continuation line belongs to its item.\n"
    )
    found = candidate_references(markdown)
    assert (2, "nora/live_item_module_for_this_test.py") in found, found
    assert all(
        path != "nora/historical_item_module_for_this_test.py" for _, path in found
    ), found


def test_a_file_level_deprecated_banner_exempts_the_whole_file() -> None:
    markdown = (
        "---\ntitle: x\n---\n\n"
        "> **DEPRECATED 2026-09-07 — this document is retained as a record.**\n\n"
        "Body text citing `nora/language/note_revisitor.py` for context.\n"
    )
    assert is_deprecated_file(markdown) is True
    # Even though the banner's own paragraph doesn't touch the later
    # reference, file-level exemption is applied by the caller before
    # `candidate_references` ever runs on a DEPRECATED file (see the real
    # gate above), so this documents the contract rather than re-deriving it.


def test_a_literal_gitignore_entry_is_not_a_dangling_reference() -> None:
    """`nora/version.json` (a build-time stamp) is absent in a checkout by
    design; a glob pattern like `*.db` must NOT exempt an unrelated path."""
    ignored = gitignore_literals(ROOT / ".gitignore")
    assert "nora/version.json" in ignored
    assert not any(entry.endswith("*") or "*" in entry for entry in ignored)

def test_agenda_docs_refer_to_the_graph_delivery_path() -> None:
    runbook = (ROOT / "docs/operations/runbook.md").read_text()
    assert "graph_stimulus_claim" in runbook
    assert "nora_proactive_stimulus_parked_total" in runbook
    assert "nora reminders list" not in runbook
    assert "nora_heartbeat_deferrals_total" not in runbook
    references = subprocess.run(
        ["git", "grep", "-nE", r"HeartbeatLoop|nora\.app\.heartbeat", "--", "nora"],
        cwd=ROOT, check=False, capture_output=True, text=True,
    )
    assert references.returncode == 1, references.stdout or references.stderr
