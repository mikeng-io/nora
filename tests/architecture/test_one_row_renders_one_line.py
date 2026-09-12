"""Every renderer that puts a row on a LINE collapses that row to one line.

**This file exists because the fix shipped to one renderer out of eight.**
`_one_line` was written on 2026-09-10 for `nora.ingest.relevance_gate` — with a
live measurement behind it (a forged commitment line took that gate to `go` 5/5
and stayed 5/5 against a hardened instruction) — as a PRIVATE function in that
module. Seven other renderers join rows with `"\\n"` and render each as
`<something>: <body>`, and every one of them went on forging rows: the tag and
fire prefill, the debrief fold, the note curator's evidence and own-sends, the
note-candidate index, the proactive standing listing, the shadow mirror post.
The tag prefill is the one a member actually talks to.

It is the `test_media_join_reaches_every_transcript.py` defect exactly, one
module over: a property established through ONE consumer, with nothing asking
whether the other seven were forgotten. And the same sweep had already been
run once and missed this: `note_candidates._render_line` gained its `escape`
call on 2026-09-10, the same day, and still rendered a raw newline — because
`escape` is blind to the line axis and nothing held the two halves together.

**A STATIC read of the source, deliberately**, for that gate's own reason: the
thing asserted is not what one renderer does with a particular row, it is that
no renderer was FORGOTTEN. Each renderer's behaviour is pinned by its own unit
test (`tests/test_prefill.py`, `tests/unit/test_relevance_gate.py`,
`tests/unit/test_debrief_render.py`, `tests/unit/test_note_curator.py`,
`tests/unit/test_prompt_one_line.py`); this is the brake on the NEXT one.

**Inverted, like that gate and for the same measured reason.** The renderers are
DISCOVERED from the source and the exemptions are the hand-written half, so a
new renderer appears in the discovered set and has to be classified — collapsed,
or exempted here with a sentence. Listing the renderers instead would catch a
rename and miss the thing that has actually happened twice: one that was never
listed at all.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: The collapse, by any name it is called under. `nora.ingest.relevance_gate`
#: imports it as `_one_line` because its call sites and its two tests read that
#: name, and `nora.agent.prefill` does the same for `age`; a gate that only knew
#: the public spelling would read that module as un-collapsed.
_COLLAPSERS = {"one_line", "_one_line", "_snippet"}

#: Interpolations that render a `.body` and do NOT collapse it, each with the
#: reason. Keyed by (module, enclosing function, the interpolated expression as
#: `ast.unparse` writes it) — stable across reformatting and line moves, and
#: precise enough that two sites in one file are two separate decisions rather
#: than one blanket pass. The function is in the key because `escape(body)` is
#: the kind of expression a file holds twice, and the second one must be a
#: decision rather than an inheritance.
#:
#: **Every entry here is a body that is NOT one line of a line-oriented block,
#: or one that was ALREADY collapsed before it got here.** Those are the only
#: two reasons this list may grow. A body between its own tags may hold newlines
#: safely — the delimiters are the tags, `escape` stops a forged one, and
#: collapsing it would destroy a paragraph break the model should see. A body
#: that is one line among many may not, whatever framing sits around it.
#:
#: The second reason is narrower than it sounds and exactly one site uses it: an
#: entry may claim it only when the collapse happens at the SOLE constructor of
#: the thing being rendered AND a named test pins it there. "Collapsed
#: somewhere upstream" without that constructor and that test is how this gate
#: becomes a formality.
_NOT_A_LINE = {
    (
        "nora/agent/prefill.py", "_replying_to_block", "escape(parent.body or '')",
    ): (
        "`<replying_to>`: the parent's message, WHOLE, between its own tags and "
        "alone in the block. Nothing joins it to a sibling, so a newline in it "
        "forges nothing — and a member who writes two paragraphs and is then "
        "quoted should be quoted as they wrote it."
    ),
    (
        "nora/agent/prefill.py", "build_tag_message", 'escape(ctx.message.body)',
    ): (
        "`<tag_message>`: the message she is answering, whole, between its own "
        "tags. Same shape and same reason as `<replying_to>` above, which it "
        "encloses."
    ),
    (
        "nora/language/standing_render.py", "render_standing", 'escape(standing.body)',
    ): (
        "`<standing>`: her whole folded position between its own tags, one per "
        "prompt. `relevance_gate` DOES collapse its standing, and the "
        "difference is the shape rather than the trust — that renderer joins "
        "its blocks into one flat document it then splits on newlines, while "
        "this one is tag-delimited. `nora.language.proactive` renders standing "
        "one line per ROOM and does collapse it."
    ),
    (
        "nora/app/eval_support.py", "run_proactive_act_case", 'seed.notes[0][1].body',
    ): (
        "Not a render at all: the eval seed composes a new NOTE REVISION body "
        "out of the previous one. The newline is deliberate content in a stored "
        "document, and nothing puts this string on a line beside another row."
    ),
    (
        "nora/engagement/debrief_render.py", "render_debrief_input", "escape(body)",
    ): (
        "`<standing_prior>` on the TAG fold's path — the same block, the same "
        "shape and the same reason as the rollup's below. Two entries rather "
        "than one because they are two renderers: the expression is identical, "
        "which is exactly why the key carries the function."
    ),
    (
        "nora/engagement/debrief_render.py", "render_rollup_input", "escape(body)",
    ): (
        "`<standing_prior>`: the position this fold is about to correct, whole, "
        "between its own tags and one per prompt. Same shape and same reason as "
        "`<standing>` in `standing_render`; the transcript block in this very "
        "module, one line per row, does collapse."
    ),
    (
        "nora/obs/determination.py", "render_mirror",
        "(row.body or '')[:WINDOW_BODY_CHARS]",
    ): (
        "ALREADY collapsed, at the only constructor. `nora/obs/determination.py` "
        "is gated on importing no `nora.*` at all "
        "(`test_the_module_imports_nothing_from_nora`), so it cannot call "
        "`one_line` — instead `window_from`, the one way a window is ever built, "
        "collapses every body with the same `str.splitlines` expression, and "
        "`test_the_mirrors_collapse_agrees_with_the_shared_one_exactly` pins the "
        "two equal over every separator Python knows."
    ),
    (
        "nora/domain/blocks.py", "render", "self.body",
    ): (
        "ALREADY collapsed, per row, at the only thing that builds a body: "
        "`render_rows` applies `one_line` to every row before joining them, and "
        "`tests/unit/test_blocks.py` pins that under every `RowStyle` in the "
        "parity table. `ContextBlock.body` is the JOINED result, so collapsing "
        "it here would destroy the line structure the collapse exists to "
        "protect — it would render an N-row transcript as one line."
    ),
}


def _modules() -> list[Path]:
    return sorted((ROOT / "nora").rglob("*.py"))


def _functions(tree: ast.AST):
    """Every function in the module, innermost included — the unit a local
    assignment is resolved within."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node


def _locals_of(fn: ast.AST) -> dict[str, list[ast.expr]]:
    """`name -> every value assigned to it` inside one function.

    **One level of aliasing is resolved, and it is not a nicety.** A renderer
    that writes `body = _one_line(row.body or "")` and then interpolates
    `{escape(body)}` is the shape `relevance_gate` actually has, and an
    interpolation-only scan reads it as a module that never collapses anything
    — so the one renderer that HAD the fix would have been invisible to the
    gate written about it. The same resolution is what makes a future
    `body = escape(row.body)` red instead of unseen.
    """
    out: dict[str, list[ast.expr]] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out.setdefault(target.id, []).append(node.value)
    return out


def _value_names(expr: ast.expr) -> set[str]:
    """The names `expr` reads AS VALUES — not the base of an attribute access and
    not the callee of a call.

    That exclusion is what keeps the aliasing from going everywhere. Resolving
    every `Name` in the subtree instead made `{found.group(0)}` and
    `{revision.note_id}` hits, because somewhere else in the same function a
    local called `found`/`revision` had been assigned from something with a
    `.body` — a renderer by coincidence of variable naming. A body reaches a
    line as a VALUE, so that is the only position worth following.
    """
    bases = {
        id(n.value) for n in ast.walk(expr) if isinstance(n, ast.Attribute)
    } | {
        id(n.func) for n in ast.walk(expr) if isinstance(n, ast.Call)
    }
    return {
        n.id for n in ast.walk(expr)
        if isinstance(n, ast.Name) and id(n) not in bases
    }


def _names(expr: ast.expr, scope: dict[str, list[ast.expr]], depth: int = 0):
    """`expr` and everything it is an alias for, to one level of indirection."""
    yield expr
    if depth:
        return
    for name in _value_names(expr):
        for value in scope.get(name, ()):
            yield from _names(value, scope, depth + 1)


def _reads_a_body(exprs) -> bool:
    return any(
        isinstance(n, ast.Attribute) and n.attr == "body"
        for expr in exprs for n in ast.walk(expr)
    )


def _is_only_a_length(exprs) -> bool:
    """`f"got {len(self.body)}"` in a validator's error message is not a render
    — the body never reaches the string, only its size does."""
    for expr in exprs:
        for node in ast.walk(expr):
            if isinstance(node, ast.Attribute) and node.attr == "body":
                parents = [
                    p for p in ast.walk(expr)
                    if isinstance(p, ast.Call) and isinstance(p.func, ast.Name)
                    and p.func.id == "len" and node in ast.walk(p)
                ]
                if not parents:
                    return False
    return True


def _collapses(exprs) -> bool:
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id in _COLLAPSERS
        for expr in exprs for n in ast.walk(expr)
    )


def _discovered() -> dict[tuple[str, str, str], int]:
    """Every f-string interpolation under `nora/` that renders a row's body,
    mapped to the line it is on. Collapsed ones are excluded — the question is
    who still has to be classified."""
    found: dict[tuple[str, str, str], int] = {}
    for path in _modules():
        rel = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                                # pragma: no cover
            continue
        for fn in _functions(tree):
            scope = _locals_of(fn)
            for node in ast.walk(fn):
                if not isinstance(node, ast.JoinedStr):
                    continue
                for part in node.values:
                    if not isinstance(part, ast.FormattedValue):
                        continue
                    exprs = list(_names(part.value, scope))
                    if not _reads_a_body(exprs):
                        continue
                    if _is_only_a_length(exprs) or _collapses(exprs):
                        continue
                    found[(rel, fn.name, ast.unparse(part.value))] = part.lineno
    return found


def test_every_renderer_of_a_row_collapses_it_or_says_why_not():
    """The gate. A new renderer either calls the collapse or lands here.

    The failure message names the file, the line and the expression, because
    the two ways out are a one-line code change and a one-sentence entry and
    the author should not have to read this file to find either.
    """
    undeclared = {
        site: line for site, line in _discovered().items() if site not in _NOT_A_LINE
    }
    assert not undeclared, (
        "these render a row's body into an f-string without collapsing it to one "
        "line. If it is one line of a line-oriented block, wrap it in "
        "`nora.domain.prompt.one_line`. If it is a whole body between its own "
        "tags, add it to `_NOT_A_LINE` with the reason:\n"
        + "\n".join(
            f"  {f}:{line}  in {func}()  {expr}"
            for (f, func, expr), line in sorted(undeclared.items())
        )
    )


def test_the_exemptions_all_still_exist():
    """The other direction, which the media-join gate learned the hard way: a
    stale exemption is a renderer this file claims to have classified and has
    not looked at since. A site that was fixed, moved or deleted must lose its
    entry rather than sit here authorising nothing."""
    live = set(_discovered())
    stale = {site: why for site, why in _NOT_A_LINE.items() if site not in live}
    assert not stale, (
        "exempted sites that no longer exist — delete the entry:\n"
        + "\n".join(f"  {f}  in {func}()  {expr}" for f, func, expr in sorted(stale))
    )


def test_every_exemption_carries_a_reason():
    """A bare entry is an exemption nobody has to justify, which is how a list
    like this stops being a decision and becomes a place to put things."""
    for site, why in _NOT_A_LINE.items():
        assert len(why.split()) >= 12, f"{site} needs a real reason, not {why!r}"


def test_the_collapse_is_reachable_from_every_layer_that_renders():
    """`one_line` lives in `nora.domain.prompt` rather than beside any one
    renderer, and this is what says that placement is still load-bearing: the
    renderers are spread across six packages that cannot import each other, so
    a collapse living in any of them would be a second copy in the other five.
    That is not hypothetical — it is what happened."""
    import collections

    packages = collections.Counter()
    for path in _modules():
        src = path.read_text(encoding="utf-8")
        if "one_line" in src and "nora/domain/prompt.py" not in path.as_posix():
            packages[path.relative_to(ROOT).parts[1]] += 1
    assert len(packages) >= 5, (
        f"only {sorted(packages)} render rows; if that is now one package, "
        "`one_line` may move to it — but check every caller first"
    )
