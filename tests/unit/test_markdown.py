"""`nora.agent.markdown` — Markdown syntax in, real Signal styling out (DEC-82).

Four things can go wrong here, and they are tested in that order:

1. **The text half drifts.** DEC-82 began as a STRIP, and what it deliberately
   leaves alone matters — the Cantonese cases most of all. `strip_markdown` is
   the text half of the conversion, so every one of those cases has to answer
   identically.
2. **The ranges do not match the text.** A style is `(start, length)` in UTF-16
   code units over the string that is LEFT once the asterisks are gone. Almost
   every assertion below re-derives the substring a range covers rather than
   comparing against a hand-counted number, because a test written against
   constants keeps passing when the text and the offsets shift together.
3. **The units are characters instead of code units.** Only visible with a
   non-BMP character, and 😅 is in a line this repo already ships.
4. **A failure becomes a wrong range instead of no range.** The fail-safe is
   asserted directly, including that it never empties a reply (R-4).
"""

from __future__ import annotations

import pytest

from nora.agent.markdown import render_markdown, strip_markdown
from nora.domain.ports import Style
from nora.domain.utf16 import utf16_len


def _spans(text: str) -> list[tuple[str, Style]]:
    """Every style in `render_markdown(text)` as `(the text it covers, style)`.

    Slicing the OUTPUT by the range is the point: it asserts the offset and the
    string together, so an off-by-one shows as the wrong substring rather than
    as a number nobody can check by eye. UTF-16 offsets are converted back to
    Python indices through `_char_slice` so that a range over an emoji is read
    the way Signal reads it, not the way Python would.
    """
    out = render_markdown(text)
    return [(_char_slice(out.text, s.start, s.length), s.style) for s in out.styles]


def _char_slice(text: str, start: int, length: int) -> str:
    """The substring a UTF-16 `(start, length)` range covers.

    Decoded from the encoded form rather than indexed with `text[start:...]` —
    those two agree only while every character is in the BMP, and disagreeing
    with `len()` is the whole reason the offsets are computed the way they are.
    """
    units = text.encode("utf-16-le")
    return units[start * 2:(start + length) * 2].decode("utf-16-le")


# ── 1. everything the strip did, it still does ──────────────────────────────

def test_bold_labels_no_longer_reach_the_group_as_asterisks():
    """Signal renders no Markdown, so `**投資** — …` would reach the group as literal
    asterisks. `voice.md` forbids bold labels and the model emits them anyway on
    long answers, which is DEC-78's lesson: enforce at the surface, not in prose."""
    out = strip_markdown("**投資** — 月供 ETF\n\n**Side hustle** — freelance")
    assert out == "投資 — 月供 ETF\n\nSide hustle — freelance"
    assert "*" not in out


def test_headings_and_italics_and_bold_italics_all_lose_their_syntax():
    assert strip_markdown("## 推介\n*平* 同 ***正***") == "推介\n平 同 正"


def test_arithmetic_and_bare_asterisks_survive():
    """NARROW on purpose — this edits her authored words, so anything that is
    not unambiguously emphasis is left exactly as written. `****` and `**/*.py`
    are here because the `(?<!\\*)(\\*{1,3})(?!\\*)` run guard is the only thing
    holding them, and it is the guard a "simplification" would drop first."""
    for text in ("3 * 4 = 12", "5*6", "a * b * c", "*", "**", "****", "**/*.py"):
        assert strip_markdown(text) == text, text


def test_nothing_that_survives_the_text_gains_a_style_either():
    """The other half of the case above, and it did not exist before: a strip
    could only fail by DELETING something, a conversion can also fail by
    styling something it correctly declined to rewrite."""
    for text in ("3 * 4 = 12", "5*6", "a * b * c", "*", "**", "****", "**/*.py"):
        assert render_markdown(text).styles == (), text


def test_underscores_are_never_touched():
    """`person_id` / `send_reply` are ordinary vocabulary here; treating `_x_`
    as emphasis would corrupt far more than it fixed."""
    out = render_markdown("_x_ and person_id and send_reply")
    assert out.text == "_x_ and person_id and send_reply"
    assert out.styles == ()


def test_emphasis_never_spans_a_blank_line():
    """`[^\\n]+?` and not `.+?` under DOTALL. Two unrelated stray asterisks in
    different paragraphs would otherwise pair up and eat everything between
    them — including, now, styling all of it bold."""
    text = "食咗飯未*\n\n我今晚得閒*"
    out = render_markdown(text)
    assert out.text == text
    assert out.styles == ()


def test_a_bare_heading_marker_is_left_as_literal_text():
    """`(?=\\S)`. The rewrite must never turn a reply that would have been SENT
    into an R-4 `empty_reply` refusal, and `###` alone is the shape that would."""
    assert strip_markdown("###") == "###"


# ── 2. the CJK case ──────────────────────────────────────────────

def test_emphasis_adjacent_to_chinese_converts_cleanly():
    """`\\w` is Unicode-aware, so 投 and 資 ARE word characters; a `(?!\\w)` guard
    fails on the real closing delimiter and the engine backtracks onto the INNER
    asterisk of each pair, emitting `*投資*方面`. Chinese has no spaces, so this
    is the COMMON shape here, not an edge case — and under conversion the ranges
    would index the mangled string.

    PROBE: with `(?<!\\w)`/`(?!\\w)` around `_MD_EMPHASIS` this goes red on the
    text, not just on the styles.
    """
    out = render_markdown("**投資**方面")
    assert out.text == "投資方面"
    assert _spans("**投資**方面") == [("投資", Style.BOLD)]


def test_two_adjacent_cjk_emphases_do_not_merge_into_one():
    """`重點係**價錢**同**位置**` became `重點係*價錢同位置*` under the broken
    regex — one span swallowing the separator. Two spans, two ranges."""
    assert strip_markdown("重點係**價錢**同**位置**") == "重點係價錢同位置"
    assert _spans("重點係**價錢**同**位置**") == [
        ("價錢", Style.BOLD), ("位置", Style.BOLD),
    ]


# ── 3. the ranges ───────────────────────────────────────────────────────────

def test_bold_and_italic_get_different_styles():
    assert _spans("*平* 同 **正**") == [("平", Style.ITALIC), ("正", Style.BOLD)]


def test_triple_asterisks_are_bold():
    """`***x***` is one range, not two overlapping ones — see `_STYLE_BY_RUN`
    for why reproducing bold-and-italic is more surface than it is worth."""
    assert _spans("***正***") == [("正", Style.BOLD)]


def test_a_heading_shifts_the_range_that_follows_it():
    """Headings are deleted with no style of their own, so every offset after
    one is short by the marker's length. Doing them BEFORE any span exists is
    what makes that automatic; doing them after would need a remap."""
    assert _spans("## 推介\n**平**") == [("平", Style.BOLD)]
    assert render_markdown("## 推介\n**平**").text == "推介\n平"


def test_nested_emphasis_yields_an_inner_range_inside_an_outer_one():
    """The pass loop's whole reason to exist. One `re.sub` over `**a *b* c**`
    takes the outer pair and leaves the inner one, so the second pass has to
    move the range the first pass found onto the string the second produced."""
    assert render_markdown("**a *b* c**").text == "a b c"
    assert _spans("**a *b* c**") == [("a b c", Style.BOLD), ("b", Style.ITALIC)]


def test_an_emoji_before_a_span_shifts_it_by_two_units_not_one():
    """The only shape where a character count and the right answer differ. 😅 is
    one Python character and TWO UTF-16 units, and the never-silent fallback
    line this repo already ships contains one.

    PROBE: replace `utf16_len` with `len` and the `start` assertion goes red
    while `_spans` — which decodes the same wrong offsets — still agrees.
    """
    out = render_markdown("😅 **好正**")
    assert out.text == "😅 好正"
    (style,) = out.styles
    assert style.start == 3
    assert style.start != out.text.index("好")
    assert style.length == 2


def test_a_span_containing_a_flag_is_measured_in_surrogate_pairs():
    """🇭🇰 is TWO non-BMP code points and FOUR UTF-16 units. A range whose
    LENGTH is wrong renders bold over half a flag and a following word."""
    out = render_markdown("**去🇭🇰玩**")
    assert out.text == "去🇭🇰玩"
    (style,) = out.styles
    assert style.length == utf16_len("去🇭🇰玩") == 6
    assert style.length != len(out.text)


def test_ranges_come_back_in_document_order():
    """Deterministic parameters, so two renders of one reply produce identical
    JSON-RPC and a Langfuse trace of one turn is comparable to another."""
    styles = render_markdown("**a** then *b* then **c**").styles
    assert [s.start for s in styles] == sorted(s.start for s in styles)


def test_every_range_lies_inside_the_text_it_was_produced_with():
    """The invariant `Styled.__post_init__` enforces, asserted against a reply
    with everything in it at once. A range that does not fit is not merely
    wrong — signal-cli fails the WHOLE send on one."""
    out = render_markdown("## 標題\n**投資**方面 *平* 同 ***正***\n😅 **好正**")
    limit = utf16_len(out.text)
    assert out.styles
    for style in out.styles:
        assert style.start >= 0
        assert style.start + style.length <= limit


# ── 4. the fail-safe ────────────────────────────────────────────────────────

def test_a_render_that_raises_degrades_to_the_draft_with_no_styles(monkeypatch):
    """R-5's shape applied to decoration: an unconverted asterisk is visible to
    everyone and harmless, a wrong range is invisible to everyone and not. So
    the degraded answer is the DRAFT — never an empty string, never a guess,
    never a refusal.

    The failure is injected into the private `_render` rather than simulated,
    because the property being asserted is `render_markdown`'s except clause and
    a test that fed it merely-unusual input would not reach that clause at all.
    """
    import nora.agent.markdown as md

    def _boom(text: str):
        raise RuntimeError("regex engine on fire")

    monkeypatch.setattr(md, "_render", _boom)

    out = md.render_markdown("**投資**方面")

    assert out.text == "**投資**方面"
    assert out.styles == ()


@pytest.mark.parametrize(
    "text",
    ["**投資**", "## 推介", "###", "*", "3 * 4", "**a *b* c**", "😅 **好正**"],
)
def test_no_non_empty_reply_is_ever_rendered_empty(text):
    """Because the reverse is the obvious guess: this cannot rescue R-4 and it
    cannot CAUSE it either."""
    assert render_markdown(text).text.strip()


def test_the_strip_is_the_text_half_of_the_render_and_not_a_second_grammar():
    """`LeakGuard` builds its haystack through `strip_markdown` and `Deliverer`
    sends `render_markdown(...).text`; a haystack that spoke a different
    Markdown dialect from the reply is a hole in the fail-closed check.
    One function, so they cannot drift."""
    for text in ("**投資**方面", "## 推介\n*平*", "**a *b* c**", "3 * 4", "_x_"):
        assert strip_markdown(text) == render_markdown(text).text, text
