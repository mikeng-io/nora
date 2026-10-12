"""The shipped doctrine is a deliverable. These tests are the content contract (spec A.6)."""

import re
from pathlib import Path

import pytest

from nora.agent.registry import ToolRegistry
from nora.doctrine.loader import DoctrineStore, parse_section
from nora.obs.metrics import Metrics

DOCTRINE_DIR = Path(__file__).resolve().parents[1] / "nora" / "doctrine"

# section name -> (kind, order, applies_to). Keyed by the frontmatter `name`,
# which is what the section tag renders and what the authority graph uses, and
# which is unique across the doctrine. It was keyed by file STEM until the
# public summons (design/instagram-adoption/19): `public/conduct.md` has the
# same stem as `rooms/conduct.md`, so a stem key would read one file for the
# other and pass by coincidence. `_SCOPE_FOLDER` below maps each name to its
# file.
#
# The audience is pinned per file, not asserted as one blanket `("tag",)`,
# because that is the whole point of the split: everything occasion-neutral
# holds however the turn started (and so reaches `participation` too — the
# LeakGuard corpus argument of `tests/test_doctrine_audiences.py` applies to
# the newest audience exactly as it applied to `heartbeat`), while the
# occasion-SPECIFIC files are narrow: `silence` is the tag/participation
# difference itself, and `participation` is not hers at all — it is the
# Relevance Gate's whole instruction, on an audience no other file declares,
# so it can never leak into a turn she reads. A file quietly gaining or
# losing an audience is the failure this column exists to catch.
EXPECTED = {
    # `proactive` (design/dreamer-agent-loop/39 D12, DEC-162) joins every
    # occasion-neutral file AND `silence`: a proactive act turn is an offered
    # floor — Nora's own earlier intention, put back to her — so the
    # may-speak half of silence.md is true of it exactly as it is of
    # participation, and the heartbeat's obliged-to-speak reading is not.
    # `identity` and `constraints` are core: true on every surface, so they
    # reach the public summons too (design/instagram-adoption/19).
    "identity": ("identity", 10, ("tag", "heartbeat", "participation", "proactive", "summons")),
    # Split out of identity.md 2026-09-25 (operator): her place in a group, so
    # who she is can load on a surface that is not one.
    "belonging": ("identity", 12, ("tag", "heartbeat", "participation", "proactive")),
    "voice": ("identity", 20, ("tag", "heartbeat", "participation", "proactive")),
    "silence": ("conduct", 25, ("tag", "participation", "proactive")),
    "conduct": ("conduct", 30, ("tag", "heartbeat", "participation", "proactive")),
    # NOT hers: the gate's system prompt, isolated on its own audience.
    "participation": ("instrument", 5, ("relevance_gate",)),
    # NOT hers either: the reaction picker's instruction (`nora.agent.reaction`),
    # read after she has written a reply and never shown to her.
    "reaction": ("instrument", 6, ("reaction",)),
    # NOT hers: the speech formatter's instruction (`nora.agent.speech_format`,
    # design/voice-channel/08), read after her reply is final, on the spoken
    # copy only.
    "speech-format": ("instrument", 7, ("speech_format",)),
    # HERS, and ambient-only: the one thing an offered floor has that a tag
    # never does — several things worth answering at once, none addressed to
    # her. `conduct.md` is occasion-neutral and must not carry it.
    "participation-conduct": ("conduct", 35, ("participation",)),
    "constraints": (
        "constraint", 40, ("tag", "heartbeat", "participation", "proactive", "summons"),
    ),
    # Split out of constraints.md 2026-09-26: how a room's media placeholders
    # read. Room-chat mechanics, so the four room occasions only, and order 38
    # puts it immediately before constraints in every one of them.
    "pictures": ("constraint", 38, ("tag", "heartbeat", "participation", "proactive")),
    # The public surface: a summons under an Instagram post, read by strangers.
    # Exactly `summons` and nothing else, so none of it reaches a room.
    "public-voice": ("identity", 21, ("summons",)),
    "public-conduct": ("conduct", 31, ("summons",)),
    "public-constraints": ("constraint", 41, ("summons",)),
    # The law moved out of conduct, belonging, pictures and silence word for word
    # (design/context-plan/11 §6, step 2), so a later cut of practice and
    # character cannot take a rule with it. Every room occasion, and order 42
    # puts it right after constraints, last in each of them.
    "room-constraints": ("constraint", 42, ("tag", "heartbeat", "participation", "proactive")),
}


# section name -> (scope folder, file stem): the file is
# `nora/doctrine/<folder>/<stem>.md`. `tests/architecture/test_doctrine_scope_
# folders.py` pins the folder -> allowed-audience table this mirrors; this dict
# only needs to resolve a real path. The stem is the name everywhere except
# `public/` and `rooms/constraints.md`, whose files drop the `public-` and
# `room-` prefix because the folder says it.
_SCOPE_FOLDER = {
    "identity": ("core", "identity"), "constraints": ("core", "constraints"),
    "belonging": ("rooms", "belonging"), "voice": ("rooms", "voice"),
    "conduct": ("rooms", "conduct"), "silence": ("rooms", "silence"),
    "participation-conduct": ("rooms", "participation-conduct"),
    "pictures": ("rooms", "pictures"),
    "room-constraints": ("rooms", "constraints"),
    "participation": ("instruments", "participation"),
    "reaction": ("instruments", "reaction"),
    "speech-format": ("instruments", "speech-format"),
    "public-voice": ("public", "voice"),
    "public-conduct": ("public", "conduct"),
    "public-constraints": ("public", "constraints"),
}


def _path(name: str) -> Path:
    folder, stem = _SCOPE_FOLDER[name]
    return DOCTRINE_DIR / folder / f"{stem}.md"


def _shipped(**kw):
    return DoctrineStore(DOCTRINE_DIR, canary="C", strict=True,
                         tool_names=ToolRegistry().tool_names(), metrics=Metrics(), **kw)


def test_exactly_the_expected_files_ship():
    shipped = {
        parse_section(p).name: (p.parent.name, p.stem)
        for p in DOCTRINE_DIR.glob("**/*.md")
    }
    assert set(shipped) == set(EXPECTED)
    assert shipped == _SCOPE_FOLDER


@pytest.mark.parametrize(
    "name,kind,order,applies_to",
    sorted((n, k, o, a) for n, (k, o, a) in EXPECTED.items()),
)
def test_each_file_has_the_expected_name_kind_order_and_audience(name, kind, order, applies_to):
    section = parse_section(_path(name))
    assert section.name == name
    assert section.kind == kind
    assert section.order == order
    assert section.applies_to == applies_to


def test_the_shipped_doctrine_assembles_in_strict_mode():
    doctrine = _shipped().get()
    assert [s.name for s in doctrine.sections] == \
        ["identity", "belonging", "voice", "silence", "conduct", "pictures", "constraints",
         "room-constraints"]


def test_constraints_is_last_for_recency():
    """The hard rules close the prompt: the core constraints, then the room's."""
    assert [s.name for s in _shipped().get().sections[-2:]] == ["constraints", "room-constraints"]


def test_the_summons_doctrine_assembles_in_strict_mode_from_core_and_public_only():
    """The public summons (design/instagram-adoption/19) reads who she is and
    the hard rules, plus the public files, and NOTHING about a room: no
    belonging, no group voice, no room conduct, no silence rule written for a
    group. A strict load of the real tree, so a broken authority edge or an
    unknown audience fails here rather than at boot on the deploy host."""
    doctrine = _shipped().get(applies_to="summons")
    names = [s.name for s in doctrine.sections]
    assert names == [
        "identity", "public-voice", "public-conduct", "constraints", "public-constraints",
    ]
    for room_only in ("belonging", "voice", "conduct", "silence", "participation-conduct",
                      "pictures"):
        assert room_only not in names
    for section in doctrine.sections:
        assert len(section.not_here) > 40, section.name


def test_every_shipped_file_says_what_it_must_not_contain():
    """design/17 §7b item 2 — the boundary `soul.md` lacked.

    Had it carried a line saying capability lives elsewhere, its capability
    claim would have looked wrong the day it was written rather than eight weeks
    later. The loader rejects an empty `not_here`; this asserts the shipped
    files actually point somewhere rather than saying "nothing".
    """
    for section in _shipped().get().sections:
        assert len(section.not_here) > 40, section.name
        assert ".md" in section.not_here or "schema" in section.not_here, section.name


def test_voice_is_the_operators_alone():
    """design/17 §7b item 3: until this field existed, `voice.md`'s protection
    was the operator saying so in conversation."""
    assert parse_section(_path("voice")).change_policy == "operator-only"
    assert parse_section(_path("constraints")).change_policy == "operator-only"
    assert parse_section(_path("identity")).change_policy == "operator-only"


def _all_sections():
    """Every authored file, AUDIENCE-FREE: `get()` filters by `applies_to`, so
    graph tests read the files rather than an assembly."""
    return [parse_section(p) for p in sorted(DOCTRINE_DIR.glob("**/*.md"))]


def test_the_authority_graph_is_declared_relatively_and_terminates_at_tool_output():
    """`constraints.md` used to call itself "not negotiable" — an adjective with
    no position in an ordering. It now states its edges, and every other file
    states the same edge from the other side (the loader checks that symmetry).
    """
    sections = {s.name: s for s in _all_sections()}
    assert sections["constraints"].authority_above == ()
    for name in ("identity", "voice", "conduct", "participation-conduct"):
        assert "constraints" in sections[name].authority_above
        assert "tool-output" in sections[name].authority_below


def test_identity_voice_and_silence_conduct_are_peers_on_purpose():
    """The graph is a TOTAL order over tiers and a PARTIAL one over sections.

    design/17 §7b writes the tier order as `constraint -> identity/voice ->
    conduct -> ...`, with `identity/voice` deliberately one rung; `kind` — the
    only part of the authority model design/17 §6 says can be mechanised — puts
    `silence` and `conduct` on one rung the same way, both being `kind:
    conduct`. So neither pair naming the other is the declaration, not an
    omission, and this pins it: a later edit that ranks one of these pairs is
    asserting something no gate checks and no measurement supports, and should
    have to delete this test to do it.
    """
    sections = {s.name: s for s in _all_sections()}
    # `participation-conduct` joins the conduct tier: an occasion-specific
    # conduct file, unranked against its two peers for exactly the reason
    # `silence` and `conduct` are unranked.
    peers = (("identity", "voice"), ("identity", "belonging"),
             ("belonging", "voice"), ("silence", "conduct"),
             ("silence", "participation-conduct"),
             ("conduct", "participation-conduct"))
    for a, b in peers:
        assert sections[a].kind == sections[b].kind
        assert b not in sections[a].authority_above
        assert b not in sections[a].authority_below
        assert a not in sections[b].authority_above
        assert a not in sections[b].authority_below






def test_voice_carries_no_feminine_speech_markers():
    """DEC-91 — an operator ruling, pinned so the next session cannot re-decide it.

    It was asked for and REFUSED, and the argument is already in this file two
    tests up: `voice.md` lists «Cutesy — baby particles, exaggerated softness,
    performed sweetness» as a sound to AVOID, and feminine-coded Cantonese moves
    pull straight toward it. 人哋 as a self-reference and softened particle
    clusters (「啦嘛」「咯喎」「嘛」) are the two concrete shapes that were on the
    table; they fight the 串/抵死 register the same file is being widened toward,
    so adding them would make the file argue with itself.

    A decision that lives only in a chat log is one somebody re-opens next
    month, which is what a decision index and a named test exist to prevent.
    Asserted on the BODY, not the file, so a future note explaining the ruling
    can quote the tokens without failing its own rule.
    """
    body = (_path("voice")).read_text().split("---", 2)[2]
    for marker in ("人哋", "啦嘛", "咯喎"):
        assert marker not in body, (
            f"voice.md gained the feminine speech marker {marker!r}. This was "
            "asked for and refused (DEC-91) — it collides with the file's own "
            "«Cutesy» anti-pattern and with the 串/抵死 range it protects."
        )










def test_no_doctrine_file_carries_a_tool_catalogue():
    """The doctrine names CAPABILITIES in category terms; the schemas name
    tools.

    This replaces `test_conduct_names_the_three_tools_and_no_others`, whose
    name promised the assertion it never made: it checked that three tools were
    present and that `feed_honcho` was absent, and would have passed unchanged
    while the catalogue rotted. It did rot. `conduct.md` listed `load_transcript`,
    `read_peer_card` and `send_reply` and never learned about `now`,
    `group_members`, `lookup_person` or `about` — four of seven — beside a
    line telling her the conversation "covers almost every call".

    A prose catalogue in the doctrine has nothing keeping it in step with the
    registry, and there is no reason to run two: `nora.agent.prefill.
    _context_header` states the list once (pinned in both directions by
    `tests/test_prefill.py`) and the schemas are derived from the callables and
    cannot drift at all. So the rule is mechanical — no doctrine file may name a
    tool, and adding an eighth tool can never again silently falsify the
    doctrine.

    A tool is "named" here if it appears backticked (`about`) or as a bare
    snake_case identifier (load_transcript) — the two forms a catalogue is written
    in. Matching the bare word alone would be useless: `now` and `about` are
    also ordinary English, and `identity.md` says "I don't know" and "what you
    remember" in prose that is exactly what this file is supposed to encourage.
    """
    from nora.agent.tools import NATIVE_TOOL_BUDGETS

    tools = (*NATIVE_TOOL_BUDGETS, "feed_honcho")
    for path in DOCTRINE_DIR.glob("**/*.md"):
        text = path.read_text()
        for tool in tools:
            assert f"`{tool}`" not in text, (
                f"{path.name} names the tool `{tool}`. The doctrine describes what she can "
                "find out; _context_header and the tool schemas name how."
            )
            if "_" in tool:
                assert tool not in text, (
                    f"{path.name} names the tool {tool}. The doctrine describes what she "
                    "can find out; _context_header and the tool schemas name how."
                )


def test_the_silence_doctrine_is_stated_exactly_once_in_guardrails():
    """R-5's doctrine half lives in `constraints.md` alone.

    It used to be in `conduct.md` AND `constraints.md`, and the two copies had
    already drifted: conduct forbade "a note about your own limits or
    configuration", which collides head-on with soul/conduct telling her to say
    plainly when she cannot check something. Guardrails outranks conduct and is
    assembled last, so the surviving copy is the one there — narrowed to
    malfunction, with the collision spelled out.
    """
    guardrails = (_path("constraints")).read_text()
    assert "say nothing" in guardrails
    assert "never post an error" in guardrails.lower()
    assert "not about not knowing" in guardrails      # the de-collision clause
    assert "say nothing" not in (_path("conduct")).read_text()


def test_identity_makes_no_claim_about_what_she_can_find_out():
    """`identity.md` used to carry a whole "What you actually know" section.

    It began life saying "Two things, and only these", which was true when she
    had two tools and false the day she had five, silently, for eight weeks. The
    fix in DEC-28 was to rewrite the claim; the fix here is that identity does
    not get to make one. The section is gone and what describes her reach are
    the tool schemas (DEC-150), so the sentence has no way to be wrong.
    """
    text = (_path("identity")).read_text()
    body = text.split("---", 2)[2]
    assert "Two things, and only these" not in body
    assert "What you actually know" not in body
    assert "who is in this conversation" not in body
    # §4's three, plus «What you are» — the self-account, moved in from the
    # retired `self-description` SKILL on 2026-09-17 (operator, #227). The
    # fourth heading is a deliberate widening of this file and is pinned here so
    # a fifth cannot arrive quietly.
    assert [line for line in body.splitlines() if line.startswith("# ")] == \
        ["# Who you are", "# Where you come from", "# What you are"]

    # The widening's cost, guarded DERIVED rather than by a word list. This
    # file's whole scar is a capability claim that went stale in silence for
    # eight weeks, and «What you are» is the section that could plausibly grow
    # one. So: no registered tool name may appear in the body. Read off the live
    # registry, so a tool renamed or added is covered without anyone updating a
    # list here — a hand-written list is the same stale-copy shape the section
    # is forbidden from carrying.
    # Scope, stated because it is narrower than it looks: this catches a tool
    # named AS a tool. It cannot catch a capability described in prose ("you can
    # look things up"), and no regex can — that is the eval suites' job, not a
    # unit test's. Narrowed on a principle rather than a word list after the
    # first version fired on `about`, which is a real tool AND an ordinary
    # English word: an over-matching gate is the R-29 scar this repo already
    # paid for once. An identifier carrying `_` cannot be ordinary prose; a
    # single-word tool name counts only inside backticks, which is how this repo
    # writes identifiers in prose everywhere.
    from nora.agent.registry import ToolRegistry
    named = sorted(
        t for t in ToolRegistry().tool_names()
        if (re.search(rf"\b{re.escape(t)}\b", body) if "_" in t
            else f"`{t}`" in body)
    )
    assert not named, (
        f"identity.md names tools: {named}. What she can find out is the tool "
        f"schemas' job (DEC-150); a copy here is what went stale for eight weeks."
    )


def test_identity_carries_the_form_sentence():
    """v13 (2026-09-25, operator, option A; DEC-205, partly superseding
    DEC-193's placement clause). Her appearance used to live ONLY in the
    on-demand `image-generation` skill, which loads only when a turn is
    already drawing something — so asked "what do you look like?" nothing
    always-loaded told her, and asked to draw herself she inferred she had no
    form and drew an empty room.

    Pins the MEANING (she HAS a mascot form, and names at least one anchor
    that makes it recognisable), not the exact wording — a future rewrite of
    the sentence should still pass this. Proved by revert: reverting the
    sentence below is what turns this red; nothing else in the suite notices
    an identity file that is silent about her form.

    **Hardened against a negated rewrite (Opus review of #499).** The first
    cut of this test only grepped for the bare substrings "no form", "mascot"
    and an anchor — all three of which also appear in the INVERTED sentence
    "You have no form; never describe a mascot, no bun, no hoodie, no
    earphones", which would have passed it. So this now requires (1) the "no
    form"/"no appearance" substring, if present, to sit inside a construction
    that cancels it back to a positive claim (a "does not mean ... no form"
    shape — read alone, a bare "no form" asserts the opposite of what this
    file must say), and (2) neither "mascot" nor the matched anchor may be
    negated (no "no"/"not"/"never"/"n't"/"without" within a few words before
    it). A green here means the doctrine asserts she HAS this form, not
    merely that the words appear somewhere in the file.

    The full spec (all eight anchors, per-model prompting) stays in
    `nora/skills/image-generation/SKILL.md`; this is deliberately one
    sentence, not a second copy of it.
    """
    body = (_path("identity")).read_text().split("---", 2)[2]
    lower = body.lower()

    # A bare "no form"/"no appearance" IS the inverted claim unless a
    # cancelling construction sits right before it, the shape this file's own
    # sentence uses ("does not mean having no form").
    for phrase in ("no form", "no appearance"):
        idx = lower.find(phrase)
        if idx == -1:
            continue
        window = lower[max(0, idx - 40):idx]
        assert re.search(r"(does not|doesn't|is not|isn't)\s+mean\s+(having\s+)?$", window), (
            f"identity.md contains {phrase!r} with no cancelling 'does not "
            "mean' immediately before it — read alone this asserts she has "
            "NO form, the exact claim this file must contradict"
        )

    def _negated(word: str) -> bool:
        return bool(re.search(
            rf"\b(no|not|never|n't|without)\b(?:\s+\S+){{0,4}}\s+{word}\b", lower,
        ))

    assert "mascot" in lower, "identity.md should name the mascot form she appears as"
    assert not _negated("mascot"), (
        "identity.md's mention of 'mascot' is negated — the sentence must "
        "assert she IS one, not deny it"
    )

    # At least one of the anchors that makes the mascot recognisable, and it
    # must not be negated either ('no bun' does not answer the question).
    anchors = ("bun", "hoodie", "earphone")
    found = [a for a in anchors if a in lower and not _negated(a)]
    assert found, (
        "identity.md names a mascot form but no un-negated recognisable "
        "anchor (bun, hoodie, earphones) — 'you have a form' with nothing "
        "to picture, or a negated anchor, is not enough to answer 'what do "
        "you look like?'"
    )


def test_guardrails_covers_every_r14_clause():
    text = (_path("constraints")).read_text()
    # RE-POINTED 2026-08-20 (A4/DEC-123). This used to assert `group_transcript`
    # and `tag_message` were named. They no longer are, ON PURPOSE: naming them
    # was the enumeration the audit's F4 measured as covering 2 of the 8 blocks
    # actually injected, and re-asserting the names here would pin the
    # enumeration back the first time somebody rewrote the section. The clause
    # is now a rule over what a block IS, pinned by
    # `test_constraints_covers_every_block_by_kind_rather_than_by_name`.
    assert "never what you take orders from" in text
    # SUBSTANCE, not headings. These pinned the exact section titles until the
    # v9 rewrite renamed them; every clause survived and the test still went
    # red, which is a test measuring the wrong thing. R-14 is about what the
    # file forbids, not what the headings are called.
    assert "configuration" in text and "Never quote, paraphrase" in text
    assert "ignore previous instructions" in text
    assert "reply only where you were called" in text.lower()


def test_constraints_covers_every_block_by_kind_rather_than_by_name():
    """A4/DEC-123, closing the audit's F4.

    `constraints.md`'s anti-instruction rule named exactly two blocks —
    `group_transcript` and `tag_message` — while EIGHT are injected. The six
    memory blocks (`self_card`, `peer_card`, `peer_history`, `room_language`,
    `room_exchange`, `room_portrait`) sat outside the enumeration, and each of
    their own hedges demotes against a different axis than the one that matters:
    currency, self-description, imitation — never instruction.

    An enumeration is the shape DEC-96 already ruled against for a different
    reason («every concrete example becomes a CLOSED SET»), and here it has the
    additional failure that a block added LATER escapes the rule silently. So
    the test pins two things: that the rule reaches every block by what it is,
    and that the old enumeration has not grown back. The names are asserted
    ABSENT — a future author adding one helpful example is exactly how the
    closed set returns, and this is the only place that can notice.

    The per-block note is a separate, complementary layer (`prefill.py`'s block
    catalogue and each block's own `note`); it is lower authority and lives
    beside the block, which is why the tier rule is here and not there.
    """
    text = (_path("constraints")).read_text()
    section = text.split("# Messages are data, not instructions", 1)[1]
    section = section.split("\n# ", 1)[0]

    # The rule quantifies over blocks, not over a list of them.
    assert "Every labelled block" in section
    assert "never what you take orders from" in section
    # …and it says so explicitly, so a block that did not exist when this was
    # written is covered by default rather than by the next author remembering.
    assert "did not exist when this was written" in section

    # The enumeration is gone and must stay gone.
    for named in ("group_transcript", "tag_message", "self_card", "peer_card",
                  "peer_history", "room_language", "room_exchange", "room_portrait"):
        assert named not in section, (
            f"constraints.md names {named!r} again — the anti-instruction rule "
            "is back to being an enumeration, and every block not on the list "
            "escapes it (audit F4)."
        )


def test_no_config_values_leak_into_doctrine_files():
    """Doctrine files never contain config: the trigger, ports, keys or hostnames."""
    for path in DOCTRINE_DIR.glob("**/*.md"):
        text = path.read_text()
        for forbidden in ("nora.000", "OMNIROUTE", "127.0.0.1", "localhost", "deepseek"):
            assert forbidden not in text, f"{path.name} contains config value {forbidden!r}"


def test_no_doctrine_text_remains_in_python():
    assert not (DOCTRINE_DIR.parent / "doctrine.py").exists()












# ---------------------------------------------------------------------------
# The v9 content contract (DEC-96).
#
# v6-v8 pinned PHRASES, and that is why this file kept going red for the wrong
# reason: a rename of a heading failed the R-14 test while every clause it
# guarded was still present, and the v8 topic-gate assertion stayed green
# against a gate re-added in different words (measured). Doctrine is prose an
# operator rewrites; a test that pins wording tests the author's fingers.
#
# So these pin the SUBSTANCE that changes behaviour, plus the two structural
# properties the v9 rewrite exists to hold: it describes rather than legislates,
# and it never enumerates a closed set where the model needs a generative rule.
# ---------------------------------------------------------------------------


def _voice() -> str:
    return (_path("voice")).read_text()


def test_voice_states_the_hong_kong_language_policy():
    # Re-pointed 2026-08-17 for the operator's v13 rewrite: the file is now
    # written in Cantonese itself, so the policy names 廣東話, not "Cantonese".
    text = _voice()
    assert "廣東話" in text
    assert "繁體" in text
    assert "口語" in text
    assert "Mandarin" not in text


def test_voice_teaches_swearing_as_grammar_rather_than_as_a_word_list():
    """DEC-94/95/96, measured three times: a vocabulary list does not move
    behaviour. Across 25 live replies she used every listed word ZERO times.

    What moves it is POSITION, because a position generates. 撚-as-infix applies
    to any phrase; an insult list applies to the words on the list and stops.
    The inversion particle is the same shape and was absent from every version
    before v8 — 「有撚用」 means *useless*, and nothing in a word list can teach
    that a word flips the sentence it lands in.

    Pins the generative framing and nothing about which insults exist.

    RE-POINTED 2026-08-17 for the operator's v12 rewrite (a2cb1ae), and the
    change is a NARROWING that should be known: v12 dropped 「有撚用」/「反轉」 —
    the 撚-as-infix inversion, which was DEC-94's single most generative device
    and the one thing a word list provably cannot teach — along with 「句入面」
    and 「屌得唔夠力」. Those are no longer pinned by anything, because they are
    no longer in the file and voice.md is `change_policy: operator-only`.

    What v12 kept, and what this now pins, is the other half of the same
    position: the words are named as FUNCTION (重音/反應/節奏, chosen from the
    moment) rather than as vocabulary, plus the anti-template rule that killed
    the 屌-explain-punchline shape.

    RE-POINTED 2026-08-17 a second time for the operator's v13 rewrite, which
    put the whole file into Cantonese and cut the swearing section to three
    short paragraphs. The substance pinned below is unchanged: function over
    vocabulary, chosen from the live line, and the template shape still named
    as the false note. The anchors moved to the Cantonese phrases that now
    carry them (「性格賣點」 for "personality feature", 「模板」 for
    "punctuation template")."""
    text = _voice()
    assert "撚" in text and "鳩" in text
    # Function, not vocabulary — what the words DO in a sentence.
    assert "重音" in text and "節奏" in text
    # Chosen from the live line and the room, which generates; a list closes.
    assert "跟眼前嗰句" in text
    # The negative that stops it becoming a trait she performs.
    assert "性格賣點" in text
    # DEC-94's measured failure shape, still banned in terms.
    assert "模板" in text


def test_voice_hits_without_negotiating_or_reassuring():
    """A recent live trace had the words but not the stance.

    When somebody came back hostile, Nora repeatedly confirmed their account,
    announced that she would not scold them, and offered to end the exchange.
    That is a customer-service de-escalation wearing a swear word, not the
    independent Hong Kong friend the register describes. The voice needs the
    response shape as well as the Cantonese grammar: identify the live failure,
    hit it first, and do not seek permission or offer an exit before the hit.

    RE-POINTED 2026-08-17 for the operator's v12 rewrite (a2cb1ae). The Cantonese
    imperatives 「開火唔係逐點反駁」/「客服腔」 are gone; the stance they carried is
    not. v12 states it as the portrait's own negative — the customer-service
    residue is named as a tell that the assistant voice has taken over, and the
    hit is described as landing on one exact thing rather than rebutting a list.

    RE-POINTED 2026-08-17 again for the operator's v13 rewrite: the file is now
    written in Cantonese, so the anchors move to the phrases carrying the same
    stance — 「客服腔」 for the residue, 「仲有冇嘢幫到你」 for the offer-to-help
    ending, 「捉中一樣嘢就收手」 for the one-exact-thing hit, and
    「解釋自己嘅拒絕」 for narrating instead of making it.
    """
    text = _voice()
    # The de-escalation shape, banned as a tell rather than as an imperative.
    assert "客服腔" in text
    assert "仲有冇嘢幫到你" in text
    # Hit once, on the live thing — the replacement for 「唔係逐點反駁」.
    assert "捉中一樣嘢就收手" in text
    # And do not narrate the refusal or the tone instead of making it.
    assert "解釋自己嘅拒絕" in text


def test_voice_does_not_enumerate_closed_sets_where_it_needs_a_generative_rule():
    """The operator's finding, and the sharpest note of the v9 review: «you're
    putting these three emojis in that I can 100% guarantee Nora would never use
    other than these 3».

    Every concrete example becomes the whole repertoire. v8 listed 👍😂🙄 as
    examples of emoji-as-reply and would have collapsed her emoji vocabulary to
    three; it listed nine insults and got one 屌 back. The rule that follows is
    structural, not stylistic: where the model needs RANGE, describe the
    disposition; where it needs a POSITION, show the position (which generates
    rather than closes).

    This pins the absence, because absence is what regresses silently — an
    author adding one helpful example is exactly how the closed set comes
    back."""
    text = _voice()
    emoji = re.findall(r'[\U0001F300-\U0001FAFF☀-➿]', text)
    assert not emoji, (
        f"voice.md names specific emoji {emoji} — the model will use those and "
        "close the set. Describe how she uses them instead."
    )
    # The swearing section teaches positions; it must not ship a pile of
    # interchangeable insults for her to pick from.
    for closed in ("廢柴 戇鳩", "傻閪 撚樣", "死蠢 硬膠"):
        assert closed not in text, f"voice.md enumerates an insult list ({closed!r})"


def test_voice_describes_and_does_not_legislate():
    """v9's whole premise (operator: «you should describe what Nora is instead
    of constantly putting rules inside», «they are fighting each other»).

    Measured before the rewrite: ~229 prohibitions to ~28 permissions across the
    doctrine, 7.8:1 inside voice.md alone. DEC-94 already established that the
    model reads the BALANCE — that ratio is the mechanism behind «Nora now just
    like a puppy», and two rules were found in direct contradiction.

    A ratio is a blunt instrument, so this is deliberately a loose ceiling: it
    catches a slide back into rule-stacking without dictating any sentence.

    RE-POINTED 2026-08-17 for the operator's v13 rewrite: the file is now
    written in Cantonese, so an English-only regex would count ~0 and go
    vacuous — exactly how rule-stacking would sneak back. The markers now
    include the Cantonese negations a rule is written in (唔好/唔使/唔准/禁止/
    避免). v13 measures 12; the ceiling drops from 65 to 40 so it still bites
    before the balance slides, without pinning the author's fingers."""
    text = _voice()
    prohibitions = len(re.findall(
        r'\b(never|not|no|avoid|banned|forbidden)\b|唔好|唔使|唔准|禁止|避免',
        text, re.I,
    ))
    assert prohibitions < 40, (
        f"voice.md carries {prohibitions} prohibitions — it is legislating "
        "again. It describes who she is; constraints.md holds the hard lines."
    )
    # RFC 2119 keywords are the loader's own line between describing and
    # legislating, and `kind: identity` refuses them. Asserted here too so the
    # intent is visible where the content lives.
    assert not re.search(r'\b(MUST|SHALL|SHOULD|REQUIRED)\b', text)


def test_voice_keeps_only_the_limit_about_absent_targets():
    """Nora may escalate in-room conflict regardless of the target's mood."""
    text = _voice()
    assert "not in the room" not in text
    assert "幾個短訊息連環炮" not in text
    constraints = (_path("constraints")).read_text()
    assert "target of a fight" in constraints
    assert "cannot answer back" in constraints


def test_conduct_allows_personal_banter_only_for_an_in_room_instigator():
    """RE-POINTED 2026-08-17 for the operator's v11 conduct rewrite (ad75351),
    which restated this in English. Every clause the Cantonese carried is still
    here: it takes an in-room instigator, she does not open fire unprovoked, and
    an absent person is never the target."""
    conduct = " ".join((_path("conduct")).read_text().split())
    constraints = (_path("constraints")).read_text()

    # Reciprocal: there has to BE an instigator, and they have to be in the room.
    assert "taking a shot at you, you can give it back" in conduct
    # 主動開火, negated: oddness alone is not a licence.
    assert "convert an odd request into a roast just because it is odd" in conduct
    # 人身攻擊 against somebody who cannot answer back. Law, so it moved to
    # rooms/constraints.md in step 2 (design/context-plan/11 §6).
    assert "Do not pull somebody who is not in the room into a joke or a jab." in \
        " ".join(_room_constraints().split())
    assert "target of a fight" in constraints


def test_voice_binds_the_one_hard_rule_of_the_open_register():
    """With no topic gate, exactly one rule carries the load: swear without
    limit, invent nothing.

    What makes it hold is the argument, not the imperative — abuse asserts
    nothing about the world and so can never be false, while a quote asserts and
    so has to be true. Without that argument «invent nothing» reads as one more
    piece of politeness in a file that has just spent sixty lines dismantling
    politeness, and DEC-94 measured what happens to restraint that reads as
    politeness.

    The permissive half is pinned by two tests above; this is the restrictive
    half, which had none until v8.

    RE-POINTED 2026-08-17 (ad75351): «Do not invent a quote» became «Do not
    invent quotes, contradictions, motives, habits, or history to make a hit
    land» — the same rule over a WIDER surface, so this pins the widened form.
    """
    text = _voice()
    assert "唔好作 quote" not in text
    # Law: in rooms/constraints.md since step 2 (design/context-plan/11 §6).
    law = _room_constraints()
    assert "Do not invent quotes" in law
    # The restriction has to reach past quotes, or «invent nothing» is only
    # about words somebody said and the hit can still be built on a fiction.
    assert "to make a hit land" in law


def test_conduct_separates_a_burst_from_one_answer_in_instalments():
    """DEC-88's lesson, applied a second time in DEC-95: removing a
    counter-signal beats adding a maxim.

    conduct.md said «never a second helping of the first», which bans the burst
    in terms, while voice.md demanded it — and a test pinned the ban in place.
    Doctrine arguing with itself resolves toward whichever half is timid, which
    is the failure being fixed.

    The bound still exists and still has to: it is about ONE ANSWER paced out,
    not about how many messages a turn may send.

    RE-POINTED 2026-08-17 (ad75351): the permission is no longer named «burst»
    or «a second act», but both halves survive verbatim in the new wording — a
    second message may be a genuinely separate act, several short messages may
    be one reply — and the BOUND is untouched, still «instalments».
    """
    raw = (_path("conduct")).read_text()
    text = " ".join(raw.split())

    assert "a genuinely separate act" in text     # the permission
    assert "one reply when the rhythm is genuinely the interaction" in text
    assert "instalments" in text                  # the bound
    assert "never a second helping of the first" not in text
    assert "you never split one answer across several" not in text
    assert "One reply, then stop" not in text


def test_conduct_keeps_judgement_and_the_three_state_honesty():
    """The two behaviours conduct.md exists for, and the ones a rewrite is most
    likely to lose because neither is a rule about message shape.

    Judgement is what stops her being a command line — she may refuse a premise
    or answer the question actually being asked. And the three-state contract is
    the repo's spine at the doctrine layer: an empty result and a broken lookup
    are different answers, and reporting the second as the first is the exact
    lie the whole system is built to make impossible."""
    text = (_path("conduct")).read_text()

    assert "judgement" in text.lower()
    assert "wrong question" in text or "refuse a premise" in text
    # The three-state contract is law, in rooms/constraints.md since step 2
    # (design/context-plan/11 §6).
    law = _room_constraints()
    assert "could not check" in law
    assert "found nothing" in law or "you found nothing" in law
    assert "never claim to have done" in law.lower()


def _conduct() -> str:
    return (_path("conduct")).read_text()


def _room_constraints() -> str:
    """The law moved out of conduct.md, belonging.md, pictures.md and silence.md
    word for word (design/context-plan/11 §6, step 2). Tests that pinned a law
    line in its old file read it here."""
    return (_path("room-constraints")).read_text()


def test_conduct_puts_the_day_boundary_at_sleep_rather_than_at_midnight():
    """The 00:30 trap (operator, 2026-08-15).

    She is given a plain wall-clock line and nothing else about days, so at
    00:30 on the 15th a stored fact dated the 15th reads as «today» while the
    person who has not slept yet is calling that same evening 「聽日」. The
    failure is not a wrong date — it is PEDANTRY: correcting them about which
    day it is, or answering «I know nothing about tomorrow» while holding the
    exact thing they asked for.

    RE-POINTED 2026-08-17 (ad75351): the sentence gained a qualifier — «…which
    day it is WHEN YOU ALREADY KNOW WHAT THEY MEAN» — so the full stop this used
    to pin no longer follows «is». The rule is unchanged and the qualifier is
    pinned too, since it is what stops the ban swallowing the genuine mismatch
    the next line still allows her to name.
    """
    text = " ".join(_conduct().split())
    assert "sleeps, not at midnight" in text
    assert "聽日" in text
    assert "Do not correct somebody about which day it is" in text
    assert "when you already know what they mean" in text


def test_the_day_boundary_is_a_position_and_not_an_hour():
    """DEC-94/95/96's finding, applied here: a position generates, a list
    closes. «A day ends when somebody sleeps» covers 00:30, 04:00 and a night
    shift alike. Any concrete cutoff — «before 6am», «between midnight and
    dawn» — answers only for the hours on the list and silently gets the
    others wrong, and it is the shape this file must not drift into.
    """
    import re

    # RE-POINTED 2026-09-22 (conduct v27): the heading is English now, with
    # the file. 「聽日」 stays in the body because the rule is about that word.
    section = _conduct().split("# When one day becomes the next", 1)[1]
    assert not re.search(r"\b(\d{1,2})\s*(am|pm|a\.m\.|p\.m\.|:00)\b", section, re.I), (
        "the day boundary must not be legislated as a clock time"
    )
    assert "6am" not in section and "before dawn" not in section


def test_conduct_separates_a_source_that_is_silent_from_a_source_that_denies():
    """R-53 / A5, closing the audit's F7 — the finding that names the incident.

    `conduct.md` already carried the TRANSPORT distinction: an empty result and
    a broken call are different outcomes. The 2026-08-17 02:16 failure was a
    SCOPE one. She had a correct, sourced answer; two minutes later a search
    returned an article covering only one category of the thing asked about
    («all four of the coloured towers»), and she read its silence about the rest
    as a denial and overturned herself. The article was not wrong — it was
    narrower than the question.

    R-27's three-state discipline is enforced on tool RESULTS and nothing
    enforced it on INFERENCE OVER results. This pins the three moves that close
    that gap, and it pins them as a POSITION rather than as the incident's own
    nouns, so it cannot be satisfied by a sentence about towers:

      1. omission is not denial,
      2. a source that does not reach the question is `unavailable`, not
         `nothing_found` — the same reading a broken lookup gets,
      3. a silence never outranks something already found.

    Move 3 is the one the incident actually needed and the one most likely to
    be lost in a rewrite, because 1 and 2 read as complete on their own.

    HONEST SCOPE: this is a content test. It measures that the rule is present
    and states all three moves, NOT that the model follows it —
    `evals/corpora/source_scope.yaml` is where that is measured, and
    DEC-78/DEC-92's finding that prose alone does not move behaviour has not
    been overturned by writing more of it.
    """
    # Law: the section moved word for word into rooms/constraints.md § Say only
    # what you found in step 2 (design/context-plan/11 §6).
    text = _room_constraints()
    assert "# Say only what you found" in text
    section = " ".join(text.split("# Say only what you found", 1)[1]
                       .split("\n# ", 1)[0].split())

    # 1. Omission is not denial.
    assert "does not mention something has not told you it is false" in section
    # 2. Out of scope is «could not check», routed to the SAME outcome the
    #    transport failure gets — which is what makes it R-27 and not a new
    #    third state invented here.
    assert "does not reach the question, you could not check" in section
    assert "the same outcome as a lookup that broke" in section
    # 3. Precedence: a silence does not overturn an established finding.
    assert "does not outweigh something you already found" in section

    # The transport distinction it extends is still there and still separate —
    # if a rewrite merges the two, the scope case becomes a footnote on the
    # empty-result case and stops being reachable.
    assert "comes back empty, say you found nothing" in text
    assert "comes back broken, say" in text


def test_pushback_is_split_by_kind_across_voice_and_conduct():
    """RESTORED 2026-08-20 (A7/DEC-123). DEC-92 split the pushback rule across
    two kinds precisely so the halves could not drift: `voice.md` owns how a
    concession or a refusal SOUNDS, `conduct.md` owns whether the challenge
    should move her at all — a grounding question that belongs beside «check
    what you found». Splitting by kind is design/17 §4's rule, and the
    duplicate-clause failure it prevents is the one recorded in
    `test_the_silence_doctrine_is_stated_exactly_once_in_guardrails`.

    PROVENANCE, measured against the file rather than taken from the audit,
    because three different commits have been blamed for this and two were
    wrong. Presence of the marker 「你講得都啱」 and of the section heading, per
    revision of `voice.md`:

        f349e1f  08-10  v6   marker YES, heading «When somebody pushes back at you»
        b532751  08-11  v7   marker YES, same heading
        2254452  08-11  v9   marker NO,  heading renamed «…says you are wrong»
        ebad01b  08-11  v10  marker NO,  same heading
        26d8004  08-12  v10  SECTION GONE
        a7976ac  08-15  v11  absent
        a2cb1ae  08-15  v12  absent   <- live during the capitulation
        8af429a  08-17  v13  absent

    So it went in TWO steps, and the first one is the one that matters:
    `2254452` (DEC-95/96, «the rulebook replaced by a portrait») compressed the
    section to a single paragraph, dropped the Cantonese register examples
    including the half-agreement marker, AND deleted this test — the gate died
    there. `26d8004` then removed the compressed remnant entirely, inside a
    commit whose subject is «separate releases from deployments», which is how
    a doctrine section leaves without anyone reading a doctrine diff.

    Neither previously-blamed commit is right: the audit's F10 names `8af429a`
    (v13), and an earlier draft of this docstring named `a7976ac` (v11). Both
    are after the section was already gone.

    RE-POINTED for what the two files look like now: the voice half is v14's
    Cantonese 「有人話你錯」, not v6's English prose, and the conduct half's
    heading is «Being contradicted». The SUBSTANCE pinned is unchanged.

    HONEST SCOPE — this restores a deleted GATE and a broken pairing. What it
    does NOT do is settle whether the prose works, and the timeline points the
    opposite way from the audit's reading: the capitulation was measured at
    02:16 to 02:27 UTC on 2026-08-17 under `voice: 12`, which did NOT contain
    this section — the last of it went 5.4 days earlier, and the DEC-92-shaped
    section with its gate 6.3 days earlier. F10's «the prose was
    present and did not hold» is false. The conduct half was present, but only
    its first two sentences; the clauses about not repudiating what you checked
    arrived in `193dd46` (DEC-112) some seven hours AFTER the incident. So
    «guardrail removed, failure followed» is the simpler reading, and this
    restoration is a candidate contribution to it rather than a proven one.
    DEC-92's own verdict — «none of this is measured, and DEC-78 predicts prose
    alone will not move it» — is untested here either way; nothing in this
    change measures behaviour.
    """
    voice = " ".join(_voice().split())
    conduct = " ".join(_conduct().split())
    law = " ".join(_room_constraints().split())

    # Both halves exist, each under its own kind.
    assert "# 有人話你錯" in voice
    assert "# Being contradicted" in conduct

    # The forbidden move itself — inventing a fault so there is one to concede —
    # is what BOTH files reach, from their own side. The English half has been
    # rooms/constraints.md since step 2 (design/context-plan/11 §6, 2026-10-12);
    # the Cantonese half stays in voice.md as its register specimen until step 4.
    assert "作一個自己嘅錯出嚟俾人認" in voice
    assert "Agreeing you invented something when you did not is not modesty" in law

    # …and the half-agreement, which is the failure in its most survivable
    # disguise and the reason the voice half is not redundant with the law's.
    assert "你講得都啱，不過" in voice

    # The split holds: register examples stay in voice alone, and the grounding
    # vocabulary stays in the law alone. A copy in either direction is the drift
    # this test exists to catch.
    assert "企硬" in voice and "企硬" not in conduct and "企硬" not in law
    assert "another claim" in law and "another claim" not in voice


def test_constraints_covers_every_occasion_it_is_served_to():
    """R-14 is about WHERE she speaks, not about who summoned her.

    `constraints.md` is served to four occasions and used to name two ways a
    turn can open — being called, and a room offering the floor — with
    «Never anywhere else, with exactly one exception». A proactive turn is
    neither: its own header says «Nobody has called you — not just now, and
    not earlier. YOU decided». So the highest-authority file in the prompt
    forbade the turn it was being read on, and a heartbeat (a reminder coming
    due) was in the same position.

    Invisible by construction: proactive and participation both count silence
    as the HEALTHY outcome, so a model obeying the wrong file looks exactly
    like one exercising restraint.

    Design 35 recorded that this clause «must be revised, not inherited» when
    participation arrived. It was revised for participation; adding proactive
    to `applies_to` (6f413c6e) changed no prose.
    """
    text = (_path("constraints")).read_text()
    # The rule survives — it is about the room, not the summons.
    assert "reply only where you were called" in text.lower()
    assert "this room, this turn, and nowhere else" in text
    # And every way a turn opens is named, so none of them reads as forbidden.
    assert "come due" in text          # heartbeat
    assert "offer you the floor" in text  # participation
    assert "on your own pass" in text     # proactive


def test_no_doctrine_file_names_one_room():
    """The same doctrine directory serves six rooms across three units
    (`policy.toml`), and the prompt never renders a room label — R-18 keeps
    ids off the wire and `_platform_line` gives her the PLATFORM only. So a
    room name written into doctrine was the only group identity she had, and
    it was the wrong one in five of the six.

    `test_no_config_values_leak_into_doctrine_files` checks the trigger, hosts
    and ports. A room label is a config value it does not look for.
    """
    for path in sorted(DOCTRINE_DIR.glob("**/*.md")):
        body = path.read_text(encoding="utf-8")
        assert "Zone 42" not in body, path.name


def test_identity_holds_who_she_is_and_belonging_holds_the_group():
    """identity.md was split 2026-09-25 (operator) so who she is can load on a
    surface that is not a group (design/instagram-adoption/19, a public
    summons), and her place in a group loads only where there is one. Pinned
    both ways: the group framing must not drift back into identity.md, where
    it would tell a public reply it is among friends, and belonging.md must
    carry all of it, so the split loses nothing for a room."""
    sections = {s.name: s for s in _all_sections()}
    identity, belonging = sections["identity"].body, sections["belonging"].body
    group_framing = ("in this group", "hold a friend's place", "The group calls you",
                     "Two loops", "What carries across turns")
    for phrase in group_framing:
        assert phrase.lower() not in identity.lower(), f"identity.md frames a group: {phrase!r}"
        assert phrase.lower() in belonging.lower(), f"belonging.md lost: {phrase!r}"
    # Law, so it moved to rooms/constraints.md in step 2 (design/context-plan/11
    # §6); still a room file, never identity.md.
    assert "you answer to the group" not in identity.lower()
    assert "you answer to the group" in sections["room-constraints"].body.lower()
    assert [line for line in belonging.splitlines() if line.startswith("# ")] == \
        ["# Your place in the group", "# Why you're here", "# How you serve a room"]
