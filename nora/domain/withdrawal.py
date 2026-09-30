"""The render-side withdrawal rule for a derived head (design/cognition-loop/
10-tiers.md §2 rule 7, migration `0075_withdrawal`, step 2c).

A Standing or culture head the withdrawal cascade marked `unsupported_at`
stops rendering into a prompt: the same `None` a room with no fold yet gets,
never a partial or stale account. The store's `read()`/`head()` still return
the row (the fold's own "previous" read re-derives from it); only a PROMPT
read filters.

ONE function, called by the tag path (`nora.engagement.context`) and the fire
path (`nora.app.fire`), because the rule lived inline in the first and was
missing from the second, so a withdrawn head rendered in every fire turn.
Two copies of a rule drift; this is the one.
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
