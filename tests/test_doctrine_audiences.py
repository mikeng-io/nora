"""Who each doctrine file is assembled for.

Every authored file used to be `applies_to: [tag]`, so a non-tag turn would have
received only the minimal non-tag sections: no identity, no voice, no
conduct, no constraints — and `LeakGuard`'s verbatim-leak corpus (R-6) would
have been far thinner on the very path that first speaks unprompted.

The split that makes a second audience possible is smaller than it looks.
`# Silence is the default` was not homogeneous: its first three sentences are
FALSE on a turn nobody called, and its last sentence is DEC-40's multi-message
rule, which is true on any turn. Splitting on the heading would have deleted
DEC-40 from the new audience without a single test noticing. DEC-88 rewrote
that rule (a second message is a second ACT, and one of the acts it may be is
asking the person who knows); it is still true on any turn, so the assertion
below still holds in both audiences and only its needle moved.
"""

import re

from nora.doctrine.loader import DoctrineStore

AUTHORED = ("identity", "voice", "conduct", "constraints")
# DEC-88 rewrote the rule this needle points at — it used to be the flat
# prohibition "never split one answer across several", which was the sentence
# multi-message and mentions were later built to contradict. The needle moves
# with it rather than the test being deleted: what this file asserts is the
# RULE's reach across audiences, never its wording, and a rule that is
# rephrased and quietly loses the heartbeat audience is exactly the failure
# this test exists to catch.
# The v9 rewrite (DEC-96) dropped the SHOUTED "second ACT" for ordinary prose —
# `kind: conduct` describes rather than legislates. What this file is actually
# checking is that the multi-message rule reaches BOTH audiences, so it needs a
# needle that is unique to that rule and indifferent to its wording.
# Re-pointed 2026-08-17 for the operator's v12 voice / v11 conduct rewrite
# (a2cb1ae, ad75351), which re-worded the permission from «a second act» to «a
# genuinely separate act». The RULE is unchanged and so is what this pins; only
# the wording it binds to moved. Still a fragment that spans no line break.
MULTI_MESSAGE = "a genuinely separate act"
# RE-POINTED for the participation occasion (design/dreamer-agent-loop/35):
# the old needle — "That's the only reason you ever speak" — was the exact
# claim the occasion overturns, and it left the file with the redefinition
# (called means must-speak, offered means may-speak). The needle is the half
# that is FALSE on a heartbeat turn, where silence is a failure: an obligation
# occasion must never read that saying nothing is success.
#
# RE-POINTED AGAIN 2026-09-11 (issue #168). `14a55ab8` rewrote silence.md from
# two occasions to three and rewrote this sentence with it — «Most offers of
# the floor end that way» became «Most turns nobody asked for end that way» —
# so the needle matched nothing and the test had been red since. The ROUTING
# was never broken: silence.md has declared `applies_to: [tag, participation,
# proactive]` throughout, and resolving the store shows its section reaching
# all three. A stale needle reads exactly like a routing defect from the
# assertion output alone, which is how the issue came to be filed as one.
#
# `_bodies` now collapses whitespace. NOT because this needle wraps — it sits
# inside one 80-column line, and removing the collapse leaves this test green.
# It is the NEGATIVE assertions it hardens: append the may-speak claim to a
# file that reaches heartbeat, hard-wrapped across two lines, and without the
# collapse `SILENCE not in _bodies("heartbeat")` passes while the claim is
# being served. Measured both ways.
SILENCE = "silence is a complete answer — a success, not a failure"


def _bodies(store: DoctrineStore, audience: str) -> str:
    """The assembled bodies, section tags removed and whitespace collapsed.

    The collapse is for the NEGATIVE assertions. Doctrine prose is hard-wrapped,
    so a forbidden claim that happens to break across two lines matches nothing
    and `X not in _bodies(...)` passes while X is being served. Falsified: the
    may-speak sentence appended to `conduct.md` (which reaches heartbeat) across
    a line break is caught with the collapse and missed without it.

    It costs nothing on the positive side and makes needles insensitive to
    re-wrapping, which is worth having — but that is the side effect, not the
    reason.
    """
    text = store.get(applies_to=audience).text
    text = re.sub(r"<section[^>]*>|</section>|<nora_doctrine>|</nora_doctrine>", "", text)
    return re.sub(r"\s+", " ", text)


def test_the_heartbeat_audience_is_not_degenerate(doctrine_store):
    """All four authored files must reach a non-tag turn. Without this she
    delivers with no persona and no constraints, and LeakGuard's verbatim-leak
    corpus is missing the identity and conduct rules."""
    text = doctrine_store.get(applies_to="heartbeat").text
    for name in AUTHORED:
        assert f'name="{name}"' in text, f"{name} missing from the heartbeat audience"


def test_silence_reaches_called_and_offered_turns_only(doctrine_store):
    """silence.md speaks to the three occasions a turn can arrive on — a tag
    (called: must-speak), a participation turn and a proactive one (both
    offered: may-speak). A heartbeat turn is OBLIGED to speak, so the may-speak
    half is false there and the file stays out.

    `proactive` is asserted because `14a55ab8` added it to the file's
    `applies_to` and nothing checked it; the third occasion is the whole point
    of that rewrite."""
    # ROUTING, asserted structurally so it cannot go stale. This is the claim
    # #168 was filed against, and prose could never have settled it: a rewritten
    # sentence and a missing section produce the same failure, and pytest elides
    # the middle of the haystack so the section is invisible either way.
    for audience in ("tag", "participation", "proactive"):
        assert 'name="silence"' in doctrine_store.get(applies_to=audience).text, audience
    assert 'name="silence"' not in doctrine_store.get(applies_to="heartbeat").text

    # CONTENT: the may-speak half must be there to read, and must not reach an
    # occasion that is obliged to speak.
    assert SILENCE in _bodies(doctrine_store, "tag")
    assert SILENCE in _bodies(doctrine_store, "participation")
    assert SILENCE in _bodies(doctrine_store, "proactive")
    assert SILENCE not in _bodies(doctrine_store, "heartbeat")


def test_the_participation_audience_is_not_degenerate(doctrine_store):
    """The same argument the heartbeat audience needed (above), one audience
    later: a participation turn must carry the full persona, conduct and
    constraint set — LeakGuard's verbatim-leak corpus is built from whatever
    the audience resolves to, and a thin corpus on the path that speaks
    UNCALLED is the worst version of thin."""
    text = doctrine_store.get(applies_to="participation").text
    for name in (*AUTHORED, "silence"):
        assert f'name="{name}"' in text, f"{name} missing from the participation audience"
    # And NOT the gate's instruction. `participation.md` describes an
    # instrument, not a person: it tells a cheap upstream call how to decide
    # whether her turn happens. Injected into HER prompt it would read as a
    # rulebook about her own worth to the room.
    assert 'name="participation"' not in text


def test_the_gate_instruction_reaches_no_audience_of_hers(doctrine_store):
    """`participation.md` is the Relevance Gate's whole system prompt, isolated
    on an audience no other file declares.

    Both directions matter. Into one of HER audiences it would be a document
    about when she is worth listening to, which is not doctrine. And the
    gate's own audience must stay thin: the superseded design injected her
    entire doctrine into the determination, and the trace that killed it
    showed the determination opening a weather tool.
    """
    needle = "You are a relevance gate"
    for aud in ("tag", "heartbeat", "participation"):
        assert needle not in _bodies(doctrine_store, aud), f"leaked into {aud}"
    gate = doctrine_store.get(applies_to="relevance_gate").text
    assert needle in gate
    assert gate.count("<section ") == 1
    for hers in ("identity", "voice", "silence", "conduct", "constraints"):
        assert f'name="{hers}"' not in gate


def test_the_multi_message_rule_survives_in_both_audiences(doctrine_store):
    """DEC-40 lived inside the silence section but is not a silence rule, and is
    true on a turn nobody called. Splitting on the heading would have deleted it.

    The needle is a fragment that does NOT span a line break in either the
    current wrapping or the replacement — a longer phrase fails on whitespace,
    not on meaning.
    """
    assert MULTI_MESSAGE in " ".join(_bodies(doctrine_store, "tag").split()).lower()
    assert MULTI_MESSAGE in " ".join(_bodies(doctrine_store, "heartbeat").split()).lower()

