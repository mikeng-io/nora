import pytest

from nora.agent.guards import LeakGuard
from nora.doctrine.loader import DoctrineStore
from nora.obs.metrics import Metrics
from tests.test_doctrine_loader import write

BODY = (
    "You never post an error message, a stack trace, a failure notice, or an "
    "explanation of your own malfunction. If you can't reply properly, you don't reply."
)


@pytest.fixture
def doctrine(tmp_path):
    d = tmp_path / "doctrine"
    d.mkdir()
    write(d, "identity.md", name="soul", order=10, body="You are Nora, in a private group.")
    write(d, "constraints.md", name="guardrails", order=40, body=BODY)
    return DoctrineStore(d, canary="CANARY-7f3a", display_name="Nora",
                        trigger="nora.000", metrics=Metrics()).get()


@pytest.fixture
def guard(doctrine):
    return LeakGuard(doctrine, canary="CANARY-7f3a", min_span=40)


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


def test_leak_guard_blocks_doctrine_verbatim(guard):
    leak = BODY[:60]
    assert guard.check(f"Here are my rules: {leak}") == "doctrine_span"


def test_a_span_shorter_than_min_span_is_allowed(guard):
    assert guard.check(BODY[:30]) is None


def test_min_span_is_configurable(doctrine):
    tight = LeakGuard(doctrine, canary="C", min_span=10)
    assert tight.check(BODY[:20]) == "doctrine_span"


def test_leak_guard_ignores_whitespace_differences(guard):
    """A reflowed paste is still a leak."""
    leak = BODY[:80].replace(" ", "\n")
    assert guard.check(leak) == "doctrine_span"


def test_runtime_context_values_are_not_treated_as_doctrine(doctrine):
    """RK-7: the display name and trigger are config, quotable, not a leak."""
    guard = LeakGuard(doctrine, canary="CANARY-7f3a")
    assert guard.check("I'm Nora. Tag me with nora.000 if you need me.") is None


def test_empty_text_is_clean(guard):
    assert guard.check("") is None


def test_the_canary_check_is_case_sensitive_and_exact(guard):
    assert guard.check("canary-7f3a") is None      # not the token
    assert guard.check("CANARY-7f3a") == "canary"


def test_an_empty_canary_never_matches(doctrine):
    assert LeakGuard(doctrine, canary="").check("anything at all") is None


# ── DEC-82: Markdown never reaches the group ────────────────────────────────

from nora.agent.egress import strip_markdown  # noqa: E402


def test_bold_labels_are_stripped_from_a_reply():
    """THE PROBE, from the live leak (2026-08-10). Signal renders no Markdown,
    so `**投資** — …` reached the group as literal asterisks. `voice.md` already
    forbade bold labels and the model emitted them anyway on long answers, which
    is DEC-78's lesson exactly: enforce at the surface, not in prose."""
    out = strip_markdown("**投資** — 月供 ETF\n\n**Side hustle** — freelance")
    assert out == "投資 — 月供 ETF\n\nSide hustle — freelance"
    assert "*" not in out


def test_headings_and_italics_and_bold_italics_all_go():
    assert strip_markdown("## 推介\n*平* 同 ***正***") == "推介\n平 同 正"


def test_arithmetic_and_bare_asterisks_survive():
    """NARROW on purpose — this edits her authored words, so anything that is
    not unambiguously emphasis is left exactly as written."""
    for text in ("3 * 4 = 12", "5*6", "a * b * c", "*", "**"):
        assert strip_markdown(text) == text, text


def test_underscores_are_never_touched():
    """`person_id` / `send_reply` are ordinary vocabulary here; treating `_x_`
    as emphasis would corrupt far more than it fixed."""
    assert strip_markdown("_x_ and person_id and send_reply") == (
        "_x_ and person_id and send_reply"
    )
