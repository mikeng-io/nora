"""The typed builders fail closed exactly where the code they replaced did.

`RequestBody` must not format its texts with `str()`: a reminder row whose
`text` was not a str (unreachable today: the column is NOT NULL text) would
put the literal «None» or «5» to the model, where
`"\\n".join(...)` raised `TypeError` and the turn failed closed. Every
case below raises the exception named here; the builders must raise the same
one, never render a widened input.

The header fields master ESCAPED (`escape(x)` takes any object) keep accepting
any object: that is not a widening, and the goldens hold their bytes.
"""

from __future__ import annotations

from unittest import mock

import pytest

from nora.app.fire import (
    participation_occasion,
    proactive_occasion,
    relay_occasion,
    reminder_occasion,
)
from nora.domain import framing
from tests.unit.test_headers_goldens import _row

NOT_STR = [None, 5, b"x", ["a"]]
IDS = ["none", "int", "bytes", "list"]


@pytest.mark.parametrize("value", NOT_STR, ids=IDS)
@pytest.mark.parametrize("factory", [reminder_occasion, proactive_occasion])
def test_a_request_whose_text_is_not_a_str_fails_the_turn_closed(factory, value):
    with pytest.raises(TypeError):
        factory([_row(text=value)])


@pytest.mark.parametrize("value", NOT_STR, ids=IDS)
def test_the_body_builder_refuses_a_non_str_text(value):
    with pytest.raises(TypeError):
        framing.RequestBody(("fine", value)).render()  # type: ignore[arg-type]


def test_a_relay_with_no_requester_is_refused_not_rendered_nameless():
    with pytest.raises(AttributeError):
        relay_occasion(
            destination=mock.Mock(platform="signal", primary_id="c"),
            requester=None, text="t", from_platform="discord",  # type: ignore[arg-type]
        )
    with pytest.raises(AttributeError):
        framing.RelayHeader(None, "discord").render()  # type: ignore[arg-type]


def test_a_score_that_is_not_a_number_is_refused_as_before():
    header = participation_occasion(
        platform="signal", conversation_id="c", relevance_score="0.5",  # type: ignore[arg-type]
    ).header
    with pytest.raises(ValueError):
        header(None, None)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["1", b"1", None, [1]], ids=["str", "bytes", "none", "list"])
def test_a_bare_number_slot_refuses_what_is_not_a_number(value):
    with pytest.raises(TypeError):
        framing._figure(value)  # type: ignore[arg-type]
