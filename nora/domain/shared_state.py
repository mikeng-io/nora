"""The unit's shared cognitive state: ONE typed projection, many audiences.

`design/notebook-memory-types/20-shared-cognitive-state.md`: «Standing» is a
shared persistent VIEW over the five Notebook kinds plus pending and executed
acts — never a kind, never a second store. `SharedView` is that projection.
Each audience (Engagement's tag turn, the unit Cognition session, a Proactive
fire turn) reads the same typed entries; a destination-scoped read marks an
entry the room may not see `permitted=False` rather than dropping it, so an
absence can never read as nothing there.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: What one backing store answered: the R-27 three states a read can return,
#: plus `partial` for a bounded read that hit its cap. A wiring gap or an
#: error is `unavailable`, never `nothing_found`.
SourceStatus = Literal["ok", "nothing_found", "partial", "unavailable"]

#: The view's whole-read verdict. `unavailable` wins over `partial`, `partial`
#: over `ok`: a consumer holding `ok` may conclude a negative («nothing is
#: pending»), and only `ok` proves it.
ViewStatus = Literal["ok", "nothing_found", "partial", "unavailable"]

#: What one entry can be. `act` is a reminder row (proactive or human-asked),
#: `wake` a self-wake, `send` a delivered own message; the rest are open
#: Notebook/ledger kinds.
ENTRY_KINDS: tuple[str, ...] = (
    "note", "decision", "commitment", "thread",
    "act", "wake", "send",
)


@dataclass(frozen=True, slots=True)
class SharedEntry:
    """One projected row: a Notebook entry, a pending or executed act, a
    self-wake, or a delivered own send.

    `id` and `refs` are canonical handles — a note id, a `d`/`c`/`th` ledger
    id, a reminder id, a message id — never prose. `delivery` is the
    code-measured send state of an act: `sent` (the row's own outcome said
    so), `none` (it fired and no sent row exists near it), `unknown` (it
    fired and a send may have landed before the process lost the row — it is
    stated as unknown, never retried silently).
    """

    kind: str
    id: str
    scope: str              # "unit" or "platform:conversation_id"
    status: str             # open / pending / done / cancelled / failed / sent
    subject: str = ""       # one-line handle (title, question, text, why)
    revision: str = ""
    destination: str = ""   # an act's room, "platform:conversation_id"
    action: str = ""        # the act's producer-typed action (tell / ask)
    subject_ref: str = ""   # the resolved "kind:id" the act rests on
    refs: tuple[str, ...] = ()
    at_ms: int = 0
    provenance: str = ""
    delivery: str = ""      # "" | "sent" | "none" | "unknown"
    permitted: bool = True  # False = withheld from THIS destination
    #: The kind's own structured content — a decision's options and leaning,
    #: a note's closing condition, a finding's `bears_on`. What the legacy
    #: renderers carried that a one-line subject drops; empty means the kind
    #: has none, not that the store declined. Withheld entries carry none.
    detail: str = ""


@dataclass(frozen=True, slots=True)
class SharedView:
    """One read of the unit's shared state.

    `sources` is the per-source coverage — `(name, status)` — so a consumer
    can name WHICH read failed instead of concluding absence. `unresolved`
    names candidates the producer could not place; they ride the view rather
    than a second store.
    """

    unit: str
    status: ViewStatus
    as_of_ms: int
    entries: tuple[SharedEntry, ...] = ()
    sources: tuple[tuple[str, str], ...] = ()
    unresolved: tuple[str, ...] = ()
