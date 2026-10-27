"""Every renderer that puts a row on a LINE collapses that row to one line.

`one_line` (`nora.domain.prompt`) is applied by each renderer that joins rows
with `"\\n"` and renders each as `<something>: <body>`: the tag and fire prefill,
the debrief fold, the note curator's evidence and own-sends, the note-candidate
index, the proactive standing listing, the shadow mirror post. A row body that
keeps a newline can forge a further line of the block, and `escape` does not
help: it is blind to the line axis.

The property asserted is that no renderer is FORGOTTEN, not what one renderer
does with a particular row, so this is a STATIC read of the source. Each
renderer's behaviour is pinned by its own unit test (`tests/test_prefill.py`,
`tests/unit/test_relevance_gate.py`, `tests/unit/test_debrief_render.py`,
`tests/unit/test_prompt_one_line.py`); this is the brake on the NEXT one. It
has the same shape as `test_media_join_reaches_every_transcript.py`.

The gate is inverted. The renderers are DISCOVERED from the source and the
exemptions are the hand-written half, so a new renderer appears in the
discovered set and has to be classified: collapsed, or exempted here with a
sentence. Listing the renderers instead would catch a rename and miss one that
was never listed at all.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: The collapse, by any name it is called under. `nora.ingest.relevance_gate`
#: imports it as `_one_line` because its call sites and its two tests read that
#: name, and `nora.agent.prefill` does the same for `age`; a gate that only knew
#: the public spelling would read that module as un-collapsed.
_DIRECT_COLLAPSERS = {"one_line", "_one_line"}

#: `render_rows` reaches the collapse for its caller, so a renderer that goes
#: through it has not forgotten. Kept apart from the direct names above because
#: the tripwire at the bottom of this file has to be able to tell "migrated"
#: from "still collapsing by hand", and one set cannot answer both questions.
#:
#: A name in this set that no longer exists does not fail anything; it quietly
#: whitelists the NEXT function anybody gives that name.
_COLLAPSERS = _DIRECT_COLLAPSERS | {"render_rows"}

#: `render_rows` is in that set because it IS the collapse: it applies
#: `one_line` to every row unconditionally and there is no `RowStyle` that turns
#: it off, which `tests/unit/test_blocks.py` pins under every style in its
#: parity table. A renderer that goes through it cannot forget.
#:
#: THIS FILE'S JOB SHRINKS AS RENDERERS MIGRATE TO `render_rows`: when every
#: renderer calls it, one unit test on one function replaces this whole gate and
#: the file should be DELETED rather than kept as a formality.
#: `test_the_collapse_is_reachable_from_every_layer_that_renders` is the
#: tripwire for that moment.

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
    ("nora/domain/standing_revise.py", "check_ledger_op", "sections[name]"): (
        "The validator serializes a whole Markdown body for storage; this is not a prompt row. "
        "The ledger renderer parses its sections and joins the individual cited lines."
    ),
    (
        "nora/agent/prefill.py", "build_tag_message",
        'escape(render_file_links(ctx.message.body))',
    ): (
        "`<tag_message>`: the message she is answering, whole, between its own "
        "tags. Same shape and same reason as `<replying_to>` above, which it "
        "encloses."
    ),
    (
        "nora/domain/blocks.py", "render", "self.body",
    ): (
        "ALREADY collapsed, per row, by whatever built the body: "
        "`render_rows` applies `one_line` to every row before joining them and "
        "`render_messages` puts each message in its own element, both pinned "
        "in `tests/unit/test_blocks.py`. `ContextBlock.body` is the JOINED "
        "result, so collapsing it here would destroy the line structure the "
        "collapse exists to protect — an N-row transcript would render as one "
        "line. What makes the exemption safe is not that this is the only "
        "place a block becomes text — it is not: `prefill.build_tag_message` "
        "and `_replying_to_block` still build their tags by hand, which is "
        "issue #239 — it is that every body reaching HERE was joined by a row "
        "renderer in this same module."
    ),
    (
        "nora/language/note_render.py", "open_note_parts", "escape(text)",
    ): (
        "`<open_note>`: one note's body, whole, inside its own element — a "
        "body is prose that runs to paragraphs, and `merged_body` depends on "
        "its blank lines. Nothing line-oriented sits beside it for a newline "
        "to forge a row of."
    ),
    (
        "nora/language/note_render.py", "open_note_parts", "escape(text[:excerpt])",
    ): (
        "The same body cut to its first `excerpt` characters, for the same "
        "reason as the whole one above."
    ),
    (
        "nora/language/standing_render.py", "render_standing", "body",
    ): (
        "`<standing>`: her whole position, between its own tags, exactly like "
        "`<open_note>` above — not one line of a line-oriented block, so "
        "nothing beside it for a newline to forge a row of. `body` starts as "
        "`escape(standing.body)` (design/cognition-loop step 3 row 3b keeps "
        "this unescaped-once rule the same as before this step) and the two "
        "new, optional sections (`I hold open`, `I owe`) are appended as "
        "further paragraphs, each independently escaped before being joined "
        "in — the block is one prose position with up to three paragraphs, "
        "never a set of rows sharing the block."
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
    `{escape(body)}` is the shape `relevance_gate` has, and an interpolation-only
    scan reads it as a module that never collapses anything. The same resolution
    makes a future `body = escape(row.body)` red instead of unseen.
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
    every `Name` in the subtree would make `{found.group(0)}` and
    `{revision.note_id}` hits, because elsewhere in the same function a local
    called `found`/`revision` may be assigned from something with a `.body`. A
    body reaches a line as a VALUE, so that is the only position worth following.
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
    """The other direction: a stale exemption is a renderer this file claims to
    have classified and has not looked at since. A site that was fixed, moved or
    deleted must lose its entry rather than sit here authorising nothing."""
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
    renderer, and this is what says that placement is still load-bearing.

    **Counts CALLS, parsed, not the substring.** Scanning each file's source
    text for `"one_line"` would be satisfied by a COMMENT or docstring that
    mentions the name without calling it. A guard a comment can satisfy measures
    documentation.

    `nora/domain` is excluded because the collapse and the shared renderer both
    live there; counting it is a tautology.

    **`_DIRECT_COLLAPSERS`, not `_COLLAPSERS`, makes this a tripwire rather than
    a formality.** Counting `render_rows` calls too would leave it counting the
    same packages forever, while the stated end-state is that every renderer goes
    through `render_rows`. Counting only the HAND-ROLLED collapse makes the
    number fall as the migration proceeds and reach zero when it completes.
    """
    import ast
    import collections

    packages: collections.Counter[str] = collections.Counter()
    for path in _modules():
        rel = path.relative_to(ROOT)
        if rel.parts[1] == "domain":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                                # pragma: no cover
            continue
        calls = sum(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id in _DIRECT_COLLAPSERS
            for n in ast.walk(tree)
        )
        if calls:
            packages[rel.parts[1]] += calls
    assert packages, (
        "NO package outside nora/domain calls `one_line` by hand any more: every "
        "renderer reaches the collapse through `render_rows`. That is this "
        "file's stated end-state — one unit test on one function now covers what "
        "this whole gate was written to watch, so DELETE it rather than keep it "
        "as a formality."
    )
