"""Every Dreamer adaptor's `store_calls_per_tick` is the number of awaited collaborator calls
its own source actually holds.

`DreamerRuntime` bounds `adaptor.inputs` and `adaptor.commit` at `store_calls_per_tick` x
`[dreamer] store_timeout_seconds`. That bound is only as honest as the constant, a
hand-written integer in a module nobody re-reads, which is why the number lives in the
adaptor's module rather than in the runtime.
`tests/unit/test_debrief.py::test_the_slot_hold_counts_every_store_await_the_stage_makes`
does this for the standing fold; this is the same instrument pointed at every adaptor.

**Derived, never listed, on three axes at once**, because each hand-written list is a
place to drift:

1. **The adaptors** come from walking the whole `nora` package for classes declaring the
   port's own fields. Nothing in `DreamAdaptor` says an adaptor must live in one package,
   and the real registry is the composition root's `dream_adaptors`.
   `test_the_adaptors_are_derived_and_not_empty` is the run-that-ran control.
2. **The collaborators** come from the `self.X = <param>` ASSIGNMENTS in `__init__`, not
   from the parameter names. An adaptor holding `candidates` as `self._db` would otherwise
   have every call on it invisible. Deriving from the wrong half of the constructor is a
   hardcoded list wearing a derivation.
3. **The count** is of awaited calls in the AST, not of `wait_for` wrappers. The defect to
   catch is a store call with no `wait_for` at all, which a count of wrappers cannot see.

**What "awaited" means here.** The walk climbs the whole expression chain from a call to
an `Await` or an `AsyncFor`, so it sees `await self._notes.read(...)`, calls inside
`await asyncio.gather(...)`, and `async for ... in self._record.stream()`. The three
forms it cannot follow (a local alias, a module-level helper, a bare `gather` over stored
coroutines) are REFUSED OUTRIGHT by
`test_no_collaborator_is_reached_by_a_route_the_counter_cannot_follow`, rather than scored
zero. A walk that cannot see a construct must say so; scoring it zero is how «no unbounded
awaits» gets reported with total confidence.

**SITES, not dynamic calls.** A call inside a per-item loop runs once per item; the config
value is an allowance per site, sized in `DEFAULT_DREAM_STORE_TIMEOUT_SECONDS` against the
widest degraded fan-out rather than against one healthy call.

**`accept` must hold none**, and that is asserted rather than assumed: the runtime bounds
`inputs` and `commit` and does NOT bound `accept`, which is a typed parse over items
already in memory. An adaptor that awaited a store there would be unbounded, with a green
constant.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
import textwrap
import types
import typing

import pytest

import nora
from nora.domain.ports import DREAM_MAX_STORE_CALLS_PER_TICK

#: Not a store. `metrics` is a counter sink (never awaited anyway) and the clock callables
#: are arithmetic. Named so each exclusion is a decision rather than an oversight, and
#: deliberately NOT a list of the things that ARE stores, which is the direction that
#: rots.
_NOT_A_STORE = frozenset({"metrics", "clock", "today", "ai_peer", "timezone"})

#: Packages that must not be imported to find an adaptor. `nora.app` is the composition
#: root (importing it drags the whole world in) and no adaptor lives there;
#: `nora.infra.sql.migrations` is alembic's own runner and raises `AttributeError` outside
#: an alembic run. Each exclusion is a PREFIX and a decision; the default is to import, so
#: a new package is walked without anyone remembering to add it.
_SKIP_PREFIXES = (
    "nora.app.", "nora.__main__", "nora.infra.sql.migrations",
)


def _adaptor_classes():
    """(module, class) for every `DreamAdaptor` implementation in `nora`,
    found by the port's own declared fields.

    No try/except around the import: a module that will not import is a red
    test, not a skipped one — swallowing it is how an adaptor becomes
    invisible to the gate written to police adaptors.
    """
    seen: set[type] = set()
    for info in pkgutil.walk_packages(nora.__path__, f"{nora.__name__}."):
        if info.name.startswith(_SKIP_PREFIXES):
            continue
        module = importlib.import_module(info.name)
        for obj in vars(module).values():
            if (
                isinstance(obj, type)
                and obj.__module__ == module.__name__
                and obj not in seen
                and isinstance(getattr(obj, "name", None), str)
                and isinstance(getattr(obj, "envelope_key", None), str)
                and callable(getattr(obj, "inputs", None))
                and callable(getattr(obj, "commit", None))
            ):
                seen.add(obj)
                yield module, obj


def _class_tree(cls: type) -> ast.AST:
    return ast.parse(textwrap.dedent(inspect.getsource(cls)))


def _receivers(cls: type) -> set[str]:
    """The collaborator ATTRIBUTES — `self._notes`, `self._db` — read off the `self.X =
    <param>` assignments in `__init__`.

    Derived from the ASSIGNMENTS rather than from the parameter names: `def __init__(self,
    *, candidates)` storing `self._db = candidates` must put `self._db` in the set, or
    every await on it is invisible.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(cls.__init__)))
    params: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            a = node.args
            params |= {
                p.arg for p in a.posonlyargs + a.args + a.kwonlyargs
                if p.arg != "self" and p.arg not in _NOT_A_STORE
            }
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                continue
            # `self._x = <param>` and `self._x = <param> if ... else ...` /
            # `<param> or Default()` — the defaulting forms this repo uses.
            if params & {
                n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)
            }:
                out.add(target.attr)
    return out


def _collaborators(cls: type) -> set[str]:
    """The subset of `_receivers` this class ever calls a METHOD on — the
    object-shaped constructor arguments, as opposed to the scalar ones.

    Derived rather than listed, so a store added to `__init__` joins by being
    used and a cap stays out by not being."""
    receivers = _receivers(cls)
    tree = _class_tree(cls)
    out: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
            and node.value.attr in receivers
        ):
            out.add(node.value.attr)
    return out


def _awaited(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """Whether `node` is consumed by an `await` or an `async for`.

    Climbs the whole expression chain rather than checking the direct parent:
    `await asyncio.gather(self._a.x(), self._b.y())` puts two store calls two
    and three levels under the `Await`, and v1 scored both zero. Stops at a
    function boundary, so a coroutine built in one function and awaited in
    another is NOT counted here — that route is refused outright by its own
    test rather than silently scored.
    """
    cur: ast.AST | None = parents.get(node)
    while cur is not None:
        if isinstance(cur, ast.Await | ast.AsyncFor):
            return True
        if isinstance(cur, ast.AsyncFunctionDef | ast.FunctionDef | ast.Lambda):
            return False
        cur = parents.get(cur)
    return False


def _enclosing(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str | None:
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, ast.AsyncFunctionDef | ast.FunctionDef):
            return cur.name
        cur = parents.get(cur)
    return None


def _awaited_calls(cls: type, *, only: frozenset[str] | None = None):
    """`(function, receiver.method, lineno)` for every AWAITED call on a
    collaborator attribute, anywhere on the class.

    Awaited, because that is what a store call is — `self._metrics.inc` and
    `self._today()` are calls on this object too and are neither awaited nor
    bounded. `only` narrows to a named set of entry points and everything they
    call; `None` reads the whole class, which is what the declared constant
    covers.
    """
    receivers = _receivers(cls)
    tree = _class_tree(cls)
    parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
    reachable = _reachable_from(tree, only) if only is not None else None

    out: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Attribute)):
            continue
        recv = f.value
        if not (isinstance(recv.value, ast.Name) and recv.value.id == "self"):
            continue
        if recv.attr not in receivers:
            continue
        if not _awaited(node, parents):
            continue
        owner = _enclosing(node, parents)
        if reachable is not None and owner not in reachable:
            continue
        out.append((owner or "?", f"self.{recv.attr}.{f.attr}", node.lineno))
    return out


def _is_scalar_hint(hint: object) -> bool:
    """`int`, `float`, `str`, `bool`, a `Literal[...]`, or an `Optional` /
    union of those — a value read, never a store."""
    if hint in (int, float, str, bool, type(None)):
        return True
    origin = typing.get_origin(hint)
    if origin is typing.Literal:
        return True
    if origin in (typing.Union, types.UnionType):
        return all(_is_scalar_hint(a) for a in typing.get_args(hint))
    return False


def _scalar_attrs(cls: type) -> set[str]:
    """`self.X` attributes that hold a VALUE, not a store: assigned from an
    expression whose only constructor parameters are scalar-typed
    (`max_expired_per_tick: int`, `mode: MergeMode`, and
    `self._pass_clock = pass_clock_name(mode)` derived from one). Read off the
    resolved type hints, so a store stays in scope however it is named."""
    try:
        hints = typing.get_type_hints(cls.__init__)
    except (NameError, TypeError):
        # Unresolvable hints scope NOTHING out, which flags more rather than
        # less: the safe direction for a refusal gate.
        return set()
    params = set(inspect.signature(cls.__init__).parameters) - {"self"}
    scalar = {n for n in params if n in hints and _is_scalar_hint(hints[n])}
    tree = ast.parse(textwrap.dedent(inspect.getsource(cls.__init__)))
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        used = {
            n.id for n in ast.walk(node.value)
            if isinstance(n, ast.Name) and n.id in params
        }
        if not used or not used <= scalar:
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                out.add(target.attr)
    return out


def _unfollowable_routes(cls: type) -> list[str]:
    """Places a collaborator escapes the counter's sight: bound to a local
    name, or handed to a call as an ARGUMENT rather than being the receiver.

    Both are legal Python and both make the count a guess. Refused rather than
    approximated — `test_slot_held_awaits_are_bounded`'s own lesson is that a
    walk which cannot see a construct must say so.

    Narrowed to the COLLABORATOR attributes — the ones this class ever calls a
    method ON. `__init__` also takes scalars (`history_limit`,
    `max_expired_per_tick`), and `self._history_limit` handed to a call is an
    int being read, not a store escaping: flagging those made the rule noisy
    on three correct lines, and a noisy gate is a disabled gate.
    """
    receivers = _collaborators(cls)
    # A constructor attribute handed WHOLE into an AWAITED call is a store escaping too,
    # even when the class never calls a method on it itself; that is how it becomes
    # invisible to `_collaborators`. An int handed to an awaited call is rare enough to be
    # worth the noise.
    awaited_receivers = _receivers(cls) - _scalar_attrs(cls)
    tree = _class_tree(cls)
    parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
    found: list[str] = []

    for node in ast.walk(tree):
        # `store = self._notes` — an alias the counter cannot follow.
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                v = node.value
                if (
                    isinstance(v, ast.Attribute)
                    and isinstance(v.value, ast.Name)
                    and v.value.id == "self"
                    and v.attr in receivers
                ):
                    found.append(
                        f"{_enclosing(node, parents)}: "
                        f"{target.id} = self.{v.attr} (line {node.lineno})"
                    )
        # `_helper(self._record)` — the collaborator handed away whole.
        if isinstance(node, ast.Call):
            watched = (
                receivers | awaited_receivers
                if _awaited(node, parents) else receivers
            )
            for arg in node.args + [k.value for k in node.keywords]:
                if (
                    isinstance(arg, ast.Attribute)
                    and isinstance(arg.value, ast.Name)
                    and arg.value.id == "self"
                    and arg.attr in watched
                ):
                    found.append(
                        f"{_enclosing(node, parents)}: "
                        f"self.{arg.attr} passed to a call (line {node.lineno})"
                    )
    return found


def _reachable_from(tree: ast.AST, entries: frozenset[str]) -> set[str]:
    """`entries` plus every method on this class they call, transitively —
    the same one-level-deep-then-repeat walk `test_objective_parser_parity`
    uses, because a tier's real work is always one `_settle`/`_gather` down
    from the port method."""
    own = {
        n.name: n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
    }
    seen = {e for e in entries if e in own}
    frontier = list(seen)
    while frontier:
        fn = own[frontier.pop()]
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if (
                isinstance(f, ast.Attribute)
                and isinstance(f.value, ast.Name)
                and f.value.id == "self"
                and f.attr in own
                and f.attr not in seen
            ):
                seen.add(f.attr)
                frontier.append(f.attr)
    return seen


ADAPTORS = list(_adaptor_classes())
IDS = [cls.__name__ for _, cls in ADAPTORS]

#: One real `receiver.method` per adaptor that the walk MUST find. The positive control,
#: and it is not optional: every assertion below is a comparison between two numbers this
#: file computes, so a walk that found nothing would report «declared 0, counted 0», or,
#: once a constant moved, a failure message pointing at the wrong thing. This is
#: `test_slot_held_awaits_are_bounded.py::test_the_validator_can_see_the_calls_it_is_checking`'s
#: job.
_MUST_SEE: dict[str, frozenset[str]] = {
    "NoteCandidateAdaptor": frozenset({
        "self._notes.standing", "self._notes.propose",
        "self._candidates.record_create_candidate",
    }),
    "NoteCuratorAdaptor": frozenset({
        "self._notes.anchored_before", "self._notes.supersede",
        "self._notes.record_wake", "self._record.recent",
    }),
    "ProactiveAdaptor": frozenset({
        "self._notes.standing", "self._notes.record_wake",
        "self._clock_store.record_fire", "self._reminders.history",
    }),
    # The SMALLEST entry here, and that is the tier rather than an oversight: culture
    # reads one thing (the account written last time) and writes one thing (the next
    # account). `head` appears at two sites, once in `inputs` to render the previous
    # account, once in `commit` to refuse a byte-identical one, and the site count, not
    # the call count, is what `STORE_CALLS_PER_TICK` is sized against.
    "CultureAdaptor": frozenset({
        "self._culture.head", "self._culture.append",
    }),
    # A person's session: its own account and the unit's room cultures in, one account
    # out.
    "CharacterPersonAdaptor": frozenset({
        "self._culture.head", "self._culture.append",
    }),
    "CharacterNoraAdaptor": frozenset({
        "self._culture.head", "self._culture.append",
    }),
    "CharacterUnitAdaptor": frozenset({
        "self._culture.head", "self._culture.append",
    }),
    # A person's Persona session: the account (revision, lines, pending `told` lines),
    # the Character head and the rows it cited, the ties and the rooms' cultures in; one
    # judged write out, in `commit` (its verbs only stage).
    "PersonaPersonAdaptor": frozenset({
        "self._persona.account_state", "self._persona.account_heads",
        "self._persona.pending_told", "self._culture.head", "self._reads.rows_by_id",
        "self._reads.tie_totals", "self._persona.persona_write",
    }),
    # The verdict judge (45 §3): the window read in `inputs`; the docket
    # re-read, the Rollup read (the code path) and the verdict writes in
    # `commit`.
    "PersonaVerdictAdaptor": frozenset({
        "self._store.due_hypotheses", "self._store.record_verdict",
        "self._reads.windowed", "self._rollup.read",
    }),
    # A collective's Persona session (a room's, the unit's, Nora's): the
    # account, the previous prose head and the rooms' cultures, the cited
    # rows in; the one write out is the parent `PersonaPersonAdaptor`'s
    # `commit`, counted there.
    "PersonaCollectiveAdaptor": frozenset({
        "self._persona.account_state", "self._persona.account_heads",
        "self._culture.head", "self._reads.rows_by_id",
    }),
    # Lessons: the held lessons in, and the horizon's retirements out. Its verbs write
    # through the book during the loop, outside `inputs`/`commit`, and are not awaited in
    # this class.
    "LessonsAdaptor": frozenset({
        "self._book.active", "self._book.retire",
    }),
    # note-merge: the change check's two reads, the fold's transaction, and the stamp that
    # must come last.
    "NoteMergeAdaptor": frozenset({
        "self._clock_store.last_fired", "self._notes.revision_chain",
        "self._notes.merge_into", "self._clock_store.record_fire",
    }),
}


def test_the_adaptors_are_derived_and_not_empty():
    """The run-that-ran control for the derivation itself. An empty or
    silently-shrinking list makes every parametrized test below vacuous, and a
    hand-written one is the hand-copied vocabulary this file exists to
    refuse."""
    assert len(ADAPTORS) >= 1, IDS


@pytest.mark.parametrize(("_module", "cls"), ADAPTORS, ids=IDS)
def test_the_walk_can_see_the_calls_it_is_counting(_module, cls):
    """**The positive control on the WALK**, which the adaptor list above does
    not give: every test here compares two numbers computed by this file, so a
    walk that saw nothing would agree with a constant of 0 and report success.

    `_MUST_SEE` is hand-written ON PURPOSE — it is the one place a list is the
    right instrument, because its job is to disagree with the derivation.
    An adaptor with no entry fails here rather than joining silently."""
    assert cls.__name__ in _MUST_SEE, (
        f"{cls.__name__} has no _MUST_SEE entry: the walk that counts its "
        f"store calls has no positive control, so a blind walk would pass"
    )
    seen = {name for _, name, _ in _awaited_calls(cls)}
    missing = _MUST_SEE[cls.__name__] - seen
    assert not missing, (
        f"the walk did not find {sorted(missing)} in {cls.__name__} — it is "
        f"not measuring what it claims to measure. Saw: {sorted(seen)}"
    )


@pytest.mark.parametrize(("_module", "cls"), ADAPTORS, ids=IDS)
def test_the_adaptor_declares_how_many_store_calls_it_holds(_module, cls):
    """The port field is REQUIRED, not defaulted — a default would silently
    fit the first adaptor that never thought about its own hold, which is the
    failure the field exists to make impossible.

    `type(...) is int`, not `isinstance`: `isinstance(True, int)` is True in
    Python and `True >= 1`, so a `store_calls_per_tick = True` would declare a
    bound of one allowance and read as a considered number."""
    declared = getattr(cls, "store_calls_per_tick", None)
    assert type(declared) is int, (
        f"{cls.__name__} declares store_calls_per_tick={declared!r}; "
        f"DreamerRuntime multiplies it to bound inputs and commit (issue #60)"
    )
    assert declared >= 1


@pytest.mark.parametrize(("_module", "cls"), ADAPTORS, ids=IDS)
def test_no_adaptor_declares_more_store_calls_than_the_wall_clock_assumes(
    _module, cls,
):
    """`[dreamer] max_unit_hold_seconds`'s startup gate (`nora.config`) proves its default
    against `DREAM_MAX_STORE_CALLS_PER_TICK` rather than against a live walk of every
    adaptor, because `nora.config` may not import `nora.language`. That proof is only as
    honest as this test: an adaptor declaring MORE than the ceiling the gate assumes would
    make its arithmetic wrong while staying green everywhere else."""
    declared = cls.store_calls_per_tick
    assert declared <= DREAM_MAX_STORE_CALLS_PER_TICK, (
        f"{cls.__name__} declares store_calls_per_tick={declared}, above "
        f"DREAM_MAX_STORE_CALLS_PER_TICK={DREAM_MAX_STORE_CALLS_PER_TICK} "
        f"(nora/domain/ports.py). Raise the ceiling AND re-check [dreamer] "
        f"max_unit_hold_seconds's startup gate together — it assumes every "
        f"adaptor fits under this number."
    )


@pytest.mark.parametrize(("_module", "cls"), ADAPTORS, ids=IDS)
def test_the_declared_count_is_the_count_the_source_holds(_module, cls):
    """**This is the assertion that makes the bound honest.** Adding a store call without
    moving the constant reddens here rather than silently widening the hold; a
    hand-maintained count drifts."""
    calls = _awaited_calls(cls)
    named = [name for _, name, _ in calls]
    assert len(calls) == cls.store_calls_per_tick, (
        f"{cls.__name__} awaits {len(calls)} collaborator calls {named} but "
        f"declares store_calls_per_tick = {cls.store_calls_per_tick}. "
        f"DreamerRuntime multiplies that number by [dreamer] "
        f"store_timeout_seconds to bound inputs and commit, so a disagreement "
        f"means the declared hold is not the real one."
    )


@pytest.mark.parametrize(("_module", "cls"), ADAPTORS, ids=IDS)
def test_no_collaborator_is_reached_by_a_route_the_counter_cannot_follow(
    _module, cls,
):
    """A store bound to a local name, or handed to a function as an argument, leaves the
    counter's sight, and the counter's answer is then a guess wearing a number.

    REFUSED rather than approximated: store awaits reached by exactly these routes would
    score low and pass every other test in this file. The rule is the sibling gate's: a
    walk that cannot see a construct must SAY so."""
    routes = _unfollowable_routes(cls)
    assert not routes, (
        f"{cls.__name__} reaches a collaborator by a route the store-call "
        f"counter cannot follow: {routes}. Call it on `self.<attr>` directly, "
        f"or the declared bound is not measuring this adaptor."
    )


@pytest.mark.parametrize(("_module", "cls"), ADAPTORS, ids=IDS)
def test_accept_awaits_no_collaborator(_module, cls):
    """`accept` is the ONE port method the runtime does not bound: it is a typed parse over
    items already in memory, between the screen and the commit. A store awaited there
    would be unbounded with the constant still green."""
    leaked = _awaited_calls(cls, only=frozenset({"accept"}))
    assert not leaked, (
        f"{cls.__name__}.accept awaits {[n for _, n, _ in leaked]}. "
        f"DreamerRuntime bounds inputs and commit and not this — either bound "
        f"it too, or move the read into inputs where the bound already is."
    )


@pytest.mark.parametrize(("_module", "cls"), ADAPTORS, ids=IDS)
def test_every_awaited_collaborator_call_is_reachable_from_a_bounded_phase(
    _module, cls,
):
    """The other direction, and the one that keeps the count from being
    satisfied by dead code: every awaited store call the class holds must be
    reachable from `inputs` or `commit` — the two the runtime actually wraps.
    A call sitting in a method nobody on those paths reaches is counted into
    the bound while running outside it.

    Both sides come from the same walk, so this cannot catch what the walk
    cannot see — which is what the two tests above are for."""
    everything = {name for _, name, _ in _awaited_calls(cls)}
    bounded = {
        name for _, name, _ in
        _awaited_calls(cls, only=frozenset({"inputs", "commit"}))
    }
    assert everything == bounded, (
        f"{cls.__name__} awaits {sorted(everything - bounded)} outside the "
        f"inputs/commit paths DreamerRuntime bounds"
    )


# ---------------------------------------------------------------------------
# the walk's own controls — the forms no shipped adaptor uses YET
# ---------------------------------------------------------------------------
#
# `_MUST_SEE` above proves the walk sees the calls the shipped adaptors actually make, and
# every one of those is a plain `await self._x.y(...)`. It says nothing about the other
# forms, which cannot be proven against production source because production does not use
# them: the gap would open on the day somebody wrote one, which is the day the gate is
# needed.
#
# So the walk is exercised against a class written to hold exactly those forms. Reverting
# `_awaited` to a direct-parent check, or `_receivers` to parameter names, reddens these
# and nothing else.


class _EscapeShapes:
    """Not an adaptor — a specimen. Five collaborator awaits, none of them the plain shape,
    plus the two routes the counter refuses outright."""

    def __init__(self, *, notes, record, candidates, history_limit=5):
        self._notes = notes
        self._record = record
        # The v1 killer: held under a name that is not its parameter's.
        self._db = candidates
        # A scalar. It must NOT become a receiver, or the refusal below goes
        # noisy on three correct lines in the shipped adaptors.
        self._history_limit = history_limit

    async def inputs(self, *, key, batch):
        import asyncio
        a, b = await asyncio.gather(
            self._notes.standing(unit=key),
            self._db.pending(unit=key),
        )
        async for _row in self._record.stream(key):
            break
        return f"{a}{b}"

    async def commit(self, *, key, proposals, now_ms):
        return await self._db.write(key=key)

    async def leaks(self, *, key):
        store = self._notes            # an alias the counter cannot follow
        _helper(self._record)          # handed away whole
        return await store.read(key)


def _helper(_store):                   # part of the specimen, not a helper
    return None


def test_the_walk_follows_gather_async_for_and_a_renamed_attribute():
    """The walk follows `gather` arguments and the `async for`, and reads receivers off
    `__init__`'s assignments rather than its PARAMETER names, so `self._db` is a
    collaborator. Reverting either to a direct-parent `Await` check or parameter names
    would score the five real store awaits as 1."""
    seen = {name for _, name, _ in _awaited_calls(_EscapeShapes)}

    assert seen == {
        "self._notes.standing",      # inside asyncio.gather(...)
        "self._db.pending",          # inside asyncio.gather(...), renamed attr
        "self._record.stream",       # async for
        "self._db.write",            # plain, renamed attr
    }, sorted(seen)


def test_a_scalar_constructor_argument_is_not_a_collaborator():
    """`_collaborators` is what the refusal below reads, and it must not sweep up
    `self._history_limit`: an int handed to a call is a number being read, not a store
    escaping. Flagging those makes the rule fire on correct lines in `ProactiveAdaptor`
    and `NoteCuratorAdaptor`, and a noisy gate is a disabled gate."""
    assert "_history_limit" in _receivers(_EscapeShapes)
    assert "_history_limit" not in _collaborators(_EscapeShapes)
    assert {"_notes", "_record", "_db"} <= _collaborators(_EscapeShapes)


def test_the_unfollowable_routes_are_refused_rather_than_scored_zero():
    """The alias and the hand-off cannot be followed by any walk this cheap.
    The rule is the sibling gate's: say so, rather than return a number that
    looks like an answer."""
    routes = _unfollowable_routes(_EscapeShapes)

    assert any("store = self._notes" in r for r in routes), routes
    assert any("self._record passed to a call" in r for r in routes), routes
