"""The framing `nora.app.memory_backfill` still replays edits with.

The live feed that used this module is gone; the importer leaves with the
backfill, and then this module does."""

from __future__ import annotations


def edit_text(*, body: str, first_body: str | None) -> str:
    """What Honcho is fed for an EDIT.

    Honcho stores a message's content once — its API updates metadata only —
    and its deriver has usually read the original before the edit arrives. So
    the edit is a NEW message, and this framing is what lets the deriver read
    it as a correction of something already said rather than as a second,
    independent statement. The first text is quoted because the deriver has no
    join from this message to that one: without it, «8pm» is a fact, not a
    change from «7pm»."""
    return f"[edited an earlier message; it first said: «{first_body or ''}»] {body}"
