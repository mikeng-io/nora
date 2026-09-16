"""`nora.instructions.dreamer.DreamerInstructions` — the Dreamer's own store.

Two halves. The first works over `tmp_path` and pins the mechanism: assembly
order, what the digest covers, what an unknown name does, hot-reload, last-good.
The second (`test_the_shipped_*`) reads the real `nora/dreamer/` tree, the way
`tests/test_doctrine_content.py` reads the real doctrine — structure only, never
wording: this is prose, and a test that pins a sentence is a test that has to be
edited every time the sentence improves.
"""

import pytest

from nora.instructions.dreamer import DreamerInstructions
from nora.instructions.frontmatter import InstructionError
from nora.obs.metrics import Metrics
from tests.objectives import SHIPPED_DREAMER as SHIPPED

DOCTRINE_FM = """\
---
name: {name}
kind: dreamer-doctrine
version: {version}
applies_to: [dreamer]
description: {desc}
change_policy: operator-only
---
{body}"""

OBJECTIVE_FM = """\
---
name: {name}
kind: {kind}
version: {version}
applies_to: [{applies}]
description: {desc}
change_policy: {change_policy}
---
{body}"""


def _touch_newer(path, text):
    import os
    import time

    path.write_text(text, encoding="utf-8")
    future = time.time() + 2
    os.utime(path, (future, future))


def write_doctrine(root, *, name="dreamer", version=1, body="Doctrine body.\n",
                   desc="d", filename="dreamer.md"):
    d = root / "doctrine"
    d.mkdir(exist_ok=True)
    path = d / filename
    path.write_text(
        DOCTRINE_FM.format(name=name, version=version, desc=desc, body=body),
        encoding="utf-8",
    )
    return path


def write_objective(root, *, name="room-language", version=1, body="Objective body.\n",
                    desc="d", kind="objective", applies="room_language_adaptor",
                    change_policy="operator-only", filename=None):
    d = root / "objectives"
    d.mkdir(exist_ok=True)
    path = d / (filename or f"{name}.md")
    path.write_text(
        OBJECTIVE_FM.format(name=name, kind=kind, version=version, applies=applies,
                            desc=desc, change_policy=change_policy, body=body),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "dreamer"
    root.mkdir()
    write_doctrine(root)
    write_objective(root)
    write_objective(root, name="room-exchange", body="Second objective.\n")
    return root


def _store(root, **kw) -> DreamerInstructions:
    return DreamerInstructions(root, metrics=Metrics(), **kw)


# --- assembly -------------------------------------------------------------


def test_the_assembly_is_the_doctrine_then_the_objective(tree):
    text = _store(tree).for_objective("room-language").text
    assert text.index("Doctrine body.") < text.index("Objective body.")
    assert "Second objective." not in text


def test_the_digest_is_over_the_text_that_is_actually_assembled(tree):
    """Assembling one way and hashing another is how a digest stops naming what
    was sent. Pinned so the two can never drift apart."""
    import hashlib

    instructions = _store(tree).for_objective("room-language")
    assert instructions.sha256 == hashlib.sha256(
        instructions.text.encode("utf-8")
    ).hexdigest()


def test_editing_the_objective_changes_the_digest(tree):
    before = _store(tree).for_objective("room-language").sha256
    _touch_newer(
        tree / "objectives" / "room-language.md",
        OBJECTIVE_FM.format(name="room-language", kind="objective", version=2,
                            applies="room_language_adaptor", desc="d",
                            change_policy="operator-only", body="Retuned.\n"),
    )
    assert _store(tree).for_objective("room-language").sha256 != before


def test_editing_the_doctrine_changes_every_adaptors_digest(tree):
    """The design's own argument, asserted rather than described (spec §4.3).
    `objective_sha256` was rejected because it names half the input: a doctrine
    edit changes the behaviour of EVERY adaptor at once — the largest blast
    radius in the subsystem — and under a half-input hash would leave no trace
    on any row anywhere."""
    store = _store(tree)
    before = {name: store.for_objective(name).sha256
              for name in ("room-language", "room-exchange")}
    _touch_newer(
        tree / "doctrine" / "dreamer.md",
        DOCTRINE_FM.format(name="dreamer", version=2, desc="d",
                           body="A different doctrine.\n"),
    )
    after = {name: store.for_objective(name).sha256
             for name in ("room-language", "room-exchange")}
    assert after["room-language"] != before["room-language"]
    assert after["room-exchange"] != before["room-exchange"]


def test_two_objectives_under_one_doctrine_get_different_digests(tree):
    store = _store(tree)
    assert store.for_objective("room-language").sha256 != \
        store.for_objective("room-exchange").sha256


def test_versions_carries_both_parts(tree):
    write_doctrine(tree, version=7)
    write_objective(tree, name="room-language", version=3)
    assert _store(tree).for_objective("room-language").versions == \
        {"dreamer": 7, "room-language": 3}


def test_an_unknown_objective_raises_rather_than_assembling_the_doctrine_alone(tree):
    """A run against the doctrine and no objective has no research goal at all:
    it would produce output that looks ordinary and answers nothing, and stamp
    those rows with a digest naming an instruction set no adaptor asked for."""
    with pytest.raises(InstructionError, match="unknown Dreamer objective 'facts'"):
        _store(tree).for_objective("facts")


def test_the_refusal_names_the_objectives_that_do_exist(tree):
    with pytest.raises(InstructionError, match="room-exchange, room-language"):
        _store(tree).for_objective("facts")


def test_objective_body_is_the_file_body_untouched(tree):
    assert _store(tree).objective_body("room-language") == "Objective body.\n"


# --- corpus rules ---------------------------------------------------------


def test_a_second_doctrine_file_is_a_startup_failure(tree):
    """Not a merge and not first-one-wins: the digest is over doctrine +
    objective, so a second file would make the assembly order — and therefore
    the hash — depend on a filename nobody chose deliberately."""
    write_doctrine(tree, name="other", filename="other.md")
    with pytest.raises(InstructionError, match="expected exactly one Dreamer doctrine"):
        _store(tree).for_objective("room-language")


def test_no_doctrine_file_at_all_is_a_startup_failure(tmp_path):
    root = tmp_path / "dreamer"
    root.mkdir()
    write_objective(root)
    with pytest.raises(InstructionError, match="no Dreamer doctrine file"):
        _store(root).for_objective("room-language")


def test_an_objective_in_the_doctrine_directory_is_refused(tmp_path):
    root = tmp_path / "dreamer"
    root.mkdir()
    (root / "doctrine").mkdir()
    (root / "doctrine" / "oops.md").write_text(
        OBJECTIVE_FM.format(name="oops", kind="objective", version=1, applies="x",
                            desc="d", change_policy="operator-only", body="b\n"),
        encoding="utf-8",
    )
    write_objective(root)
    with pytest.raises(InstructionError, match="does not belong in doctrine/"):
        _store(root).for_objective("room-language")


def test_an_unknown_kind_is_refused(tree):
    write_objective(tree, name="room-language", kind="constraint")
    with pytest.raises(InstructionError, match="kind 'constraint' is not one of"):
        _store(tree).for_objective("room-language")


def test_an_unknown_change_policy_is_refused(tree):
    write_objective(tree, name="room-language", change_policy="anyone-may-edit")
    with pytest.raises(InstructionError, match="change_policy 'anyone-may-edit'"):
        _store(tree).for_objective("room-language")


def test_a_missing_required_key_names_it(tree):
    (tree / "objectives" / "room-language.md").write_text(
        "---\nname: room-language\nkind: objective\nversion: 1\n---\nBody.\n",
        encoding="utf-8",
    )
    with pytest.raises(InstructionError, match=r"key\(s\): 'applies_to'"):
        _store(tree).for_objective("room-language")


def test_the_doctrines_own_frontmatter_keys_are_not_required(tree):
    """`order`, `authority_above`/`authority_below` and `not_here` are read by
    the doctrine's gates, and those gates do not run here (spec §5). Requiring a
    field no gate reads and no assembly renders is how frontmatter becomes
    ceremony."""
    assert _store(tree).for_objective("room-language").versions


def test_two_objectives_claiming_one_name_is_a_startup_failure(tree):
    write_objective(tree, name="room-language", filename="duplicate.md")
    with pytest.raises(InstructionError, match="duplicate objective name"):
        _store(tree).for_objective("room-language")


# --- reload ---------------------------------------------------------------


def test_an_edit_is_picked_up_without_reconstruction(tree):
    store = _store(tree)
    assert "Objective body." in store.for_objective("room-language").text
    _touch_newer(
        tree / "objectives" / "room-language.md",
        OBJECTIVE_FM.format(name="room-language", kind="objective", version=2,
                            applies="x", desc="d", change_policy="operator-only",
                            body="Edited on the bind mount.\n"),
    )
    assert "Edited on the bind mount." in store.for_objective("room-language").text


def test_startup_mode_ignores_later_edits(tree):
    store = _store(tree, reload="startup")
    store.for_objective("room-language")
    _touch_newer(
        tree / "objectives" / "room-language.md",
        OBJECTIVE_FM.format(name="room-language", kind="objective", version=2,
                            applies="x", desc="d", change_policy="operator-only",
                            body="Edited.\n"),
    )
    assert "Objective body." in store.for_objective("room-language").text


def test_a_broken_edit_keeps_the_last_good_set_and_counts_it(tree):
    metrics = Metrics()
    store = DreamerInstructions(tree, metrics=metrics)
    good = store.for_objective("room-language")

    _touch_newer(tree / "objectives" / "room-language.md", "totally broken")
    after = store.for_objective("room-language")

    assert after.sha256 == good.sha256
    assert store.reload_failures == 1
    assert metrics.get("nora_dreamer_instruction_reload_failures_total") == 1


def test_a_broken_corpus_at_startup_raises_instead_of_running_on_nothing(tmp_path):
    root = tmp_path / "dreamer"
    root.mkdir()
    write_doctrine(root)
    (root / "objectives").mkdir()
    (root / "objectives" / "room-language.md").write_text("broken", encoding="utf-8")
    with pytest.raises(InstructionError):
        _store(root).for_objective("room-language")


def test_lenient_mode_skips_a_broken_objective_and_the_adaptor_still_fails_loudly(tree):
    """Skipping is not silent tolerance. A skipped objective is simply absent,
    and `for_objective` refuses to assemble the doctrine alone — so the adaptor
    that wanted it fails, rather than running against half its instructions."""
    (tree / "objectives" / "room-language.md").write_text("broken", encoding="utf-8")
    store = _store(tree, strict=False)
    assert store.objective_names() == ("room-exchange",)
    with pytest.raises(InstructionError, match="unknown Dreamer objective"):
        store.for_objective("room-language")


# --- the shipped tree -----------------------------------------------------


def test_the_shipped_doctrine_parses_in_strict_mode():
    """The bind mount is a deploy: `nora/dreamer/` on master is what the running
    container reads, and a file that does not parse there costs a Dreamer tick
    rather than a test run. This is the check that keeps that from reaching it."""
    store = DreamerInstructions(SHIPPED, reload="startup", strict=True, metrics=Metrics())
    # `objective_names()` is what forces the scan: strict mode raises on any
    # malformed file in either subdirectory, including the doctrine itself.
    assert isinstance(store.objective_names(), tuple)


def test_there_is_exactly_one_shipped_dreamer_doctrine_and_it_is_operator_only():
    """One doctrine, and not an agent-editable one: this text steers a component
    that will hold `web_fetch`."""
    from nora.instructions.frontmatter import parse_document

    files = sorted((SHIPPED / "doctrine").glob("*.md"))
    assert len(files) == 1
    doc = parse_document(files[0], required=("name", "kind", "change_policy", "version"))
    assert doc.meta["kind"] == "dreamer-doctrine"
    assert doc.meta["change_policy"] == "operator-only"
    assert doc.body.strip()


# --- every shipped objective is accounted for ------------------------------
#
# Three objectives were LIFTED verbatim out of inline constants in
# `nora/language/`, and this file used to pin each one byte-for-byte against a
# pre-move fixture so a loader that mangled the text could not pass. All three
# were the language tier's and went with it, along with their fixtures — so
# there is no lift left to pin, and the remaining check is the other direction:
# nothing ships that nobody declared.
#
# The list is explicit rather than derived, and that is the whole guard. A set
# difference alone cannot tell an accidental file apart from an honest new one;
# naming each objective here makes adding one a deliberate act.
NATIVELY_AUTHORED = {
    "standing-debrief",
    # Phase 2 of issue #78 split the debrief's judge into its own objective.
    # Same directory for the same reason as `standing-debrief` below: a
    # TAG-PATH stage's hot-reloading instruction text, read through
    # `objective_body` and never `for_objective`.
    "standing-verdict",
    # Written for the curator, never lifted: the note tier had no
    # background pass before it, so there is no prior prompt to have
    # moved and no fixture that could pin one. Version 2 (issue #99) folds in
    # the former `note-revisit` objective — merged into the same file rather
    # than lifted from anywhere, so it stays here under the one name.
    "note-curation",
    # The note tier's CREATE PATH (issue #98). Same reason as
    # `note-curation`: a new adaptor has no prior prompt to have moved and no
    # fixture that could pin one.
    "note-candidates",
    # The proactive adaptor's decide turn (design/dreamer-agent-loop/39,
    # DEC-162). Written for it; version 1 is the starting text the two
    # proactive eval suites tune (39 §5), and nothing was lifted.
    "proactive",
    # The culture tier's only pass (design/culture-tier, DEC-182). Written for
    # it; there was no prior prompt, and the earlier plan to route this through
    # the memory system's dialectic endpoint never shipped a prompt at all —
    # it was falsified and closed unmerged (#237) before one existed.
    "culture",
}

# `standing-debrief` is the odd one in that set and it is worth saying why
# it lives in this directory at all: Standing is a TAG-PATH STAGE, not a
# Dreamer adaptor (design/dreamer-agent-loop/26-standing, §1) — it runs
# inside the reply slot rather than on the Dreamer's clock. The file is here
# because this is where the bind-mounted, hot-reloading instruction text
# lives, and the stage reads it through `objective_body` and never
# `for_objective`: the latter prepends the Dreamer's research-framed
# doctrine, which on a task with no research is one of the two named causes
# of the token runaway that made the debrief a plain completion.


def test_every_shipped_objective_is_declared_native():
    """An objective appearing with no entry in `NATIVELY_AUTHORED` is a file
    nobody meant to ship, or one somebody added without deciding it belonged
    on the bind mount. Both are worth a red test.

    The positive control is the assertion under it: an empty
    `objective_names()` would satisfy the difference vacuously, and an empty
    Dreamer directory is exactly the failure this cannot be allowed to miss."""
    store = DreamerInstructions(SHIPPED, reload="startup", strict=True, metrics=Metrics())
    shipped = set(store.objective_names())
    unaccounted = shipped - NATIVELY_AUTHORED
    assert unaccounted == set(), (
        "objective(s) with no NATIVELY_AUTHORED entry: "
        f"{', '.join(sorted(unaccounted))}"
    )
    assert shipped == NATIVELY_AUTHORED, (
        "an objective was declared here and is not on disk — a stale entry "
        "makes the check above pass over a file that no longer ships"
    )


