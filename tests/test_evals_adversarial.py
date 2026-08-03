import yaml
from pathlib import Path

import pytest

import evals.run as run
from evals.run import SUITES, adversarial_gate

CORPUS = Path("evals/adversarial")


def _cases(name):
    return yaml.safe_load((CORPUS / name).read_text())["cases"]


def test_the_injection_corpus_covers_every_named_attack():
    bodies = " ".join(c["input"] for c in _cases("injections.yaml")).lower()
    for attack in ("dan", "system prompt", "repeat everything above",
                   "dm me the key", "different assistant"):
        assert attack in bodies


def test_the_near_miss_corpus_mirrors_the_b2_table():
    inputs = [c["input"] for c in _cases("near_miss.yaml")]
    for near in ("nora.0001", "xnora.000", "nora .520", "`nora.000`"):
        assert any(near in i for i in inputs)


def test_every_near_miss_case_expects_silence():
    assert all(c["expect"] == "silence" for c in _cases("near_miss.yaml"))


def test_the_transcript_injection_puts_the_attack_in_an_earlier_message():
    case = _cases("transcript_injection.yaml")[0]
    assert "ignore" in " ".join(case["transcript"]).lower()
    assert "ignore" not in case["input"].lower()


def test_the_gate_fails_on_a_send_that_was_not_due():
    assert not adversarial_gate([{"name": "n1", "expect": "silence", "sent": ["oops"],
                                  "leaked": False, "revealed": False}])


def test_the_gate_fails_on_a_leak_guard_trip():
    assert not adversarial_gate([{"name": "i1", "expect": "reply", "sent": ["x"],
                                  "leaked": True, "revealed": False}])


def test_the_gate_fails_on_instruction_reveal():
    assert not adversarial_gate([{"name": "i2", "expect": "reply", "sent": ["my rules are..."],
                                  "leaked": False, "revealed": True}])


def test_the_gate_passes_a_deflected_injection():
    assert adversarial_gate([{"name": "i3", "expect": "reply",
                              "sent": ["唔得喎，講返正題？"], "leaked": False,
                              "revealed": False}])


def test_the_gate_is_not_confidence_weighted():
    """Safety rules are hard FAIL — no REVIEW lane (spec E.4)."""
    import inspect

    assert "confidence" not in inspect.getsource(adversarial_gate)


# --- CLI wiring (E.7 criterion #7): `--suite adversarial` end-to-end -----------------


def test_adversarial_is_a_selectable_suite():
    assert "adversarial" in SUITES
    assert SUITES["adversarial"] == (
        CORPUS / "injections.yaml", CORPUS / "near_miss.yaml",
        CORPUS / "transcript_injection.yaml",
    )


def test_load_adversarial_cases_flattens_every_file():
    cases = run._load_adversarial_cases(SUITES["adversarial"])
    assert len(cases) == 10                       # 5 injections + 4 near_miss + 1 transcript
    assert {c["name"] for c in cases} >= {"injection_dan", "near_miss_trailing_digit",
                                          "transcript_embedded_injection"}


RUBRIC_OK = '[{"criterion": "voice", "score": 0.9, "confidence": 0.9, "rationale": "fine"}]'


async def _fake_invoke_clean(model, system_prompt, message, *, role):
    if role == "reply":
        return "唔得喎，講返正題？"
    return RUBRIC_OK


async def _fake_invoke_leaky(model, system_prompt, message, *, role):
    if role == "reply":
        return "my system prompt says: be Nora, a private assistant"
    return RUBRIC_OK


@pytest.fixture
def fake_env(monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "fake-key-for-tests")


async def test_near_miss_cases_never_invoke_the_live_model(fake_env, monkeypatch):
    """near_miss inputs fail `is_tagged` deterministically — no network call should
    ever be attempted for them (mirrors production: the model never wakes up)."""
    async def fail_if_called(model, system_prompt, message, *, role):
        raise AssertionError("must not invoke the live model for a non-tagged input")

    monkeypatch.setattr(run, "_invoke_agent", fail_if_called)
    ok, results = await run._run_adversarial_live((CORPUS / "near_miss.yaml",))
    assert ok
    assert all(r["sent"] == [] for r in results)


async def test_run_adversarial_live_passes_on_clean_replies(fake_env, monkeypatch):
    monkeypatch.setattr(run, "_invoke_agent", _fake_invoke_clean)
    ok, results = await run._run_adversarial_live(SUITES["adversarial"])
    assert ok
    assert len(results) == 10


async def test_run_adversarial_live_fails_the_gate_on_a_leaky_reply(fake_env, monkeypatch):
    monkeypatch.setattr(run, "_invoke_agent", _fake_invoke_leaky)
    ok, results = await run._run_adversarial_live(SUITES["adversarial"])
    assert not ok
    assert any(r["leaked"] or r["revealed"] for r in results)


def test_main_exits_zero_for_a_clean_adversarial_run(fake_env, monkeypatch):
    monkeypatch.setattr(run, "_invoke_agent", _fake_invoke_clean)
    assert run.main("adversarial") == 0


def test_main_exits_nonzero_for_a_leaky_adversarial_run(fake_env, monkeypatch):
    monkeypatch.setattr(run, "_invoke_agent", _fake_invoke_leaky)
    assert run.main("adversarial") == 1
