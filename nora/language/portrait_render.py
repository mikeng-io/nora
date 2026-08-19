"""Renders `RoomPortrait` into the `<room_portrait>` block — the portrait
sibling of `nora.language.render`/`nora.language.exchange_render`. Much
simpler than either: `RoomPortrait.narrative` is already the complete,
finished sentence(s) `nora.language.portrait_synthesizer` built (closing frame
included — see that module's docstring for why the withhold-rule disclaimer
is baked into the stored text there rather than appended here, unlike its two
siblings), so this module's only job is XML-escaping and the "empty renders
no block" ruling both siblings already apply.

`canonical_form` values feeding the stored narrative are PARTLY model-
authored (Plan 1's `LexemeObserver`, length-capped but not escaped at that
layer) — escaping the WHOLE narrative here, not per-field at synthesis time,
keeps the durable `room_portrait.narrative` column holding raw semantic text
(matching `room_lexeme`/`room_exchange`'s own convention of storing raw and
escaping only at the render boundary) and is what R-13 actually requires:
nothing group-derived reaches the prefill unescaped.
"""

from __future__ import annotations

from ..domain.models import RoomPortrait
from ..domain.prompt import escape


def render_room_portrait(portrait: RoomPortrait) -> str:
    """`""` when there is nothing to say — mirrors `render_room_language`/
    `render_room_exchange`."""
    if not portrait:
        return ""
    return f"<room_portrait>\n{escape(portrait.narrative)}\n</room_portrait>"
