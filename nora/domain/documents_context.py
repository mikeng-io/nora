"""`DocumentsView`: what the `<documents>` block renders.

A plain value the reader builds and the renderer only formats, the shape
`nora.domain.computer_context` gives `<computer>`. It carries `now` so every age is
computed against the moment the turn read, never the moment the prompt was rendered.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

#: Documents shown, most recently revised first. A room that outgrows this is the
#: moment to add a list tool, and a real miss is what says so.
DOCUMENTS_SHOWN = 5
#: Archived and deleted documents shown, most recently revised first, each led by its
#: state. A put-away document is in no list anybody else reads, so this is the one place a
#: later turn can learn its id to restore it; none put away renders nothing.
PUT_AWAY_SHOWN = 3
#: A title is a model's words about a room's work, cut to this on one line.
TITLE_CHARS = 50


@dataclass(frozen=True)
class DocumentLine:
    title: str
    document_id: str
    revision: int
    revised_at: datetime
    state: str = "active"


@dataclass(frozen=True)
class DocumentsView:
    lines: tuple[DocumentLine, ...]
    now: datetime
    put_away: tuple[DocumentLine, ...] = ()
