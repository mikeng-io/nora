"""`generate_image`'s text, held to ADR 0015.

The docstring describes the TOOL (what it does, when to call it, its parameters
and statuses); the image-generation skill carries the JUDGEMENT (whether she is
in the picture, her look, what the picture states, each model's conventions).
These pin both halves of that split for this one tool.

Read from the source with `ast`, the way `tests/test_tool_surface_policy.py`
reads every tool, and counted over what Strands sends: the whole docstring
except its `Args:` section (strands 1.50.2, `tools/decorator.py`).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import tiktoken

from nora.skills.loader import SkillStore

TOOLS = Path(__file__).resolve().parents[2] / "nora" / "agent" / "tools.py"
SKILLS_DIR = Path(__file__).resolve().parents[2] / "nora" / "skills"

#: ADR 0015 §3's Level 3 ceiling: Level 1 plus a contract of statuses she
#: acts on differently.
LEVEL_3_CEILING = 320


def _function() -> ast.AsyncFunctionDef:
    tree = ast.parse(TOOLS.read_text())
    return next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "generate_image"
    )


def _description() -> str:
    doc = ast.get_docstring(_function()) or ""
    return re.split(r"\n\s*Args:\s*\n", doc)[0].strip()


def _result_dicts() -> list[ast.Dict]:
    """Every dict literal the tool returns, i.e. those with a `status` key."""
    return [
        node for node in ast.walk(_function())
        if isinstance(node, ast.Dict) and any(
            isinstance(k, ast.Constant) and k.value == "status" for k in node.keys
        )
    ]


def _returned_statuses() -> set[str]:
    out: set[str] = set()
    for node in _result_dicts():
        for key, value in zip(node.keys, node.values, strict=True):
            if isinstance(key, ast.Constant) and key.value == "status":
                if isinstance(value, ast.Constant):
                    out.add(value.value)
                elif isinstance(value, ast.Name) and value.id == "HELD":
                    out.add("held_for_review")
    return out


def _model_facing_strings() -> list[str]:
    """Every string constant in the tool's body that is not a docstring or a
    log message: the notes and statuses she reads."""
    fn = _function()
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(fn)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body and isinstance(node.body[0], ast.Expr)
    }
    logged = {
        id(arg)
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name) and node.func.value.id == "log"
        for arg in node.args
    }
    return [
        node.value for node in ast.walk(fn)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings and id(node) not in logged
    ]


def test_the_description_fits_level_3():
    tokens = len(tiktoken.get_encoding("cl100k_base").encode(_description()))
    assert tokens <= LEVEL_3_CEILING, tokens


def test_the_description_lists_exactly_the_statuses_the_tool_returns():
    """The description names the statuses, the result explains them (0015
    §3)."""
    listed = set(re.findall(r"^- `([a-z_]+)`:", _description(), re.MULTILINE))
    assert listed == _returned_statuses() == {
        "ok", "held_for_review", "nothing_found", "unavailable",
    }


def test_every_result_carries_a_note():
    """How to report a result is in the result: the `ok` branches carry a note,
    and what to do on `ok` (never quote the handle, paste no link) is not
    description text paid on every call."""
    missing = [
        ast.unparse(node) for node in _result_dicts()
        if not any(isinstance(k, ast.Constant) and k.value == "note" for k in node.keys)
    ]
    # The quota/auth branch builds its note from a table; it is the one
    # dict whose note is a name rather than a literal, and it has the key.
    assert missing == [], missing


def test_no_dash_in_the_description_or_in_any_note():
    for text in (_description(), *_model_facing_strings()):
        assert "—" not in text and "–" not in text, text


def test_the_description_carries_no_judgement_the_skill_owns():
    """The image-generation skill owns whether she is in the picture, her
    look, what the picture states and each model's conventions. None of it
    belongs in text that is paid on every call and read before the skill."""
    text = _description().lower()
    for judgement in (
        "media_fetch", "what to keep", "never guess", "say plainly",
        "quote", "mascot", "yourself", "clock", "date", "grok", "xai",
    ):
        assert judgement not in text, judgement


def test_fixed_options_are_types_not_prose():
    """0015 §3: an argument that takes one of a fixed set is a `Literal`."""
    args = {a.arg: a for a in _function().args.args}
    for name in ("size", "aspect", "quality"):
        assert "Literal[" in ast.unparse(args[name].annotation), name
    for listed in ('"small"', '"16:9"', '"standard"'):
        assert listed not in _description(), listed


#: A model asked to draw herself could call `generate_image` straight from
#: memory with no `reference`, never loading the skill that holds the canonical
#: handle and the appearance rules. So the tool points AT the skill rather than
#: copying the skill's content into the tool (single source of truth); these
#: two tests pin the pointer and that it cannot dangle.
_SKILL_NAME_RE = re.compile(r"\bthe ([a-z][a-z0-9-]*) skill\b")


def test_description_points_to_the_image_generation_skill():
    """MUST: the docstring sends her to the skill before she calls this tool.
    The reference handle and the appearance rules stay in the skill only
    (0015 §3, `test_the_description_carries_no_judgement_the_skill_owns`)."""
    desc = _description()
    assert "MUST: load the image-generation skill before calling this tool" in desc, desc
    match = _SKILL_NAME_RE.search(desc)
    assert match is not None, desc
    assert match.group(1) == "image-generation", match.group(1)


def test_the_named_skill_exists():
    """The name the docstring points at must resolve through `SkillStore`, the
    same lookup Nora's own `skills` tool uses (`nora.skills.loader`), so a
    skill rename or removal cannot leave this pointer dangling without
    turning a test red."""
    match = _SKILL_NAME_RE.search(_description())
    assert match is not None
    skill = SkillStore(SKILLS_DIR).resolve(match.group(1))
    assert skill is not None, match.group(1)
    assert skill.name == "image-generation", skill.name
