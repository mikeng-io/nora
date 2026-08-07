"""Who each doctrine file is assembled for.

Every authored file used to be `applies_to: [tag]`, so a non-tag turn would have
received only the generated capability section: no identity, no voice, no
conduct, no constraints — and `LeakGuard`'s verbatim-leak corpus (R-6) would
have shrunk to that one block on the very path that first speaks unprompted.

The split that makes a second audience possible is smaller than it looks.
`# Silence is the default` was not homogeneous: its first three sentences are
FALSE on a turn nobody called, and its last sentence is DEC-40's multi-message
rule, which is true on any turn. Splitting on the heading would have deleted
DEC-40 from the new audience without a single test noticing.
"""

import re
from collections import Counter
from pathlib import Path

from nora.doctrine.loader import DoctrineStore

ROOT = Path(__file__).resolve().parents[1]

AUTHORED = ("identity", "voice", "conduct", "constraints")
MULTI_MESSAGE = "never split one answer across several"
SILENCE = "That's the only reason you ever speak"

# The only prose the split is allowed to ADD to the tag audience: one heading in
# `conduct.md` for the sentence that stayed behind, and `voice.md`'s new section
# on the register times are said back in (spec §10.2). Written out here rather
# than read off disk on purpose — a bound that sources itself from the thing it
# bounds is not a bound.
NEW_CONDUCT_HEADING = "# One answer, however many messages"
NEW_VOICE_SECTION = """
# Times you are given, and times you say back

When somebody asks you to remember something for later, you answer in the register they
asked in. 「等陣提我洗衫」 gets 「好, 等陣提你」 — not 「好, 19:47 提你」. 「七點提我出門」 gets
「好, 七點」. The exact moment is your problem, not theirs; saying a timestamp back at
someone who said 「等陣」 is the machinery showing through.

You still say something back, so a wrong guess dies now instead of at the wrong hour. And
you only ask 「幾點?」 when there is genuinely nothing sensible to assume — a friend who
answers 「等陣提我洗衫」 with 「幾點? 15 分鐘定 30 分鐘?」 is not being careful, they are being
a judge.
"""
ADDED_TOKENS = set(NEW_CONDUCT_HEADING.split()) | set(NEW_VOICE_SECTION.split())


def _bodies(store: DoctrineStore, audience: str) -> str:
    text = store.get(applies_to=audience).text
    return re.sub(r"<section[^>]*>|</section>|<nora_doctrine>|</nora_doctrine>", "", text)


def test_the_heartbeat_audience_is_not_degenerate(doctrine_store):
    """All four authored files must reach a non-tag turn. Without this she
    delivers with no persona and no constraints, and LeakGuard's verbatim-leak
    corpus shrinks to the capability block."""
    text = doctrine_store.get(applies_to="heartbeat").text
    for name in AUTHORED:
        assert f'name="{name}"' in text, f"{name} missing from the heartbeat audience"


def test_silence_reaches_tags_only(doctrine_store):
    assert SILENCE in _bodies(doctrine_store, "tag")
    assert SILENCE not in _bodies(doctrine_store, "heartbeat")


def test_the_multi_message_rule_survives_in_both_audiences(doctrine_store):
    """DEC-40 lived inside the silence section but is not a silence rule, and is
    true on a turn nobody called. Splitting on the heading would have deleted it.

    The needle is a fragment that does NOT span a line break in either the
    current wrapping or the replacement — a longer phrase fails on whitespace,
    not on meaning.
    """
    assert MULTI_MESSAGE in " ".join(_bodies(doctrine_store, "tag").split())
    assert MULTI_MESSAGE in " ".join(_bodies(doctrine_store, "heartbeat").split())


def test_tag_audience_words_are_unchanged_except_the_new_prose(doctrine_store):
    """The split adds one <section> boundary and one heading; it must not change
    or lose any RULE. Byte-identity is impossible — `_assemble` wraps per file —
    so compare token multisets against a capture taken BEFORE any doctrine file
    was edited, and allow exactly the tokens the two new blocks introduce.
    """
    fixture = ROOT / "tests" / "fixtures" / "tag_doctrine_bodies.txt"
    before = Counter(fixture.read_text().split())
    after = Counter(_bodies(doctrine_store, "tag").split())
    removed = before - after
    added = after - before
    assert not removed, f"tag audience LOST tokens: {sorted(removed)}"
    assert set(added) <= ADDED_TOKENS, f"unexpected additions: {sorted(set(added) - ADDED_TOKENS)}"
