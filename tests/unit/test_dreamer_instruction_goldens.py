"""Pin the assembled instruction text, provenance and raw objective for each job.

These snapshots change when the authored policy deliberately changes. They guard
assembly and version attribution; model behavior is measured separately.
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
def test_each_adaptor_reads_its_recorded_instruction(name):
    store = shipped_objectives()
    got = store.for_objective(name)
    want = GOLDENS[name]
    assert got.text == want["text"], name
    assert got.sha256 == want["sha256"], name
    assert got.versions == want["versions"], name
    assert store.objective_body(name) == want["objective_body"], name
