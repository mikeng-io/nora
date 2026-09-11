"""A `nora_errors_total{stage=...}` label a fail-soft path raises is DECLARED.

`tests/architecture/test_every_recorded_metric_is_declared.py` covers metric
NAMES. Nothing covered label VALUES, and for a fail-soft block they are the
whole instrument: the block is lost and the turn is not, so absence is the only
symptom and `nora_errors_total` is the only tell. A stage nobody declared is a
series nobody knows to graph — which is `nora_peer_card_empty_total`'s own
lesson (a counter that ran on every tagged turn and was absent from `/metrics`
for the life of a deployment), one axis over.

So: every stage constant the ambient loop's memory reads use must appear
verbatim in `metric_specs`' own `nora_errors_total` description. The check runs
in BOTH directions — a constant missing from the description fails, and a
declared stage with no constant behind it fails too, because a declaration
nothing increments is the mirror lie `AGENTS.md` already names ("a declared
metric with no call site is a lie").
"""

from __future__ import annotations

import pytest

from nora.app.fire import (
    STAGE_FIRE_MEMORY,
    STAGE_FIRE_PEOPLE,
    STAGE_FIRE_SELF_CARD,
)
from nora.language.proactive import STAGE_PROACTIVE_ROSTER_INDEX
from nora.obs.metric_specs import _COUNTERS

#: The four issue-#210 stages, as the code spells them. Imported from the
#: modules rather than retyped: a vocabulary hand-copied into its own test
#: measures the copy (`evals/harness.py`'s `TRIGGER_TEXT` war-story).
_STAGES = (
    STAGE_FIRE_SELF_CARD,
    STAGE_FIRE_PEOPLE,
    STAGE_FIRE_MEMORY,
    STAGE_PROACTIVE_ROSTER_INDEX,
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


def test_the_declaration_names_no_stage_the_code_does_not_raise() -> None:
    """The other direction. A stage declared and never incremented reads as
    «that path is healthy» when it is «that path was deleted»."""
    import re

    declared = set(re.findall(r'stage="([a-z_]+)"', _errors_description()))
    unknown = declared - set(_STAGES)
    assert not unknown, (
        f"metric_specs declares {sorted(unknown)} on nora_errors_total, but no "
        f"stage constant spells them — either the call site went away or the "
        f"declaration was written for code that never landed"
    )
