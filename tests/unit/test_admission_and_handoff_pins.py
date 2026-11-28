"""Identity grounding in memory admission, and the queued handoff's person rebuild."""

from types import SimpleNamespace

from nora.app.bridge_payloads import _person_dict, _person_from_dict
from nora.domain.memory_admission import _grounded_identity
from nora.domain.models import PersonRef


def _ground(ref, *, shown, authored_by=()):
    relation = SimpleNamespace(unit="u1")
    evidence = (SimpleNamespace(source="s1"),)
    sources = {"s1": SimpleNamespace(identities=tuple(authored_by))}
    return _grounded_identity(
        ("person", f"u1:person:{ref}"), relation, evidence, sources, frozenset(shown),
    )


def test_a_person_pointer_needs_a_cited_source_in_their_scope_or_a_row_they_authored():
    assert _ground("ada", shown=(), authored_by=()) is None
    assert _ground("ada", shown=(), authored_by=("ada",)) is not None
    assert _ground("ada", shown=("ada",), authored_by=()) is not None


def test_a_queued_handoff_with_a_removed_person_key_is_still_rebuilt():
    person = PersonRef("ada", "Ada")
    data = {**_person_dict(person), "peer_id": "removed-field"}
    rebuilt = _person_from_dict(data)
    assert (rebuilt.person_id, rebuilt.name) == ("ada", "Ada")


def test_the_handoff_writer_no_longer_emits_the_removed_person_key():
    assert "honcho_peer_id" not in _person_dict(PersonRef("ada", "Ada"))


def test_a_reaction_label_marks_what_it_leaves_out_with_a_count():
    from nora.domain.models import RowReactions

    reactions = RowReactions(
        counts=tuple((f"emoji-{i}", 1) for i in range(40)), recorded=40,
    )
    label = reactions.bounded_label(20)
    assert label is not None and "+" in label.split()[-1]
