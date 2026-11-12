"""Drafted expectations are kept only when they fit the case format."""

from scripts.cognition_loop.draft import parse


def test_only_well_formed_state_expectations_are_kept():
    raw = 'Here: [{"polarity": "must", "kind": "note", "text": "the plan note names Friday"},' \
          ' {"polarity": "maybe", "kind": "note", "text": "x"},' \
          ' {"polarity": "must_not", "kind": "tool", "text": "y"},' \
          ' {"polarity": "must_not", "kind": "profile", "text": " "}]'
    assert parse(raw) == [{"polarity": "must", "kind": "note",
                           "text": "the plan note names Friday"}]
    assert parse("no json here") == []
