"""ADR 0007 structural enforcement: every model-facing tool description must
match the tool-surface policy.

The policy (docs/adr/0007-tool-surface-policy.md, accepted 2026-08-31):

1. Opening = what + when-to-call. The first sentence(s) state what the tool
   does and when to call it. Style guidance, voice control and negative
   examples live AFTER the opening in a labelled SHOULD block, never as
   conditions on calling.
2. RFC 2119 register: MUST is only turn correctness; SHOULD is style;
   MAY is permission. Permission is never phrased as obligation, and
   advisory style is never phrased as a reason not to call.
3. English-only normative prose. Quoted non-English tokens are allowed
   only as load-bearing examples (a trigger word the tool fires on, an
   example argument). CJK in instruction prose fails.
4. Three-level length ladder: L1 sentence (what+when), L2 +one constraint,
   L3 contract (status machines). Most tools are L1.

Background — why this exists (measured 2026-08-31): the old `send_reply`
description was a 3,960-char style essay ("Read text back... fix these: Too
long. A verdict that is only a scoreboard... Too soft."). An ISO probe
(same prompt, same model, same tool_choice=required, n=5, only description
changed) showed reasoning models read that guidance as preconditions on
deserving to call: `none` 5/5 tool_calls, `low` 0/5 prose, `high` 0/5
prose. A MUST-first rewrite restored 5/5/5. This test exists so the essay
shape cannot return and the opening cannot drift back to style-first.
"""

import ast
import re
from pathlib import Path

import pytest

TOOLS_PATH = Path(__file__).parent.parent / "nora" / "agent" / "tools.py"

#: When-to-call trigger frames, in the tool's own register. A description
#: opening must contain at least one of these after the what-clause.
WHEN_TRIGGERS = re.compile(
    r"call this when|call this for|use when|use this when|when you|"
    r"when somebody|when someone|the moment somebody|the moment someone|"
    r"whenever someone|whenever somebody|whenever you|if somebody|"
    r"when the room|when answering|call it when|somebody tells you|"
    r"someone tells you|when the time|when a message|when somebody asks|"
    r"when someone asks|when the room wants|use it to|call this before|"
    r"call list_events|only call this if|reach for it when|before you|"
    r"reach for it before|call it whenever|whenever a number",
    re.IGNORECASE,
)

#: Permission-suppression tokens: advisory style that a reasoning model can
#: read as "do not call". If any appears in the OPENING (before the first
#: SHOULD/labelled block), the description fails. NOTE: "skip" as a leading
#: action verb ("Skip the track") is a tool's WHAT, not a suppression — the
#: pattern only matches "skip" when it reads as a recommendation not to act.
SUPPRESSION_TOKENS = re.compile(
    r"too long|too soft|only a scoreboard|do not send|never send|"
    r"is not worth|not good enough|do not call|don't call|"
    r"not worth calling|no need to call|better not to",
    re.IGNORECASE,
)

#: MUST phrasing in the whole docstring (register discipline: MUST is
#: reserved for turn correctness; a MUST that is really style fails).
MUST_RE = re.compile(r"\bMUST\b")
SHOULD_LABEL_RE = re.compile(r"^SHOULD\b", re.MULTILINE)

#: CJK range — normative prose must be English. Quoted examples (「」…)
#: are allowed; the check strips quoted spans before testing prose.
CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
# CJK 角括號 «…» and 「…」 are both quoted-example delimiters used in this
# codebase; strip both so the English-only check tests normative prose only.
QUOTED_RE = re.compile(r"(?:「[^「」]*」|«[^«»]*»)")


def _model_facing_docstrings() -> list[tuple[str, str]]:
    """(tool_name, docstring) for every tool registered via `_spec(...)`."""
    src = TOOLS_PATH.read_text()
    tree = ast.parse(src)
    func_docs: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node) or ""
            if len(doc.strip()) >= 40:
                func_docs[node.name] = doc
    out: list[tuple[str, str]] = []
    for m in re.finditer(r'_spec\("([^"]+)",\s*([a-z_]+)', src):
        name, fname = m.group(1), m.group(2)
        if fname in func_docs:
            out.append((name, func_docs[fname]))
    return out


def _unquoted(s: str) -> str:
    """Strip quoted spans (「…」 / "…" / '…') so English-only checks test
    normative prose, not load-bearing quoted examples."""
    return QUOTED_RE.sub("", s)


def test_known_spec_tools_are_covered() -> None:
    docs = dict(_model_facing_docstrings())
    # A representative set must be present so a `_spec` rename or a docstring
    # that drops below the extraction floor turns this file red loudly.
    assert {"send_reply", "weather", "note_create", "media_fetch", "version"} <= set(docs)


@pytest.mark.parametrize(
    ("tool", "doc"),
    _model_facing_docstrings(),
    ids=[t for t, _ in _model_facing_docstrings()],
)
def test_opening_has_a_when_to_call_clause(tool: str, doc: str) -> None:
    # The opening is everything before the first labelled block (SHOULD/MAY)
    # or before 8 lines of body, whichever comes first.
    head = doc.split("SHOULD")[0].split("MAY")[0]
    head_lines = [ln for ln in head.splitlines() if ln.strip()]
    head_text = " ".join(head_lines[:8])
    assert WHEN_TRIGGERS.search(head_text), (
        f"{tool}: opening has no when-to-call trigger. First sentences: "
        f"{head_text[:120]!r}"
    )


@pytest.mark.parametrize(
    ("tool", "doc"),
    _model_facing_docstrings(),
    ids=[t for t, _ in _model_facing_docstrings()],
)
def test_no_permission_suppression_in_the_opening(tool: str, doc: str) -> None:
    head = doc.split("SHOULD")[0].split("MAY")[0]
    head_text = " ".join(ln for ln in head.splitlines() if ln.strip())[:8]
    hit = SUPPRESSION_TOKENS.search(head_text)
    assert hit is None, (
        f"{tool}: opening contains permission-suppression phrasing "
        f"{hit.group(0)!r} — style guidance must come after the opening, "
        "labelled SHOULD, never as a condition on calling"
    )


@pytest.mark.parametrize(
    ("tool", "doc"),
    _model_facing_docstrings(),
    ids=[t for t, _ in _model_facing_docstrings()],
)
def test_normative_prose_is_english(tool: str, doc: str) -> None:
    prose = _unquoted(doc)
    match = CJK_RE.search(prose)
    assert match is None, (
        f"{tool}: normative prose contains CJK {match.group(0)!r}. Quoted "
        "non-English tokens are allowed only as load-bearing examples "
        "(trigger words, example arguments); instruction prose must be English."
    )


@pytest.mark.parametrize(
    ("tool", "doc"),
    _model_facing_docstrings(),
    ids=[t for t, _ in _model_facing_docstrings()],
)
def test_a_must_is_turn_correctness_not_style(tool: str, doc: str) -> None:
    # A MUST whose object is a style judgement ("too long", "too soft",
    # "polite", "concise") is a SHOULD wearing a MUST costume.
    if not MUST_RE.search(doc):
        return
    style_must = re.search(
        r"MUST NOT be|MUST be (?:short|long|polite|concise|soft|hard|friendly)",
        doc,
        re.IGNORECASE,
    )
    assert style_must is None, (
        f"{tool}: MUST used for a style judgement ({style_must.group(0)!r}) — "
        "MUST is reserved for turn correctness (RFC 2119 register split)"
    )


@pytest.mark.parametrize(
    ("tool", "doc"),
    _model_facing_docstrings(),
    ids=[t for t, _ in _model_facing_docstrings()],
)
def test_style_guidance_is_labelled(tool: str, doc: str) -> None:
    # If the docstring has a second paragraph / bullet body beyond the
    # opening, it must be behind a register label (SHOULD or MAY), not a
    # bare "Read text back..." imperative essay.
    lines = [ln.strip() for ln in doc.strip().splitlines() if ln.strip()]
    if len(lines) <= 2:
        return  # L1 sentence: no style block to label
    # Everything after the opening should be under SHOULD/MAY/Args or status
    # contract lines. Look for a bare imperative paragraph in the middle.
    labels_or_args = 0
    for ln in lines[1:]:
        if ln.startswith(("SHOULD", "MAY", "Args:", "- ", "* ", "`", "**")):
            labels_or_args += 1
    # If the docstring is long but has no labelled register block at all, it
    # is the essay shape.
    if len(lines) > 6 and labels_or_args == 0:
        pytest.fail(
            f"{tool}: {len(lines)} lines but no labelled register block "
            "(SHOULD/MAY). Long descriptions must structure guidance behind "
            "register labels (ADR 0007 §2)."
        )


#: A catalogue line's token ceiling — `tests/unit/test_tool_catalogue.py`'s
#: `LINE_CEILING`, restated here because this is the test that holds the
#: SOURCE of that line to it. The two must agree.
L1_LINE_CEILING = 40


@pytest.mark.parametrize(
    ("tool", "doc"),
    _model_facing_docstrings(),
    ids=[t for t, _ in _model_facing_docstrings()],
)
def test_the_opening_paragraph_is_an_l1(tool: str, doc: str) -> None:
    """Policy 4's L1 — «what + when» — is the docstring's FIRST PARAGRAPH, and
    that paragraph is the tool's catalogue line (`nora.agent.registry.
    _opening`, design/tool-catalogue/01). So it must carry the when-to-call
    clause ITSELF, not somewhere in the next eight lines, and fit a line.

    Measured when this was added (2026-09-23): 24 of 55 openings carried a
    when-clause and one ran to 136 tokens. `test_opening_has_a_when_to_call_
    clause` above passed all 55 because it reads eight lines, not the unit
    the catalogue is cut from — a gate on the wrong span.
    """
    import tiktoken

    opening = " ".join(doc.strip().split("\n\n")[0].split())
    assert WHEN_TRIGGERS.search(opening), (
        f"{tool}: the first paragraph (its catalogue line) says what the tool "
        f"does but not when to call it: {opening!r}"
    )
    tokens = len(tiktoken.get_encoding("cl100k_base").encode(f"- {tool} — {opening}"))
    assert tokens <= L1_LINE_CEILING, (
        f"{tool}: the first paragraph is {tokens} tokens as a catalogue line "
        f"(ceiling {L1_LINE_CEILING}). Split it: L1 first, the rest after a "
        "blank line."
    )


# ---- ADR 0015 §3 / §6 (DEC-199): level, ceiling, language, punctuation --------
#
# Counted over the description Strands SENDS (`ToolRegistry._descriptions()`:
# the docstring minus `Args:`), not the AST docstring the tests above read:
# that is the text every call pays for.

#: ADR 0015 §3's ceilings, cl100k tokens over the sent description. Starting
#: values: each moves only with a measurement recorded beside it.
LEVEL_CEILINGS = {1: 80, 2: 160, 3: 320}

#: The tools not yet rewritten to ADR 0015. It only SHRINKS: it must stay a
#: subset of the digest-pinned 2026-09-24 name snapshot, so a tool added after
#: the ADR can never be exempted here. A rewrite batch removes its tools in the
#: same commit that rewrites them.
_NOT_YET_REWRITTEN = frozenset({
    "send_reply",
})


#: A run of CJK, kana or hangul. ADR 0015 §3's unit: a distinct maximal run,
#: counted once however often it repeats. Punctuation such as 「」 separates runs.
_NON_LATIN_RUN = re.compile(r"[㐀-䶿一-鿿぀-ヿ가-힯]+")
_DASHES = ("—", "–")  # em dash, en dash


def _sent_descriptions() -> dict[str, str]:
    from nora.agent.registry import ToolRegistry

    return dict(ToolRegistry()._descriptions())


def _sent_specs() -> dict[str, str]:
    """Each tool's WHOLE spec as Strands sends it (description, parameter
    descriptions, schema), as JSON text. The dash and language rules apply to
    all of it: an `Args:` line is model-facing text too, and a check over the
    description alone would let one hide there. Built for a Discord room and a
    Dreamer adaptor so the platform- and Dreamer-scoped tools are included."""
    import json

    from nora.agent.ports import adapt
    from nora.agent.registry import ToolRegistry
    from nora.agent.room import Room
    from nora.obs.metrics import Metrics
    from nora.testing.fakes import FakeMemoryStore

    class _Persons:
        def resolve(self, platform: str, sender_id: str):
            return None

    class _Store:
        async def recent(self, *, conversation_id: str, limit: int):
            return []

    room = Room(platform="discord", conversation_id="g1", roster_source=None,
                persons=_Persons())
    specs = ToolRegistry().specs(
        store=_Store(), conversation_id="g1", conversation_type="group",
        persons=_Persons(), honcho=FakeMemoryStore(), deliverer=None,
        metrics=Metrics(), room=room, dream_adaptor="policy",
    )
    return {
        s.name: json.dumps(adapt(s).tool_spec, ensure_ascii=False) for s in specs
    }


def _rewritten() -> list[str]:
    from nora.agent.tools import NATIVE_TOOLS

    # A plugin tool (`skills`) is built and described by its plugin, not by
    # this repo, so there is no text of ours for these rules to govern.
    return sorted(
        n for n, spec in NATIVE_TOOLS.items()
        if n not in _NOT_YET_REWRITTEN and not spec.plugin
    )


def test_the_allowlist_only_shrinks() -> None:
    from tests.architecture.test_tool_names import _SNAPSHOT_2026_09_24

    extra = sorted(_NOT_YET_REWRITTEN - _SNAPSHOT_2026_09_24)
    assert not extra, (
        f"{extra} added to _NOT_YET_REWRITTEN: a tool added after ADR 0015 "
        "meets it from its first commit and is never exempted"
    )


def test_every_rewritten_tool_declares_a_level() -> None:
    from nora.agent.tools import NATIVE_TOOLS

    bad = {n: NATIVE_TOOLS[n].level for n in _rewritten()
           if NATIVE_TOOLS[n].level not in LEVEL_CEILINGS}
    assert not bad, f"declare level=1|2|3 on NativeTool (ADR 0015 §3): {bad}"


def test_every_declared_level_is_on_the_ladder() -> None:
    from nora.agent.tools import NATIVE_TOOLS

    bad = {n: s.level for n, s in NATIVE_TOOLS.items()
           if s.level is not None and s.level not in LEVEL_CEILINGS}
    assert not bad, f"level must be one of {sorted(LEVEL_CEILINGS)}: {bad}"


def test_a_rewritten_description_fits_its_level() -> None:
    import tiktoken

    from nora.agent.tools import NATIVE_TOOLS

    enc = tiktoken.get_encoding("cl100k_base")
    sent = _sent_descriptions()
    over = {}
    for name in _rewritten():
        level = NATIVE_TOOLS[name].level
        if name not in sent or level not in LEVEL_CEILINGS:
            continue
        tokens = len(enc.encode(sent[name]))
        if tokens > LEVEL_CEILINGS[level]:
            over[name] = f"{tokens} > {LEVEL_CEILINGS[level]} (level {level})"
    assert not over, f"over the level ceiling (ADR 0015 §3): {over}"


def test_no_dash_in_a_rewritten_tool_spec() -> None:
    sent = _sent_specs()
    bad = {n: sum(sent[n].count(d) for d in _DASHES)
           for n in _rewritten() if n in sent and any(d in sent[n] for d in _DASHES)}
    assert not bad, (
        f"em or en dashes in the tool spec, description or arguments (ADR 0015 "
        f"§3); use a comma, full stop, colon or parentheses: {bad}"
    )


def test_at_most_one_non_english_string_in_a_rewritten_tool_spec() -> None:
    sent = _sent_specs()
    bad = {}
    for name in _rewritten():
        runs = set(_NON_LATIN_RUN.findall(sent.get(name, "")))
        if len(runs) > 1:
            bad[name] = sorted(runs)
    assert not bad, (
        f"more than one non-English string in the tool spec (ADR 0015 §3: keep "
        f"only the trigger the tool fires on; examples go): {bad}"
    )


_SECTION_HEADERS = (
    "Args:", "Returns:", "Raises:", "Yields:", "Examples:", "Example:", "Note:", "Notes:",
)


@pytest.mark.parametrize(
    ("tool", "doc"),
    _model_facing_docstrings(),
    ids=[t for t, _ in _model_facing_docstrings()],
)
def test_no_text_after_args_is_silently_dropped(tool: str, doc: str) -> None:
    """Strands drops the `Args:` section AND any unindented text after it that
    is not a recognised section header: prose placed below the arguments never
    reaches the model, with no error. Everything she must read goes above
    `Args:`."""
    import inspect

    lines = inspect.cleandoc(doc).splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == "Args:")
    except StopIteration:
        return
    for ln in lines[start + 1:]:
        if not ln.strip() or ln.startswith((" ", "\t")):
            continue
        assert ln.strip().startswith(_SECTION_HEADERS), (
            f"{tool}: {ln.strip()[:60]!r} sits after Args: and is dropped by "
            "Strands before the model sees it. Move it above Args:."
        )
        break
