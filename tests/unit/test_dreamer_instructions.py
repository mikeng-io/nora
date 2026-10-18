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
from tests.objectives import shipped_objectives

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


def test_each_half_is_named_by_its_own_tag(tree):
    """The doctrine and the objective are each wrapped in their own tag, so the
    second document, with different authority, is visibly marked as having
    begun (the adaptor's rendered INPUT is XML-framed throughout)."""
    text = _store(tree).for_objective("room-language").text
    assert text.startswith('<dreamer_doctrine name="dreamer">\n')
    assert text.endswith("\n</dreamer_objective>")
    assert text.index("</dreamer_doctrine>") < text.index('<dreamer_objective name=')
    assert text.count("<dreamer_doctrine") == text.count("<dreamer_objective") == 1


def test_the_digest_covers_the_envelope_and_not_only_the_bodies(tree):
    """A digest taken over the bodies would name LESS than the prompt does, so an
    operator who renamed an objective would move the served text and leave every
    row's `instruction_sha256` unchanged."""
    import hashlib

    instructions = _store(tree).for_objective("room-language")
    bodies_only = hashlib.sha256(
        b"Doctrine body.\n\nObjective body.\n"
    ).hexdigest()
    assert instructions.sha256 != bodies_only


def test_renaming_an_objective_changes_the_digest_with_both_bodies_untouched(tree):
    """The direct consequence of the line above, and the one an operator can
    actually trip: `name` is rendered into the served tag, so it is part of the
    instruction set's identity even though no body moved."""
    before = _store(tree).for_objective("room-language").sha256
    (tree / "objectives" / "room-language.md").unlink()
    write_objective(tree, name="room-idiom", body="Objective body.\n")
    after = _store(tree).for_objective("room-idiom").sha256
    assert after != before


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
    """The design's own argument, asserted rather than described. A hash of the
    objective alone would name half the input: a doctrine edit changes the
    behaviour of EVERY adaptor at once — the largest blast radius in the
    subsystem — and would leave no trace on any row anywhere."""
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


PRELUDE_FM = """\
---
name: {name}
kind: dreamer-prelude
version: {version}
applies_to: [{applies}]
description: d
change_policy: operator-only
---
{body}"""

SKILL_FM = """\
---
name: {name}
kind: skill
version: {version}
applies_to: [cognition_session]
description: d
change_policy: operator-only
---
{body}"""


def write_prelude(root, *, name="room-language-prelude", applies="room-language",
                  version=1, body="Prelude body.\n", filename=None):
    d = root / "preludes"
    d.mkdir(exist_ok=True)
    path = d / (filename or f"{name}.md")
    path.write_text(PRELUDE_FM.format(name=name, applies=applies, version=version, body=body),
                    encoding="utf-8")
    return path


def write_skill(root, *, name="plans", version=1, body="\nSkill body.\n\n", filename=None):
    d = root / "skills"
    d.mkdir(exist_ok=True)
    path = d / (filename or f"{name}.md")
    path.write_text(SKILL_FM.format(name=name, version=version, body=body), encoding="utf-8")
    return path


def test_a_prelude_replaces_the_doctrine_for_the_objective_it_names_only(tree):
    """Cognition reads its own prelude; every other adaptor's text and digest are
    unchanged by the prelude."""
    before = _store(tree).for_objective("room-exchange")
    write_prelude(tree)
    store = _store(tree)
    got = store.for_objective("room-language")
    assert got.text.startswith('<dreamer_doctrine name="room-language-prelude">\nPrelude body.')
    assert "Doctrine body." not in got.text
    assert got.versions == {"room-language-prelude": 1, "room-language": 1}
    assert store.for_objective("room-exchange") == before


def test_a_prelude_moves_its_objectives_digest(tree):
    before = _store(tree).for_objective("room-language").sha256
    write_prelude(tree)
    assert _store(tree).for_objective("room-language").sha256 != before


def test_two_preludes_for_one_objective_are_a_startup_failure(tree):
    write_prelude(tree)
    write_prelude(tree, name="another-prelude")
    with pytest.raises(InstructionError, match="has two preludes"):
        _store(tree).objective_names()


def test_a_prelude_naming_no_loaded_objective_is_a_startup_failure(tree):
    """A typo there would serve the shared doctrine to the objective it meant."""
    write_prelude(tree, applies="room-langauge")
    with pytest.raises(InstructionError, match="not a loaded objective"):
        _store(tree).objective_names()


#: Every `applies_to` that is not a list of objective names, as the YAML a prelude
#: would carry. Left unchecked, each raises TypeError (an int or a bool is not iterable, a nested
#: list is unhashable at the dict lookup) or is quietly misread (a mapping iterates its
#: keys); each must be an `InstructionError`.
BAD_APPLIES_TO = [
    "5", "true", "3.5", "[[room-language]]", "[5]", "[true]", "{room-language: 1}",
    "room-language", "~", "[~]", "[[]]", "[]",
]


def write_prelude_raw(root, applies_yaml, *, name="room-language-prelude"):
    d = root / "preludes"
    d.mkdir(exist_ok=True)
    path = d / f"{name}.md"
    path.write_text(
        "---\n"
        f"name: {name}\nkind: dreamer-prelude\nversion: 1\napplies_to: {applies_yaml}\n"
        "description: d\nchange_policy: operator-only\n---\nPrelude body.\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("applies", BAD_APPLIES_TO)
def test_a_prelude_applies_to_that_is_not_a_list_of_names_is_an_instruction_error_at_startup(
    tree, applies,
):
    """The watcher tolerates only `InstructionError`; at startup anything else crash-loops
    the process."""
    write_prelude_raw(tree, applies)
    with pytest.raises(InstructionError, match=r"room-language-prelude\.md: applies_to must be"):
        _store(tree).objective_names()


@pytest.mark.parametrize("applies", BAD_APPLIES_TO)
def test_a_bad_prelude_applies_to_on_a_hot_reload_keeps_the_last_good_scan_and_counts_it(
    tree, applies,
):
    """A bad shape must not raise TypeError out of the reload: the watcher does
    not catch it, so EVERY `for_objective` would raise until the file was fixed."""
    write_prelude(tree)
    metrics = Metrics()
    store = DreamerInstructions(tree, metrics=metrics)
    good = store.for_objective("room-language")
    assert good.versions == {"room-language-prelude": 1, "room-language": 1}

    _touch_newer(tree / "preludes" / "room-language-prelude.md",
                 write_prelude_raw(tree, applies).read_text(encoding="utf-8"))
    still = store.for_objective("room-language")            # does not raise

    assert still == good
    assert store.reload_failures == 1
    assert metrics.get("nora_dreamer_instruction_reload_failures_total") == 1
    # and a fixed file is served again
    _touch_newer(tree / "preludes" / "room-language-prelude.md",
                 write_prelude(tree, version=2, body="Fixed.\n").read_text(encoding="utf-8"))
    assert store.for_objective("room-language").versions["room-language-prelude"] == 2


def test_lenient_mode_skips_a_prelude_whose_applies_to_is_not_a_list(tree):
    """Like any broken instruction file: absent, and the objective then reads the doctrine."""
    write_prelude_raw(tree, "5")
    store = _store(tree, strict=False)
    assert store.for_objective("room-language").versions == {
        "dreamer": 1, "room-language": 1,
    }


def test_a_prelude_in_the_doctrine_directory_is_refused(tree):
    """A second file in `doctrine/` stops an older image at its next restart."""
    path = write_prelude(tree)
    path.rename(tree / "doctrine" / path.name)
    with pytest.raises(InstructionError, match="expected exactly one Dreamer doctrine"):
        _store(tree).objective_names()


def test_a_skill_is_served_as_its_body_with_a_digest_over_exactly_that(tree):
    import hashlib

    write_skill(tree)
    skill = _store(tree).skill("plans")
    assert skill is not None
    assert skill.instructions == "Skill body."
    assert skill.sha256 == hashlib.sha256(b"Skill body.").hexdigest()
    assert skill.version == 1
    assert _store(tree).skill("absent") is None


def test_a_skill_edit_is_picked_up_without_reconstruction(tree):
    path = write_skill(tree)
    store = _store(tree)
    assert store.skill("plans").instructions == "Skill body."
    _touch_newer(path, SKILL_FM.format(name="plans", version=2, body="Edited.\n"))
    got = store.skill("plans")
    assert got is not None and (got.instructions, got.version) == ("Edited.", 2)


def test_a_skill_and_a_prelude_leave_every_objectives_text_alone_when_unused(tree):
    before = {n: _store(tree).for_objective(n) for n in ("room-language", "room-exchange")}
    write_skill(tree)
    after = _store(tree)
    assert {n: after.for_objective(n) for n in before} == before
    assert after.versions()["plans"] == 1


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
    the doctrine's gates, and those gates do not run here. Requiring a
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


def test_every_shipped_objective_assembles_inside_the_two_tags():
    """Over the real tree rather than a fixture: an objective whose body happens
    to open a tag it never closes would leave the seam unreadable for that one
    adaptor only, and nothing in the tmp_path half would see it."""
    store = shipped_objectives()
    for name in store.objective_names():
        text = store.for_objective(name).text
        # Cognition reads its own prelude in the doctrine's place; every other
        # adaptor reads the shared doctrine.
        doctrine = "cognition-prelude" if name == "cognition" else "dreamer"
        assert text.startswith(f'<dreamer_doctrine name="{doctrine}">\n'), name
        assert text.endswith("\n</dreamer_objective>"), name
        assert text.count("<dreamer_doctrine") == 1, name
        assert text.count("</dreamer_doctrine>") == 1, name
        assert text.count(f'<dreamer_objective name="{name}">') == 1, name
        assert text.count("</dreamer_objective>") == 1, name


def test_no_shipped_description_reaches_the_model():
    """`render_part` promotes `name` and stops. These descriptions were authored
    as repo metadata, not prompt text — the doctrine's ends with a note about
    which startup gates the repo does not run — so promoting the field would
    change seven `operator-only` documents' audience without their author's say.
    Pinned from the other end: the descriptions are checked ABSENT from what is
    served."""
    from nora.instructions.frontmatter import parse_document

    store = shipped_objectives()
    doctrine_file = next(iter(sorted((SHIPPED / "doctrine").glob("*.md"))))
    doctrine_desc = str(parse_document(
        doctrine_file, required=("name", "description")
    ).meta["description"])
    assert len(doctrine_desc) > 100          # the field really is prose, not a label
    for name in store.objective_names():
        text = store.for_objective(name).text
        assert doctrine_desc not in text
        desc = str(parse_document(
            SHIPPED / "objectives" / f"{name}.md", required=("name", "description")
        ).meta["description"])
        assert desc not in text, name
    # the prelude's and the skill's descriptions are repo metadata too
    cognition = store.for_objective("cognition").text
    skill = store.skill("cognition-register")
    assert skill is not None
    for path in (SHIPPED / "preludes" / "cognition-prelude.md",
                 SHIPPED / "skills" / "cognition-register.md"):
        desc = str(parse_document(path, required=("name", "description")).meta["description"])
        assert len(desc) > 100
        assert desc not in cognition and desc not in skill.instructions, path.name


# --- every shipped objective is accounted for ------------------------------
# The list is explicit rather than derived, and that is the whole guard. A set
# difference alone cannot tell an accidental file apart from an honest new one;
# naming each objective here makes adding one a deliberate act.
NATIVELY_AUTHORED = {
    "standing-debrief",
    # The culture tier's only pass (DEC-182).
    "culture",
    # The Cognition session's instruction (DEC-244); it lists no tool and no limit.
    "cognition",
    # A person's Character session (DEC-278).
    "character-person",
    # The unit's Character session.
    "character-unit",
    # Her own Character session.
    "character-nora",
    # A person's Persona session, in shadow (DEC-317).
    "persona-person",
    # The hypothesis verdict judge: it grades what persona-person wrote, and is
    # never the writer (45 §3).
    "persona-verdict",
    # The Lessons session.
    "lessons",
    # The doctrine check: an instrument's instruction, read through
    # `objective_body`, never with the Dreamer doctrine.
    "doctrine-check",
}

# `standing-debrief` lives in this directory although Standing is a TAG-PATH
# STAGE, not a Dreamer adaptor: it runs inside the reply slot rather than on the
# Dreamer's clock. The file is here because this is where the bind-mounted,
# hot-reloading instruction text lives; the stage reads it through
# `objective_body` and never `for_objective`, which prepends the Dreamer's
# research-framed doctrine.


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


