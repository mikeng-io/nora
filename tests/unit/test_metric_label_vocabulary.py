"""A label VALUE a call site raises is declared in the metric's help text.

`test_every_recorded_metric_is_declared` covers metric NAMES and states
labels out of scope; `test_metric_specs_stages` pins `nora_errors_total`'s
stage values. The projection skip counter's `outcome=` is the other value
that must not silently widen: `graph_entry_about` refuses a relation
fail-closed with `outcome="ungrounded"`, and a value the code can raise but
the declaration omits is a series nobody can graph.
"""

from nora.domain.memory_admission import GRAPH_PROJECTION_OUTCOMES
from nora.obs.metric_specs import _COUNTERS


def test_the_ungrounded_projection_outcome_is_declared() -> None:
    assert "ungrounded" in GRAPH_PROJECTION_OUTCOMES
    spec = next(
        s for s in _COUNTERS if s.name == "nora_memory_graph_projection_total"
    )
    assert "ungrounded" in spec.help_text
