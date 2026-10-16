"""May an edit open a turn? Only when it NEWLY addresses Nora.

The same engage policy every message meets runs twice: on the edit, and on the
version the edit replaced — the same message with that version's `body` and
`mentions`. The edit engages only when the first says yes and the second says
no. That single comparison is what separates the two cases that look alike at
the gate:

* `@Nora` ADDED to a message that had none — a member addressing her by
  editing. The first says yes, the second no: a turn.
* a typo fixed in a message that already tagged her — both say yes. She has
  answered it or is answering it; a second turn would be a second answer to
  one question. The corrected text is in front of her next turn anyway.

A reply to Nora cannot be re-targeted by an edit on any of the four networks,
so a message that replied to her was addressed to her in every version — but
not every platform repeats the quote on the edit event (Instagram's delta has
none), so that fact is read off the RECORDED message
(`EditedMessage.replies_to_nora`), never off the edit. Without it, adding an
@-mention to a reply she had already answered read as newly addressed.

**Unknown is no.** A version stored before `messages.mentions_json` existed
has no recorded mentions, and the second call cannot be made honestly. R-5:
silence beats a wrong answer, and a second reply to a question already
answered is the wrong answer this rule exists to prevent.

**Never a Relevance Gate candidate.** A non-engaging edit is not new room
activity, so `relevance_gate` is cleared whatever the policy said — the
quiescence trigger counts messages, and an edit is not one.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from nora.domain.models import EditedMessage, NormalizedMessage
from nora.domain.ports import EngageDecision, EngagePolicy

from . import gate as gate_stage


async def run(
    *, message: NormalizedMessage, previous: EditedMessage, policy: EngagePolicy,
    metrics: Any,
) -> EngageDecision:
    now = await gate_stage.run(message=message, policy=policy, metrics=metrics)
    if not now.engage:
        return dataclasses.replace(now, relevance_gate=False)
    if previous.replies_to_nora:
        return EngageDecision(engage=False, reason="edit_already_addressed")
    if previous.mentions is None:
        return EngageDecision(engage=False, reason="edit_previous_unknown")
    before = await gate_stage.run(
        message=dataclasses.replace(
            message, body=previous.body or "", mentions=previous.mentions,
        ),
        policy=policy, metrics=metrics,
    )
    if before.engage:
        return EngageDecision(engage=False, reason="edit_already_addressed")
    return now
