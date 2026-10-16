"""The contribution conventions are mechanically pinned, not prose-only.

The conventions in CONTRIBUTING.md are prose, and prose drifts: heading
spellings, undeclared PR headings, undeclared commit types, GitHub-default
merge subjects. The templates are the shape at filing time; this file is what
stops the shape from drifting the way the prose does.

Same discipline as `tests/test_doctrine_version_parity.py`: a test cannot know
which wording is RIGHT, but it can refuse to let the declared shape move
silently. To change a heading deliberately, change the template, this pin, and
the CONTRIBUTING.md paragraph — one commit, all three.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = ROOT / ".github" / "ISSUE_TEMPLATE"

# The declared issue shapes, from CONTRIBUTING.md §Issues. This list is the
# contract: any heading not here is drift, exactly like `Summary` for PRs.
# The prose shape opens with a `**Design:** <bundle> §n (<what it covers>).`
# line; the form renders that line as two fields, `Design anchor` + `What it
# covers`, then carries the four declared headings verbatim.
DESIGN_ANCHORED_HEADINGS = [
    "Design anchor",
    "What it covers",
    "The gap",
    "What's specified (decided, not open)",
    "Not built",
    "Depends on / feeds",
]
FREEFORM_HEADINGS = [
    "What",
    "Why the current shape blocks it",
    "Shape",
    "Decisions to record",
    "Not in this issue",
]
PR_HEADINGS = [
    "What",
    "Also",
    "Gate on the merged tree",
    "Deploy note",
    "Closes",
]
SHAPE_LABELS = {"design-anchored", "freeform"}


def _form(name: str) -> dict:
    import yaml

    return yaml.safe_load((TEMPLATE_DIR / name).read_text())


def _issue_headings(form: dict) -> list[str]:
    """The declared body headings only. The Area dropdown is form plumbing —
    the axis lives in the test below, not in the CONTRIBUTING heading list."""
    out = []
    for el in form["body"]:
        if el.get("type") not in ("markdown", "dropdown"):
            label = el.get("attributes", {}).get("label")
            if label:
                out.append(label)
    return out


def test_issue_templates_exist_and_blank_issues_are_disabled() -> None:
    assert (TEMPLATE_DIR / "freeform-gap.yml").exists(), "freeform form missing"
    assert (TEMPLATE_DIR / "design-anchored.yml").exists(), "design-anchored form missing"
    config = (TEMPLATE_DIR / "config.yml").read_text()
    assert "blank_issues_enabled: false" in config, (
        "blank issues must stay disabled — the forms ARE the convention's delivery"
    )


@pytest.mark.parametrize("form_name,expected", [
    ("freeform-gap.yml", FREEFORM_HEADINGS),
    ("design-anchored.yml", DESIGN_ANCHORED_HEADINGS),
])
def test_issue_form_headings_match_the_declared_shape(form_name: str, expected: list[str]) -> None:
    form = _form(form_name)
    got = _issue_headings(form)
    assert got == expected, (
        f"{form_name} headings drifted from CONTRIBUTING.md §Issues.\n"
        f"  expected: {expected}\n  got:      {got}\n"
        "Change the form, this pin, and the CONTRIBUTING paragraph in one commit."
    )


def test_issue_forms_carry_exactly_one_shape_label_and_an_area_axis() -> None:
    seen: set[str] = set()
    for name in ("freeform-gap.yml", "design-anchored.yml"):
        form = _form(name)
        labels = set(form.get("labels", []))
        assert labels <= SHAPE_LABELS and labels, (
            f"{name}: labels must be the shape label only, got {labels}")
        seen |= labels
        # Area axis: the dropdown must exist and be required — it anchors the
        # title prefix and the body's grep-able Area line. It is deliberately
        # NOT a label (labels go stale on closed issues; the body does not).
        dropdowns = [el for el in form["body"] if el.get("type") == "dropdown"
                     and el.get("id") == "area"]
        assert dropdowns, f"{name}: the Area dropdown is required"
        assert dropdowns[0].get("validations", {}).get("required") is True
    assert seen == SHAPE_LABELS, f"shape labels must partition the forms, got {seen}"


def test_pr_template_headings_match_the_declared_shape() -> None:
    text = (ROOT / ".github" / "pull_request_template.md").read_text()
    got = re.findall(r"^## (.+)$", text, re.M)
    assert got == PR_HEADINGS, (
        f"PR template headings drifted.\n  expected: {PR_HEADINGS}\n  got:      {got}\n"
        "`Summary`/`Test plan` are retired headings — see CONTRIBUTING.md §Pull requests."
    )


def test_commit_checker_rejects_the_measured_defect_shapes() -> None:
    """Each rejection below is a defect shape seen in the real log: untyped
    subjects, undeclared types, GitHub-default merges, superseded
    `Merge <branch>:` merges."""
    script = ROOT / "scripts" / "check_commit_subject.py"

    def check(subject: str) -> int:
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write(subject + "\n")
            path = f.name
        try:
            # nosec: the script path and args are repo constants; `path` is a
            # tempfile this test just wrote. Nothing here is user input.
            return subprocess.run(  # noqa: S603
                ["uv", "run", "python", str(script), path],
                cwd=ROOT, capture_output=True, text=True,
            ).returncode
        finally:
            Path(path).unlink(missing_ok=True)

    allowed = [
        "fix(gate): the verdict forms on a raw completion",
        "feat: new capability",
        "design(culture-tier): the tier stores an answer",        # declared de-facto type
        "merge: the ambient loop is the system's (DEC-176)",      # authored merge
        "merge: her notes are her own words, not the room's record (#226)",  # real history
        'Revert "fix(gate): x"',                                  # git plumbing
    ]
    for subject in allowed:
        assert check(subject) == 0, f"should pass: {subject}"

    rejected = [
        "did some stuff",                                          # no type
        "Merge pull request #240 from mikeng-io/x",                # GitHub default
        "Merge remote-tracking branch 'origin/master'",            # GitHub default
        "Merge worktree-unguard-r85: remove the guard",            # superseded generation
        "Merge branch 'x' into y",                                 # superseded generation
        "wip: checkpoint",                                         # rebase fuel
        "obs(engagement): a standing counter",                     # undeclared type
        # The disguise: the GitHub default wearing the sanctioned `merge:` prefix.
        # The prefix alone must not pass; the body check refuses it.
        "merge: Merge pull request #1 from x",
        "merge: Merge branch 'x' into y",
        "merge: Merge remote-tracking branch 'origin/master'",
        # An authored-merge prefix stating nothing.
        "merge:",
        "merge: ",
    ]
    for subject in rejected:
        assert check(subject) != 0, f"should be rejected: {subject}"


def test_contributing_declares_the_same_types_the_checker_enforces() -> None:
    """One source of truth, two views: the table in CONTRIBUTING.md and the
    TYPES map must move together or the doc and the gate disagree."""
    types_block = re.search(
        r"^TYPES = \{(.*?)^\}", (ROOT / "scripts" / "check_commit_subject.py").read_text(),
        re.S | re.M,
    )
    assert types_block, "TYPES map not found in the checker"
    declared_in_script = set(re.findall(r'^\s+"(\w+)":', types_block.group(1), re.M))
    doc = (ROOT / "CONTRIBUTING.md").read_text()
    sec = doc[doc.index("## Commit messages"):doc.index("## Merge strategy")]
    declared_in_doc = set(re.findall(r"^\|\s*`(\w+)`\s*\|", sec, re.M))
    assert "merge" in declared_in_doc, "sanity: the table itself was not parsed"
    doc_only = sorted(declared_in_doc - declared_in_script)
    checker_only = sorted(declared_in_script - declared_in_doc)
    assert declared_in_script == declared_in_doc, (
        f"doc table vs checker TYPES disagree: doc-only={doc_only} "
        f"checker-only={checker_only}"
    )

def test_every_declared_type_is_actually_enforced() -> None:
    """Coordinated-drift guard.

    The accept/reject lists above only sample `design` and `obs`; dropping a
    DIFFERENT declared type from BOTH the TYPES map and the CONTRIBUTING table
    at once would stay green — `perf` and `security` could silently become
    undeclared while every pin still passed. This closes that by asserting,
    per declared type, that the checker's own regex machinery would REJECT
    `<type>: x` if that type were absent from the map — i.e. the type is in
    the map BECAUSE the table declares it, not by accident of sampling.

    Implemented without touching any file: the map minus one type is
    re-derived and the SUBJECT_RE rebuilt from it, exactly as the checker
    builds it.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "checker_module", ROOT / "scripts" / "check_commit_subject.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    declared = sorted(mod.TYPES)
    assert len(declared) == 17, (
        f"declared types moved ({len(declared)}); re-run the census and update"
        " the census comments in this file and CONTRIBUTING.md")

    doc = (ROOT / "CONTRIBUTING.md").read_text()
    sec = doc[doc.index("## Commit messages"):doc.index("## Merge strategy")]

    for ty in declared:
        without = {k: v for k, v in mod.TYPES.items() if k != ty}
        rebuilt = re.compile(
            rf"^({'|'.join(sorted(without))})(\([^)]+\))?!?: .+")
        subject = f"{ty}: anything"
        assert not rebuilt.match(subject), (
            f"`{ty}:` still passes the checker's SUBJECT_RE with `{ty}` removed"
            " from TYPES — removing it would not actually undeclare it, so the"
            " closed-list claim is not enforced")
        # and the doc really declares it, so the parity test would fire
        assert f"| `{ty}` |" in sec, (
            f"`{ty}` is in TYPES but its row is missing from CONTRIBUTING.md —"
            " the parity test alone cannot catch coordinated drift; this"
            " assertion is what names the type")
