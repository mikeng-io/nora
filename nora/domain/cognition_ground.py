"""What code hands a Cognition session to JUDGE from (design/cognition-loop/
30-cognition.md §1; #637 review, major 2).

**The failure this exists for.** An agenda line is a count: «room R has 23 new
messages». A session handed only that has nothing to tell a plan from a joke,
cannot cite a message id in a note (a body line MUST end in the ids it rests on,
`nora.domain.note_body`) and cannot see what is already recorded, so it writes
a duplicate or closes `no_change` on a room it never read. AGENTS.md's spine
says it once for every model that judges: the input has to explain itself, and
a judgement whose input cannot ground it does not fail loudly, it answers
wrong at a rate nothing reports. So §1 says what code hands it, and this is
that: for each item the Record rows that raised its stimuli (author, room, id,
time and, when the room said something with a photo in it, what the photo
shows), and the index lines of the Register rows the session would otherwise
duplicate or contradict. Index lines, never bodies (§1): a body is one read
tool away, and a pushed body is a disclosure decision nobody made.

**Interpret once.** `Grounding` is built once per session by the caller and
handed, whole, to the pure renderer (`nora.domain.cognition_render`); nothing
downstream re-reads a row. It is also what the session row records, so a
session is replayable from what it was actually shown.

**What is never in it** (each has a test): a message the withdrawal cascade
hid (`nora person forget`, the Record read excludes it), the words of a
message its author deleted (the row is shown as deleted, like `record_query`),
and any row of a room that is not one of the unit's rooms. Framework-free, per
`nora/domain/`'s rule.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from nora.domain.cognition_session import Room
from nora.domain.stimuli import Stimulus

#: The tables a message stimulus's `ref` names a row of (`nora.ingest.stages.
#: stimulus._REF_TABLE`): a message or an edit names the message, a reaction
#: and a delete name their own row.
RECORD_REF_TABLES: tuple[str, ...] = ("messages", "message_reaction", "message_delete")

RecordKind = Literal["message", "edit", "reaction", "delete"]


def record_ref(stimulus: Stimulus) -> tuple[str, int] | None:
    """`(table, row id)` of the Record row a `message` stimulus names, or None
    for every other kind and for a `ref` that is not one."""
    if stimulus.kind != "message":
        return None
    table, _, tail = stimulus.ref.partition(":")
    if table not in RECORD_REF_TABLES or not tail.isdigit():
        return None
    return table, int(tail)


@dataclass(frozen=True, slots=True)
class RecordItem:
    """One Record row that raised a stimulus, as the session reads it.

    `message_id` is the id a note line cites. `author` is the identity-
    canonical name (`Persons.name_transcript`), falling back to the self-set
    profile name, and never a platform id (R-18). `text` is None when the words
    are not shown: the author deleted the message. A `reaction` names its
    emoji and the message it is on; nobody's account is named for a reaction
    (the Record holds the sender's platform id, not a person)."""

    ref: str
    kind: RecordKind
    message_id: int
    room: Room
    author: str
    at_ms: int
    text: str | None
    emoji: str = ""
    edited: bool = False
    #: The stimulus's person id (or None), what the session row keeps in the
    #: NAME's place: `cognition_session.situation` is append-only and
    #: `nora person forget` cannot rewrite it, so a canonical name written
    #: there would outlive the person's withdrawal. A name is rendered to the
    #: model at read time and never serialized (#637 second review, minor 5).
    person_id: str | None = None


@dataclass(frozen=True, slots=True)
class NoteLine:
    """An open note's index line: its id (what `note_update` takes), title and
    one-line description, never its body."""

    id: str
    title: str
    description: str


@dataclass(frozen=True, slots=True)
class EventLine:
    """An open (proposed) event's index line."""

    id: str
    room: Room
    title: str
    when_ms: int | None
    venue: str
    status: str
    revision: str | None = None


@dataclass(frozen=True, slots=True)
class Grounding:
    """Everything one session is shown beside its agenda, built once.

    `records` is keyed by the stimulus `ref` (`messages:12`), so the renderer
    joins a stimulus to its row without re-parsing anything. `notes` is the
    unit's (the unit is the read boundary, DEC-231); `events` are the rooms on
    the agenda's. There is no Standing here: Standing is pulled, never pushed
    (30 §1), and `standing_read` does not exist yet."""

    records: Mapping[str, RecordItem] = field(default_factory=dict)
    notes: tuple[NoteLine, ...] = ()
    events: tuple[EventLine, ...] = ()

    def as_dict(self) -> dict[str, object]:
        """The JSON the session row keeps: what she was shown, by reference
        (a message id, a note id), not the bodies and NEVER a name: the row is
        append-only, and a person's forget must not leave their canonical name
        in it."""
        return {
            "records": [
                {"ref": r.ref, "kind": r.kind, "message_id": r.message_id,
                 "platform": r.room.platform, "conversation_id": r.room.conversation_id,
                 "person_id": r.person_id, "at_ms": r.at_ms, "words_shown": r.text is not None}
                for r in self.records.values()
            ],
            "notes": [n.id for n in self.notes],
            "events": [e.id for e in self.events],
        }


__all__ = [
    "RECORD_REF_TABLES", "EventLine", "Grounding", "NoteLine", "RecordItem", "RecordKind",
    "record_ref",
]
