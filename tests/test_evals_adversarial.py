import yaml
from pathlib import Path

from evals.run import adversarial_gate

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
