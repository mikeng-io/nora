"""The `<character>` block (DEC-278): one account per person, each with its own age."""

from __future__ import annotations

from datetime import UTC, datetime

from nora.domain.culture import CultureRevision
from nora.language.culture_render import render_character

NOW = datetime(2026, 10, 3, tzinfo=UTC)


def _rev(body: str) -> CultureRevision:
    return CultureRevision(
        id="r", culture_key="k", body=body,
        written_at=int(NOW.timestamp() * 1000) - 3 * 86_400_000, established_by="culture",
    )


def test_character_block_renders_each_person_with_an_age():
    accounts = [("Miles", _rev("dry and loyal")), ("Ada", _rev("<b>quiet</b>"))]
    block = render_character(accounts, now=NOW)
    assert block is not None and block.name == "character"
    out = block.render()
    assert 'name="Miles"' in out and "dry and loyal" in out and "written=" in out
    assert "<b>" not in out  # escaped: an account is model-authored prose
    assert render_character([], now=NOW) is None
