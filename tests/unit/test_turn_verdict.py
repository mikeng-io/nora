"""`TurnVerdict` — the per-turn judgement, and the contradiction it refuses."""

from __future__ import annotations

import pytest

from nora.domain.turn_verdict import (
    UNSUPPORTED_MAX_CHARS,
    UNSUPPORTED_MAX_ITEMS,
    TurnVerdict,
)


def _v(**kw) -> TurnVerdict:
    return TurnVerdict(**{"tag_id": "t1", "conversation_id": "g1",
                          "grounded": True, **kw})


def test_a_grounded_turn_cannot_carry_unsupported_claims():
    """The one refusal that is about MEANING rather than size.

    `grounded=True` with a non-empty `unsupported` is not a partial verdict, it
    is a self-contradiction — and this column is what S2's re-derivation
    trigger reads, so a trigger firing off a contradictory row is worse than
    one that never fires. Refused in the type AND by
    `turn_verdict_grounded_has_no_unsupported_check`, because the CHECK exists
    for whatever bypasses the type.
    """
    with pytest.raises(ValueError, match="cannot carry unsupported"):
        _v(grounded=True, unsupported=("invented a price",))


def test_an_ungrounded_turn_may_name_what_was_unsupported():
    v = _v(grounded=False, unsupported=("claimed the shop shuts at 9",))
    assert v.grounded is False
    assert v.unsupported == ("claimed the shop shuts at 9",)


def test_an_ungrounded_turn_may_name_nothing():
    """Legal, and deliberately so: the fold can be sure a turn was ungrounded
    without being able to enumerate why. Refusing this would push the model
    toward inventing a list to satisfy the schema — the invent-a-value move the
    note tier's own docstring forbids elsewhere."""
    assert _v(grounded=False).unsupported == ()


def test_too_many_unsupported_claims_is_refused():
    """A turn with more than ten distinct unsupported assertions went wrong in
    a way the LIST cannot express. Truncating would report the first ten as
    though they were all of them."""
    with pytest.raises(ValueError, match="over the"):
        _v(grounded=False,
           unsupported=tuple(f"claim {i}" for i in range(UNSUPPORTED_MAX_ITEMS + 1)))


def test_exactly_the_limit_is_accepted():
    v = _v(grounded=False,
           unsupported=tuple(f"claim {i}" for i in range(UNSUPPORTED_MAX_ITEMS)))
    assert len(v.unsupported) == UNSUPPORTED_MAX_ITEMS


def test_an_over_long_claim_is_refused():
    with pytest.raises(ValueError, match="character cap"):
        _v(grounded=False, unsupported=("x" * (UNSUPPORTED_MAX_CHARS + 1),))


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_a_blank_claim_is_refused(blank):
    with pytest.raises(ValueError, match="cannot be blank"):
        _v(grounded=False, unsupported=(blank,))


@pytest.mark.parametrize("field", ["tag_id", "conversation_id"])
def test_the_keys_cannot_be_blank(field):
    with pytest.raises(ValueError, match="needs"):
        _v(**{field: "   "})


def test_the_verdict_is_frozen():
    """Never rewritten (§3) starts here: the row cannot be edited in place
    either. `standing` is the type that gets revised; this one does not."""
    v = _v()
    with pytest.raises(Exception):  # noqa: B017 — FrozenInstanceError
        v.grounded = False          # type: ignore[misc]
