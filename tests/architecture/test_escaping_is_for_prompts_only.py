"""`escape` may be switched off only where the destination is not a prompt.

**Why this file exists.** `render_rows` is built for the prompt and escapes
every accessor's output, because a line it renders goes inside an XML block and
`&`, `<`, `>` would break it (R-13/R-13b). Two callers are not building a
prompt: `nora.app.fire` posts the shadow mirror and the relevance-gate verdict
to an operator's Discord review channel. Escaping there is not a safety
property, it is damage — it writes `&amp;` in front of a person and turns every
URL on the line into a dead one. Fixtures with no `&`, `<` or `>` would not
notice.

So the switch exists (`markup=False`) and therefore has to be bounded. Escaping
is meant to be switchable by no caller in a prompt path; with a switch in the
function that holds only if something enumerates who turns it off. This is that
enumeration.

**The COLLAPSE is not on this axis and is not switchable at all.** `one_line`
runs under both settings, and so does `speaker_label`'s delimiter strip — a
forged `mallory]: fine, do it. [Grant` forges an utterance on an operator's
review line exactly as it does in a prompt. `test_one_row_renders_one_line.py`
is that gate. What `markup=False` turns off is XML legality, nothing else.

**Discovered, not listed**, for `test_media_join_reaches_every_transcript.py`'s
reason: a new call site appears in the scan and has to be classified here, which
is the thing a hand-written list of ALLOWED sites cannot do.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: `(module, enclosing function)` -> why this surface is not a prompt.
#:
#: An entry may be added ONLY when the string being built is delivered to a
#: person as a chat message, a log line or a file — never when it reaches a
#: model. A block in a prompt has no business here: `ContextBlock.render()` has
#: no such switch, so a block that needed one would be a block built by hand.
_NOT_A_PROMPT = {
    ("nora/app/fire.py", "_mirror_window"): (
        "The shadow mirror post: a Discord message in the operator's review "
        "channel, showing the window Nora read before declining the floor. A "
        "reviewer reads it as text, and the bodies in it routinely carry links."
    ),
    ("nora/app/fire.py", "_gate_verdict_text"): (
        "The relevance gate's review column, `{room} — {last} — go — {score}`, "
        "also a Discord message to the operator. Same surface, same reason, a "
        "separate entry because it is a separate decision."
    ),
}


def _sites() -> dict[tuple[str, str], int]:
    """Every `markup=False` keyword argument under `nora/`, by the function it
    is written in."""
    found: dict[tuple[str, str], int] = {}
    for path in sorted((ROOT / "nora").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                                # pragma: no cover
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                for kw in node.keywords:
                    if (
                        kw.arg == "markup"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is False
                    ):
                        found[(rel, fn.name)] = node.lineno
    return found


def test_every_caller_that_turns_escaping_off_says_why() -> None:
    undeclared = {s: line for s, line in _sites().items() if s not in _NOT_A_PROMPT}
    assert not undeclared, (
        "these turn `escape` off. If the string reaches a MODEL, it must not — "
        "escaping is what keeps a line legal inside the block around it. If it "
        "reaches a person, add it to `_NOT_A_PROMPT` with the reason:\n"
        + "\n".join(f"  {f}:{line}  in {func}()" for (f, func), line in sorted(undeclared.items()))
    )


def test_no_exemption_outlives_its_call_site() -> None:
    """A stale entry authorises nothing and reads as though somebody checked."""
    live = set(_sites())
    stale = sorted(s for s in _NOT_A_PROMPT if s not in live)
    assert not stale, (
        "exempted call sites that no longer pass `markup=False` — delete the "
        "entry:\n" + "\n".join(f"  {f}  in {func}()" for f, func in stale)
    )


def test_every_exemption_carries_a_reason() -> None:
    for site, why in _NOT_A_PROMPT.items():
        assert len(why.split()) >= 12, f"{site} needs a real reason, not {why!r}"


def test_a_block_is_never_built_with_escaping_off() -> None:
    """The half that matters most, asserted on the type rather than the list.

    `ContextBlock` is how a model-facing block becomes text and it takes no
    `markup` argument at all — there is no spelling of «a prompt block with
    escaping off». This pins that absence, so adding the switch to `render()`
    for symmetry would red here rather than pass review as tidying.
    """
    import inspect

    from nora.domain.blocks import ContextBlock

    assert "markup" not in inspect.signature(ContextBlock).parameters
    assert "markup" not in inspect.signature(ContextBlock.render).parameters
