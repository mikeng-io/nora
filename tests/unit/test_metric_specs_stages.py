"""A `nora_errors_total{stage=...}` label a fail-soft path raises is DECLARED.

`tests/architecture/test_every_recorded_metric_is_declared.py` covers metric
NAMES. Nothing covered label VALUES, and for a fail-soft block they are the
whole instrument: the block is lost and the turn is not, so absence is the only
symptom and `nora_errors_total` is the only tell. A stage nobody declared is a
series nobody knows to graph — like a counter that runs on every tagged turn
but is absent from `/metrics`.

So: every stage constant the ambient loop's memory reads use must appear
verbatim in `metric_specs`' own `nora_errors_total` description.

The reverse direction is a check on the DECLARATION, not a cap on it: declaring
other fail-soft stages (`fire_peer_card`, `fire_notes`, `fire_standing`,
`fire_unit`, `fire_context`, `tagger_card`, `proactive_self_card`,
`proactive_standing`, `fire_feedback`) must stay legal. What the reverse check
asks is that a declared stage is one some call site actually RAISES — because a declaration nothing
increments is the mirror lie `AGENTS.md` already names ("a declared metric with
no call site is a lie").
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from nora.app.fire import (
    STAGE_FIRE_MEMORY,
    STAGE_FIRE_PEOPLE,
)
from nora.obs.metric_specs import _COUNTERS

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The ambient loop's memory-read stages, as the code spells them. Imported from the
#: modules rather than retyped: a vocabulary hand-copied into its own test
#: measures the copy.
_STAGES = (
    STAGE_FIRE_PEOPLE,
    STAGE_FIRE_MEMORY,
)


def _errors_description() -> str:
    for spec in _COUNTERS:
        if spec.name == "nora_errors_total":
            return spec.help_text
    raise AssertionError("nora_errors_total is not declared in metric_specs")


@pytest.mark.parametrize("stage", _STAGES)
def test_every_ambient_memory_stage_is_declared(stage: str) -> None:
    assert f'stage="{stage}"' in _errors_description(), (
        f"the fail-soft path raising stage={stage!r} counts on a series no "
        f"declaration names — an operator cannot graph what nothing documents"
    )


def test_every_declared_stage_is_one_some_call_site_actually_raises() -> None:
    """The other direction WIDENS rather than caps: declare as many
    stages as you like, but each one has to exist in the code that runs."""
    declared = set(re.findall(r'stage="([a-z_]+)"', _errors_description()))
    assert declared, "the description declares no stage at all"
    sources = "\n".join(
        p.read_text() for p in (REPO_ROOT / "nora").rglob("*.py")
    )
    phantom = {
        stage for stage in declared
        if f'"{stage}"' not in sources and f"'{stage}'" not in sources
    }
    assert not phantom, (
        f"metric_specs declares {sorted(phantom)} on nora_errors_total, but no "
        f"call site under nora/ spells them — either the path went away or the "
        f"declaration was written for code that never landed"
    )
