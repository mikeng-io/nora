"""`TagTrace` — one completed tag turn, captured whole for the debrief to
fold and for retention to rebuild from.

`messages` is `agent.messages[baseline:]`, sanitised and capped by
`nora.agent.tag_trace_extract.extract_tag_trace` -- never semantically
filtered here or there. `prior` (the previous `Standing`) is deliberately NOT
a field: this type is built at the `nora/agent/` extraction boundary, which
has no storage access, so `prior` is the fold's second argument.

Framework-free, per `nora/domain/`'s rule: stdlib and dataclasses only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import TranscriptRow

VALID_OUTCOMES = frozenset({
    "send_reply",
    "recovered",
    "silent",
    "vetoed",
    "timeout",
    "llm_error",
})


@dataclass(frozen=True, slots=True)
class TagTrace:
    tag_id: str
    conversation_id: str
    messages: tuple[dict[str, Any], ...]
    transcript: tuple[TranscriptRow, ...]
    outcome: str
    created_at: int

    def __post_init__(self) -> None:
        if self.outcome not in VALID_OUTCOMES:
            raise ValueError(
                f"outcome must be one of {sorted(VALID_OUTCOMES)}, got "
                f"{self.outcome!r}"
            )
