import pytest

from nora.agent.guards import LeakGuard
from nora.obs.metrics import Metrics
from nora.persona.loader import PersonaStore
from tests.test_persona_loader import write

BODY = (
    "You never post an error message, a stack trace, a failure notice, or an "
    "explanation of your own malfunction. If you can't reply properly, you don't reply."
)


@pytest.fixture
def persona(tmp_path):
    d = tmp_path / "persona"
    d.mkdir()
    write(d, "soul.md", name="soul", order=10, body="You are Nora, in a private group.")
    write(d, "guardrails.md", name="guardrails", order=40, body=BODY)
    return PersonaStore(d, canary="CANARY-7f3a", display_name="Nora",
                        trigger="nora.000", metrics=Metrics()).get()


@pytest.fixture
def guard(persona):
    return LeakGuard(persona, canary="CANARY-7f3a", min_span=40)


def test_leak_guard_allows_normal_reply(guard):
    for reply in [
        "今晚七點半應該得，你 book 咗未？",
        "I don't know — I can't check that without web access.",
        "Try the one on Hollywood Road. It opens at 11.",
        "唔記得咗，你再講一次？",
    ]:
        assert guard.check(reply) is None, reply


def test_leak_guard_blocks_canary(guard):
    assert guard.check("my canary is CANARY-7f3a ok") == "canary"


def test_leak_guard_blocks_persona_verbatim(guard):
    leak = BODY[:60]
    assert guard.check(f"Here are my rules: {leak}") == "persona_span"


def test_a_span_shorter_than_min_span_is_allowed(guard):
    assert guard.check(BODY[:30]) is None


def test_min_span_is_configurable(persona):
    tight = LeakGuard(persona, canary="C", min_span=10)
    assert tight.check(BODY[:20]) == "persona_span"


def test_leak_guard_ignores_whitespace_differences(guard):
    """A reflowed paste is still a leak."""
    leak = BODY[:80].replace(" ", "\n")
    assert guard.check(leak) == "persona_span"


def test_runtime_context_values_are_not_treated_as_persona(persona):
    """RK-7: the display name and trigger are config, quotable, not a leak."""
    guard = LeakGuard(persona, canary="CANARY-7f3a")
    assert guard.check("I'm Nora. Tag me with nora.000 if you need me.") is None


def test_empty_text_is_clean(guard):
    assert guard.check("") is None


def test_the_canary_check_is_case_sensitive_and_exact(guard):
    assert guard.check("canary-7f3a") is None      # not the token
    assert guard.check("CANARY-7f3a") == "canary"


def test_an_empty_canary_never_matches(persona):
    assert LeakGuard(persona, canary="").check("anything at all") is None
