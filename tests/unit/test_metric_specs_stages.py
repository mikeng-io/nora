"""Every `nora_errors_total{stage=...}` a fail-soft path declares is one some call site raises.

`tests/architecture/test_every_recorded_metric_is_declared.py` covers metric
NAMES. Nothing covered label VALUES, and for a fail-soft block they are the
whole instrument: the block is lost and the turn is not, so absence is the only
symptom and `nora_errors_total` is the only tell. A declaration nothing
increments is the mirror lie `docs/code-standards.md` already names ("a declared
metric with no call site is a lie").
"""

from __future__ import annotations

import re
from pathlib import Path

from nora.obs.metric_specs import _COUNTERS

REPO_ROOT = Path(__file__).resolve().parents[2]


def _errors_description() -> str:
    for spec in _COUNTERS:
        if spec.name == "nora_errors_total":
            return spec.help_text
    raise AssertionError("nora_errors_total is not declared in metric_specs")


def test_every_declared_stage_is_one_some_call_site_actually_raises() -> None:
    """Declare as many stages as you like, but each one has to exist in the
    code that runs."""
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
