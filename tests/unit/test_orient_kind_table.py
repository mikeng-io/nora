"""Node 33: the live wake's input and admission, without a model call."""

import pytest

from nora.app.comprehend import Proposal, proposal_scope

TEXT = "I like Lantern Game. I have a spare controller."




def _payload():
    """One relation statement: a person likes an inline new work, citing row 1."""
    return {"statements": [{
        "type": "relation", "rel": "likes",
        "subject": "@person:generated-person",
        "object": {"new": {"kind": "work", "name": "Lantern Game"}},
        "cite": [{"source": 1, "quote": "I like Lantern Game"}]}]}


def _admit_args(state, payload=None):
    """The `_admit` arguments over a hand-built single-item state."""
    item = state.memory_items[0]
    return (
        item.work,
        Proposal.model_validate(payload or _payload()),
        state.graph_fetched,
        state.session_id,
        proposal_scope(
            [item], state.graph_fetched,
            shown_keys=getattr(state, "shown_keys", frozenset())),
    )


def _reading(quote):
    return {"type": "reading",
            "about": [{"person_id": "generated-person", "reference": "I"}],
            "what": "has a spare controller", "where": None, "how": "stated",
            "happened_at": {"source": 1, "expression": None},
            "cite": [{"source": 1, "quote": quote}]}






@pytest.mark.parametrize("kind", ["note", "lesson", "reading", "profile line"])
async def test_a_notebook_row_is_refused_as_an_entity_kind(kind):
    """A kind-table row that is not a graph entity kind refuses; it never reaches the store."""
    payload = _payload()
    payload["statements"][0]["object"] = {"new": {"kind": kind, "name": "Generated"}}
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="literal_error"):
        Proposal.model_validate(payload)















def test_every_primary_refusal_reason_has_a_field_and_a_repair():
    """The repair map covers the whole refusal vocabulary: a reason with no
    repair must never reach the model."""
    from nora.domain.comprehend import REFUSAL_REASONS, REFUSAL_REPAIR

    assert set(REFUSAL_REPAIR) == set(REFUSAL_REASONS)
    assert all(
        spec["field"] and spec["repair"] for spec in REFUSAL_REPAIR.values())








GENERIC_ATTRIBUTE_REPAIR = (
    "An attribute of this statement was refused for {reason}. Read the "
    "connected state before acting on it; re-sending a statement whose "
    "relation was saved is refused."
)


def _assert_generic_attribute_parts(entry):
    """Every refused attribute part carries the generic message, its own
    attribute name and cite — and claims NOTHING about saved state."""
    for part in entry.get("refused_parts", ()):
        if "attribute" not in part:
            continue
        assert part["field"] == f"attributes.{part['attribute']}"
        assert part["repair"] == GENERIC_ATTRIBUTE_REPAIR.format(
            reason=part["reason"])
        # The message makes NO saved-state claim: the only truth it states
        # is the refusal reason and that a re-send of a saved statement is
        # refused — verified below by the resubmission probe.
        assert "not saved" not in part["repair"]
        assert "saved from" not in part["repair"]
        assert "do not retry" not in part["repair"]





































































