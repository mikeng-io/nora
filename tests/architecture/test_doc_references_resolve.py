"""Docs cite repo paths inline in backticks — `design/09-decision-index.md`,
`nora/agent/tools.py` — and nothing checks that the cited path still exists.
**This actually broke this session**: the ADRs moved from `design/adr/` to
`docs/adr/` and every backticked citation of the old location went dangling
silently, because a doc move is not a `grep` anyone runs by habit.

**The one deliberate, large-scale exception is `docs/superpowers/` (and
`.superpowers/`).** AGENTS.md's reading-order section states outright that the
superpowers process was removed 2026-09-07 and its ~115 historical citations
"resolve only in git history" — they are kept ON PURPOSE as provenance for
*why*, not as live pointers, and rewriting them would erase exactly the
history the reading order says to go read. This gate exempts that tree by
name, in both directions: a citation FROM a file under it, and a citation OF a
path under it from anywhere else.

**Conservative on purpose.** CLAUDE.md's own R-29 story is that a noisy gate
is a disabled gate, and an initial unscoped sweep of every backticked,
slash-containing token in every tracked `.md` file produced a large majority
of non-defects before any tightening: version-like numbers and IPs (no
extension), bare relative mentions inside a file already scoped to that
subtree (`agent/guards.py` inside a doc under `design/whatsapp-adoption/`),
and — the largest category — prose that talks ABOUT a deleted or not-yet-built
file rather than pointing at a live one. Each tightening below is a
STRUCTURAL signal already present in this repo's own conventions, not a
per-path guess:

  - Only a reference anchored at a real top-level directory (`nora/`,
    `design/`, `docs/`, `tests/`, `evals/`, `deploy/`, `scripts/`,
    `release-fragments/`, `.github/`) or a known root file counts as a path
    CLAIM. A bare `doctrine/identity.md` is prose shorthand for a concept
    ("her doctrine"), not a root-relative path — `doctrine/` is not itself a
    top-level directory (the real files live under `nora/doctrine/` and
    `nora/dreamer/doctrine/`), so nothing anchored there is checked.
  - A path that is itself under `docs/superpowers/` or `.superpowers/` is
    never checked, from anywhere (the exemption above).
  - A file whose own first ~30 lines carry a `> **DEPRECATED` /
    `> **PARTLY DEPRECATED` banner is a self-declared historical record —
    CLAUDE.md states this is how a deprecated design node is marked — and is
    skipped whole, the same reasoning as the superpowers exemption one level
    down from a directory to a single file.
  - `design/proposed/` names its own status in its path: a not-yet-built
    design describing a file that does not exist yet is not dangling, it is
    early.
  - A PARAGRAPH (not just the one line the path sits on) containing the word
    "RETIRED", "DEPRECATED", "SUPERSEDED", or "deleted", or the phrase
    "resolves in git history", is read as the same kind of deliberate
    historical citation the superpowers exemption already covers — measured
    against `design/09-decision-index.md`, whose own header states that a
    decision whose subject is deleted is marked exactly this way and never
    rewritten.
  - A path that is a LITERAL (non-glob) entry in `.gitignore` is a generated
    artifact, not a moved one — `nora/version.json` is built at image time and
    is `unavailable` by design in a source checkout (ARCHITECTURE.md says so
    in the same breath it names the path).

What survives all of that and still does not resolve is pinned in
`_KNOWN_DANGLING`, exactly like `_ALLOWED` / `_UNRESOLVABLE` elsewhere in this
directory: a NEW dangling reference fails the gate, a fixed one must be
dropped from the pin (checked below), and the pinned list itself is this
gate's actual finding — real drift in `design/event-abstraction/`,
`design/discord-music/`, `design/11-recall-and-threading.md`, and elsewhere,
reported to the operator rather than mass-rewritten (some of it is a design
brief predating the code it describes, not a broken link to fix by editing
prose).

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
    "release-fragments/",
    "scripts/",
    ".github/",
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
        "ARCHITECTURE.md",
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
_EXEMPT_SOURCE_PREFIXES = ("docs/superpowers/", ".superpowers/", "design/proposed/")

# A paragraph carrying any of these is read as a deliberate historical
# citation (a RETIRED/DEPRECATED/SUPERSEDED decision-index row, or prose
# stating outright that the file "deleted" / "resolves in git history only").
_HISTORICAL_MARKERS = ("RETIRED", "DEPRECATED", "SUPERSEDED", "deleted", "resolves in git history")

#: A whole-file historical record. **`PARTLY DEPRECATED` is deliberately NOT
#: here.** A "partly" banner says one tier or section of the file is history and
#: the rest still stands — treating it as a whole-file exemption unguarded 20 of
#: the most-referenced design nodes under `design/dreamer-agent-loop/`,
#: including the Standing documents. Reproduced 2026-09-08: a dangling reference
#: injected into `26-standing.md`'s live Standing section, which its own banner
#: calls unaffected, did not fail this gate. Paragraph-level markers
#: (`_HISTORICAL_MARKERS`) carry the partly-deprecated case instead, which is
#: the right grain: the banner scopes itself in prose, so the check should too.
_DEPRECATED_BANNER = re.compile(r">\s*\*\*DEPRECATED")

_LIST_ITEM = re.compile(r"\s*(?:[-*+]|\d+[.)])\s")

_PATH_TOKEN = re.compile(r"`([A-Za-z0-9_./-]+/[A-Za-z0-9_./-]+\.[A-Za-z0-9]+)`")


def _is_checked_path(path: str) -> bool:
    if path.startswith(_EXEMPT_TARGET_PREFIXES):
        return False
    return path.startswith(_CHECKED_PREFIXES) or path in _CHECKED_ROOT_FILES


def _paragraphs(markdown: str) -> list[tuple[int, list[str]]]:
    """`(first line number, lines)` for each blank-line-delimited paragraph —
    and each TABLE ROW on its own.

    A table row is one record, not a sentence of a paragraph. Treated as one
    blank-line-delimited block, a whole table was one «paragraph», so a
    historical marker on ONE row exempted every other row in it: marking
    DEC-145 deprecated (2026-09-24) silently unguarded R-83's and DEC-146's
    live references in `design/09-decision-index.md`.

    A LIST ITEM starts a new paragraph for the same reason (2026-09-24): an
    unbroken bulleted list was one block, so "deleted" in one bullet of
    AGENTS.md's "Never" list exempted all 84 lines of it. Continuation lines
    (indented, not themselves a bullet) stay with their item."""
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
    # --- hidden by a NEIGHBOURING table row until 2026-09-24 ---
    #
    # A table was one «paragraph» to this gate, so one row carrying a
    # historical marker exempted every row in its table. Splitting rows out
    # (`_paragraphs`) surfaced these; none was introduced by that change.
    # Pinned, not rewritten, per this gate's own rule — each needs checking
    # whether the code moved, was renamed or was deleted (#422).
    **dict.fromkeys(
        (
            ("design/09-decision-index.md", "deploy/compose/nora-db.compose.yml"),
            ("design/09-decision-index.md", "deploy/compose/presence.compose.yml"),
            ("design/09-decision-index.md", "deploy/egress.sh"),
            ("design/09-decision-index.md", "deploy/watchdog.sh"),
            ("design/09-decision-index.md", "evals/adversarial/identity_grounding.yaml"),
            ("design/09-decision-index.md", "evals/adversarial/source_scope.yaml"),
            ("design/09-decision-index.md", "evals/evaluators.py"),
            ("design/09-decision-index.md", "evals/journeys.py"),
            ("design/09-decision-index.md", "evals/judge.py"),
            ("design/09-decision-index.md", "evals/run.py"),
            ("design/09-decision-index.md", "nora/agent/disclosure.py"),
            ("design/09-decision-index.md", "tests/test_evals_intent.py"),
            ("design/09-decision-index.md", "tests/test_evals_journeys.py"),
            ("design/09-decision-index.md", "tests/test_evals_live_runner.py"),
            ("design/evaluation/02-the-rebuild.md", "nora/app/eval_support.py"),
        ),
        "surfaced 2026-09-24 when table rows became their own paragraphs; "
        "not yet triaged (#422)",
    ),
    (
        "design/instagram-adoption/17-implementation-plan.md",
        "nora/infra/social/instagram_links.py",
    ): (
        "planned file, created by P10 (Instagram link parsing); drop this entry in that PR"
    ),
    # --- hidden by a NEIGHBOURING list item until 2026-09-24 ---
    #
    # A bulleted list was one «paragraph», so a marker in one bullet exempted
    # every bullet in the list. Splitting items out (`_paragraphs`) surfaced
    # these; each is a design record naming the code of its day.
    ("design/evaluation/10-the-scenario-map.md", "evals/rebuilt/__main__.py"): (
        "the old CLI, recorded as deleted in the same bullet (capitalized "
        "'Deleted.', which the lowercase marker does not match)"
    ),
    ("design/evaluation/10-the-scenario-map.md", "evals/rebuilt/report.py"): (
        "the old CLI's renderer, deleted in the same step"
    ),
    ("design/social-per-network/10-changes.md", "tests/test_social_fetch_tool.py"): (
        "the plan names the file it replaces; DEC-201 PR 1 (abeab8e4) moved "
        "those tests to per-tool tests under tests/unit/"
    ),
    # --- the self-reference bundle, DEC-193 ---
    #
    # The ingest-script pin was dropped when DEC-193 was built (2026-09-23,
    # `scripts/ingest_self_asset.py` exists). What remains is history.
    ("design/self-reference/00-overview.md", "design/proposed/31-self-reference.md"): (
        "the single-file draft this bundle replaced; deleted when it was "
        "restructured into design/self-reference/, and the line records that "
        "history (see _index.yaml `revision`)"
    ),
    # --- the one-path rebuild, 2026-09-21 (design/evaluation/11 §1.3) ---
    #
    # The old harness `evals/rebuilt/runner.py` and the pre-port corpus copies
    # under `evals/cases/` and `evals/adversarial/` are deleted; the live
    # corpora are `evals/corpora/*.yaml` and the runner is
    # `evals/backbone.py`. These lines record what was true when written.
    **{
        (doc, "evals/rebuilt/runner.py"): (
            "the old harness, deleted 2026-09-21; `evals/backbone._drive` is "
            "the one path (design/evaluation/11 §1.3)"
        )
        for doc in (
            "design/evaluation/04-the-convention.md",
            "design/evaluation/07-sdk-native.md",
            "design/evaluation/08-the-sdk-surface.md",
            "design/evaluation/GOAL.md",
        )
    },
    **{
        (doc, old): f"pre-port copy deleted 2026-09-21; now evals/corpora/{new}"
        for doc, old, new in (
            ("design/09-decision-index.md",
             "evals/adversarial/being_contradicted.yaml", "being_contradicted.yaml"),
            ("design/dreamer-agent-loop/13-doctrine.md",
             "evals/cases/note_candidates/note_candidates.yaml", "note_candidates.yaml"),
            ("design/dreamer-agent-loop/35-ambient-participation.md",
             "evals/cases/participation/participation.yaml", "participation.yaml"),
        )
    },
    # --- the old eval layer, deleted 2026-09-18 (design/evaluation Step 5) ---
    #
    # These are HISTORY, not broken links: each describes what was true when it
    # was written, and `evals/run.py` was the CLI at the time. Pinned rather
    # than rewritten, for this gate's own stated reason — a design brief that
    # names the code of its day is not drift, and mass-rewriting prose to chase
    # a delete loses the record of what the delete removed.
    #
    # The OPERATOR-facing docs were fixed instead, because those tell somebody
    # to run a command: `docs/runbook.md`, `docs/eval-sop.md`,
    # `docs/CONVENTIONS.md` and the `make eval-*` targets now name
    # `python -m evals` (2026-09-20, `10-the-scenario-map.md` §15/16 — the
    # corpus port superseded `python -m evals.rebuilt` in turn).
    **{
        (doc, "evals/run.py"): (
            "the old eval CLI, deleted 2026-09-18; this line records what was "
            "true when it was written (design/evaluation Step 5)"
        )
        for doc in (
            "design/ambient-loop/02-doctrine-and-measurement.md",
            "design/capability-drop/03-code-surface.md",
            "design/capability-drop/implementation-brief.md",
            "design/dreamer-agent-loop/13-doctrine.md",
            "design/dreamer-agent-loop/39-proactive-ooda-loop.md",
            "design/evaluation/06-first-full-run.md",
        )
    },
    # The eval-layer modules RE-HOMED into `tests/` on 2026-09-18 — they were
    # in `nora/` and `evals/` only because of an import contract that no longer
    # binds anything. These references name where the code WAS.
    ("ARCHITECTURE.md", "evals/harness.py"): (
        "re-homed into `tests/` 2026-09-18; the reference records where "
        "it lived (design/evaluation Step 5)"
    ),
    ("design/capability-drop/03-code-surface.md", "evals/harness.py"): (
        "re-homed into `tests/` 2026-09-18; the reference records where "
        "it lived (design/evaluation Step 5)"
    ),
    ("design/13-trace-content-and-redaction.md", "evals/judge.py"): (
        "re-homed into `tests/` 2026-09-18; the reference records where "
        "it lived (design/evaluation Step 5)"
    ),
    ("design/capability-drop/03-code-surface.md", "nora/app/eval_support.py"): (
        "re-homed to `tests/offline_eval_support.py` 2026-09-18 (design/evaluation Step 5); the "
        "reference records where it lived"
    ),
    ("design/capability-drop/implementation-brief.md", "evals/harness.py"): (
        "re-homed to `tests/offline_eval_harness.py` 2026-09-18 (design/evaluation Step 5); the "
        "reference records where it lived"
    ),
    ("design/capability-drop/implementation-brief.md", "nora/app/eval_support.py"): (
        "re-homed to `tests/offline_eval_support.py` 2026-09-18 (design/evaluation Step 5); the "
        "reference records where it lived"
    ),
    ("design/dreamer-agent-loop/16-open-items.md", "evals/harness.py"): (
        "re-homed to `tests/offline_eval_harness.py` 2026-09-18 (design/evaluation Step 5); the "
        "reference records where it lived"
    ),
    ("design/dreamer-agent-loop/39-proactive-ooda-loop.md", "nora/app/eval_support.py"): (
        "re-homed to `tests/offline_eval_support.py` 2026-09-18 (design/evaluation Step 5); the "
        "reference records where it lived"
    ),
    ("design/evaluation/01-boundaries.md", "evals/harness.py"): (
        "re-homed to `tests/offline_eval_harness.py` 2026-09-18 (design/evaluation Step 5); the "
        "reference records where it lived"
    ),
    ("design/evaluation/01-boundaries.md", "nora/app/eval_support.py"): (
        "re-homed to `tests/offline_eval_support.py` 2026-09-18 (design/evaluation Step 5); the "
        "reference records where it lived"
    ),
    ("design/evaluation/01-boundaries.md", "nora/app/offline.py"): (
        "re-homed to `tests/offline_pipeline.py` 2026-09-18 (design/evaluation Step 5); the "
        "reference records where it lived"
    ),
    ("design/evaluation/02-the-rebuild.md", "nora/app/offline.py"): (
        "re-homed to `tests/offline_pipeline.py` 2026-09-18 (design/evaluation Step 5); the "
        "reference records where it lived"
    ),
    ("design/evaluation/04-the-convention.md", "evals/harness.py"): (
        "re-homed to `tests/offline_eval_harness.py` 2026-09-18 (design/evaluation Step 5); the "
        "reference records where it lived"
    ),
    ("design/evaluation/04-the-convention.md", "nora/app/offline.py"): (
        "re-homed to `tests/offline_pipeline.py` 2026-09-18 (design/evaluation Step 5); the "
        "reference records where it lived"
    ),
    ("design/evaluation/05-live-parity.md", "nora/app/eval_support.py"): (
        "re-homed to `tests/offline_eval_support.py` 2026-09-18 (design/evaluation Step 5); the "
        "reference records where it lived"
    ),
    (
        "docs/adr/0011-the-relevance-gate-stays-off-the-sdk-transport.md",
        "nora/app/eval_support.py",
    ): (
        "re-homed to `tests/offline_eval_support.py` 2026-09-18 (design/evaluation Step 5); the "
        "reference records where it lived"
    ),
    ("design/evaluation/04-the-convention.md", "nora/app/eval_support.py"): (
        "the convention names where it WAS while explaining where it went"
    ),
    ("design/evaluation/04-the-convention.md", "evals/relevance_gate.py"): (
        "the convention quotes its docstring as the evidence that the facade was a boundary adapter"
    ),
    ("design/evaluation/04-the-convention.md", "tests/integration/test_live_parity_gate.py"): (
        "the convention names the parity test as the one-way door the delete passed; the "
        "reference is the point"
    ),
    # The ROUTING tier, which has no counterpart — DEC-76 is NOT ENFORCED.
    # These two lines NAME that absence, so the reference is the point.
    ("voice-lab/docs/audit-brief-kimi-integration.md", "nora/platform/discord/voice_session.py"): (
        "a 2026-09 audit brief moved in from the voice-lab repo; it proposes where "
        "stage-1 TTS+ducking WOULD live. The file was never created (the voice "
        "channel went the sidecar route, design/voice-channel/05) — the brief is a "
        "historical record of the proposal, not a link to fix"
    ),
    ("scrub/README.md", "docs/ai-thinkathon-2026-09.md"): (
        "the note was removed from HEAD once its event had happened, and the "
        "scrub rewrites HISTORY, where the file still is — so the citation "
        "still points at the scrub's actual subject. The entry it sits in is "
        "the lesson that path deletion misses renames; repointing it at a live "
        "path would delete the example"
    ),
    ("docs/CONVENTIONS.md", "tests/fixtures/presence_corpus_sample.py"): (
        "the war-story's subject: 11,431 real GPS fixes, deleted from the tree by "
        "a32acc90 and the whole point of the entry. The dangling path IS the "
        "record — naming the file is how the next reader finds the blob that "
        "outlived it, and rewriting the reference would erase what happened"
    ),
    ("design/discord-integration/00-overview.md", "deploy/watchdog.sh"): (
        "the shell watchdog was deleted 2026-09-09 once Prometheus covered its one "
        "condition with a real delivery path; this design records the reasoning as it "
        "stood, and rewriting a decision record to match later code is what this "
        "repo's design docs deliberately do not do"
    ),
    ("design/whatsapp-adoption/01-bridge-container.md", "deploy/watchdog.sh"): (
        "same deletion, same reason — a historical decision record, not a broken link"
    ),
    ("deploy/README.md", "deploy/egress.sh"): (
        "documented as the versioned egress governance script; not tracked in this checkout"
    ),
    ("design/08-stack-refactor-brief.md", "nora/store/db.py"): (
        "pre-refactor handoff brief describing the SQLite-era code the brief itself "
        "proposed replacing; nora/store/ no longer exists post-refactor"
    ),
    ("design/08-stack-refactor-brief.md", "nora/store/migrate.py"): (
        "pre-refactor handoff brief describing the SQLite-era code the brief itself "
        "proposed replacing; nora/store/ no longer exists post-refactor"
    ),
    ("docs/runbook.md", "nora/store/db.py"): (
        "same pre-refactor path as design/08-stack-refactor-brief.md; runbook not yet updated"
    ),
    ("design/09-decision-index.md", ".github/workflows/gates.yml"): (
        "A12 names a CI gate file; this repo deliberately runs no CI (operator decision) "
        "and the file was never added back after that decision"
    ),
    (
        "design/dreamer-agent-loop/34-note-dreamer-consolidation.md",
        "nora/language/note_revisitor.py",
    ): (
        "the consolidation this document designs never happened: NoteRevisitor "
        "was deleted outright rather than merged into the curator (2026-09-08 "
        "deprecation, R-75), and issue #99 was closed as superseded on that "
        "basis. Surfaced only once PARTLY DEPRECATED stopped exempting whole "
        "files. The document needs its own banner; that is the author's call, "
        "not this gate's"
    ),
    ("design/11-recall-and-threading.md", "nora/engagement/recall.py"): (
        "early recall design; the module never landed at this path"
    ),
    ("design/11-recall-and-threading.md", "nora/infra/sql/migrations/versions/0003_quoted_ts.py"): (
        "migration was renumbered/renamed before landing (0003_presence_places.py today), "
        "same shape as 0017_events.py's own documented renumbering"
    ),
    (
        "design/discord-music/01-playback-and-source.md",
        "tests/contract/test_track_source_contract.py",
    ): (
        "planned contract suite; TrackSource has no shared fake or contract suite yet "
        "(also pinned NO_SHARED_FAKE in test_port_fakes_live_in_nora_testing.py)"
    ),
    ("design/discord-music/04-deferred.md", "tests/contract/test_track_source_contract.py"): (
        "same not-yet-built contract suite as 01-playback-and-source.md"
    ),
    ("design/event-abstraction/02-naming-rename.md", "nora/infra/sql/events.py"): (
        "naming-rename plan; shipped as nora/domain/events.py instead"
    ),
    ("design/event-abstraction/02-naming-rename.md", "nora/testing/events.py"): (
        "naming-rename plan; no such fake module was built"
    ),
    ("design/event-abstraction/02-naming-rename.md", "tests/test_events_domain.py"): (
        "naming-rename plan's proposed test names; shipped as tests/unit/test_standing_event.py"
    ),
    ("design/event-abstraction/02-naming-rename.md", "tests/test_events_service.py"): (
        "naming-rename plan's proposed test names; shipped as tests/unit/test_standing_event.py"
    ),
    ("design/event-abstraction/02-naming-rename.md", "tests/test_event_tools.py"): (
        "naming-rename plan's proposed test names; shipped as tests/unit/test_standing_event.py"
    ),
    ("design/event-abstraction/02-naming-rename.md", "tests/test_web_events.py"): (
        "naming-rename plan's proposed test names; shipped as tests/unit/test_standing_event.py"
    ),
    (
        "design/event-abstraction/02-naming-rename.md",
        "tests/contract/test_event_store_contract.py",
    ): (
        "naming-rename plan's proposed contract suite; not built under this name"
    ),
    ("design/event-abstraction/04-dispatch-and-batching.md", "tests/test_events.py"): (
        "same event-abstraction planning-doc-vs-shipped-name drift as 02-naming-rename.md"
    ),
    ("docs/CONVENTIONS.md", "nora/language/note_revisitor.py"): (
        "war-story citation of a fixed historical bug; note_revisitor.py was later merged "
        "into note_curator.py (design/dreamer-agent-loop/34-note-dreamer-consolidation.md)"
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
    # without this. Measured 2026-09-08: ~420 root-anchored path references
    # across the tracked docs (after the doctrine-prefix / superpowers /
    # design-proposed / gitignore tightening above).
    assert total_candidates >= 350, (
        f"only matched {total_candidates} root-anchored doc path references — "
        "the extraction likely stopped matching; fix candidate_references "
        "before trusting a green run"
    )

    unpinned = {k: v for k, v in found.items() if k not in _KNOWN_DANGLING}
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
    """A table row is its own record (2026-09-24). While a whole table was
    one «paragraph», marking DEC-145 deprecated silently unguarded R-83's and
    DEC-146's live references in the decision index. Two adjacent rows: the
    deprecated one's path is suppressed, the live one's is still reported —
    each assertion is the other's control."""
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
    """A list item is its own record, the same way a table row is. While an
    unbroken bulleted list was one «paragraph», the word "deleted" in one
    bullet of AGENTS.md's "Never" list (about GC-tracked objects) exempted the
    whole 84-line list, and a citation of a test deleted 2026-09-16 sat in it
    unflagged for eight days (found 2026-09-24). Two adjacent bullets: the
    historical one's path is suppressed, the live one's is still reported."""
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
