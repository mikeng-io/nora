"""The render-side withdrawal rule for a derived head (design/cognition-loop/
10-tiers.md §2 rule 7, migration `0075_withdrawal`, step 2c).

A Standing or culture head marked `unsupported_at` stops rendering into a prompt: the
same `None` a room with no fold yet gets, never a partial or stale account. NOTHING
MARKS A HEAD any more (the cascade was removed 2026-10-03): this guards rows marked
before then, and goes with the `unsupported_at` columns. The store's `read()`/`head()` still return
the row (the fold's own "previous" read re-derives from it); only a PROMPT
read filters.

ONE function, `render_supported`. Every culture head reaches a prompt through
`nora.language.culture_render.read_culture`, which applies it, so the tag turn,
the fire turn and the Cognition session share
one copy (proactive, note-candidates, the curator and the event fold did, until
2026-10-03, DEC-277). A Standing head has no shared reader (each caller bounds and counts
its own read), so each applies it at its call site: the tag path
(`nora.engagement.context`), the fire path (`nora.app.fire`), the relevance gate,
the Cognition session's reads and the culture fold's `<standing>`. The rule lived
inline in the tag path and was missing from the fire path, so a withdrawn head
rendered in every fire turn, and from every Dreamer prompt until step 5 of
design/context-assembly/04-every-occasion.md. Two copies of a rule drift;
`tests/architecture/test_withdrawal_reaches_every_prompt.py` reads the tree for
a head that reaches a prompt without passing through it.

**The deliberate exception is a fold's OWN prior** (the culture fold's
`previous_account`, the debrief's `standing_prior`): it is
read raw so the fold sees what it wrote and can supersede it; the cascade sends a
withdrawn row to "its writer's next session for re-derivation" (10-tiers.md §2
rule 7), and a writer that could not see the row could not re-derive it. Each is
named, with its reason, in that test.
"""

from __future__ import annotations

from typing import Protocol, TypeVar


class _Withdrawable(Protocol):
    @property
    def unsupported_at(self) -> int | None: ...


H = TypeVar("H", bound=_Withdrawable)


def render_supported(found: H | None) -> H | None:
    """`found`, or `None` when it is absent or withdrawn."""
    if found is not None and found.unsupported_at is not None:
        return None
    return found
