"""A frozen wake's diff rebuilds the after-state the judges read."""

from scripts.cognition_loop.rejudge import frozen_diff


def test_the_after_state_is_created_revised_and_unchanged_never_retired():
    frozen = {"diff": {"note": {
        "created": [{"status": "created", "entry_id": "n1", "after": {"t": "a"}}],
        "revised": [{"status": "revised", "entry_id": "n2",
                     "before": {"t": "x"}, "after": {"t": "y"}}],
        "retired": [{"status": "retired", "entry_id": "n3", "before": {"t": "z"}}],
        "unchanged": [{"status": "unchanged", "entry_id": "n4",
                       "before": {"t": "u"}, "after": {"t": "u"}}],
    }}}
    diff, after = frozen_diff(frozen)
    assert set(after.entries("note")) == {"n1", "n2", "n4"}
    assert after.entries("note")["n2"] == {"t": "y"}
    assert "note:n3" in diff.entry_ids()
