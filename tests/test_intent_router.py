"""The intent router: precision-first, ABSTAINING. It must force `place_info`
ONLY on an explicit opening-hours / still-open surface, and abstain (None) on
everything else — especially the carpark vacancy case, which correctly wants
`web_search` and shares a mall name with the hours case ("same place, different
intent")."""

import pytest

from nora.agent.intent_router import route_intent

# The eight journey messages (evals/journeys.py) — the router's real acceptance set.
FORCE_PLACE_INFO = [
    "又一城today幾點關門?",        # mall (seed)
    "海港城宜家仲營業緊嗎?",       # mall_alt (held-out paraphrase)
]
ABSTAIN = [
    "今日凍唔凍呀?",                # weather
    "出街使唔使帶遮呀?",            # weather_alt
    "金鐘太古廣場而家仲有冇車位?",  # carpark — MUST abstain (web_search is correct)
    "銅鑼灣時代廣場泊車貴唔貴呀而家?",  # carpark_alt — MUST abstain
    "我邊度返工嚟?",                # where_work
    "我間公司個地址係邊嚟?",         # where_work_alt
]


@pytest.mark.parametrize("q", FORCE_PLACE_INFO)
def test_opening_hours_forces_place_info(q):
    assert route_intent(q) == "place_info", q


@pytest.mark.parametrize("q", ABSTAIN)
def test_everything_else_abstains(q):
    assert route_intent(q) is None, q


def test_empty_abstains():
    assert route_intent("") is None
    assert route_intent("   ") is None


def test_never_forces_web_search_or_any_non_place_tool():
    # The router only ever forces place_info (the one measured residual) — it must
    # never force web_search (that would be the fail-closed confident-wrong-route).
    for q in FORCE_PLACE_INFO + ABSTAIN:
        assert route_intent(q) in (None, "place_info")
