"""A store method with no production caller is pinned here, with a reason.

WHAT THIS IS FOR
----------------
Some store methods are called by nothing under `nora/`, and a green contract
suite does not notice: it measures that the verb WORKS, and nothing measures
that the verb is USED. A method whose only caller is its own contract test, or
a test asserting that it raises, is certified by the doer's own gate — the
no-self-attestation rule landing on a store method. Wiring a pinned method into
production forces its row OUT (see `test_pinned_methods_still_have_no_caller`),
which is the graduation this mechanism exists to require.

This file is not a to-do list and emphatically not an instruction to wire these
up. Several of them SHOULD NOT be wired; there is nothing on the other end. It
exists so that the inventory is a checked artifact, and so that the NEXT method
to arrive unwired has to be classified deliberately rather than by default.

THE FOUR SHAPES, because lumping them together makes the list look like a pile
of bugs:

  `read_back_affordance` — a by-id read shipped on the store's own surface for
      the contract suite and for an operator, with no consumer designed. Its
      own docstring usually says so. Wiring it up would be inventing a caller.
  `deferred_consumer`    — the write half is live and the read half is named
      in a spec but deliberately not built yet.
  `defect`               — a live write path creates state that no production
      code can act on or leave. These are the ones that want fixing.
  `operator_script`      — a real caller exists and runs it deliberately, but
      it lives under `scripts/`, which this file does not scan (`_call_sites`
      walks `nora/` only, by design — a script is not the daemon). Wiring
      THIS one up would mean moving the caller into `nora/`, which is the
      wrong direction: the whole point is that a manual, human-invoked path
      stays outside the code the tag/participation loop runs.

Methods in `_AMBIGUOUS` (below) may also have no production caller and cannot
be proven so here; `SqlTagTraceStore.recent` is one, because its name is shared
with other stores. A count in prose that nothing recomputes is a claim, so this
docstring states none.

HOW A CALLER IS RESOLVED, AND WHAT THAT STILL CANNOT SEE
--------------------------------------------------------
A caller of a METHOD is a PARSED `ast.Call` whose function is an attribute
access on a NAME-BOUND receiver — `self._facts.propose(...)`,
`store.pending(...)`. A caller of a PROPERTY is a bare Load of the same shape,
because a property is read rather than called. Every part of that is
load-bearing:

  - Parsing, not text, because a DOCSTRING that mentions a method would vouch
    for it: `nora/domain/compute.py` contains the prose «Unlike
    `ReminderStore.due()`», which must not certify `SqlReminderStore.due`.
  - A name-bound receiver, because `(await conn.execute(stmt)).all()` — which
    appears in many store modules — would certify `SqlMusicSessionStore.all`.
    A store is always reached through a name, never off the result of a call.
  - CALLS for methods, not every attribute, because collecting bare Loads for
    everything lets a dataclass field, a SQLAlchemy column accessor, a
    decorator, an annotation and even an assignment TARGET vouch for a method.
    None of those names is a method, so `_namesakes` never sees them and the
    result would read `ok` instead of `unavailable`.

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
  - A THIRD-PARTY namesake, the real remaining hole. `_namesakes` only sees
    classes defined under `nora/`, so a store method whose name matches a
    method on a stdlib or library object is wrongly cleared:
      `count`  — `sa.func.count()` (`nora/infra/sql/audit.py`,
                 `reminders.py`) and `str.count` in the compute package;
      `close`  — `await client.close()` (`nora/platform/discord/adapter.py`),
                 `h.close()` (`nora/obs/logging.py`).
    Neither name is defined on any class under `nora/`, so nothing pushes them
    into `_AMBIGUOUS`: a store method landing on either name reads `ok` rather
    than `unavailable`, on those call sites alone. (`all` shows the mechanism
    working — every `.all()` in this tree is SQLAlchemy's `Result.all`, and it
    stays checked only because `MusicSessionStore` also declares it and pushes
    it into `_AMBIGUOUS`.) Closing this properly needs receiver type inference,
    which is more machinery than this gate is worth. The cheap partial
    mitigation: add the name to `_AMBIGUOUS` by hand, which turns the silent
    clear into a loud "unchecked".
  - Dynamic dispatch — `getattr(store, name)`, a string-keyed registry. Nothing
    here would catch it.
  - REACHABILITY. A caller that is itself dead — an unimported module, a branch
    behind a never-true flag — reads as a live caller.
  - Whether the call actually lands on THIS store. A call on a port whose bound
    implementation is a different class counts here.

WHAT FALSIFIES EACH GUARD (production code only; a mutation must apply AND still
parse, or it can redden for the wrong reason):
  - A new uncalled public store method:
    `test_every_uncalled_store_method_is_pinned` RED, naming it.
  - A call to a PINNED method added to a live module:
    `test_pinned_methods_still_have_no_caller` RED — wiring one up forces its
    row OUT rather than leaving a stale excuse.
  - A store method renamed: BOTH halves RED.
  - A second `forget` defined on another store: the derived-ambiguity test RED,
    and the pin independently RED as missing.
  - One row deleted from the pin table: RED, so the table is content and not
    decoration.
  - Deleting the only real caller of `SqlReminderStore.due` or
    `SqlMusicSessionStore.all`: RED, since neither a docstring nor `Result.all()`
    vouches for them.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path
from typing import Literal, NamedTuple, get_args

ROOT = Path(__file__).resolve().parents[2]
SQL = ROOT / "nora" / "infra" / "sql"

Shape = Literal[
    "read_back_affordance", "deferred_consumer", "defect", "operator_script"
]
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
    ("persona.py", "account_heads"): _Pin(
        "deferred_consumer",
        "A Persona account as it stands, the lines a session is shown with "
        "their handles. Persona step 5 built the store and its one write; "
        "the reader is the Persona adaptor's session (design/unified-memory/45 "
        "§4, 00 «Sequence» step 6), which is the next build. Until then the "
        "tests read the account back through it.",
    ),
    ("culture.py", "chain"): _Pin(
        "deferred_consumer",
        "The revision chain, newest first, walked through parent links. The "
        "tier's ONE genuine contribution is that the chain exists — a single "
        "account is a snapshot, because the model that wrote it had no memory "
        "of the one before — so this verb is the point of the store rather "
        "than an extra. It has no caller because `02-measurement §5` sequences "
        "the culture tier as WRITING-ONLY (step 4): the chain must accumulate "
        "real revisions before any prompt reads it, since a tier judged on its "
        "first snapshot is judged on the one thing it does not contribute. "
        "Step 5 is the consumer, and it is gated on a measured improvement "
        "rather than assumed.",
    ),
    # `assess.py`'s `recent` needs no pin: `recent` is already ambiguous
    # (audit.py/decision_log.py/tag_traces.py/tool_call_log.py all define one),
    # so `_ambiguous` skips it before the pin table is ever consulted.
    ("rollups.py", "rows"): _Pin(
        "read_back_affordance",
        "Every row of one unit rollup table in a build, surrogate ids and "
        "system time stripped: what the step-2 determinism gate compares "
        "between an incremental build and a rebuild "
        "(tests/integration/test_rollup_builds.py). Its consumer at runtime is "
        "`rollup_read` (design/cognition-loop/30-cognition.md §2), not built "
        "in step 2a.",
    ),
    ("computer_events.py", "orphaned_starts"): _Pin(
        "read_back_affordance",
        "The computer audit's crash marker (design/sandbox-runtime §12): "
        "`started` rows with no `finished` row, a call interrupted by a crash "
        "or a restart. The operator's query, pinned by the contract suite; no "
        "daemon consumer is designed (a reaper or an alert is S2b-2's).",
    ),
    ("notes.py", "revision_by_id"): _Pin(
        "read_back_affordance",
        "Revision-by-id read-back; no consumer designed.",
    ),
    ("gatherings.py", "revisions_for"): _Pin(
        "read_back_affordance",
        "An event's full revision chain (10-tiers.md §3/§8; migration "
        "0074_event_revision). No prompt block or tool reads the chain today "
        "— `event_propose`/`event_revise`/`event_set_status` only ever read "
        "the head through `head_revision_id` for `base_revision_id`'s "
        "compare-and-swap. Its consumer is a `History` section on an event "
        "(12-schemas.md §3's `Changes`, 'every revision: field, from, to, "
        "mode, why, when'), not built at step 2b.",
    ),
    ("social_stream.py", "item_state"): _Pin(
        "deferred_consumer",
        "An item's current state, the join every read of social items makes "
        "(design/cognition-loop/22-social-stream.md §5). The poller writes "
        "states; nothing reads social items yet.",
    ),
    ("standing.py", "write_if_unchanged"): _Pin(
        "operator_script",
        "The compare-and-set write for a manual Standing edit (design/"
        "dreamer-agent-loop/26-standing.md §9, 2026-09-25). Its one caller is "
        "`scripts/standing_edit.py`, deliberately outside `nora/` — this is "
        "the operator's own entry point, not a verb the tag/participation "
        "loop should ever reach, and `_call_sites` scans `nora/` only.",
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
# a non-Protocol class elsewhere under `nora/`. One method KNOWN to have no
# production caller hides in here and cannot be proven so: `tag_traces.recent`.
#
# `lookup` is here because `MediaObjectStore.lookup` shares the name:
# `nora/infra/version.py` and `nora/infra/transit/catalogue.py` each define an
# unrelated one, so `.lookup(...)` cannot be attributed. Its real caller is
# `nora.domain.media_join.MediaJoin`, pinned by
# `tests/unit/test_media_join.py` and by the store's own contract suite.
_AMBIGUOUS: frozenset[str] = frozenset(
    {
        "aclose", "append", "claim", "create", "delete",
        "describe", "enqueue", "get", "history", "lookup", "mark", "put", "read",
        "recent", "record", "resolve", "rsvps_for", "save", "search",
        "sweep_firing", "apply",
        # `SqlDocumentStore.set_state` (DEC-255) shares the name with another store's.
        "set_state",
        # `SubagentBackend.cancel` is a `ComputeBackend` method, not a store's, and
        # shares its name with a store's.
        "cancel",
        # `SqlMemoStore.revisions` (DEC-306) shares the name with `SqlLessonStore`'s.
        "revisions",
        # `SqlRecordStore.drain_index_queue` (memory index S4) shares the name
        # with `SqlRollupStore`'s: both drain the one queue, from the nora and
        # the analytics process. Callers: `nora.app.runtime.memory_drain_loop`
        # and `nora.analytics.runner`.
        "drain_index_queue",
        # `MemoryEmbedWorker.run_once` shares the
        # name with other workers' ticks. Caller: `nora.app.runtime.
        # memory_drain_loop`, pinned by `tests/unit/test_runtime.py`.
        "run_once",
    }
)

# THE POSITIVE CONTROL, and it must be a LITERAL: every store module that
# defines at least one public method, written out rather than globbed.
#
# A bare name COUNT has enough slack that several modules could vanish from the
# walk with every assertion still green, and a module SET computed from the
# same glob the walk uses narrows both sides equally. A control derived from the
# thing it controls is not a control.
#
# `engine.py`, `migrations_runner.py` and `schema.py` are the package's only
# other files and define no public methods, so they are simply not here.
_STORE_MODULES: frozenset[str] = frozenset(
    {
        "assess.py", "audit.py", "cognition_store.py", "lessons.py",
        "tension.py",
        "compute_jobs.py", "computer_events.py", "computer_exports.py",
        "computer_sessions.py",
        "character.py", "culture.py", "decision_log.py", "documents.py", "dream_schedule.py",
        "gatherings.py", "geocode_cache.py", "language.py",
        "media_descriptions.py", "media_objects.py", "memo.py", "memory_embed.py",
        "memory_read.py", "mobility.py",
        "music_queue.py", "music_session.py", "notes.py", "otp.py",
        "participation_state.py", "persona.py", "presence.py",
        "reasoning_strip.py", "record_query.py",
        "reminders.py", "revisions.py", "rollup_read.py", "rollups.py",
        "snapshot_at.py", "social_stream.py",
        "stimuli.py",
        "source_health.py", "tag_debounce.py",
        "standing.py", "summons.py", "tag_traces.py", "tool_call_log.py",
        "video_readings.py",
    }
)

# Secondary, against the walk going thin WITHIN the modules it does reach.
# The floor sits just under the number of names the walk finds, rather than at a
# round number far below it, so a thinning walk is noticed.
_MIN_METHOD_NAMES = 95


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

    `nora/testing/` is the fakes package, and any `tests/` directory inside
    `nora/` is a test suite — a call from either is exactly the "the contract
    suite is the gate" mistake this file is about, so neither may vouch for
    anything.
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

    Collecting every attribute for both would let a dataclass field, a
    SQLAlchemy column accessor, a decorator, a type annotation and even an
    assignment TARGET vouch for a store method, concentrated in exactly the
    column-name space store methods draw from (`due_at`, `revisit_at`,
    `disputed_at`, `anchor_date`). None of them is a method, so `_namesakes`
    would not see them either and the name would read `ok` rather than
    `unavailable`.

    The receiver must be a `Name` or an `Attribute`, never the result of a call:
    `(await conn.execute(stmt)).all()` is not a store call, and treating it as
    one would certify `SqlMusicSessionStore.all` as used.
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
    # numeric floor has slack a set does not: with a count alone, several of
    # the twenty store modules could vanish and every assertion stayed green.
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
        "than defaulted into: add a row saying which of the four shapes it is "
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


def _scripts_call_sites() -> dict[str, list[_Site]]:
    """Call sites under `scripts/`, in the same shape as `_call_sites`'s
    `calls` half. `_call_sites` deliberately never looks here (`scripts/` is
    not the daemon), which is exactly why this scan exists: it verifies an
    `operator_script` pin's claim — "a real caller exists, just not where this
    file scans" — rather than leaving it unfalsifiable by the other tests."""
    calls: dict[str, list[_Site]] = defaultdict(list)
    for path in sorted((ROOT / "scripts").glob("*.py")):
        for node in ast.walk(_parse(path)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name | ast.Attribute)
            ):
                calls[node.func.attr].append(_Site(path, node.lineno))
    return calls


def test_every_operator_script_pin_has_a_caller_under_scripts() -> None:
    """`operator_script` is not a rebadged `read_back_affordance` — it claims
    a REAL caller exists, just outside `_call_sites`' `nora/`-only scan. A pin
    with no `scripts/` caller at all would be a plain defect wearing this
    shape's label instead of being fixed or reclassified."""
    calls = _scripts_call_sites()
    missing = [
        f"{mod}::{name}"
        for (mod, name), pin in sorted(_NO_PRODUCTION_CALLER.items())
        if pin.shape == "operator_script" and name not in calls
    ]
    assert not missing, (
        "operator_script pins with no call site anywhere under scripts/: "
        f"{missing} — either the caller moved, was renamed, or this pin is "
        "not actually this shape"
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
