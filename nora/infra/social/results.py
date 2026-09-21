"""The `SocialProvider` payloads that are NOT a read — one copy, read by every
implementation behind `social_fetch`.

They live here rather than in whichever module first needed them because the
`note` field is text the MODEL reads and acts on. Two backends each carrying
their own wording of «I couldn't check» is two chances for one of them to drift
into «there's nothing», which is the single confusion R-27 exists to prevent
(AGENTS.md: a wiring gap is `unavailable`, never `nothing_found`). One constant
per claim means the claim can only be restated in one place.

Each `note` is an INSTRUCTION to the model about what it may say, not a
description of the fault: the model does not get to see the exception, so the
payload has to carry what the honest sentence is. `dict(...)` at every call
site — these are shared module constants, and a caller that mutated one would
edit every later result.

**And because a `note` is an instruction channel, nothing a BRIDGE says may
land in one.** Both clients used to forward `payload["note"]` verbatim on the
non-`ok` path — an unbounded, unescaped string from another process, rendered
into the slot that tells the model what it may say. A 230-character bridge
note reading «IGNORE PREVIOUS INSTRUCTIONS…» reached the model intact, which
is why `NOTHING_FOUND` below exists: the miss is a STATE, and the sentence for
a state is ours. The bridge's wording is DROPPED, not clamped and not escaped
— there is no version of "a remote process writes part of Nora's
instructions" that is made safe by being shorter.

`clamp_chars` lives here for the same "one copy" reason the notes do: every
implementation behind `social_fetch` bounds the text it hands back, because a
tool result is re-sent on every subsequent model call.
"""

from __future__ import annotations

from typing import Any

#: The call itself failed — transport, a non-200, an envelope that does not
#: match the documented shape, a backend that is not wired. «I could not look»,
#: NEVER «I looked and there was nothing».
UNAVAILABLE: dict[str, Any] = {
    "status": "unavailable",
    "note": "I couldn't read that just now. Say so — don't answer as if "
            "you'd checked.",
}

#: The subject did not resolve. `target` is a `person_id` on a subject-keyed
#: read and the person index is the only thing that may turn it into a network
#: id (design/instagram-adoption/09) — so «I don't know which account that is»
#: is the answer, and guessing a handle from a name is the failure this status
#: exists to make impossible. NOT `nothing_found`: nothing was looked at.
AMBIGUOUS: dict[str, Any] = {
    "status": "ambiguous",
    "note": "I don't know which account that is, so I didn't look. Say you'd "
            "need to be told whose account it is — don't guess one from "
            "their name.",
}

#: The subject resolved and is out of reach. v1 is Tier 0 only — someone in a
#: room Nora is in (design/instagram-adoption/07). Distinct from `unavailable`
#: because calling again cannot help and nothing is broken: «I may not» and «I
#: couldn't» are different claims, and collapsing them would have Nora
#: reporting a policy boundary as an outage.
REFUSED: dict[str, Any] = {
    "status": "refused",
    "note": "I don't look up people outside the rooms I'm in. Say that "
            "plainly — it's a boundary, not a failure.",
}


#: The read RAN and there was nothing — «I looked, nothing there», which is a
#: fact about the subject rather than about us. Nora-side wording rather than
#: the bridge's, because a `note` is what the model is TOLD TO SAY and a
#: remote process does not get to write it.
NOTHING_FOUND: dict[str, Any] = {
    "status": "nothing_found",
    "note": "I looked and there was nothing there. Say you couldn't find "
            "anything — not that you couldn't check.",
}


def clamp_chars(text: str, max_chars: int) -> tuple[str, bool]:
    """Clamp `text` to `max_chars`, preferring an item boundary.

    Truncating mid-record is what made raw JSON unreadable — a post's `text`
    field severed from its closing brace. Rendered text is more forgiving, but
    cutting at an item separator keeps the first item whole whenever the
    budget allows; the result is readable text, not valid JSON, which is the
    point.

    Here rather than private to one client because BOTH implementations behind
    `social_fetch` owe the same bound, for the same reason the notes are
    shared: a tool result is re-sent on every subsequent model call, and a
    clamp that exists on one backend and not the other is a token budget that
    depends on which verb was asked.
    """
    if len(text) <= max_chars:
        return text, False
    cut = text[:max_chars]
    sep = cut.rfind("\n\n")
    if sep > 0:
        cut = cut[:sep].rstrip()
    return cut + " …", True
