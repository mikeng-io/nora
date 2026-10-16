"""Every Dreamer adaptor but Cognition reads, byte for byte, the instruction it
read before Cognition had its own prelude.

The Cognition session does not read the shared doctrine (`nora/dreamer/doctrine/
dreamer.md`, «You look; you do not act… nobody sees this»), which is false for a
writer, and reads `nora/dreamer/preludes/cognition-prelude.md` in its place. That
is a change to ONE adaptor's instruction. Editing `dreamer.md` instead would
change every adaptor's at once and is the largest blast radius in the subsystem
(`nora.instructions.dreamer`'s docstring); this file turns that red.

The goldens in `tests/fixtures/dreamer_instructions_pre_prelude_goldens.json`
were captured by calling the real `DreamerInstructions` over the shipped tree
before any loader or doctrine file changed. What is compared is each objective's
whole assembled text, its digest (the `instruction_sha256` every row it writes
carries), its versions and its raw body (`objective_body`, the path
`standing-debrief` reads). Nothing here regenerates a golden: changing one is a
model-visible change for another adaptor and belongs in a change that says so.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.objectives import shipped_objectives

GOLDENS = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures"
     / "dreamer_instructions_pre_prelude_goldens.json").read_text(encoding="utf-8")
)["objectives"]


def test_the_goldens_cover_every_shipped_objective_but_cognition():
    """A new objective with no golden is not silently unpinned."""
    assert set(GOLDENS) == set(shipped_objectives().objective_names()) - {"cognition"}


@pytest.mark.parametrize("name", sorted(GOLDENS))
def test_every_other_adaptor_reads_exactly_what_it_read_before(name):
    store = shipped_objectives()
    got = store.for_objective(name)
    want = GOLDENS[name]
    assert got.text == want["text"], name
    assert got.sha256 == want["sha256"], name
    assert got.versions == want["versions"], name
    assert store.objective_body(name) == want["objective_body"], name
