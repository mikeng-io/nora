"""One spelling of "this span is a model call", and it lives in one module.

Langfuse renders a span as a GENERATION — with a model, a latency attributed
to the call, and somewhere to put input and output — only when the span carries
`langfuse.observation.*`; the OTel GenAI convention (`gen_ai.*`) is the other
half, what a future collector and Jaeger's attribute search read. They are two
spellings of one fact, and a call site that sets neither produces a rectangle
named after a model call rather than a trace of one.

**The war-story is drift, measured.** Two call sites hand-rolled the block —
`nora/ingest/relevance_gate.py` and `nora/engagement/stages/debrief.py` — and by
the time there were two they had already diverged in what they carried. A third
group of model calls (the media comprehension, and everything reached through
`nora.infra`) carried no spelling at all. Read back against the live collector
on 2026-09-09: 1041 traces, four different dialects, and the media read absent
from all of them.

So the vocabulary now lives exactly once, in `nora.obs.tracing.generation` /
`annotate_generation`, and this test is what keeps it there. It is the same
rule AGENTS.md already states for metric labels — "label vocabularies are
DERIVED from the code, never hand-copied" — pointed at span attributes, and it
derives its own forbidden list from `GENERATION_ATTRIBUTES` rather than
repeating the strings, so a new attribute added to the helper is guarded the
moment it is added.

PROBE (run 2026-09-09): put `"langfuse.observation.type": "generation"` back
into the `span(...)` call in `nora/ingest/relevance_gate.py` — this test failed
naming that file and line. Removed immediately after.
"""

from __future__ import annotations

import ast
from pathlib import Path

from nora.obs.tracing import GENERATION_ATTRIBUTES, generation

ROOT = Path(__file__).resolve().parents[2]

#: The one module allowed to write these strings — it is where they are defined.
_HOME = ROOT / "nora" / "obs" / "tracing.py"

#: The NAMESPACES that make a span a generation. `GENERATION_ATTRIBUTES` names
#: one exact key from each; the drift was never in those two keys but in their
#: neighbours — `langfuse.observation.input` present on one block and absent
#: from the other, `gen_ai.usage.*` absent from both until a live trace showed
#: every gate call priced at zero tokens. So the check is on the whole
#: namespace: anything a collector reads as part of "this span is a model
#: call" belongs to the helper.
#:
#: Derived by taking each declared key back to its root namespace rather than
#: written out, so adding a key from a third vendor to `GENERATION_ATTRIBUTES`
#: extends this guard without touching this file.
_FAMILIES = tuple(sorted({
    ("langfuse.observation." if key.startswith("langfuse.") else
     key.split(".")[0] + ".")
    for key in GENERATION_ATTRIBUTES
}))


def _production_modules() -> list[Path]:
    return sorted(
        p for p in (ROOT / "nora").rglob("*.py")
        if ".venv" not in p.parts and p != _HOME
    )


def test_no_module_spells_a_generation_attribute_by_hand() -> None:
    """Every `langfuse.observation.*` / `gen_ai.*` literal outside the helper.

    A string CONSTANT, read off the AST rather than by grep, so a mention in a
    docstring or a comment — which is prose about the convention, not a span
    carrying it — does not fire. That distinction matters here: several modules
    now explain in comments why they route through the helper.
    """
    offenders: list[str] = []
    for path in _production_modules():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # not ours to police
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if node.value.startswith(_FAMILIES):
                offenders.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}: {node.value!r}"
                )
    assert not offenders, (
        "these call sites spell a generation attribute by hand instead of "
        "going through `nora.obs.tracing.generation` / `annotate_generation`, "
        "which is how the two that existed drifted apart:\n  "
        + "\n  ".join(offenders)
    )


def test_the_helper_still_emits_both_spellings(exported_spans) -> None:
    """The POSITIVE CONTROL for the test above, and it is not optional.

    `test_no_module_spells_a_generation_attribute_by_hand` is a zero-assertion:
    it is equally true of a codebase that emits these attributes nowhere at
    all — including one where somebody deleted them from the helper, which
    would silently return every generation to a plain span while this file
    stayed green. AGENTS.md: "pair every zero-assertion with a positive
    control; assert something only a run that RAN produces."

    **It reads an EXPORTED SPAN, not the helper's source**, and that correction
    is the reason this docstring is longer than the test. The first version
    searched `tracing.py`'s text for each key — which can never fail, because
    `GENERATION_ATTRIBUTES` is itself a tuple of those strings sitting in that
    same file. A control that is true by construction is not a control, which
    is exactly the failure the rule it cites exists to prevent. Probed: with
    `"langfuse.observation.type"` deleted from `generation`'s attribute dict,
    the source version still passed; this version fails.
    """
    with generation("probe", model="a-model"):
        pass
    spans = [s for s in exported_spans.get_finished_spans() if s.name == "probe"]
    assert spans, "the helper opened no span at all"
    attributes = spans[0].attributes or {}
    for key in GENERATION_ATTRIBUTES:
        assert key in attributes, (
            f"{key} is declared in GENERATION_ATTRIBUTES but `generation` no "
            "longer puts it on the span — every generation is now a plain span"
        )
