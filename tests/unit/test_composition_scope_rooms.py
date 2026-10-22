"""The scope-keyed home-room map refuses an id that names two rooms."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nora.app.composition import StartupError, scope_home_rooms


def _conv(unit: str, platform: str, primary: str, *ids: str, location: bool = False):
    return SimpleNamespace(
        unit=unit, platform=platform, primary_id=primary,
        ids=(primary, *ids), location=location,
    )


def test_an_id_shared_by_two_units_is_refused_at_startup() -> None:
    allowlist = SimpleNamespace(conversations=[
        _conv("friends", "signal", "same-id"),
        _conv("work", "discord", "same-id"),
    ])
    with pytest.raises(StartupError, match="two policy rooms share"):
        scope_home_rooms(allowlist)


def test_two_id_forms_of_the_same_room_are_not_a_collision() -> None:
    allowlist = SimpleNamespace(conversations=[
        _conv("friends", "signal", "new-id", "old-id"),
    ])
    rooms = scope_home_rooms(allowlist)
    assert rooms["new-id"] is rooms["old-id"]
    assert rooms["new-id"].unit == "friends"
