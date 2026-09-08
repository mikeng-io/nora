"""Every collaborator await inside a held conversation slot is bounded.

`nora/engagement/stages/debrief.py` has had this validator, derived from its
own parameter names, since the slot-hold ceiling was found understated by 2x.
`nora/ingest/stages/standing_event.py` runs the SAME fold, holds the SAME
`ConversationSlots` lock, and had none — so when the media join (issue #116)
landed in both, the debrief's version was caught and bounded in the same commit
and this one shipped as the only unbounded await on a slot-held path in the
repo. Two independent reviewers found it, from two directions.

**Why bounding matters more here than tidiness.** `MediaJoin.enrich` catches
exceptions, not hangs. A wedged `media_objects` does not raise — it does not
answer — and the `finally: slots.release_and_dispatch(key)` below cannot run
until the await returns. That is one conversation's rollup slot held
indefinitely, on a path nobody is watching.

**Derived, not listed.** The receiver set comes from what `_event_fold` and
`_fold_batch` are HANDED, so a collaborator this module gains is caught the
moment it is awaited rather than when somebody remembers to extend a list. That
derivation is the half that matters: the debrief's own v2 was a hardcoded regex
and caught exactly what it had been written against.
"""

from __future__ import annotations

import ast
import inspect

from nora.ingest.stages import standing_event

#: The functions that run while the slot is held. `_check` is included because
#: it runs before the slot is taken but on the same listener path, and its own
#: reads are already bounded — a regression there is the same shape.
_HELD = {"_event_fold", "_fold_batch", "_check"}

#: Not a store call. `client` is the MODEL, bounded by the `wait_for` around
#: the fold itself and covered separately in the timeout sum; `slots` is the
#: lock, and awaiting the release is the point rather than a risk. Named so
#: each exclusion is a decision rather than an oversight.
_NOT_A_STORE = {"client", "slots", "objective"}


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}


def _real_timeout(call: ast.Call) -> bool:
    """Whether a `wait_for` actually BOUNDS anything.

    `asyncio.wait_for(x, timeout=None)` waits forever — it is the documented
    way to say «no timeout» — so a scan that stopped at «is there a wait_for
    ancestor» let round three's slot-deadlock fix be defeated by one word while
    its own gate stayed green. That is exactly the failure the fix's docstring
    describes, reintroduced through the instrument written to prevent it.
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


def _slot_held_awaits() -> list[tuple[str, bool]]:
    """`(receiver.method, bounded)` for every collaborator call awaited inside
    the slot-held functions — including through `wiring.<name>`, which is how
    this module reaches all of them."""
    tree = ast.parse(inspect.getsource(standing_event))
    parents = _parents(tree)

    receivers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in _HELD:
            a = node.args
            receivers |= {p.arg for p in a.posonlyargs + a.args + a.kwonlyargs}
    receivers -= _NOT_A_STORE

    out: list[tuple[str, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not isinstance(f, ast.Attribute):
            continue
        # `store.read(...)` and `wiring.record_store.since(...)` are both
        # collaborator calls; the second is how this module names all of them.
        if isinstance(f.value, ast.Name):
            recv = f.value.id
        elif (isinstance(f.value, ast.Attribute)
              and isinstance(f.value.value, ast.Name)
              and f.value.value.id in receivers):
            recv = f.value.attr
        else:
            continue
        if recv in _NOT_A_STORE or (
            isinstance(f.value, ast.Name) and recv not in receivers
        ):
            continue
        bounded = _in_wait_for(node, parents)
        if not (bounded or isinstance(parents.get(node), ast.Await)):
            continue
        out.append((f"{recv}.{f.attr}", bounded))
    return out


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
    """THE POSITIVE CONTROL, and the reason the debrief's own validator was
    rewritten twice: a walk that found nothing would report «no unbounded
    awaits» with total confidence.

    The media join is named specifically because it is the call this file was
    written for, and because it is the one whose absence would look like a
    pass."""
    found = _slot_held_awaits()

    assert len(found) >= 4, f"the walk went thin — it found only {found}"
    names = {name for name, _ in found}
    assert "media_join.enrich" in names, (
        "the media join is no longer awaited on the fold path, or the walk "
        "cannot see it"
    )
    assert "record_store.since_including_nora" in names
    assert "store.read" in names


def test_a_wait_for_with_no_timeout_does_not_count_as_bounded() -> None:
    """The gate's own instrument, checked before its output is read.

    `asyncio.wait_for(x, timeout=None)` waits forever, and a scan that stopped
    at «is there a wait_for ancestor» reported it as bounded — so the fix above
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
