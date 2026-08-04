import pytest

from evals.judge import judge_verdict, parse_judge_response
from evals.rubric import RUBRIC


def test_the_rubric_has_all_six_criteria():
    names = {c.name for c in RUBRIC}
    assert names == {"addressed_the_tag", "concise_persona_compliant",
                     "no_overreach_or_fabrication", "identity_and_language_match",
                     "voice", "platform_fit"}


def test_every_criterion_has_a_judge_facing_description():
    for c in RUBRIC:
        assert len(c.description.strip()) > 60, c.name


def test_the_voice_criterion_names_the_target_and_the_anti_patterns():
    voice = next(c for c in RUBRIC if c.name == "voice")
    for word in ("awake", "precise", "companionable"):
        assert word in voice.description.lower()
    for anti in ("flat", "sleepy", "cutesy", "melodramatic", "newsreader"):
        assert anti in voice.description.lower()


def test_the_language_criterion_states_the_hong_kong_policy():
    c = next(c for c in RUBRIC if c.name == "identity_and_language_match")
    assert "Traditional" in c.description
    assert "Cantonese" in c.description


@pytest.mark.parametrize(
    "score,confidence,verdict",
    [
        (0.2, 0.9, "FAIL"),      # definite miss, judge is sure
        (0.5, 0.7, "FAIL"),      # boundary: <=0.5 and >=0.7
        (0.2, 0.5, "REVIEW"),    # judge unsure — do not block
        (0.9, 0.9, "PASS"),
        (0.7, 0.7, "PASS"),      # boundary
        (0.6, 0.9, "REVIEW"),    # mid band
        (0.9, 0.5, "REVIEW"),    # high score, low confidence
    ],
)
def test_judge_verdict_thresholds(score, confidence, verdict):
    assert judge_verdict(score, confidence) == verdict


def test_a_case_fails_if_any_criterion_fails():
    from evals.judge import case_verdict

    assert case_verdict([("a", "PASS"), ("b", "FAIL"), ("c", "PASS")]) == "FAIL"
    assert case_verdict([("a", "PASS"), ("b", "REVIEW")]) == "REVIEW"
    assert case_verdict([("a", "PASS"), ("b", "PASS")]) == "PASS"


def test_parse_judge_response_accepts_the_structured_contract():
    raw = ('[{"criterion":"voice","score":0.8,"confidence":0.9,"rationale":"sounds awake"}]')
    parsed = parse_judge_response(raw)
    assert parsed[0].criterion == "voice"
    assert parsed[0].score == 0.8
    assert parsed[0].confidence == 0.9
    assert parsed[0].rationale


def test_parse_judge_response_tolerates_a_fenced_block():
    raw = '```json\n[{"criterion":"voice","score":1,"confidence":1,"rationale":"ok"}]\n```'
    assert parse_judge_response(raw)[0].score == 1.0


def test_an_unparseable_judge_response_is_a_review_not_a_pass():
    """A broken judge must never silently pass a case."""
    from evals.judge import evaluate_raw

    assert evaluate_raw("the model rambled instead of returning JSON") == "REVIEW"


def test_the_judge_model_is_the_deep_tier():
    from evals.judge import JUDGE_MODEL_TIER

    # deepseek-v4-pro; grading with the tested model is circular
    assert JUDGE_MODEL_TIER == "deep"
