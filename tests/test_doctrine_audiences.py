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
# (called means must-speak, offered means may-speak). The new needle is the
# half that is FALSE on a heartbeat turn, where silence is a failure: an
# obligation occasion must never read that saying nothing is success.
SILENCE = "Most offers of the floor end that way"


def _bodies(store: DoctrineStore, audience: str) -> str:
    text = store.get(applies_to=audience).text
    return re.sub(r"<section[^>]*>|</section>|<nora_doctrine>|</nora_doctrine>", "", text)


def test_the_heartbeat_audience_is_not_degenerate(doctrine_store):
    """All four authored files must reach a non-tag turn. Without this she
    delivers with no persona and no constraints, and LeakGuard's verbatim-leak
    corpus is missing the identity and conduct rules."""
    text = doctrine_store.get(applies_to="heartbeat").text
    for name in AUTHORED:
        assert f'name="{name}"' in text, f"{name} missing from the heartbeat audience"


def test_silence_reaches_called_and_offered_turns_only(doctrine_store):
    """The redefined split (design/dreamer-agent-loop/35): silence.md speaks to
    a tag turn (must-speak) and a participation turn (may-speak) — the two
    audiences the must/may distinction exists between. A heartbeat turn is
    OBLIGED to speak, so the may-speak half is false there and the file stays
    out."""
    assert SILENCE in _bodies(doctrine_store, "tag")
    assert SILENCE in _bodies(doctrine_store, "participation")
    assert SILENCE not in _bodies(doctrine_store, "heartbeat")


def test_the_participation_audience_is_not_degenerate(doctrine_store):
    """The same argument the heartbeat audience needed (above), one audience
    later: a participation turn must carry the full persona, conduct and
    constraint set — LeakGuard's verbatim-leak corpus is built from whatever
    the audience resolves to, and a thin corpus on the path that speaks
    UNCALLED is the worst version of thin."""
    text = doctrine_store.get(applies_to="participation").text
    for name in (*AUTHORED, "silence", "participation-conduct"):
        assert f'name="{name}"' in text, f"{name} missing from the participation audience"


def test_participation_conduct_reaches_no_other_audience(doctrine_store):
    """The may-speak rulebook is the participation occasion's own. On a tag it
    would contradict must-speak; on a heartbeat turn it would licence the one
    thing an obligation occasion may not do."""
    needle = "a question nobody asked"
    bodies = {
        aud: " ".join(_bodies(doctrine_store, aud).split())
        for aud in ("participation", "tag", "heartbeat")
    }
    assert needle in bodies["participation"]
    assert needle not in bodies["tag"]
    assert needle not in bodies["heartbeat"]


def test_the_multi_message_rule_survives_in_both_audiences(doctrine_store):
    """DEC-40 lived inside the silence section but is not a silence rule, and is
    true on a turn nobody called. Splitting on the heading would have deleted it.

    The needle is a fragment that does NOT span a line break in either the
    current wrapping or the replacement — a longer phrase fails on whitespace,
    not on meaning.
    """
    assert MULTI_MESSAGE in " ".join(_bodies(doctrine_store, "tag").split()).lower()
    assert MULTI_MESSAGE in " ".join(_bodies(doctrine_store, "heartbeat").split()).lower()

