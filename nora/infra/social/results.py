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
