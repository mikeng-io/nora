"""The Event abstraction — WHY an unprompted turn is happening (DEC-139).

A first-class domain type. The factories that BUILD events are app-layer code in
`nora/app/fire.py` (they close over the rows and jobs a turn is about). What a
header SAYS is not there: each kind's situation paragraph is a typed builder in
`nora.domain.framing` (`FIRE_HEADERS`), and a factory only names its inputs.

`Event` answers *what happens* (via `kind`); `nora.domain.reminders`
answers *when* (the scheduler). A fire turn's shape is one Event, and the
registry in `nora/app/fire.py` maps a reminder row's `kind` to the factory
that renders it.

`EventFactory` is the kind -> factory protocol. The registry itself is a
plain `dict[str, EventFactory]` wired at the composition root — matching
the codebase's existing idiom (plain functions over a strategy-object
hierarchy for five cases), and keeping the domain type free of app wiring.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TypeAlias

from nora.domain.media import MediaBlob
from nora.domain.models import PersonRef
from nora.domain.ports import OutboundAttachment
from nora.domain.reminders import Reminder


@dataclass(frozen=True)
class Event:
    """WHY an unprompted turn is happening, and everything that differs because
    of it. Everything else about the turn is the same turn.

    `platform`/`conversation_id`/`conversation_type` carry the conversation
    — every conversation key in this codebase is a TUPLE, and a value
    carrying the id alone can build neither a `ConversationTarget` nor a
    `Policy.is_allowed` call. `body` is the words put to her.

    `person_id` is WHOSE AUTHORITY the turn runs under — `None` means "this
    turn is not held on anyone's behalf" (a version announcement).

    `header` is the situation paragraph, as a callable rather than a
    string, because it is the one part that has to be rendered against
    values only the turn knows: the resolved `PersonRef` (or `None`) and
    the instant the turn ran.

    `kind` names the event in log lines; the factories in `nora/app/fire.py`
    use it to select header prose and payload reading.
    """

    kind: str
    platform: str
    conversation_id: str
    conversation_type: str
    person_id: str | None
    body: str
    header: Callable[[PersonRef | None, datetime], str]
    speaker: str = ""
    # URLs of documents code read back for this occasion (R-100). The fire turn seeds its link
    # carrier with them so a link the body names survives delivery; nothing else may mint one.
    document_urls: tuple[str, ...] = ()
    # Links code attaches to the turn's message itself (`LinkCarry.attach`): a finished job's
    # result URL (a video clip whose bytes could not be fetched), which the model is told about
    # and never has to copy.
    attached_urls: tuple[str, ...] = ()
    # Media code attaches to the turn's message itself (`LinkCarry.carry_media`): a finished
    # video clip's bytes, already kept in the library (`media_blobs` names its custody handle,
    # so the audit row records it as Nora-held). The model is told it is attached.
    attachments: tuple[OutboundAttachment, ...] = ()
    media_blobs: tuple[MediaBlob, ...] = ()
    # The brief's intent (`tell`/`ask`, nora.domain.cognition_remind.INTENTS) when the occasion has
    # one; None otherwise. The Deliverer records it on every message the turn sends, so the
    # Rollup knows whether the send expected an answer.
    intent: str | None = None


EventFactory: TypeAlias = Callable[[Sequence[Reminder]], Event]
