"""Every collaborator await inside a held conversation slot is bounded.

The walk is over a DECLARED REGISTRY of slot-held entry points, and adding a
module to it is one line. What this file knows is that the entries below are
bounded; a slot-held path that is not registered is not checked, and the
registry is what a reviewer has to think about.

**Why bounding matters more here than tidiness.** `MediaJoin.enrich` catches
exceptions, not hangs. A wedged `media_objects` does not raise — it does not
answer — and the `finally: slots.release_and_dispatch(key)` cannot run
until the await returns. That is one conversation's rollup slot held
indefinitely, on a path nobody is watching.

**Derived, not listed.** Within each registered module the receiver set is
DERIVED — from what the slot-held functions are HANDED, or from what the class
was CONSTRUCTED with (`reasoning_strip`, where the store arrives through
`__init__` and is reached as `self._store`). A collaborator a registered
module gains is caught the moment it is awaited, rather than when somebody
remembers to extend a list. That derivation is the half that matters: a
hardcoded regex catches exactly what it was written against.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import typing
from typing import NamedTuple

from nora.agent import reasoning_strip

#: Not a store call. `client` is the MODEL, bounded by the `wait_for` around
#: the fold itself and covered separately in the timeout sum; `slots` is the
#: lock, and awaiting the release is the point rather than a risk. `metrics`
#: and `clock` are in-process. Named so each exclusion is a decision rather
#: than an oversight.
_NOT_A_STORE = {"client", "slots", "objective", "metrics", "clock"}


class _Module(NamedTuple):
    """One module whose awaits run inside a held conversation slot.

    `held` names the functions that run under the lock. `receivers_from` says
    where the collaborator NAMES are derived from — `"params"` for a module of
    functions handed their collaborators, `"init"` for a
    class constructed with them and reaching them as `self._<name>`
    (`reasoning_strip`). Neither spells the collaborators out.
    """

    name: str
    module: object
    held: frozenset[str]
    receivers_from: str


#: THE REGISTRY. Adding a module is one line; the entries are what a reviewer
#: has to think about, and a slot-held path that is not here is not checked.
#:
#: `flush` is `reasoning_strip`'s whole surface that touches a store, and its
#: callers await it with the slot held: `invoke.run`, `FirePath._invoke` and
#: the exits of `DreamerRuntime._sample`.
_MODULES: tuple[_Module, ...] = (
    _Module(
        "reasoning_strip", reasoning_strip, frozenset({"flush"}), "init",
    ),
)


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}


def _real_timeout(call: ast.Call) -> bool:
    """Whether a `wait_for` actually BOUNDS anything.

    `asyncio.wait_for(x, timeout=None)` waits forever — it is the documented
    way to say «no timeout» — so a scan that stopped at «is there a wait_for
    ancestor» would let a slot-deadlock fix be defeated by one word while its
    own gate stayed green.
    """
    args = [k.value for k in call.keywords if k.arg == "timeout"]
    if len(call.args) > 1:
        args.append(call.args[1])
    if not args:
        return False        # `wait_for` with no timeout at all
    return not any(
        isinstance(a, ast.Constant) and a.value is None for a in args
    )


def _in_wait_for(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, ast.Call):
            f = cur.func
            if ((isinstance(f, ast.Attribute) and f.attr == "wait_for")
                    or (isinstance(f, ast.Name) and f.id == "wait_for")):
                return _real_timeout(cur)
        if isinstance(cur, ast.AsyncFunctionDef | ast.FunctionDef):
            return False
        cur = parents.get(cur)
    return False


def _receivers(tree: ast.AST, entry: _Module) -> set[str]:
    """The collaborator NAMES this module reaches its stores through.

    `"params"` — what the slot-held functions are handed. `"init"` — what the
    class was constructed with, which it then holds as `self._<name>`; the
    parameter list is the derivation in both cases, so a module that gains a
    collaborator is covered without anyone editing this file.
    """
    names: set[str] = set()
    wanted = entry.held if entry.receivers_from == "params" else {"__init__"}
    for node in ast.walk(tree):
        if (isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
                and node.name in wanted):
            a = node.args
            names |= {p.arg for p in a.posonlyargs + a.args + a.kwonlyargs}
    return names - _NOT_A_STORE - {"self"}


def _store_fields(annotation: object) -> set[str]:
    """The dataclass field names on `annotation` whose OWN type is a `*Store`
    collaborator (`StandingStore`, `RecordStore`, …) — this repo's own naming
    convention for a store port (`nora.domain.ports`), so `wiring.store` is
    recognised as reaching a real store the same way
    `wiring.record_store.since_including_nora(...)` is, without hand-naming
    "wiring" or "store" literally. A field whose type is not store-shaped
    (`display_name: str`, `store_timeout_seconds: float`) contributes nothing,
    which a hand-written name list would have to get right by hand."""
    if not dataclasses.is_dataclass(annotation):
        return set()
    out: set[str] = set()
    for f in dataclasses.fields(annotation):
        t = f.type
        t_name = t if isinstance(t, str) else getattr(t, "__name__", "")
        if t_name.endswith("Store"):
            out.add(f.name)
    return out


def _container_receiver_fields(
    entry: _Module, receivers: set[str],
) -> dict[str, set[str]]:
    """`{receiver name: store-shaped field names}` for a `"params"`-derived
    module whose slot-held functions are handed a bundling object (`Wiring`)
    rather than individual collaborators. Empty for an `"init"`-derived
    module, and empty for a receiver whose annotation is not a dataclass
    (`Standing | None`, `Sequence[TranscriptRow]`)."""
    if entry.receivers_from != "params":
        return {}
    out: dict[str, set[str]] = {}
    for name in entry.held:
        fn = getattr(entry.module, name, None)
        if fn is None:
            continue
        try:
            hints = typing.get_type_hints(fn)
        except (NameError, TypeError):
            # An unresolved forward reference, or a `fn` that is not a
            # function at all — nothing this walk can derive fields from,
            # the same "contributes nothing" outcome as a plain-value
            # parameter.
            continue
        for pname, ptype in hints.items():
            if pname not in receivers:
                continue
            fields = _store_fields(ptype)
            if fields:
                out.setdefault(pname, set()).update(fields)
    return out


def _module_awaits(entry: _Module) -> list[tuple[str, bool]]:
    """`(receiver.method, bounded)` for every collaborator call awaited inside
    one module's slot-held functions."""
    tree = ast.parse(inspect.getsource(entry.module))  # type: ignore[arg-type]
    parents = _parents(tree)
    receivers = _receivers(tree, entry)
    container_fields = _container_receiver_fields(entry, receivers)

    # For an `init`-derived module the call has to be INSIDE a held function,
    # because `self._store` is reachable from every method on the class. For a
    # `params`-derived one the receiver name itself is only in scope there.
    held_bodies: list[ast.AST] = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
        and n.name in entry.held
    ]

    def _inside_held(node: ast.AST) -> bool:
        if entry.receivers_from == "params":
            return True
        cur: ast.AST | None = node
        while cur is not None:
            if cur in held_bodies:
                return True
            cur = parents.get(cur)
        return False

    out: list[tuple[str, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id not in entry.held:
            # A write verb such as `standing_revise` is a plain function, not a
            # method on a collaborator: it takes `store=` as a keyword and makes
            # its own bounded store call inside itself. A call shaped
            # `f.value.id.attr` cannot see it — it is a bare `Name` call
            # (`standing_revise(store=wiring.store, ...)`) — so a collaborator
            # handed as a KEYWORD ARGUMENT to an awaited bare-name call counts
            # too, the same rule `test_the_slot_hold_counts_every_store_await_
            # the_stage_makes` (`tests/unit/test_debrief.py`) applies to the
            # debrief stage's own walk.
            if not _inside_held(node):
                continue
            bounded = _in_wait_for(node, parents)
            if not (bounded or isinstance(parents.get(node), ast.Await)):
                continue
            for kw in node.keywords:
                # Two shapes of "the receiver is a keyword argument": a bare
                # name (`store=store`, debrief's own param) and one level of
                # attribute access on a bundling object (`store=wiring.store`,
                # a module whose collaborators arrive bundled in `Wiring`, not
                # as individual parameters). The second shape is checked
                # against `container_fields`, the store-shaped fields `Wiring`
                # actually declares, not against "is the base name a receiver"
                # alone — that weaker check would match
                # `read_culture(timeout_seconds=wiring.store_timeout_seconds)`,
                # a plain float, as though it were a collaborator.
                if isinstance(kw.value, ast.Name) and kw.value.id in receivers:
                    out.append((f"{f.id}({kw.arg}={kw.value.id})", bounded))
                    break
                if (isinstance(kw.value, ast.Attribute)
                        and isinstance(kw.value.value, ast.Name)
                        and kw.value.attr in container_fields.get(
                            kw.value.value.id, set())):
                    out.append((
                        f"{f.id}({kw.arg}={kw.value.value.id}.{kw.value.attr})",
                        bounded,
                    ))
                    break
            continue
        if not isinstance(f, ast.Attribute):
            continue
        # `store.read(...)`, `wiring.record_store.since(...)` and
        # `self._store.record_observation(...)` are all collaborator calls; the
        # second and third are how those two modules name theirs.
        if isinstance(f.value, ast.Name):
            recv = f.value.id
        elif (isinstance(f.value, ast.Attribute)
              and isinstance(f.value.value, ast.Name)
              and (f.value.value.id in receivers
                   or (f.value.value.id == "self"
                       and f.value.attr.lstrip("_") in receivers))):
            recv = f.value.attr.lstrip("_")
        else:
            continue
        if recv in _NOT_A_STORE or (
            isinstance(f.value, ast.Name) and recv not in receivers
        ):
            continue
        if not _inside_held(node):
            continue
        bounded = _in_wait_for(node, parents)
        if not (bounded or isinstance(parents.get(node), ast.Await)):
            continue
        out.append((f"{recv}.{f.attr}", bounded))
    return out


def _slot_held_awaits() -> list[tuple[str, bool]]:
    """Every registered module's slot-held collaborator awaits, together."""
    return [call for entry in _MODULES for call in _module_awaits(entry)]


def test_every_slot_held_collaborator_await_is_bounded() -> None:
    """A hang is not an exception. Every one of these runs while the
    conversation's slot is held and `release_and_dispatch` cannot run until it
    returns, so an unbounded one is a room that stops answering."""
    unbounded = [name for name, bounded in _slot_held_awaits() if not bounded]

    assert not unbounded, (
        f"these collaborator awaits run inside the held conversation slot "
        f"with no `asyncio.wait_for`, or with `timeout=None`: {unbounded}. "
        f"`enrich`/`read`/`write` catch exceptions, not hangs — the slot is "
        f"held until they return."
    )


def test_the_validator_can_see_the_calls_it_is_checking() -> None:
    """THE POSITIVE CONTROL: a walk that found nothing would report «no
    unbounded awaits» with total confidence.

    The registry holds one module (DEC-277), so the control names the one
    await that remains: the reasoning-strip row write, whose absence would look
    like a pass."""
    found = _slot_held_awaits()

    assert len(found) >= 1, f"the walk went thin — it found only {found}"
    names = {name for name, _ in found}
    assert "store.record_observation" in names, (
        "the reasoning-strip row write is no longer awaited inside `flush`, or "
        "the `init`-derived receiver walk cannot see it — which is exactly the "
        "state this gate was in when that write shipped unbounded (#325)"
    )


def test_every_registered_module_contributes_at_least_one_await() -> None:
    """A registry entry that finds nothing is an entry that checks nothing, and
    it would read as a pass.

    This is the control on the REGISTRY rather than on the walk: a module
    renamed, a held function renamed, or a collaborator that stopped arriving
    through `__init__` all turn a real check into a silent one, and the gate
    would go green having verified a property of the empty set."""
    for entry in _MODULES:
        assert _module_awaits(entry), (
            f"`{entry.name}` is registered as holding a conversation slot but "
            f"the walk found no collaborator await in {sorted(entry.held)}. "
            f"Either the module no longer belongs here, or the derivation "
            f"({entry.receivers_from}) has stopped matching it — and a "
            f"registry entry that matches nothing reports «bounded» about "
            f"nothing."
        )


def test_a_wait_for_with_no_timeout_does_not_count_as_bounded() -> None:
    """The gate's own instrument, checked before its output is read.

    `asyncio.wait_for(x, timeout=None)` waits forever, and a scan that stopped
    at «is there a wait_for ancestor» would report it as bounded, so the bound
    could be defeated by one word with this file still green. Driven through
    the same predicate the walk uses, on synthetic source, because the
    production text has (correctly) no such call to find."""
    def _first_call(src: str) -> ast.Call:
        tree = ast.parse(src)
        return next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "wait_for"
        )

    assert _real_timeout(_first_call("asyncio.wait_for(x(), timeout=5.0)"))
    assert _real_timeout(_first_call("asyncio.wait_for(x(), timeout=t)"))
    assert _real_timeout(_first_call("asyncio.wait_for(x(), 5.0)"))
    assert not _real_timeout(_first_call("asyncio.wait_for(x(), timeout=None)"))
    assert not _real_timeout(_first_call("asyncio.wait_for(x(), None)"))
    assert not _real_timeout(_first_call("asyncio.wait_for(x())"))
