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
    "note": "I couldn't read that just now. Say so. Don't answer as if "
            "you'd checked.",
}

#: The person has SEVERAL accounts on file. The person index is the only
#: thing that may turn a `person_id` into a network id
#: (design/instagram-adoption/09), and it holds more than one with nothing to
#: say which was meant — so nothing is read, and picking one is the failure
#: this status exists to make impossible. It does not tell the model to ASK:
#: the person in the room cannot resolve it either, only the operator's file
#: can (design/instagram-adoption/18, 2026-09-24).
AMBIGUOUS: dict[str, Any] = {
    "status": "ambiguous",
    "note": "They have more than one account on file and I can't tell which, "
            "so I didn't look. Say so; don't pick one.",
}

#: The person has NO account on file. `nothing_found` — the honest «there is
#: nothing to read» — but the note says what the nothing is: no account on
#: file, not «no story up», and nothing was fetched. Split out of `AMBIGUOUS`
#: on 2026-09-24; the two used to share one word and one sentence, which
#: told the model to ask about an account nobody had recorded.
NO_ACCOUNT: dict[str, Any] = {
    "status": "nothing_found",
    "note": "I don't have an Instagram account on file for them, so I didn't "
            "look. Say that; don't guess one from their name.",
}

#: A typed handle or a pasted link named a username Instagram has no account
#: for: the bridge's exact username lookup ran and matched nobody
#: (design/instagram-adoption/15 §Links, /17 P9, P10). `nothing_found`, a fact
#: about Instagram, and different from `NO_ACCOUNT`, which is about the
#: operator's file. The note says not to guess a near spelling: the lookup is
#: exact on purpose, and a guessed handle reads a different human.
NO_SUCH_HANDLE: dict[str, Any] = {
    "status": "nothing_found",
    "note": "Instagram has no account with that username. Say so; don't "
            "guess a similar one.",
}

#: The subject resolved and is out of reach. A story is Tier 0 only — a
#: person the operator LISTED in a group room (design/instagram-adoption/07,
#: /15) — and it is refused in a turn nobody asked for (/18, 2026-09-24).
#: Distinct from `unavailable` because calling again cannot help and nothing
#: is broken: «I may not» and «I couldn't» are different claims. The note
#: names the listing, not "the rooms I'm in": someone can be IN the chat and
#: not listed, and "outside my rooms" would be false about them. Since P10 it
#: also answers a story named by a typed handle or a pasted link whose account
#: maps to nobody listed, which covers a stranger: the same boundary, so the
#: same sentence.
REFUSED: dict[str, Any] = {
    "status": "refused",
    "note": "I only open stories of people the operator has listed in one of "
            "my rooms. Say it's a boundary.",
}


def not_set_up(platform: str) -> dict[str, Any]:
    """The answer for a read whose backend is not composed at all — a
    CONFIGURATION state, stable across calls, not «couldn't read that just
    now», which invites a retry that cannot help. The platform name comes
    from a closed map, never from the caller's string."""
    name = {"instagram": "Instagram", "x": "X"}.get(platform, "That")
    return {
        "status": "unavailable",
        "note": f"{name} isn't set up for me, so I can't read it. Say so "
                "plainly; it isn't a passing failure.",
    }


#: The read RAN and there was nothing — «I looked, nothing there», which is a
#: fact about the subject rather than about us. Nora-side wording rather than
#: the bridge's, because a `note` is what the model is TOLD TO SAY and a
#: remote process does not get to write it.
NOTHING_FOUND: dict[str, Any] = {
    "status": "nothing_found",
    "note": "I looked and there was nothing there. Say you couldn't find "
            "anything, not that you couldn't check.",
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
