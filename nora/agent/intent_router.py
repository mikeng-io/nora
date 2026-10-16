"""A precision-first, ABSTAINING intent router.

A schema-token nudge does not move mall opening-hours off `web_search`: the
model's "look up a business's hours = web search" prior is too strong. For a
narrow, high-precision surface where the model reliably misroutes, this FORCES
the first tool via `tool_choice` (the `ReplyGuard._before_model` seam that
already mutates it).
**Abstaining is the whole discipline.** Forcing a NAMED tool turns a probabilistic
misroute into a DETERMINISTIC one — a misclassification ships a confident wrong
route, exactly the fail-closed violation the CMS spine forbids. So this fires ONLY
when it is nearly certain (an explicit opening-hours / still-open surface) and
returns None — leave it to the model — on anything else, including the carpark
vacancy case that legitimately wants `web_search`. It never forces `web_search`,
never forces on ambiguity, and knows nothing the tool schemas don't; it only
raises the floor on the one ask-shape prose could not.
"""

from __future__ import annotations

import re

# High-precision opening-hours / still-open surface, Cantonese + English. Keyed on
# the ASK (hours / open-close / 營業), never on a place name — because "same place,
# different intent" is the trap: 又一城…幾點關門 (place_info) and 太古廣場…有冇車位
# (web_search) both name a mall. `車位`/`泊車`/`貴唔貴` deliberately do NOT match.
_HOURS_OPEN = re.compile(
    r"幾點.{0,4}(開|關|收)"          # 幾點開門 / 幾點關門 / 幾點收
    r"|(開門|關門|收工|營業).{0,3}時間"  # 開門時間 / 營業時間
    r"|營業緊|仲營業|仲開緊|開唔開門|仲開唔開"  # still operating / still open (a place)
    r"|opening\s+hours?"
    r"|what\s+time.{0,25}(open|clos)"
    r"|(is|are|still)\b.{0,20}\bopen\b",
    re.IGNORECASE,
)


def route_intent(question: str) -> str | None:
    """The tool to FORCE for this turn's first model call, or None to abstain.

    Pure and stateless — a `measure` over the comprehended message, not a
    collaborator with IO, so the pipeline calls it directly. Returns `"place_info"`
    only for an explicit opening-hours / still-open ask; None otherwise.
    """
    if not question:
        return None
    if _HOURS_OPEN.search(question):
        return "place_info"
    return None
