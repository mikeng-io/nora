"""The Nora account renderer and the location boundary on its audience."""

from __future__ import annotations

from datetime import UTC, datetime

from nora.domain.cognition_ground import Grounding
from nora.language.cognition_render import _BLOCKS as COGNITION_BLOCKS
from nora.language.cognition_render import render_agenda

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
UNIT = "friends"


_POLICY_TOML = """
default = "deny"

[[conversations]]
platform = "signal"
id = "g1"
label = "On"
persons = "*"
unit = "friends"
location = true

[[conversations]]
platform = "whatsapp"
id = "g2"
label = "Off"
persons = "*"
unit = "friends"

[[conversations]]
platform = "signal"
id = "w1"
label = "Quiet one"
persons = "*"
unit = "quiet"

[[conversations]]
platform = "discord"
id = "100000000000000001/100000000000000002"
label = "Quiet two"
persons = "*"
unit = "quiet"

[[conversations]]
platform = "signal"
id = "o1"
label = "Open one"
persons = "*"
unit = "open"
location = true
"""


def _policy(tmp_path):
    from nora.policy import load_policy

    f = tmp_path / "policy.toml"
    f.write_text(_POLICY_TOML)
    return load_policy(f)


def test_the_profile_is_the_first_row_of_the_cognition_table():
    assert COGNITION_BLOCKS[0].name == "graph_profile"
    assert COGNITION_BLOCKS[0].order == min(d.order for d in COGNITION_BLOCKS)


def _cognition_prompt(grounding: Grounding | None) -> str:
    return render_agenda(
        [], grounding=grounding, now_ms=int(NOW.timestamp() * 1000),
        last_session_ms=None,
    )


def test_a_cognition_prompt_opens_with_her_account_before_the_clock():
    from nora.domain.blocks import ContextBlock
    from nora.testing.context_blocks import compose_fixture

    prompt = _cognition_prompt(Grounding(graph_profiles=(
        ContextBlock("graph_profile", compose_fixture("she keeps it short here"),
                     attrs=(("of", "nora"),)),
    )))
    assert prompt.startswith("<graph_profile ")
    assert prompt.index("<graph_profile ") < prompt.index("<clock")
    assert "she keeps it short here" in prompt
    assert 'of="nora"' in prompt
    assert COGNITION_BLOCKS[0].name == "graph_profile"


def test_a_cognition_prompt_has_no_self_block_without_an_account():
    for grounding in (Grounding(), None):
        first = _cognition_prompt(grounding).splitlines()[0]
        assert first.startswith("<graph_profile ")
        assert first.endswith("/>")
        assert 'of="' not in first


# ---- R-31: the account is unit-wide, so a room shows it only where it may -----


def test_a_room_shows_her_account_only_where_it_may(tmp_path):
    p = _policy(tmp_path)
    # mixed unit: the location-ON room shows it, the location-OFF room does not
    assert p.shows_self_in("signal", "g1") is True
    assert p.shows_self_in("whatsapp", "g2") is False
    # a unit with no location room at all shows it everywhere
    assert p.shows_self_in("signal", "w1") is True
    assert p.shows_self_in("discord", "100000000000000001/100000000000000002") is True
    # a unit that is all location-on shows it
    assert p.shows_self_in("signal", "o1") is True
    # an unknown room never does
    assert p.shows_self_in("signal", "nowhere") is False


def test_a_unit_scoped_prompt_shows_it_only_when_every_room_agrees(tmp_path):
    p = _policy(tmp_path)
    assert p.shows_self_to_unit("friends") is False      # mixed
    assert p.shows_self_to_unit("quiet") is True         # all off
    assert p.shows_self_to_unit("open") is True          # all on
    assert p.shows_self_to_unit("no-such-unit") is False
