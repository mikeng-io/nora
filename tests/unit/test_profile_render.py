"""ADR 0029's typed block carries stable subject and author ids."""
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from nora.domain.profile import ProfileLine, ProfileView
from nora.language.profile_render import render_profile

NOW = datetime(2026, 10, 25, tzinfo=UTC)
TS = int(NOW.timestamp() * 1000)


@pytest.mark.parametrize("entity,subject", [("person", "ada"), ("room", "reading-room"),
                                            ("unit", "readers"), ("nora", "nora")])
def test_profile_tag_names_its_subject_and_author_by_id(entity, subject):
    line = ProfileLine(
        id="synthetic-line", profile_key="readers#character:ada", part="facts",
        kind="attribute", text="Reads books.", mode="told", citations=("1",),
        state="current", unit="readers", written_at=TS - 3600000,
        written_by_session="synthetic", told_by="ada", fact_key="occupation",
        fact_value="librarian",
    )
    block = render_profile(ProfileView(entity=entity, of=subject, lines=(line,)),
                           name_of=lambda _: "Ada", now=NOW)
    assert block is not None
    assert block.render() == (
        f'<profile of="{subject}" written_by="nora" '
        'source="what people said in chat" written="60m ago">\n'
        'facts:\n- Occupation: librarian [told] (told by Ada)\n'
        'traits:\nguesses (unconfirmed):\n</profile>'
    )
    assert 'of="you"' not in block.render()
    assert render_profile(replace(ProfileView(entity=entity, of=subject), lines=()),
                          name_of=lambda _: "Ada", now=NOW) is None


def test_retired_config_section_refuses_and_names_replacement(example_config_path):
    from nora.config import ConfigError, load
    text = example_config_path.read_text().replace("[profile]", "[persona]")
    example_config_path.write_text(text)
    with pytest.raises(ConfigError, match=r"\[persona\].*\[profile\]"):
        load(example_config_path)
