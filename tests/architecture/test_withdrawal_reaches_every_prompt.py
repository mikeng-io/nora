"""A Standing or culture head reaches a prompt through the withdrawal rule, or
the read is pinned here as deliberately raw, with the reason.

A head the cascade marked `unsupported_at` rests on a Record item that was
withdrawn (a message unsent, a person forgotten). The rule
(design/cognition-loop/10-tiers.md §2 rule 7, `nora.domain.withdrawal`) is that
it stops rendering. It was written inline in the tag path, missing from the
fire path, and absent from every Dreamer prompt: seven prompts pushed a head
with no check, and those prompts feed models that go on to WRITE notes, Standing
and culture, so a retracted message could be laundered into a new row (step 5 of
design/context-assembly/04-every-occasion.md). The store readers return the
withdrawn row on purpose (a fold re-derives from it), so nothing at the store
stops a new reader forgetting.

So the test reads the tree (`nora/`, less the stores themselves in `nora/infra`
and the doubles in `nora/testing`). A READ is a call spelt like a store reader:
`.head(key=...)` (the culture store), `.read(platform=..., conversation_id=...)`
(the Standing store), `.read_head(...)` or `.read_as_of(...)`. It is GUARDED when
its result reaches `render_supported` or `read_culture` (which applies it, for
every occasion): the call is an argument of one of them, or it is assigned to a
name that one of them is given, in the same function; or the function returns it
and every call of that function in its module is an argument of one. Every other
read must be on `RAW`, keyed by `(module, function, reader)`, with the reason it is safe.
The reader is part of the key because one function can read two heads: the culture
fold's `inputs` reads its own prior ACCOUNT raw (`head`) and a Standing (`read`) that
must be guarded, and a pin for the first must not cover the second.
A raw read on `RAW` that no longer exists is an error too, so the list cannot
rot into a licence.

**What this test cannot see**, so a reviewer knows what is left to read:

  - a Standing store whose receiver is not spelt with `standing` or `store`
    (`self._positions.read(...)`), which is how the computer, documents and
    presence readers, called the same way, are told apart from it;
  - a reader reached through a name that does not look like a store call (a
    callable the composition root hands in, such as `prior_standing`): the
    composition's lambda that builds it is on `RAW`, and what is done with the
    result is read there;
  - a head that reaches a prompt by another route than a store read (a head
    held in a `TagContext` or a `Wiring` and handed to a renderer): those are
    read once, by a guarded read, and handed on;
  - a guard applied to the wrong variable that still shares a name with the
    right one in the same function (the match is by name, not by flow);
  - a head read by something outside `nora/` (`evals/`, `scripts/`).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: Where a store is DEFINED or doubled, not where a prompt is built.
SKIPPED = ("nora/infra/", "nora/testing/")

GUARDS = frozenset({"render_supported", "read_culture"})

#: `(module, function, reader) -> why a raw read of a head there is safe`. A fold's own
#: prior is the first reason, and it is the one the cascade's design asks for:
#: a withdrawn row "goes to its writer's next session for re-derivation"
#: (10-tiers.md §2 rule 7), and a writer that could not see it could not
#: re-derive it.
RAW = {
    ("nora/engagement/stages/debrief.py", "run", "read"):
        "the tag fold's OWN prior position (`standing_prior`): read raw so the fold "
        "sees what it wrote and can supersede it",
    ("nora/ingest/stages/standing_event.py", "_event_fold", "read"):
        "the event fold's OWN prior position (`standing_prior`), for the same reason "
        "as the tag fold's",
    ("nora/ingest/stages/standing_event.py", "_check", "read"):
        "the trigger counts the rows since the prior's watermark to decide whether to "
        "fold; renders nothing",
    ("nora/language/culture.py", "inputs", "head"):
        "the culture fold's OWN prior account (`previous_account`): the fold must see "
        "what it wrote to supersede it. Its `<standing>` beside it IS guarded",
    ("nora/language/culture.py", "commit", "head"):
        "compares the head's body with the proposal to skip an identical write; "
        "renders nothing",
    ("nora/language/proactive.py", "_changed_since", "read"):
        "a change signal (`updated_at` newer than the last pass): renders nothing, "
        "and the pass it triggers renders through the guarded `_standing_lines`",
    ("nora/subject/standing_fold.py", "read_fold_turn", "read_as_of"):
        "the eval seam reads the prior as of a past instant to seed a sandbox; the "
        "fold under test reads it raw like production's own, and nothing renders here",
    ("nora/subject/standing_fold.py", "fold", "read"):
        "the eval seam checks the sandbox holds (or lacks) the prior it seeded, and "
        "reads the position the fold wrote back; renders nothing",
    ("nora/agent/tools.py", "standing_read", "read_head"):
        "the `standing_read` tool: `nora.agent.standing_read.render_standing` returns "
        "nothing for an unsupported head before it looks at anything else "
        "(`test_the_standing_read_tool_applies_withdrawal_first` below)",
    ("nora/app/composition.py", "build_dependencies", "read_head"):
        "builds `standing_of` for the Cognition session: its write verb reads the head "
        "to name the revision it replaces (the fold's own prior, a base for "
        "`standing_revise`); the session sees Standing only through `standing_read`",
}


def _store_receiver(func: ast.Attribute) -> bool:
    """The receiver is spelt like a Standing store (`_standing`, `standing_head`,
    `store`, `wiring.store`). `.read(platform=, conversation_id=)` is also how the
    computer, documents and presence readers are called, and none of those is a
    head."""
    last = func.value.attr if isinstance(func.value, ast.Attribute) else (
        func.value.id if isinstance(func.value, ast.Name) else "")
    return "standing" in last or "store" in last


def _is_reader(node: ast.Call) -> str | None:
    """The reader a call is spelt as, or `None`."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    keywords = {k.arg for k in node.keywords}
    if func.attr == "head" and "key" in keywords:
        return "head"
    if func.attr == "read" and {"platform", "conversation_id"} <= keywords \
            and _store_receiver(func):
        return "read"
    if func.attr in ("read_head", "read_as_of"):
        return func.attr
    return None


def _guard_name(node: ast.Call) -> str | None:
    func = node.func
    name = func.id if isinstance(func, ast.Name) else (
        func.attr if isinstance(func, ast.Attribute) else None)
    return name if name in GUARDS else None


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}


def _enclosing(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.AST | None:
    while node in parents:
        node = parents[node]
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            return node
    return None


def _assigned_names(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> set[str]:
    """The names the statement holding `node` assigns, if it is an assignment."""
    while node in parents and not isinstance(node, ast.stmt):
        node = parents[node]
    targets: list[ast.expr] = []
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
    return {n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)}


def _inside_guard_args(node: ast.AST, scope: ast.AST) -> bool:
    for call in ast.walk(scope):
        if isinstance(call, ast.Call) and _guard_name(call):
            args = [*call.args, *(k.value for k in call.keywords)]
            if any(node is n for a in args for n in ast.walk(a)):
                return True
    return False


def _guarded(call: ast.Call, tree: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    scope = _enclosing(call, parents) or tree
    if _inside_guard_args(call, scope):
        return True
    names = _assigned_names(call, parents)
    for guard in ast.walk(scope):
        if isinstance(guard, ast.Call) and _guard_name(guard):
            args = [*guard.args, *(k.value for k in guard.keywords)]
            if names & {n.id for a in args for n in ast.walk(a) if isinstance(n, ast.Name)}:
                return True
    # A helper that builds the awaitable its caller hands to the guard.
    if isinstance(scope, ast.FunctionDef | ast.AsyncFunctionDef):
        returned = any(
            isinstance(r, ast.Return) and r.value is not None
            and any(call is n for n in ast.walk(r.value))
            for r in ast.walk(scope)
        )
        callers = [
            c for c in ast.walk(tree)
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
            and c.func.id == scope.name
        ]
        if returned and callers and all(_inside_guard_args(c, tree) for c in callers):
            return True
    return False


def unguarded(source: str) -> list[tuple[str, int, str]]:
    """`(function, line, reader)` for every read in `source` that does not reach
    a guard."""
    tree = ast.parse(source)
    parents = _parents(tree)
    out = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call) and (reader := _is_reader(node))
            and not _guarded(node, tree, parents)
        ):
            fn = _enclosing(node, parents)
            out.append((fn.name if fn is not None else "<module>", node.lineno, reader))
    return sorted(out, key=lambda t: t[1])


def _modules() -> list[str]:
    return sorted(
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / "nora").rglob("*.py")
        if "__pycache__" not in p.parts
        and not p.relative_to(ROOT).as_posix().startswith(SKIPPED)
    )


def _tree_findings() -> dict[tuple[str, str, str], list[int]]:
    found: dict[tuple[str, str, str], list[int]] = {}
    for rel in _modules():
        for function, line, reader in unguarded((ROOT / rel).read_text(encoding="utf-8")):
            found.setdefault((rel, function, reader), []).append(line)
    return found


#: One positive control per shape the scan claims to flag, and one per shape it
#: must leave alone. A scan that cannot flag the offender it exists for is
#: decoration.
FLAGGED = {
    "culture head rendered raw":
        "async def f(store):\n    h = await store.head(key='k')\n    return h.body\n",
    "standing read rendered raw":
        "async def f(store):\n    p = await store.read(platform='a', "
        "conversation_id='b')\n    return p.body\n",
    "read inside wait_for, no guard":
        "async def f(store):\n    p = await asyncio.wait_for(store.read(platform='a', "
        "conversation_id='b'), timeout=1)\n    return p\n",
    "read_head and read_as_of":
        "async def f(store):\n    a = await store.read_head(platform='a', conversation_id='b')\n"
        "    b = await store.read_as_of(platform='a', conversation_id='b', before_ms=1)\n",
    "guard applied to ANOTHER read in the same function":
        "async def f(store, c):\n    p = await store.read(platform='a', conversation_id='b')\n"
        "    prev = await c.head(key='k')\n    return render_supported(p), prev\n",
    "a helper whose caller bypasses the guard":
        "def h(c, k):\n    return c.head(key=k)\nasync def f(c):\n    return await h(c, 'k')\n",
    "a helper with one guarded and one raw caller":
        "def h(c, k):\n    return c.head(key=k)\n"
        "async def f(c):\n    a = await read_culture(h(c, 'k'))\n    return a, await h(c, 'j')\n",
    "a guard that is never given the read":
        "async def f(store):\n    p = await store.read(platform='a', conversation_id='b')\n"
        "    return render_supported(None), p\n",
}

CLEAN = {
    "read then render_supported":
        "async def f(store):\n    p = await store.read(platform='a', conversation_id='b')\n"
        "    return render_supported(p)\n",
    "read inside wait_for then render_supported":
        "async def f(store):\n    p = await asyncio.wait_for(store.read(platform='a', "
        "conversation_id='b'), timeout=1)\n    return render_supported(p)\n",
    "annotated assignment then render_supported":
        "async def f(store):\n    p: object = await store.read(platform='a', conversation_id='b')\n"
        "    return render_supported(p)\n",
    "read inside the guard":
        "async def f(store):\n    return render_supported(await store.read(platform='a', "
        "conversation_id='b'))\n",
    "head inside read_culture":
        "async def f(c):\n    return await read_culture(c.head(key='k'), stage='s')\n",
    "head inside read_culture, through traced_read":
        "async def f(c):\n    return await read_culture(traced('c', c.head(key='k')))\n",
    "read nested in a renderer that is given the guard":
        "async def f(store):\n    return render(render_supported(await store.read(platform='a', "
        "conversation_id='b')))\n",
    "helper whose every caller is inside read_culture":
        "def h(c, k):\n    return None if not k else c.head(key=k)\n"
        "async def f(c):\n    return await read_culture(h(c, 'k'), stage='s')\n",
    "a method of the same names with other arguments":
        "async def f(notes, x):\n    a = await notes.read(unit='u', note_id='n')\n"
        "    b = x.head()\n    return a, b\n",
}


@pytest.mark.parametrize("shape", sorted(FLAGGED))
def test_the_scan_flags_this_shape(shape: str):
    assert unguarded(FLAGGED[shape]), shape


@pytest.mark.parametrize("shape", sorted(CLEAN))
def test_the_scan_leaves_this_shape_alone(shape: str):
    assert not unguarded(CLEAN[shape]), shape


def test_the_scan_flags_the_unguarded_one_and_not_the_guarded_one_beside_it():
    source = (
        "async def f(store, c):\n"
        "    p = await store.read(platform='a', conversation_id='b')\n"
        "    prev = await c.head(key='k')\n"
        "    return render_supported(p), prev\n"
    )
    assert [(fn, reader) for fn, _, reader in unguarded(source)] == [("f", "head")]


def test_a_pin_for_one_reader_does_not_cover_another_in_the_same_function():
    source = (
        "async def inputs(self):\n"
        "    previous = await self._culture.head(key='k')\n"
        "    block = render(await self._standing.read(platform='a', conversation_id='b'))\n"
    )
    assert {(fn, reader) for fn, _, reader in unguarded(source)} == {
        ("inputs", "head"), ("inputs", "read"),
    }


def test_every_read_of_a_head_is_guarded_or_pinned_raw_with_its_reason():
    found = _tree_findings()
    unpinned = {key: lines for key, lines in found.items() if key not in RAW}
    assert not unpinned, (
        f"{unpinned} read a Standing or culture head and never pass it through "
        "`render_supported` or `read_culture`: a head the withdrawal cascade marked "
        "`unsupported_at` renders in that prompt, and the model can carry it into a "
        "note or a fold. Read culture through `read_culture`; apply `render_supported` "
        "to a Standing head; or, for a fold's OWN prior or a read that renders "
        "nothing, add the (module, function, reader) to RAW with the reason."
    )


def test_no_pinned_raw_read_is_stale():
    found = _tree_findings()
    stale = sorted(set(RAW) - set(found))
    assert not stale, (
        f"{stale} are on RAW and no longer read a head unguarded: delete them, "
        "or the list becomes a licence for the next unguarded read in that function."
    )
    assert all(reason.strip() for reason in RAW.values())


def test_every_raw_exemption_names_a_function_that_exists():
    for rel, function, _reader in RAW:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        names = {
            n.name for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        assert function in names, f"{rel} has no function {function!r}"


def test_the_standing_read_tool_applies_withdrawal_first():
    """The `standing_read` exemption above is true only while its renderer drops
    an unsupported head before it renders anything."""
    tree = ast.parse((ROOT / "nora/agent/standing_read.py").read_text(encoding="utf-8"))
    render = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "render_standing"
    )
    text = [ast.unparse(stmt) for stmt in render.body]
    checked = next(i for i, t in enumerate(text) if "unsupported_at" in t)
    lineage = next(i for i, t in enumerate(text) if "visible_sections" in t)
    assert checked < lineage, "the withdrawal check no longer comes before lineage"


def test_the_guard_is_the_one_function():
    """`read_culture` applies the same `render_supported` the call sites do; it is
    not a second implementation of the rule."""
    tree = ast.parse((ROOT / "nora/language/culture_render.py").read_text(encoding="utf-8"))
    read = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "read_culture"
    )
    assert any(
        isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
        and c.func.id == "render_supported"
        for c in ast.walk(read)
    )
