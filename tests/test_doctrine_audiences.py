"""Who each doctrine file is assembled for.

Every authored file reaches every audience she reads, not only `tag`: a non-tag
turn that received only the minimal sections (no identity, no voice, no conduct,
no constraints) would also make `LeakGuard`'s verbatim-leak corpus (R-6) far
thinner on the path that speaks unprompted.

**What this file owns.** Two things: which sections reach which audience
(ROUTING), and that each one arrives carrying a body (NON-DEGENERACY). It does
NOT own the WORDING of any rule — a needle is a containment test and containment
cannot see polarity, so the inversion of a sentence is invisible here by
construction. See the block above `SILENCE` for where polarity IS pinned.

The multi-message rule (DEC-40, DEC-88: a second message is a second ACT, and
one of the acts it may be is asking the person who knows) is true on any turn,
so its assertion holds in every audience.
"""

import re

import pytest

from nora.doctrine.loader import DoctrineStore
from nora.domain.framing import ParticipationHeader, ProactiveHeader

# `pictures` and `room-constraints` are room-occasion files only.
AUTHORED = (
    "identity", "belonging", "voice", "conduct", "pictures", "constraints", "room-constraints",
)

# Every audience of HERS, and exactly what the ASSEMBLY must resolve to.
#
# Not a restatement of `test_doctrine_content.EXPECTED`'s `applies_to` column.
# That one reads the FILES and pins what each one declares; this reads the
# assembled prompt and pins what a turn actually receives. The two can disagree
# — a routing change in `DoctrineStore.get`, an `always`, a duplicate `name`
# losing a file to the dedupe — and when they do, this is the side that says
# what the model was handed.
#
# `==`, not a subset, in both directions. A file silently GAINING an audience
# is the same class of defect as one losing it: `participation.md` is the
# Relevance Gate's instruction and must never resolve into a turn she reads.
#
# Deleting `proactive` from identity/voice/conduct/constraints would leave that
# audience resolving to `sections=['silence']` — no identity, no voice, no
# constraints, on a path that speaks UNCALLED — and deleting `tag` from
# identity/voice/constraints would gut her primary path. Per-audience routing
# tests alone would not notice either.
#
# HERS, so `relevance_gate` and `dreamer` are deliberately absent — they are
# instruments, not turns she reads. That leaves the gate's own instruction with
# NO non-degeneracy floor here: a gutted `participation.md` is caught only by
# `_has_body` in `nora/ingest/relevance_gate.py` (a "carries any prose" bar) and
# by its repin-able sha. This file's subject is the doctrine SHE receives; the
# gate's instrument wants its own floor next to the gate.
#
# `tag` and `proactive` assemble to byte-identical text today, so these are
# four parameters over three distinct assemblies — not four independent checks.
# They stay separate so a divergence between them is caught per audience.
HER_AUDIENCES: dict[str, tuple[str, ...]] = {
    "tag": AUTHORED,
    "heartbeat": AUTHORED,
    "participation": (*AUTHORED, "participation-conduct"),
    "proactive": AUTHORED,
    # The public summons: who she is and the hard
    # rules from core/, and the public/ files — and nothing written for a room.
    # Strangers read this surface, so belonging, the group voice and room
    # conduct are exactly what must NOT arrive; `==` pins that side too.
    "summons": (
        "identity", "public-voice", "public-conduct", "constraints", "public-constraints",
    ),
}

# A section tag with nothing behind it is not doctrine. `voice.md` truncated to
# its front matter plus a bare `# 你係邊個` still emits `name="voice"`, so every
# `f'name="{name}" in text'` assertion in this file would pass while the
# audience carried an empty persona.
#
# 400 is a floor, not a target: the shipped bodies sit well above it, so an
# honest edit does not trip it, and it is far above the ~10 characters a
# heading-only file leaves. Characters, not bytes: `voice.md` is
# Cantonese and a byte count would hold it to a third of the standard.
#
# It is a SHALLOW bar and it is meant to be: 94% of `conduct.md` can be deleted
# and a 401-character remnant passes. It answers "did a section arrive empty"
# and nothing about whether what arrived is any good. Raising it toward the
# shipped sizes would turn every honest tightening of a file into a failure,
# which is how a gate gets deleted rather than obeyed.
#
# It is also the ONLY thing standing after a legitimate edit that truncates
# `voice.md`, bumps its `version` and repins its sha: the parity gate goes green
# while all four cases below stay red. That is why it is here and not folded
# into the sha.
MIN_BODY_CHARS = 400

# A NEEDLE CANNOT CHECK POLARITY, AND THIS FILE DOES NOT PRETEND TO.
#
# `SILENCE` below is a substring of its own inversion — write "it is never true
# that silence is a complete answer — a success, not a failure" into
# `silence.md` and every assertion here still passes, because the needle is
# still in the haystack. That is structural: `in` is a containment test and
# containment is blind to the words wrapped around it. Lengthening the needle
# moves the blind spot, it does not close it.
#
# Deliberately NOT fixed here, and this is the "everything unenforced says so,
# in place" note rather than a silent gap. Polarity is pinned by
# `tests/test_doctrine_version_parity.py`: it holds a sha over every doctrine
# file's served section, so ANY edit to a body — inversion, negation, a deleted
# "not" — turns it red unless the author also bumps the file's `version` and
# repins, which is the deliberate act that gate exists to force.
#
# With that inversion applied to `silence.md`, this file passes, and the
# `[silence]` case of `test_doctrine_version_parity.py`'s
# `test_the_served_section_has_not_moved_without_the_version_moving` fails. A
# second sha pin here would duplicate that, and a duplicated pin is two places
# to update and one place to forget.
#
# **AND THE SHA PIN ONLY CATCHES THE UNANNOUNCED INVERSION.** The same
# inversion WITH a `version:` bump turns red only the version literal in
# `PINNED`, which the author then repins — that is the documented workflow, not
# an abuse of it. After the repin NOTHING IN THIS REPO CATCHES AN INVERTED
# DOCTRINE RULE. That is sharpest on `silence.md`, which is
# `change_policy: agent-may-edit`, so the agent inverting the sentence is the
# same one filling in the paperwork that would have flagged it.
#
# Recorded rather than fixed because the fix is not a test. Polarity is a
# claim about MEANING; the instruments that could judge it are a human reading
# a diff and the eval suite reading behaviour, and neither is a unit test. What
# a unit test can do — refuse a silent edit, refuse an empty section, refuse a
# wrong audience — is done here and next door. Do not read either gate as
# "the rules cannot be reversed".
#
# What this file DOES own is ROUTING and NON-DEGENERACY: which sections reach
# which audience, and that each arrives with a body. Wording is the sha's, and
# meaning is nobody's.
# What this file asserts is the multi-message RULE's reach across audiences,
# never its wording (DEC-88, DEC-96: `kind: conduct` describes rather than
# legislates), so the needle must be unique to that rule and indifferent to its
# wording. A rule that is rephrased and quietly loses the heartbeat audience is
# exactly the failure this test exists to catch. The needle is a fragment that
# spans no line break.
MULTI_MESSAGE = "a genuinely separate act"
# The needle is the stem of the claim that is FALSE on a heartbeat turn, where
# silence is a failure: an obligation occasion must never read that saying
# nothing is success. The claim lives in the participation and proactive
# headers, worded «silence is a complete answer here, not a failure»; the stem
# is shared by both wordings and is asserted ABSENT from every audience's
# doctrine.
#
# `_bodies` collapses whitespace, not because this needle wraps but for the
# NEGATIVE assertions: append the may-speak claim to a file that reaches
# heartbeat, hard-wrapped across two lines, and without the collapse
# `SILENCE not in _bodies("heartbeat")` passes while the claim is being served.
SILENCE = "silence is a complete answer"


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


_SECTION_RE = re.compile(
    r'<section [^>]*\bname="(?P<name>[^"]+)"[^>]*>\n(?P<body>.*?)\n</section>',
    re.DOTALL,
)


def _sections_in(store: DoctrineStore, audience: str) -> dict[str, str]:
    """`name` -> that section's body, as the audience actually receives it.

    Parsed out of the ASSEMBLED text rather than read off the files, because
    the assembly is what reaches the model: a section that never got selected
    is simply absent here, and one that arrived empty has an empty body here.
    Both are invisible to `f'name="{x}"' in text`.

    **Fails closed on anything it cannot parse cleanly.** Section BODIES are not
    escaped — only attributes are — so a body containing a literal `</section>`
    followed by a second `<section … name="voice" …>` re-anchors the scan, and a
    plain dict comprehension keeps the LAST match for a duplicate name. That
    shape serves `voice` as a ~10-character section and every assertion below
    would pass. Counting the matches against the open tags, and refusing a
    repeated name, turns a silent pass into a loud failure. Hostile doctrine
    prose is far-fetched; a body that quotes the prompt format while explaining
    it is not.

    The guard counts OPEN tags, so one direction is left: a stray `</section>`
    with no opening partner truncates a parsed body rather than adding a
    section, and the count still matches. That can only produce a false RED —
    a body reading shorter than it was served — which is the safe direction.
    """
    text = store.get(applies_to=audience).doctrine_text
    matches = list(_SECTION_RE.finditer(text))
    assert len(matches) == text.count("<section "), (
        f"the {audience} assembly has {text.count('<section ')} open tags but "
        f"{len(matches)} parseable sections — a body is carrying section markup "
        f"and this helper can no longer say what was served"
    )
    sections: dict[str, str] = {}
    for m in matches:
        assert m["name"] not in sections, (
            f'two sections named {m["name"]!r} in the {audience} assembly'
        )
        sections[m["name"]] = m["body"]
    return sections


@pytest.mark.parametrize("audience", sorted(HER_AUDIENCES))
def test_no_audience_of_hers_is_degenerate(doctrine_store, audience: str):
    """Every audience she reads gets the full authored set, and every section
    it gets arrives with a body.

    Without the authored files she delivers with no persona and no constraints, and `LeakGuard`'s
    verbatim-leak corpus (R-6) is built from whatever the audience resolves to,
    so a thin audience is a thin corpus on exactly the path that speaks.

    `proactive` is the sharpest case: it speaks UNCALLED, off her own earlier
    intention, so an audience that resolved to almost nothing would go unnoticed.
    """
    sections = _sections_in(doctrine_store, audience)

    assert set(sections) == set(HER_AUDIENCES[audience]), (
        f"the {audience} audience resolved to {sorted(sections)}, expected "
        f"{sorted(HER_AUDIENCES[audience])} — a file's `applies_to` moved, or "
        f"`get()` stopped selecting it"
    )
    for name, body in sorted(sections.items()):
        assert len(body.strip()) >= MIN_BODY_CHARS, (
            f"{name} reaches the {audience} audience as {len(body.strip())} "
            f"characters (floor {MIN_BODY_CHARS}). A section tag with nothing "
            f"behind it is not doctrine — the audience is degenerate even "
            f"though the tag is emitted."
        )


def test_the_right_to_stay_silent_comes_with_the_offered_turn(doctrine_store):
    """A participation turn and a proactive one are OFFERED: silence answers them.
    A heartbeat turn is OBLIGED to speak, so the may-speak claim is false there.

    What a turn IS belongs with the turn, so the claim lives only in
    `ParticipationHeader` and `ProactiveHeader`, and no doctrine any audience
    reads may carry it, or it reaches the heartbeat, which assembles from the
    same authored files."""
    for header in (ParticipationHeader(), ProactiveHeader(rows=())):
        text = " ".join(header.render().split())
        assert "silence is a complete answer here, not a failure" in text, header
    # The base rate is the proactive turn's only (ParticipationHeader's
    # docstring says why).
    assert "most turns like this one end that way" in ProactiveHeader(rows=()).render()
    assert "most turns" not in ParticipationHeader().render()
    for audience in ("tag", "heartbeat", "participation", "proactive"):
        assert 'name="silence"' not in doctrine_store.get(applies_to=audience).text
        assert SILENCE not in _bodies(doctrine_store, audience), audience


def test_the_gate_instrument_is_not_in_the_participation_audience(doctrine_store):
    """`participation.md` describes an instrument, not a person: it tells a
    cheap upstream call how to decide whether her turn happens. Injected into
    HER prompt it would read as a rulebook about her own worth to the room.

    `test_no_audience_of_hers_is_degenerate` already forbids it by asserting
    the exact section set. This stays as the NAMED statement of that one rule,
    because the reason is not readable from a set comparison, and because the
    confusable pair `participation` / `participation-conduct` is exactly where
    somebody will later "fix" the set by adding the wrong one of the two.

    Reads `doctrine_text`, not `text`, so the `<runtime_context>` block is out
    of scope. That block carries no section tags, so nothing is lost.
    """
    text = doctrine_store.get(applies_to="participation").doctrine_text
    assert 'name="participation-conduct"' in text
    assert 'name="participation"' not in text.replace('name="participation-conduct"', "")


def test_the_gate_instruction_reaches_no_audience_of_hers(doctrine_store):
    """`participation.md` is the Relevance Gate's whole system prompt, isolated
    on an audience no other file declares.

    Both directions matter. Into one of HER audiences it would be a document
    about when she is worth listening to, which is not doctrine. And the
    gate's own audience must stay thin: injecting her entire doctrine into the
    determination makes it behave like a turn (it opens tools).
    """
    needle = "You are a relevance gate"
    for aud in ("tag", "heartbeat", "participation"):
        assert needle not in _bodies(doctrine_store, aud), f"leaked into {aud}"
    gate = doctrine_store.get(applies_to="relevance_gate").text
    assert needle in gate
    assert gate.count("<section ") == 1
    for hers in ("identity", "belonging", "voice", "conduct", "pictures",
                 "constraints"):
        assert f'name="{hers}"' not in gate


def test_the_multi_message_rule_survives_in_both_audiences(doctrine_store):
    """DEC-40 is not a silence rule and is true on a turn nobody called, so it
    must reach every audience.

    The needle is a fragment that does NOT span a line break — a longer phrase
    fails on whitespace, not on meaning.
    """
    assert MULTI_MESSAGE in " ".join(_bodies(doctrine_store, "tag").split()).lower()
    assert MULTI_MESSAGE in " ".join(_bodies(doctrine_store, "heartbeat").split()).lower()



_ROOM_RUNTIME_CONTEXT = (
    "<runtime_context>\n"
    "display_name: Nora\n"
    "trigger: nora.000\n"
    "language: zh-HK\n"
    "canary: test-canary\n"
    "</runtime_context>"
)


def test_the_summons_is_told_no_language_and_every_room_occasion_still_is(doctrine_store):
    """The language hint is the configured GROUP default, and a public comment
    is not the group. A summons that carried `language: yue-Hant-HK` in its
    runtime context would answer an English comment in Cantonese. The language
    follows the comment (public/voice.md), so the summons gets no `language:`
    line at all, and every room occasion keeps its block byte-identical.
    """
    summons = doctrine_store.get(applies_to="summons")
    assert not [line for line in summons.text.splitlines() if line.startswith("language:")]
    assert "language:" not in summons.runtime_context
    assert "canary: test-canary" in summons.runtime_context
    for audience in ("tag", "heartbeat", "participation", "proactive"):
        doctrine = doctrine_store.get(applies_to=audience)
        assert doctrine.runtime_context == _ROOM_RUNTIME_CONTEXT, audience
        assert doctrine.text.endswith(_ROOM_RUNTIME_CONTEXT), audience


def test_the_summons_carries_no_room_media_mechanics(doctrine_store):
    """How a room's media placeholders read (`[image a3f2c1: …]`, the
    transcript, asking for the full reading) is room-chat mechanics. It lives in
    rooms/pictures.md, not core, so a summons does not carry it to a stranger's
    comment, where there is no transcript and no placeholder. Every room
    occasion gets it; its law half is rooms/constraints.md, the last section of
    every room occasion."""
    summons = doctrine_store.get(applies_to="summons").text
    assert "What you already know about a picture" not in summons
    assert "transcript" not in summons
    assert "[image a3f2c1" not in summons
    for audience in ("tag", "heartbeat", "participation", "proactive"):
        doctrine = doctrine_store.get(applies_to=audience)
        assert "# What you already know about a picture" in doctrine.text, audience
        assert "[image a3f2c1" in doctrine.text, audience
        names = [s.name for s in doctrine.sections]
        assert names[-3:] == ["pictures", "constraints", "room-constraints"], (audience, names)
