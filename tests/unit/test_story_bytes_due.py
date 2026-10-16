"""`nora.domain.social_stream.bytes_due` — when a story frame's bytes go.

At `expires_at` plus `keep_days` only once the frame is both perceived and
embedded; at `expires_at` plus `max_days` whatever exists. Nothing before.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nora.domain.social_stream import bytes_due

EXPIRES = datetime(2026, 10, 1, 12, tzinfo=UTC)


def due(days: float, *, perceived: bool = True, embedded: bool = True) -> str | None:
    return bytes_due(
        expires_at=EXPIRES, now=EXPIRES + timedelta(days=days), perceived=perceived,
        embedded=embedded, keep_days=7, max_days=30,
    )


def test_nothing_goes_before_the_first_bound_even_when_both_exist() -> None:
    assert due(0) is None
    assert due(6.99) is None


@pytest.mark.parametrize(("perceived", "embedded"), [(False, True), (True, False), (False, False)])
def test_at_the_first_bound_the_bytes_stay_until_both_exist(
    perceived: bool, embedded: bool,
) -> None:
    assert due(7, perceived=perceived, embedded=embedded) is None
    assert due(29.99, perceived=perceived, embedded=embedded) is None


def test_once_both_exist_the_bytes_go_at_the_first_bound() -> None:
    assert due(7) == "embedded"
    assert due(12) == "embedded"


@pytest.mark.parametrize(("perceived", "embedded"), [(False, True), (True, False), (False, False)])
def test_at_the_hard_limit_the_bytes_go_whatever_exists(perceived: bool, embedded: bool) -> None:
    assert due(30, perceived=perceived, embedded=embedded) == "hard_limit"


def test_a_frame_both_perceived_and_embedded_goes_as_embedded_even_late() -> None:
    assert due(31) == "embedded"
