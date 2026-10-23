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


# DEC-82's tests live in `tests/unit/test_markdown.py`: the grammar is in
# `nora.agent.markdown`, and a text-rewrite assertion does not belong in a module
# about doctrine leaks. What stays here is the half that is genuinely about
# LeakGuard — the haystack speaking the same dialect as the candidate, above.


def test_a_recital_of_what_the_model_was_shown_is_caught_across_a_dash(tmp_path):
    """The model reads doctrine with its em dashes turned into commas
    (`nora.agent.punctuation`), so a verbatim recital carries the comma. The
    haystack has to be what she was SHOWN, or every window across a dash is a
    blind spot."""
    body = ("Silence is never a failure here — when you cannot answer "
            "properly, you say nothing at all and let the room carry on.")
    d = tmp_path / "doctrine"
    d.mkdir()
    write(d, "constraints.md", name="guardrails", order=40, body=body)
    guard = LeakGuard(DoctrineStore(d, canary="C", metrics=Metrics()).get(), canary="C",
                      min_span=40)
    # 44 characters with the comma at 20: every 40-character window crosses it.
    shown = "never a failure here, when you cannot answer"
    assert len(shown) == 44
    assert guard.check(shown) == "doctrine_span"


def test_leak_guard_blocks_the_prompts_transcript_markup(guard):
    """A draft quoting a transcript row by its tag is the prompt leaking, not a
    reply: members' bodies are escaped when rendered, so `<m n=` can only come
    from the prompt (e.g. a deliberation delivered as prose)."""
    draft = 'Let me reread.\n\n<m n="12">see you at six</m> came after my question.\n\n"Six works."'
    assert guard.check(draft) == "prompt_markup"
    assert guard.check("<m  n = '7'>x</m>") == "prompt_markup"


def test_leak_guard_allows_an_escaped_or_ordinary_angle_bracket(guard):
    """What a member can actually type reaches her escaped, and ordinary
    angle brackets are chat content."""
    for reply in ["&lt;m n=\"1\"&gt; is how it looks", "3 < 5 and <b>", "<mine> n=2"]:
        assert guard.check(reply) is None, reply


# Invisible format characters (zero-width space and joiner, word joiner, BOM,
# directional marks and overrides, a variation selector) carry nothing a reader
# sees, so inserting one into a verbatim span or the canary used to dodge the
# comparison while the text still read as the leak.
INVISIBLE = ("\u200b", "\u2060", "\ufeff", "\u200e", "\u202e", "\u2066", "\ufe0f", "\U000e0100")


@pytest.mark.parametrize("ch", INVISIBLE)
def test_an_invisible_character_inside_the_canary_is_still_the_canary(guard, ch):
    canary = "CANARY-7f3a"
    for i in range(1, len(canary)):
        text = f"the code is {canary[:i]}{ch}{canary[i:]}"
        assert guard.check(text) == "canary", (repr(ch), i)


@pytest.mark.parametrize("ch", INVISIBLE)
def test_an_invisible_character_inside_a_verbatim_doctrine_span_is_still_a_leak(guard, ch):
    span = BODY[:60]
    for i in range(len(span) + 1):
        text = f"Here are my rules: {span[:i]}{ch}{span[i:]}"
        assert guard.check(text) == "doctrine_span", (repr(ch), i)


# A combining mark (Mn, Me, Mc) is invisible in a rendered reply like the
# characters above, so it must not dodge the canary or a verbatim span either.
COMBINING = ("\u034f", "\u0301", "\u0903", "\u20dd")


@pytest.mark.parametrize("ch", COMBINING)
def test_a_combining_mark_inside_the_canary_is_still_the_canary(guard, ch):
    text = f"my canary is CANA{ch}RY-7f3a ok"
    assert guard.check(text) == "canary", repr(ch)


@pytest.mark.parametrize("ch", COMBINING)
def test_a_combining_mark_inside_a_verbatim_doctrine_span_is_still_a_leak(guard, ch):
    span = BODY[:60]
    for i in range(len(span) + 1):
        text = f"Here are my rules: {span[:i]}{ch}{span[i:]}"
        assert guard.check(text) == "doctrine_span", (repr(ch), i)


def test_an_invisible_character_in_ordinary_reply_does_not_make_it_a_leak(guard):
    assert guard.check("今晚七點半\u200b應該得，你 book 咗未？") is None
