"""The Orient-instruction experiment's invariants: P0 composes byte-identically,
P1 is a strict deletion-only subsequence of it, and P1 keeps every safety
sentence. The P0 baseline is the recorded golden so a future instruction
drift fails here rather than silently moving the comparison arm.
"""

from pathlib import Path

from nora.language.instruction_variants import (
    SAFETY_SENTENCES,
    admitted_relations,
    instruction_variant_text,
    nonadmitted_relation_usage,
    normalise,
    p1_subset_failures,
    safety_failures,
)
from tests.objectives import shipped_objectives


def _p0() -> str:
    from nora.language.comprehend_render import render_session_system
    store = shipped_objectives()
    loaded = store.for_objective("cognition")
    skill = store.skill("cognition-register")
    assert skill is not None
    return render_session_system(
        loaded.text, f"\n\n# Skill: {skill.name}\n\n{skill.instructions}\n",
        memory=True)


def test_p0_is_byte_identical_to_the_recorded_composition():
    golden = (
        Path(__file__).parents[2] / "tests" / "fixtures" / "cognition_system_p0.txt"
    ).read_text()
    assert _p0() == golden


def test_p1_is_a_strict_deletion_only_subsequence_of_p0():
    p1 = instruction_variant_text("P1")
    assert p1 is not None
    assert p1_subset_failures(_p0(), p1) == []


def test_p1_keeps_every_safety_sentence():
    p1 = normalise(instruction_variant_text("P1") or "")
    missing = [
        name for name, forms in SAFETY_SENTENCES
        if all(normalise(form) not in p1 for form in forms)
    ]
    assert missing == []


def test_the_check_guards_fire_on_broken_variants():
    """The check is fail-closed: each guard must fire on its own break — a
    missing safety sentence, and a non-admitted name used as a relation."""
    p1 = instruction_variant_text("P1") or ""
    without_safety = p1.replace("A plan does not establish attendance.\n", "")
    assert without_safety != p1
    assert safety_failures(without_safety) == ["attendance"]
    misuse = "use relation `settles`, `cancels` and X -records-> Y here."
    assert nonadmitted_relation_usage(misuse) == {"settles", "cancels", "records"}
    assert nonadmitted_relation_usage(misuse).isdisjoint(admitted_relations())
    # Ordinary prose using the same words is not a relation use.
    assert not nonadmitted_relation_usage(
        "whatever settles one settles the other; a reading records it")
