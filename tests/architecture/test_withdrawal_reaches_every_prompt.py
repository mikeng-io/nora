"""A Standing or culture head reaches a prompt through the withdrawal rule, or
the read is pinned here as deliberately raw, with the reason.

A head the cascade marked `unsupported_at` rests on a Record item that was
withdrawn (today only a person forgotten by `nora person forget`; a message deleted or
unsent withdraws nothing yet, design/context-assembly/04 §7). The rule
(design/cognition-loop/10-tiers.md §2 rule 7, `nora.domain.withdrawal`) is that
it stops rendering. It was written inline in the tag path, missing from the
fire path, and absent from every Dreamer prompt: seven prompts pushed a head
with no check, and those prompts feed models that go on to WRITE notes, Standing
and culture, so a retracted message could be laundered into a new row (step 5 of
design/context-assembly/04-every-occasion.md). The store readers return the
withdrawn row on purpose (a fold re-derives from it), so nothing at the store
stops a new reader forgetting.

So the test reads the tree (`nora/`, less the doubles in `nora/testing`). A READ is
a call spelt like a store reader: `.head(key=...)` (the culture store),
`.read(platform=..., conversation_id=...)` (the Standing store), `.read_head(...)`
or `.read_as_of(...)`. It is GUARDED when its result reaches `render_supported` or
`read_culture` (which applies it, for every occasion) AND the guarded value is the
one used: the call is an argument of one of them, or it is assigned to a name that
one of them is given, in the same function; the guard's own result is not thrown
away (`render_supported(p); return p.body` is not a guard), and the raw name is not
read again after the guard unless the guard rebinds it (`p = render_supported(p)`);
or the function returns the read and every call of that function in its module is an
argument of one. A guard must also BE the guard: a module that defines or rebinds
`render_supported`/`read_culture` itself, or imports it from anywhere but
`nora.domain.withdrawal` / `nora.language.culture_render`, is an error.

Every other read must be on `RAW`, keyed by `(module, function, reader)`, with the
NUMBER of raw reads there and the reason it is safe. The count is part of the pin
so that a second raw read added to a pinned function is a failure, not a licence:
the reviewer of this step added a raw `.read` to `_changed_since` and a raw
`.head(key='another-room')` to the culture fold's `inputs`, and the first version,
keyed without a count, passed both. The reader is part of the key because one
function can read two heads: the culture fold's `inputs` reads its own prior ACCOUNT
raw (`head`) and a Standing (`read`) that must be guarded. A pin that no longer
matches a read is an error too, so the list cannot rot into a licence.

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
  - the match between a read and its guard is by NAME in one function, not by flow:
    `a, b = await asyncio.gather(x.read(...), y.read(...))` with `render_supported(a)`
    clears `b` too, and a guard applied to a different variable that shares a name
    with the right one passes;
  - a reader or a guard called through an alias or a wrapper: a method alias
    (`r = store.read; await r(platform=..., ...)`), `getattr(store, "read")`,
    `functools.partial(store.read, ...)`, a `**kwargs` call (`store.read(**kw)` has no
    keywords to see), and `rs = render_supported` (the read then reads as unguarded,
    which fails closed);
  - a guard that is given the read and does the wrong thing: only `render_supported`'s
    own body is checked (by the mutation table);
  - reads that are not a store method at all: `nora/infra/sql/snapshot.py` `_culture`
    (:396) selects a culture revision as of a past instant in SQL, and
    `nora/subject/standing_fold.py` (:267) and `snapshot` (:305) read Standing revisions
    as of a past instant to seed an eval sandbox; a revision carries no mark (only the
    head does), so none of them could be guarded. The last two are on `RAW`, the first
    cannot be seen;
  - a head read by something outside `nora/` (`evals/`, `scripts/`).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: Where a store is doubled, not where a prompt is built. `nora/infra/` is scanned:
#: its two findings are on `RAW` with their reasons.
SKIPPED = ("nora/testing/",)

GUARDS = frozenset({"render_supported", "read_culture"})

#: Where each guard lives, and so the only module a call site may import it from.
CANONICAL = {
    "render_supported": ("nora/domain/withdrawal.py", "domain.withdrawal"),
    "read_culture": ("nora/language/culture_render.py", "language.culture_render"),
}

#: `(module, function, reader) -> (how many raw reads, why raw is safe)`. A fold's own
#: prior is the first reason, and it is the one the cascade's design asks for:
#: a withdrawn row "goes to its writer's next session for re-derivation"
#: (10-tiers.md §2 rule 7), and a writer that could not see it could not
#: re-derive it.
RAW = {
    ("nora/engagement/stages/debrief.py", "run", "read"): (1,
        "the tag fold's OWN prior position (`standing_prior`): read raw so the fold "
        "sees what it wrote and can supersede it"),
    ("nora/language/culture.py", "inputs", "head"): (1,
        "the culture fold's OWN prior account (`previous_account`): the fold must see "
        "what it wrote to supersede it. Its `<standing>` beside it IS guarded"),
    ("nora/language/culture.py", "commit", "head"): (1,
        "compares the head's body with the proposal to skip an identical write; "
        "renders nothing"),
    ("nora/subject/standing_fold.py", "read_fold_turn", "read_as_of"): (1,
        "the eval seam reads the prior as of a past instant to seed a sandbox; the "
        "fold under test reads it raw like production's own, and nothing renders here"),
    ("nora/subject/standing_fold.py", "fold", "read"): (2,
        "the eval seam checks the sandbox holds (or lacks) the prior it seeded, and "
        "reads the position the fold wrote back; renders nothing"),
    ("nora/agent/tools.py", "standing_read", "read_head"): (1,
        "the `standing_read` tool: `nora.agent.standing_read.render_standing` returns "
        "nothing for an unsupported head before it looks at anything else "
        "(`test_the_standing_read_tool_applies_withdrawal_first` below)"),
    ("nora/app/composition.py", "build_dependencies", "read_head"): (1,
        "builds `standing_of` for the Cognition session: its write verb reads the head "
        "to name the revision it replaces (the fold's own prior, a base for "
        "`standing_revise`); the session sees Standing only through `standing_read`"),
    ("nora/infra/sql/culture.py", "chain", "head"): (1,
        "the store's own history walk starts from its head; store internals, which "
        "return the row on purpose, and nothing here renders"),
    ("nora/infra/sql/snapshot.py", "snapshot", "read_as_of"): (1,
        "the eval snapshot reads the Standing revision as of a past instant to seed a "
        "sandbox; a revision carries no mark (only the head does), and nothing renders"),
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


def _statement(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.AST:
    while node in parents and not isinstance(node, ast.stmt):
        node = parents[node]
    return node


def _assigned_names(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> set[str]:
    """The names the statement holding `node` assigns, if it is an assignment."""
    stmt = _statement(node, parents)
    targets: list[ast.expr] = []
    if isinstance(stmt, ast.Assign):
        targets = list(stmt.targets)
    elif isinstance(stmt, ast.AnnAssign):
        targets = [stmt.target]
    return {n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)}


def _args(call: ast.Call) -> list[ast.expr]:
    return [*call.args, *(k.value for k in call.keywords)]


def _discarded(call: ast.Call, parents: dict[ast.AST, ast.AST]) -> bool:
    """The call's value is thrown away: a bare statement, awaited or not."""
    node: ast.AST = call
    while node in parents:
        parent = parents[node]
        if isinstance(parent, ast.Await):
            node = parent
            continue
        return isinstance(parent, ast.Expr)
    return False


def _guards_over(node: ast.AST, scope: ast.AST) -> list[ast.Call]:
    """The guard calls in `scope` that are given `node` among their arguments."""
    return [
        g for g in ast.walk(scope)
        if isinstance(g, ast.Call) and _guard_name(g)
        and any(node is n for a in _args(g) for n in ast.walk(a))
    ]


def _inside_guard_args(node: ast.AST, scope: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    return any(not _discarded(g, parents) for g in _guards_over(node, scope))


def _raw_use_after(guard: ast.Call, names: set[str], scope: ast.AST,
                   parents: dict[ast.AST, ast.AST]) -> bool:
    """A name the guard was given is read again after it, outside any guard, and
    the guard did not rebind it: the guarded value is not the one that is used."""
    rebound = _assigned_names(guard, parents)
    inside = {id(n) for a in _args(guard) for n in ast.walk(a)}
    start = (guard.lineno, guard.col_offset)
    return any(
        isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
        and n.id in names - rebound and id(n) not in inside
        and (n.lineno, n.col_offset) > start
        for n in ast.walk(scope)
    )


def _guarded(call: ast.Call, tree: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    scope = _enclosing(call, parents) or tree
    if _inside_guard_args(call, scope, parents):
        return True
    names = _assigned_names(call, parents)
    for guard in ast.walk(scope):
        if not (isinstance(guard, ast.Call) and _guard_name(guard)) or _discarded(guard, parents):
            continue
        given = {n.id for a in _args(guard) for n in ast.walk(a) if isinstance(n, ast.Name)}
        if names & given and not _raw_use_after(guard, names & given, scope, parents):
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
        if returned and callers and all(_inside_guard_args(c, tree, parents) for c in callers):
            return True
    return False


def unguarded(source: str) -> list[tuple[str, int, str]]:
    """`(function, line, reader)` for every read in `source` that does not reach
    a guard that is used."""
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


def not_the_guard(source: str, rel: str) -> list[str]:
    """Where `source` defines, rebinds or imports a guard from anywhere but its home."""
    out = []
    for node in ast.walk(ast.parse(source)):
        defined = []
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            defined = [node.name]
        elif isinstance(node, ast.Assign):
            defined = [n.id for t in node.targets for n in ast.walk(t) if isinstance(n, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined = [node.target.id]
        elif isinstance(node, ast.ImportFrom | ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                home = CANONICAL.get(alias.name)
                if bound in GUARDS and not (
                    isinstance(node, ast.ImportFrom) and alias.name == bound and home
                    and (node.module or "").endswith(home[1])
                ):
                    out.append(f"{bound} imported from {getattr(node, 'module', None)!r}")
        for name in defined:
            if name in GUARDS and CANONICAL[name][0] != rel:
                out.append(f"{name} defined or rebound here")
    return out


def _modules() -> list[str]:
    return sorted(
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / "nora").rglob("*.py")
        if "__pycache__" not in p.parts
        and not p.relative_to(ROOT).as_posix().startswith(SKIPPED)
    )


def _counts(source: str) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for function, _line, reader in unguarded(source):
        counts[(function, reader)] = counts.get((function, reader), 0) + 1
    return counts


def _tree_findings() -> dict[tuple[str, str, str], int]:
    found: dict[tuple[str, str, str], int] = {}
    for rel in _modules():
        for (function, reader), n in _counts((ROOT / rel).read_text(encoding="utf-8")).items():
            found[(rel, function, reader)] = n
    return found


def pin_problems(
    found: dict[tuple[str, str, str], int],
    raw: dict[tuple[str, str, str], tuple[int, str]],
) -> list[str]:
    """What is wrong between the raw reads found and the pins that name them."""
    out = [f"{key}: {n} raw read(s), not pinned" for key, n in sorted(found.items())
           if key not in raw]
    for key, (count, reason) in sorted(raw.items()):
        if found.get(key, 0) != count:
            out.append(f"{key}: pinned {count}, found {found.get(key, 0)}")
        if not reason.strip():
            out.append(f"{key}: no reason")
    return out


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
    "a guard whose result is discarded":
        "async def f(store):\n    p = await store.read(platform='a', conversation_id='b')\n"
        "    render_supported(p)\n    return p.body\n",
    "a guard around the read whose result is discarded":
        "async def f(store):\n    render_supported(await store.read(platform='a', "
        "conversation_id='b'))\n",
    "read_culture whose result is discarded":
        "async def f(c):\n    await read_culture(c.head(key='k'), stage='s')\n",
    "the guarded value is assigned, the raw one is used":
        "async def f(store):\n    p = await store.read(platform='a', conversation_id='b')\n"
        "    q = render_supported(p)\n    return p.body\n",
    "the raw value is used beside the guard":
        "async def f(store):\n    p = await store.read(platform='a', conversation_id='b')\n"
        "    return render_supported(p), p.body\n",
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
    "the guard rebinds the name, so the raw value is gone":
        "async def f(store):\n    p = await store.read(platform='a', conversation_id='b')\n"
        "    p = render_supported(p)\n    return p.body if p is not None else None\n",
    "the guarded value is the one used":
        "async def f(store):\n    p = await store.read(platform='a', conversation_id='b')\n"
        "    q = render_supported(p)\n    return q.body if q is not None else None\n",
    "the raw value is only looked at before the guard":
        "async def f(store):\n    p = await store.read(platform='a', conversation_id='b')\n"
        "    if p is None:\n        return None\n    return render_supported(p)\n",
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


#: A pinned function as the scan sees it, and what each way of breaking its pin
#: looks like. The pin is `(1, ...)` for one raw read.
_PINNED = "m.py", "inputs", "head"
_PIN = {_PINNED: (1, "the fold's own prior account")}
_ONE_RAW = "async def inputs(self):\n    p = await self._culture.head(key='k')\n    return p\n"


def _problems(source: str, raw=_PIN) -> list[str]:
    found = {("m.py", fn, reader): n for (fn, reader), n in _counts(source).items()}
    return pin_problems(found, raw)


def test_a_pinned_function_with_exactly_its_pinned_reads_is_clean():
    assert not _problems(_ONE_RAW)


def test_a_second_raw_read_of_the_same_reader_in_a_pinned_function_is_caught():
    two_head = _ONE_RAW + "    q = await self._culture.head(key='another-room')\n"
    assert _problems(two_head) == ["('m.py', 'inputs', 'head'): pinned 1, found 2"]
    # The reviewer's other case, in a function pinned for `read`.
    pin = {("m.py", "_changed_since", "read"): (1, "a change signal")}
    one = ("async def _changed_since(self):\n    p = await self._standing.read("
           "platform='a', conversation_id='b')\n")
    assert not _problems(one, pin)
    assert _problems(one + "    q = await self._standing.read(platform='c', "
                     "conversation_id='d')\n", pin) == [
        "('m.py', '_changed_since', 'read'): pinned 1, found 2"]


def test_a_second_raw_read_that_is_a_different_reader_is_caught():
    mixed = _ONE_RAW + "    q = await self._standing.read(platform='a', conversation_id='b')\n"
    assert _problems(mixed) == [
        "('m.py', 'inputs', 'read'): 1 raw read(s), not pinned"]


def test_a_pinned_read_that_has_gone_is_caught():
    gone = "async def inputs(self):\n    return None\n"
    assert _problems(gone) == ["('m.py', 'inputs', 'head'): pinned 1, found 0"]


def test_a_pinned_read_that_becomes_guarded_is_caught_as_stale():
    guarded = ("async def inputs(self):\n    return render_supported("
               "await self._culture.head(key='k'))\n")
    assert _problems(guarded) == ["('m.py', 'inputs', 'head'): pinned 1, found 0"]


def test_every_read_of_a_head_is_guarded_or_pinned_raw_with_its_reason():
    problems = pin_problems(_tree_findings(), RAW)
    assert not problems, (
        f"{problems}: a Standing or culture head is read and never passed through "
        "`render_supported` or `read_culture` (or the pin for it no longer matches): a "
        "head the withdrawal cascade marked `unsupported_at` renders in that prompt, and "
        "the model can carry it into a note or a fold. Read culture through "
        "`read_culture`; apply `render_supported` to a Standing head and USE its result; "
        "or, for a fold's OWN prior or a read that renders nothing, pin the "
        "(module, function, reader) in RAW with its count and the reason."
    )


#: What `not_the_guard` must flag, and what it must not.
SHADOWED = {
    "a local def": "def render_supported(found):\n    return found\n",
    "a local async def": "async def read_culture(head, **kw):\n    return await head\n",
    "an assignment": "render_supported = lambda found: found\n",
    "an annotated assignment": "read_culture: object = None\n",
    "an import from elsewhere": "from nora.util import render_supported\n",
    "a guard imported from the wrong home":
        "from nora.domain.withdrawal import read_culture\n",
    "a plain import of a module by the name": "import render_supported\n",
}
GENUINE = {
    "the real import": "from nora.domain.withdrawal import render_supported\n",
    "the real relative import": "from ..language.culture_render import read_culture\n",
    "an alias under another name (its reads then fail closed)":
        "from nora.domain.withdrawal import render_supported as keep\n",
    "an unrelated name": "def render_standing(x):\n    return x\n",
}


@pytest.mark.parametrize("shape", sorted(SHADOWED))
def test_the_scan_flags_a_guard_that_is_not_the_guard(shape: str):
    assert not_the_guard(SHADOWED[shape], "nora/x.py"), shape


@pytest.mark.parametrize("shape", sorted(GENUINE))
def test_the_scan_leaves_the_real_guard_alone(shape: str):
    assert not not_the_guard(GENUINE[shape], "nora/x.py"), shape


def test_a_guard_may_be_defined_in_its_own_module_only():
    source = "def render_supported(found):\n    return found\n"
    assert not not_the_guard(source, "nora/domain/withdrawal.py")
    assert not_the_guard(source, "nora/language/culture_render.py")


def test_no_module_defines_rebinds_or_imports_a_guard_from_elsewhere():
    offenders = {
        rel: found for rel in _modules()
        if (found := not_the_guard((ROOT / rel).read_text(encoding="utf-8"), rel))
    }
    assert not offenders, (
        f"{offenders}: `render_supported` and `read_culture` must be the functions in "
        "`nora.domain.withdrawal` and `nora.language.culture_render`, imported by those "
        "names; a local one with the same name would satisfy this test and guard nothing."
    )


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
        isinstance(r, ast.Return) and isinstance(r.value, ast.Call)
        and isinstance(r.value.func, ast.Name) and r.value.func.id == "render_supported"
        for r in ast.walk(read)
    ), "`read_culture` no longer returns what `render_supported` made of the head"
