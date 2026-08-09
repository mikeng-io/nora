"""D3: the seed journeys — four real incidents from the two live groups, each
expressed as an `Intent` against the `SEED_INTENT_GRAPH`.

These are the ground truth the routing evaluators (D2) grade against. Every one
is a real turn the operator saw go wrong or watched closely, and every one is a
"same same but different" case: a short, ordinary question whose CORRECT tool is
NOT the one a near-identical question would take. A frozen-reply judge cannot
tell these apart — the reply reads fine either way — so the discriminating
signal is the tool path.

A `Journey` is a user message plus the `Intent` it must satisfy; `grade` runs
the three routing evaluators over a `Trajectory` and returns their `Result`s.
The offline test (`tests/test_evals_journeys.py`) pins the intended vs the
regression path per journey; the network-gated test live-runs each real message
through Nora and grades what she actually did.
"""

from __future__ import annotations

from dataclasses import dataclass

from evals.evaluators import (
    SEED_INTENT_GRAPH,
    ConvergesInBudget,
    Intent,
    NoSiblingLeakage,
    PathMatchesIntent,
    Result,
)


@dataclass(frozen=True)
class Journey:
    """One real incident: the user's message, the tool path it must take, and a
    note on what actually happened / why the routing is what it is."""

    intent: Intent
    message: str
    note: str


# The tuning set — the four incidents the E1/E2 fix was written from. Grading a
# fix on ONLY these is the C1/DEC-75 overfitting trap (the eval can no longer tell
# rule-adherence from memorization of the incident's exact words), which is why
# `PARAPHRASE_JOURNEYS` below exists.
SEED_JOURNEYS: tuple[Journey, ...] = (
    Journey(
        intent=Intent(name="weather", required=frozenset({"weather"}), budget=1),
        message="今日凍唔凍呀?",  # "is it cold today?"
        note=(
            "A HK weather ask. Correct: the keyless HKO `weather` tool. The trap "
            "is `web_search` — the operator retired SearXNG precisely because the "
            "model kept web-searching HK weather instead of using the real feed."
        ),
    ),
    Journey(
        intent=Intent(name="carpark", required=frozenset({"web_search"}), budget=3),
        message="金鐘太古廣場而家仲有冇車位?",  # "any parking at Pacific Place now?"
        note=(
            "Real-time carpark vacancy has NO dedicated tool — it is not a bus, a "
            "route, the weather or a saved place, so `web_search` is the honest "
            "path. The trap is `place_info` (which answers ratings/open-hours, not "
            "live vacancy) or `transit` — a local tool that would confabulate a "
            "number. Budget 3: a couple of searches is fine, a runaway is not."
        ),
    ),
    Journey(
        intent=Intent(name="mall", required=frozenset({"place_info"}), budget=2),
        message="又一城today幾點關門?",  # "what time does Festival Walk close today?"
        note=(
            "Mall opening hours is `place_info`'s job ('whether it's open'). The "
            "trap is `web_search` — a generic lookup for something a place tool "
            "answers directly, the same over-reach the local-vs-web edge guards."
        ),
    ),
    Journey(
        intent=Intent(name="where_work", required=frozenset({"saved_places"}), budget=2),
        message="我邊度返工嚟?",  # "where is it that I work again?"
        note=(
            "The «work» label this household SAVED, so `saved_places`. The trap is "
            "`where_is` — the model reaching for live GPS to answer a question "
            "about a REMEMBERED place, which is the exact '我邊度返工' bug (DEC-74 "
            "context): a saved-label question answered with a location fix."
        ),
    ),
)


# HELD-OUT paraphrases (F2, external review 2026-08-10): the SAME routing intent
# as a seed journey, phrased so it does NOT reuse the doctrine's or the seed's
# literal trigger words ("天氣", "返工", "屋企", "車位", "關門"). conduct.md E2 now
# literally teaches "their home, their work, «屋企»… look for it before you decide
# you don't know it"; grading only the verbatim incident measures memorization.
# These test whether the ROUTING generalizes — a model that passes the seed but
# fails its paraphrase has learned the phrase, not the rule.
PARAPHRASE_JOURNEYS: tuple[Journey, ...] = (
    Journey(
        intent=Intent(name="weather_alt", required=frozenset({"weather"}), budget=1),
        message="出街使唔使帶遮呀?",  # "do I need to bring an umbrella out?" — no "天氣"
        note="Weather intent without the word weather — routes to `weather`, trap `web_search`.",
    ),
    Journey(
        intent=Intent(name="carpark_alt", required=frozenset({"web_search"}), budget=3),
        message="銅鑼灣時代廣場泊車貴唔貴呀而家?",  # parking price/availability, different mall
        note="Carpark/parking lookup, no dedicated tool → `web_search`; trap `place_info`.",
    ),
    Journey(
        intent=Intent(name="mall_alt", required=frozenset({"place_info"}), budget=2),
        message="海港城宜家仲營業緊嗎?",  # "is Harbour City still open now?" — no "關門"
        note="Is-a-place-open, different mall, no 'close' word → `place_info`; trap `web_search`.",
    ),
    Journey(
        intent=Intent(name="where_work_alt", required=frozenset({"saved_places"}), budget=2),
        message="我間公司個地址係邊嚟?",  # "what's my company's address?" — no "返工"/"屋企"
        note=(
            "A saved-place question (the company/office this household saved) phrased "
            "as «公司地址», not «返工» — routes to `saved_places`, trap `where_is`."
        ),
    ),
)


# The full gated set: tuning incidents PLUS their held-out paraphrases. A fix that
# passes the seeds but fails the paraphrases has overfit to the incident's words.
JOURNEYS: tuple[Journey, ...] = SEED_JOURNEYS + PARAPHRASE_JOURNEYS


def grade(journey: Journey, trajectory) -> dict[str, Result]:
    """Run the three routing evaluators over `trajectory` for `journey`.

    Returns a name→`Result` map so a caller (the two-tier gate, D4) can see WHICH
    routing property failed, not just that one did.
    """
    intent = journey.intent
    return {
        "PathMatchesIntent": PathMatchesIntent(intent).evaluate(trajectory),
        "NoSiblingLeakage": NoSiblingLeakage(intent, SEED_INTENT_GRAPH).evaluate(trajectory),
        "ConvergesInBudget": ConvergesInBudget(intent).evaluate(trajectory),
    }


#: The routing checks that HARD-FAIL the live journeys gate. Only
#: `PathMatchesIntent` for now: it is backend-independent — "did she reach for
#: the right tool at all" is true or false whether or not that tool's backend was
#: up. `NoSiblingLeakage` and `ConvergesInBudget` are REPORTED but advisory,
#: because a down backend drives fallback calls that inflate both (the D3
#: measurement saw carpark fall back into `place_info` only because `web_search`
#: had failed). Promote them to hard once the live journeys run wires real tool
#: backends — see `docs/eval-sop.md`.
HARD_ROUTING_CHECKS = ("PathMatchesIntent",)


def journeys_gate(graded: dict[str, Result]) -> bool:
    """Pass/fail one journey's graded routing for the live gate (D4 tier 2).

    Hard-fails iff any `HARD_ROUTING_CHECKS` result failed. Fail-closed: a missing
    key counts as a failure, so a caller that forgot to run a hard check cannot
    pass by omission."""
    return all(graded.get(name, Result(False, "not run")).passed for name in HARD_ROUTING_CHECKS)
