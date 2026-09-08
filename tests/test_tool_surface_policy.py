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
