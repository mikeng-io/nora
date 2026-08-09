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


JOURNEYS: tuple[Journey, ...] = (
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
