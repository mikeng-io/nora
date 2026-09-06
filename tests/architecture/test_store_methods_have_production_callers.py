"""A store method with no production caller is pinned here, with a reason.

WHAT THIS IS FOR
----------------
A full audit of every store method across the standing, knowledge, language and
note tiers found methods that nothing under `nora/` calls, plus reverse-direction
gaps — columns with no writer, tables with no store, a config key read nowhere.
The obvious reading is "unfinished work", and it is wrong for most of them. The
reading that survives all of it is narrower and worse:

    **the contract suite was accepted as the completion gate.**

`SqlNoteStore.record_wake` had roughly fifteen call sites in
`tests/contract/test_note_store_contract.py` and zero in production for the
seven months between its build (2026-08-28) and the note-curator merge
(design/dreamer-agent-loop/34, issue #99), which wired it into
`NoteCuratorAdaptor.commit` — its row here is DELETED accordingly (see "wiring
one up forces its row OUT", below), the exact graduation this file's own
mechanism exists to require rather than merely permit.
`SqlTurnVerdictStore.read` says outright that it is "present for the contract
suite". `nora/domain/tag_trace_rederive.rebuild_state` has a named test
asserting that it raises. In each case a green test measured that the verb
WORKS, and nothing anywhere measured that the verb is USED — which is
CLAUDE.md's no-self-attestation rule landing on a store method: the doer's own
gate certified the wrong property.

So this file is not a to-do list and emphatically not an instruction to wire
these up. Several of them SHOULD NOT be wired; there is nothing on the other
end. It exists so that the inventory is a checked artifact instead of one that
a session rediscovers every few weeks at the cost of a twenty-agent sweep, and
so that the NEXT method to arrive unwired has to be classified deliberately
rather than by default.

THE THREE SHAPES, because lumping them together is what made the list look
like a pile of bugs:

  `read_back_affordance` — a by-id read shipped on the store's own surface for
      the contract suite and for an operator, with no consumer designed. Its
      own docstring usually says so. Wiring it up would be inventing a caller.
  `deferred_consumer`    — the write half is live and the read half is named
      in a spec but deliberately not built yet. A decision with a date on it.
  `defect`               — a live write path creates state that no production
      code can act on or leave. These are the ones that want fixing.

THE COUNTS, so they reconcile with something: ELEVEN methods are pinned below.
TWO more are known to have no production caller and cannot be proven so here —
`SqlTagTraceStore.recent` and `SqlTurnVerdictStore.read` — because their names
are shared with other stores (see the ambiguity rule). Thirteen in total; the
eleven are what this file can check.

HOW A CALLER IS RESOLVED, AND WHAT THAT STILL CANNOT SEE
--------------------------------------------------------
A caller of a METHOD is a PARSED `ast.Call` whose function is an attribute
access on a NAME-BOUND receiver — `self._facts.propose(...)`,
`store.pending(...)`. A caller of a PROPERTY is a bare Load of the same shape,
because a property is read rather than called; `window_ms` is the one pinned
member that needs it. Every part of that is load-bearing, and successive
versions of this file got each part wrong in turn:

  - Parsing, not text, because a DOCSTRING that mentions a method vouched for
    it. `nora/domain/compute.py` contains the prose «Unlike `ReminderStore.due()`»
    and that alone certified `SqlReminderStore.due` as called; deleting its one
    real caller left this gate green.
  - A name-bound receiver, because `(await conn.execute(stmt)).all()` — which
    appears in twenty-odd store modules — certified `SqlMusicSessionStore.all`.
    A store is always reached through a name, never off the result of a call.
  - CALLS for methods, not every attribute, because collecting bare Loads for
    everything let a dataclass field, a SQLAlchemy column accessor, a decorator,
    an annotation and even an assignment TARGET vouch for a method. 1265 names
    on this tree were borrowable that way, and none of them is a method — so
    `_namesakes` never saw them and the result read `ok` instead of
    `unavailable`.

What remains unresolvable is a NAMESAKE: a method of the same name on some
other class. Two kinds are excluded from checking entirely and listed in
`_AMBIGUOUS`:

  - names defined on more than one store class (`read`, `recent`, `save`,
    `resolve`, `supersede`, `get`, `create`, …), and
  - names also defined on a non-Protocol class elsewhere under `nora/`
    (`record` is `CallLedger.record` and `ReplyGuard.record` as well as
    `SqlRenderedClaimStore.record`).

Protocol and ABC declarations are deliberately NOT treated as namesakes: a port
in `nora/domain/ports.py` declaring `propose` IS this store's own interface, and
calls land on it legitimately. Excluding those too would make almost every
method unresolvable and the gate would check nothing while looking thorough.

A name in `_AMBIGUOUS` is UNCHECKED, never clean — the same R-27 rule the rest
of these gates keep. Both pins are checked in both directions, and `_AMBIGUOUS`
is DERIVED from the code every run rather than hand-maintained, so a name that
stops being ambiguous cannot keep its exemption.

STILL NOT SEEN, and stated rather than left to be discovered:
  - A THIRD-PARTY namesake, and this is the real remaining hole. `_namesakes`
    only sees classes defined under `nora/`, so a store method whose name
    matches a method on a stdlib or library object is wrongly cleared. Two
    demonstrated, both still passing this gate today:
      `SqlFactStore.count`  — cleared by `essence.count("/")` (`str.count`) and
                              by `sa.func.count()`;
      `SqlFactStore.close`  — cleared by `self.deps.client.close()`.
    Neither name is defined on any class under `nora/`, so nothing pushes them
    into `_AMBIGUOUS` and they read `ok` rather than `unavailable`. It takes a
    NEW store method landing on a borrowed name to bite — every row below was
    confirmed by a separate audit, not by this gate alone — and closing it
    properly needs receiver type inference, which is more machinery than this
    gate is worth. Said plainly instead of left for someone to trip over. The
    cheap partial mitigation if it ever does bite: add the name to `_AMBIGUOUS`
    by hand, which turns the silent clear into a loud "unchecked".
  - Dynamic dispatch — `getattr(store, name)`, a string-keyed registry. Nothing
    here would catch it.
  - REACHABILITY. A caller that is itself dead — an unimported module, a branch
    behind a never-true flag — reads as a live caller.
  - Whether the call actually lands on THIS store. A call on a port whose bound
    implementation is a different class counts here.

PROBE (2026-08-29, production code only; each mutation asserted to have applied
AND to still parse, because one that does not compile can redden for the wrong
reason and one whose anchor missed measures the original code):
  - A new uncalled `SqlFactStore.totally_new_read`:
    `test_every_uncalled_store_method_is_pinned` RED, naming it.
  - `await self._facts.clear_disputed()` added to
    `nora/engagement/recognition.py`: `test_pinned_methods_still_have_no_caller`
    RED — wiring one up forces its row OUT rather than leaving a stale excuse.
  - `SqlFactStore.disputed_facts` renamed: BOTH halves RED.
  - A second `forget` defined on another store: the derived-ambiguity test RED,
    and the pin independently RED as missing.
  - One row deleted from the pin table: RED, so the table is content and not
    decoration.
  - THE TWO A REVIEWER USED TO BREAK THE PREVIOUS VERSION, both now caught and
    both green before: deleting the only real caller of `SqlReminderStore.due`
    (previously vouched for by a docstring) and of `SqlMusicSessionStore.all`
    (previously vouched for by `Result.all()`).
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path
from typing import Literal, NamedTuple, get_args

ROOT = Path(__file__).resolve().parents[2]
SQL = ROOT / "nora" / "infra" / "sql"

Shape = Literal["read_back_affordance", "deferred_consumer", "defect"]
SHAPES: frozenset[str] = frozenset(get_args(Shape))


class _Pin(NamedTuple):
    shape: Shape
    why: str


# Public store methods with NO production caller, by (module, method).
#
# Every row carries the shape and the reason, because a bare list is exactly
# the artifact that gets re-derived from scratch by the next session that
# notices one of them.
_NO_PRODUCTION_CALLER: dict[tuple[str, str], _Pin] = {
    ("announcements.py", "announced"): _Pin(
        "read_back_affordance",
        "Its own docstring: «the operator's read, and what the tests assert the "
        "ledger on». No consumer designed.",
    ),
    ("embeddings.py", "forget"): _Pin(
        "defect",
        "`supersede` IS wired (nora/language/fact_adaptor.py) and facts.py names "
        "`forget` as its write-side answer, so a retracted fact keeps its vector "
        "forever. NOT cost-only, which an earlier version of this row claimed: "
        "knowledge_search grounds AFTER fusing, so orphaned vectors occupy "
        "channel top-k and fused slots ahead of the standing filter and can "
        "starve standing facts out of the result — which then surfaces as "
        "`nothing_found`. Re-grounding does guarantee a retracted claim is never "
        "RENDERED; it does not stop one crowding out a live one.",
    ),
    ("fact_candidates.py", "candidate_by_id"): _Pin(
        "read_back_affordance",
        "fact_candidates.py: «Not on `FactCandidateReader`: `pending` is the "
        "port's job … a by-id read is this store's own surface».",
    ),
    ("facts.py", "clear_disputed"): _Pin(
        "defect",
        "Half of a state machine whose other half is LIVE. `mark_disputed` fires "
        "from nora/engagement/recognition.py on a member's objection, and the "
        "mark then surfaces whenever that subject is selected into <facts> — not "
        "on every later turn, since the room slice is filtered to the subjects "
        "in play and capped. Supersession is the only exit (facts.py's "
        "`supersede` nulls all four dispute columns), so a dispute that was "
        "itself wrong cannot be dismissed. Pairs with `disputed_facts`.",
    ),
    ("facts.py", "disputed_facts"): _Pin(
        "defect",
        "The disputed backlog its own docstring calls «the priority queue's "
        "input», with no drain: nora/language/fact_adaptor.py contains zero "
        "occurrences of the string `disput`. With `clear_disputed`, this makes a "
        "wired write path a one-way door on a rendered column.",
    ),
    ("facts.py", "fact_by_id"): _Pin(
        "read_back_affordance",
        "History/audit read-back; no consumer designed.",
    ),
    ("facts.py", "recent_facts"): _Pin(
        "defect",
        "STALE CLAIM, not a missing loop. Its docstring asserts it «runs on the "
        "same critical path, inside the same asyncio.gather». Three fact reads "
        "do reach the prefill — `standing_facts`, `facts_asserted_by` and "
        "`standing_count` — and this is not one of them; it had a caller on "
        "origin/master and lost it. The defect is the false docstring.",
    ),
    ("mobility.py", "trips_for_subject"): _Pin(
        "read_back_affordance",
        "Kept correct for a future caller by explicit decision — its docstring "
        "argues against silently discarding a column, «a trap for the next "
        "caller». Outside the three memory tiers.",
    ),
    ("notes.py", "note_by_id"): _Pin(
        "read_back_affordance",
        "Retired-note lookup; no consumer designed.",
    ),
    ("notes.py", "revision_by_id"): _Pin(
        "read_back_affordance",
        "Revision-by-id read-back; no consumer designed.",
    ),
    ("rendered_claims.py", "window_ms"): _Pin(
        "defect",
        "An accessor built to be the single source of the recognition window, "
        "whose one would-be consumer (nora/engagement/recognition.py) recomputes "
        "the same arithmetic instead. Both are fed from the same config key, so "
        "they agree TODAY; the duplication is the defect, not a current wrong "
        "answer.",
    ),
}

# Names this file CANNOT resolve, pinned so the set cannot grow in silence.
# It is DERIVED every run (see `_ambiguous`) and asserted to equal this list, so
# the pin is a record of a decision rather than a second source of truth: a name
# that becomes ambiguous fails here instead of quietly dropping out of the
# checked set, and one that stops being ambiguous cannot keep its exemption.
#
# Two kinds, both unresolvable for the same reason — a call to `.read(...)`
# cannot be attributed: defined on more than one store class, or also defined on
# a non-Protocol class elsewhere under `nora/`. Two methods KNOWN to have no
# production caller hide in here and cannot be proven so: `tag_traces.recent`
# and `turn_verdicts.read`.
_AMBIGUOUS: frozenset[str] = frozenset(
    {
        "aclose", "append", "append_observations", "claim", "create", "delete",
        "describe", "enqueue", "get", "mark", "mark_disputed", "pending",
        "propose", "prune", "prune_all", "put", "read", "recent", "record",
        "rescore", "resolve", "rsvps_for", "save", "search", "supersede",
        "sweep_firing", "update", "upsert", "watermark",
        # `SqlEntityStore.lookup` (32 §3.4) shares its name with
        # `TransitCatalogue.lookup` and `VersionManifest.lookup`, both
        # non-Protocol classes — a call to `.lookup(...)` cannot be
        # attributed, so the name is UNCHECKED, never clean. Its caller is
        # `nora.agent.tools`' `knowledge_entity`/`get_knowledge`, pinned by
        # `tests/test_tools.py` instead.
        "lookup",
    }
)

# THE POSITIVE CONTROL, and it must be a LITERAL: every store module that
# defines at least one public method, written out rather than globbed.
#
# Two versions of this control were wrong before it. A bare name COUNT had
# enough slack that nine of these modules could vanish from the walk with every
# assertion still green. Replacing it with a module SET did not fix it either,
# because the expected set was computed from the same glob the walk used —
# narrowing the walk narrowed both sides equally, and a reviewer dropped three
# modules, one of them carrying a freshly added uncalled method, and stayed
# green. A control derived from the thing it controls is not a control.
#
# `engine.py`, `migrations_runner.py` and `schema.py` are the package's only
# other files and define no public methods, so they are simply not here.
_STORE_MODULES: frozenset[str] = frozenset(
    {
        "announcements.py", "audit.py", "compute_jobs.py",
        "dream_schedule.py", "embeddings.py",
        "entities.py", "exchange.py", "fact_candidates.py", "facts.py",
        "gatherings.py", "geocode_cache.py", "language.py",
        "media_descriptions.py", "media_objects.py", "mobility.py",
        "music_queue.py", "music_session.py", "notes.py", "presence.py",
        "reminders.py", "rendered_claims.py", "room_portrait.py",
        "standing.py", "tag_traces.py", "tool_call_log.py", "turn_verdicts.py",
    }
)

# Secondary, against the walk going thin WITHIN the modules it does reach.
_MIN_METHOD_NAMES = 115


class _Method(NamedTuple):
    module: str
    cls: str
    name: str
    line: int
    is_property: bool


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _store_modules() -> list[Path]:
    return sorted(SQL.glob("*.py"))


def _store_methods() -> dict[str, list[_Method]]:
    """Every public method on every class in `nora/infra/sql/`, keyed by name."""
    found: dict[str, list[_Method]] = defaultdict(list)
    for path in _store_modules():
        for cls in (n for n in ast.walk(_parse(path)) if isinstance(n, ast.ClassDef)):
            for node in cls.body:
                if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                if node.name.startswith("_"):
                    continue
                is_prop = any(
                    isinstance(d, ast.Name) and d.id == "property"
                    for d in node.decorator_list
                )
                found[node.name].append(
                    _Method(path.name, cls.name, node.name, node.lineno, is_prop)
                )
    return found


def _production_sources() -> list[Path]:
    """Everything that could hold a production caller.

    `nora/testing/` is the fakes package and `nora/compute/smith/tests/` is a
    test suite that lives inside `nora/` — a call from either is exactly the
    "the contract suite is the gate" mistake this file is about, so neither may
    vouch for anything. The second was missed by an earlier version, whose
    exclusion checked for the path component "testing" while that directory is
    named "tests".
    """
    return sorted(
        p
        for p in (ROOT / "nora").rglob("*.py")
        if ".venv" not in p.parts
        and "testing" not in p.parts
        and "tests" not in p.parts
    )


def _is_protocol(cls: ast.ClassDef) -> bool:
    return any(
        (isinstance(b, ast.Name) and b.id in ("Protocol", "ABC"))
        or (isinstance(b, ast.Attribute) and b.attr in ("Protocol", "ABC"))
        for b in cls.bases
    )


def _namesakes() -> dict[str, set[str]]:
    """Method names defined on a non-Protocol class OUTSIDE the store package.

    These make a name unresolvable: a call to `.record(...)` may be reaching
    `CallLedger.record` rather than `SqlRenderedClaimStore.record`.

    Protocols and ABCs are excluded on purpose. A port declaring `propose` IS
    the store's own interface and calls land on it legitimately; counting those
    as namesakes would push nearly every method into the unchecked set and this
    file would check almost nothing while still looking thorough.
    """
    found: dict[str, set[str]] = defaultdict(set)
    for path in _production_sources():
        if path.parent == SQL:
            continue
        for cls in (n for n in ast.walk(_parse(path)) if isinstance(n, ast.ClassDef)):
            if _is_protocol(cls):
                continue
            for node in cls.body:
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    found[node.name].add(f"{path.relative_to(ROOT)}::{cls.name}")
    return found


class _Site(NamedTuple):
    path: Path
    line: int


def _call_sites() -> tuple[dict[str, list[_Site]], dict[str, list[_Site]]]:
    """(calls, reads) — attribute accesses on a NAME-BOUND receiver, by name.

    TWO sets, because a method and a property are used differently and treating
    them alike is a false-clear machine. A method is CALLED, so only an
    attribute that is the `func` of an `ast.Call` counts; a property is READ, so
    a bare Load counts too.

    Collecting every attribute for both — which an earlier version did — meant a
    dataclass field, a SQLAlchemy column accessor, a decorator, a type
    annotation and even an assignment TARGET all vouched for a store method. A
    reviewer measured 1265 such borrowable names on this tree, concentrated in
    exactly the column-name space store methods draw from (`due_at`,
    `revisit_at`, `disputed_at`, `anchor_date`). None of them is a method, so
    `_namesakes` never saw them either and the name read `ok` rather than
    `unavailable` — this file's own subject, one level up inside it.

    The receiver must be a `Name` or an `Attribute`, never the result of a call:
    `(await conn.execute(stmt)).all()` is not a store call, and treating it as
    one previously certified `SqlMusicSessionStore.all` as used.
    """
    calls: dict[str, list[_Site]] = defaultdict(list)
    reads: dict[str, list[_Site]] = defaultdict(list)
    for path in _production_sources():
        for node in ast.walk(_parse(path)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name | ast.Attribute)
            ):
                calls[node.func.attr].append(_Site(path, node.lineno))
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.ctx, ast.Load)
                and isinstance(node.value, ast.Name | ast.Attribute)
            ):
                reads[node.attr].append(_Site(path, node.lineno))
    return calls, reads


def _ambiguous(methods: dict[str, list[_Method]]) -> frozenset[str]:
    shared = {name for name, defs in methods.items() if len(defs) > 1}
    elsewhere = _namesakes()
    unique = {name for name, defs in methods.items() if len(defs) == 1}
    return frozenset(shared | {n for n in unique if n in elsewhere})


def _caller_of(
    method: _Method, sites: tuple[dict[str, list[_Site]], dict[str, list[_Site]]]
) -> str | None:
    calls, reads = sites
    for site in (reads if method.is_property else calls).get(method.name, []):
        if site.path.parent == SQL and site.path.name == method.module:
            continue  # its own module cannot vouch for it
        return f"{site.path.relative_to(ROOT)}:{site.line}"
    return None


def test_every_uncalled_store_method_is_pinned() -> None:
    methods = _store_methods()
    sites = _call_sites()
    ambiguous = _ambiguous(methods)

    # POSITIVE CONTROL, on the MODULE SET rather than a count. An empty result
    # is also what a walk that silently dropped a module produces, and a
    # numeric floor has slack a set does not: with a count alone, nine of the
    # twenty-five store modules could vanish and every assertion stayed green.
    seen = {m.module for defs in methods.values() for m in defs}
    assert seen >= _STORE_MODULES, (
        "the walk missed store modules entirely: "
        f"{sorted(_STORE_MODULES - seen)}. Every module named in _STORE_MODULES "
        "defines public methods, so one absent from `seen` means the walk "
        "stopped reaching it and everything it holds silently left the checked "
        "set. Fix the walk before trusting a green run."
    )
    unknown = seen - _STORE_MODULES
    assert not unknown, (
        "store modules with public methods that are not in _STORE_MODULES — add "
        f"them so the control keeps covering the package: {sorted(unknown)}"
    )
    assert len(methods) >= _MIN_METHOD_NAMES, (
        f"only {len(methods)} public store method names found, below "
        f"{_MIN_METHOD_NAMES}. The walk stopped matching."
    )

    unpinned: list[str] = []
    for name, defs in sorted(methods.items()):
        if name in ambiguous:
            continue
        method = defs[0]
        if _caller_of(method, sites) is not None:
            continue
        if (method.module, name) in _NO_PRODUCTION_CALLER:
            continue
        unpinned.append(
            f"nora/infra/sql/{method.module}:{method.line} {method.cls}.{name}"
        )

    assert not unpinned, (
        "store methods with no production caller that are not pinned in "
        "_NO_PRODUCTION_CALLER:\n  " + "\n  ".join(unpinned) + "\n\n"
        "This is not necessarily a bug — most such methods are deliberate "
        "read-back affordances. It IS a decision, and it has to be made rather "
        "than defaulted into: add a row saying which of the three shapes it is "
        "and why. A green contract suite proves the verb WORKS and says nothing "
        "about whether it is USED, which is how every row already here arrived."
    )


def test_pinned_methods_still_have_no_caller() -> None:
    """The pin must not rot. Wiring one of these up should DELETE its row, not
    leave an excuse behind for whatever lands on the same name later."""
    methods = _store_methods()
    sites = _call_sites()
    by_key = {
        (defs[0].module, name): defs[0]
        for name, defs in methods.items()
        if len(defs) == 1
    }

    now_called: list[str] = []
    missing: list[str] = []
    for key in sorted(_NO_PRODUCTION_CALLER):
        method = by_key.get(key)
        if method is None:
            missing.append(f"{key[0]}::{key[1]}")
            continue
        caller = _caller_of(method, sites)
        if caller is not None:
            now_called.append(f"{key[0]}::{key[1]} is now called from {caller}")

    assert not now_called, (
        "pinned methods that DO have a production caller now — delete their "
        "rows from _NO_PRODUCTION_CALLER:\n  " + "\n  ".join(now_called)
    )
    assert not missing, (
        "_NO_PRODUCTION_CALLER rows naming no unique public store method — the "
        "method was renamed, deleted, or its name stopped being unique:\n  "
        + "\n  ".join(missing)
    )


def test_no_pinned_method_is_ambiguous() -> None:
    """A pinned row asserts «nothing calls this». If its name became ambiguous
    the row would stop being checkable, and an unchecked row is an excuse."""
    methods = _store_methods()
    ambiguous = _ambiguous(methods)
    bad = sorted(name for _mod, name in _NO_PRODUCTION_CALLER if name in ambiguous)
    assert not bad, (
        "pinned methods whose names are no longer resolvable, so their rows "
        "assert something this file can no longer check: " + str(bad)
    )


def test_every_pin_declares_a_known_shape() -> None:
    bad = [
        f"{mod}::{name} -> {pin.shape}"
        for (mod, name), pin in _NO_PRODUCTION_CALLER.items()
        if pin.shape not in SHAPES
    ]
    assert not bad, f"pins with an undeclared shape (see `Shape`): {bad}"
    thin = [
        f"{mod}::{name}"
        for (mod, name), pin in _NO_PRODUCTION_CALLER.items()
        if len(pin.why) < 40
    ]
    assert not thin, (
        "pins whose reason is too short to be a reason. A row here is the only "
        "record of why a method nothing calls is allowed to exist: " + str(thin)
    )


def test_the_ambiguous_set_is_exactly_what_the_code_makes_ambiguous() -> None:
    """Derived, not hand-maintained — and pinned, so it cannot grow in silence.

    Without the pin an unresolvable name simply vanishes from the checked set,
    which is the very substitution this file is about one level up: `unavailable`
    reported as clean. With it, a store method that collides with something else
    fails here and someone has to look.
    """
    derived = _ambiguous(_store_methods())
    assert derived == _AMBIGUOUS, (
        "_AMBIGUOUS has drifted from the code.\n"
        f"  newly ambiguous (add them — they are no longer checked, and any of "
        f"them may now have no caller without this gate noticing): "
        f"{sorted(derived - _AMBIGUOUS)}\n"
        f"  no longer ambiguous (delete them — they are checkable now): "
        f"{sorted(_AMBIGUOUS - derived)}"
    )
