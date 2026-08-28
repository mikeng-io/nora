"""A store method with no production caller is pinned here, with a reason.

WHAT THIS IS FOR
----------------
A full audit of every store method across the standing, knowledge, language and
note tiers found thirteen public methods that nothing under `nora/` calls, and
twelve more reverse-direction gaps — columns with no writer, tables with no
store, a config key read nowhere. The obvious reading is "unfinished work", and
it is wrong for most of them. The reading that survives all of it is narrower
and worse:

    **the contract suite was accepted as the completion gate.**

`SqlNoteStore.record_wake` has roughly fifteen call sites in
`tests/contract/test_note_store_contract.py` and zero in production.
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
like thirteen bugs:

  `read_back_affordance` — a by-id read shipped on the store's own surface for
      the contract suite and for an operator, with no consumer designed. Its
      own docstring usually says so. Wiring it up would be inventing a caller.
  `deferred_consumer`    — the write half is live and the read half is named
      in a spec but deliberately not built yet. A decision with a date on it.
  `defect`               — a live write path creates state that no production
      code can act on or leave. These are the ones that want fixing.

WHAT THIS CAN AND CANNOT SEE
----------------------------
Callers are resolved BY METHOD NAME, which is only sound when the name is
unique across every store class. 99 of 120 public store method names are; the
other 21 (`read`, `recent`, `save`, `search`, `resolve`, `supersede`, `get`,
`create` …) are shared, so a call to any one of them looks like a call to all
of them. Those are pinned in `_NAME_NOT_UNIQUE` and are NOT checked here —
`SqlTagTraceStore.recent` and `SqlTurnVerdictStore.read` both have no
production caller and this file cannot prove it, because `SqlRecordStore.recent`
and `SqlNoteStore.read` do.

That is stated rather than hidden because it is the same rule the rest of these
gates keep: a name this file cannot resolve is UNCHECKED, never clean. Both
pins are checked in both directions, so a row that stops matching reality fails
rather than rotting.

PROBE (2026-08-29, production code only; each mutation asserted to have
applied AND to still parse, because one that does not compile can redden for
the wrong reason and one whose anchor missed measures the original code):
  - Added `await self._facts.clear_disputed()` to
    `nora/engagement/recognition.py`: `test_pinned_methods_still_have_no_caller`
    FAILED with «facts.py::clear_disputed is now called from
    nora/engagement/recognition.py:393». So wiring one of these up forces its
    row OUT of the pin rather than leaving a stale excuse behind for the next
    method to inherit. Restored.
  - Renamed `SqlFactStore.disputed_facts` to `disputed_facts2`: BOTH halves
    fired — `test_every_uncalled_store_method_is_pinned` on the new unpinned
    name, and `test_pinned_methods_still_have_no_caller` on the row that now
    matches nothing. Restored.
"""

from __future__ import annotations

import ast
import re
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
# Sorted by module. Every row carries the shape and the reason, because a bare
# list is exactly the artifact that gets re-derived from scratch by the next
# session that notices one of them.
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
        "forever and keeps eating the retrieval budget. COST ONLY, not "
        "correctness: `standing_among` re-grounds every retrieval hit against "
        "`fact`, so a retracted claim cannot come back through the corpus — the "
        "loop was closed by the fail-safe instead of by the verb.",
    ),
    ("fact_candidates.py", "candidate_by_id"): _Pin(
        "read_back_affordance",
        "fact_candidates.py: «Not on `FactCandidateReader`: `pending` is the "
        "port's job … a by-id read is this store's own surface».",
    ),
    ("facts.py", "clear_disputed"): _Pin(
        "defect",
        "Half of a state machine whose other half is LIVE. `mark_disputed` fires "
        "from nora/engagement/recognition.py on a member's objection and "
        "fact_render then renders the dispute on every later turn; the only exit "
        "is supersession. A dispute that was itself wrong cannot be dismissed. "
        "Pairs with `disputed_facts` — see it.",
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
        "same critical path, inside the same asyncio.gather» — it does not; only "
        "the subject-filtered `standing_facts` reaches the prefill. It had a "
        "caller on origin/master and lost it. The defect is the false docstring.",
    ),
    ("mobility.py", "trips_for_subject"): _Pin(
        "read_back_affordance",
        "Kept correct for a future caller by explicit decision — its docstring "
        "argues against silently discarding a column «a trap for the next "
        "caller». Outside the three memory tiers.",
    ),
    ("notes.py", "note_by_id"): _Pin(
        "read_back_affordance",
        "Retired-note lookup; no consumer designed.",
    ),
    ("notes.py", "record_wake"): _Pin(
        "defect",
        "THE PUREST INSTANCE of this file's subject. Built 2026-08-28, contract-"
        "tested ~15x, invoked zero times — its own release fragment says "
        "«Nothing calls it yet; the revisitor wires it separately» and that "
        "second half never came. It is the ONLY writer of note.revisit_at / "
        "strikes / last_wake_revision_id, so NoteRevisitor's `revisit_at` branch "
        "is permanently False: a note with no anchor date is never revisited at "
        "all, and the strikes starvation-breaker is inert. LATENT — the note "
        "table holds 0 rows — so it costs nothing today and everything later.",
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

# Method names shared by two or more store classes. A name-based caller search
# cannot attribute a call to one of them, so these are NOT checked by this file
# — including two known-unwired ones (`tag_traces.recent`, `turn_verdicts.read`)
# whose siblings on other stores ARE called. Unchecked, never clean.
_NAME_NOT_UNIQUE: frozenset[str] = frozenset(
    {
        "aclose", "append", "append_observations", "claim", "create", "get",
        "mark", "mark_disputed", "pending", "propose", "prune", "prune_all",
        "read", "recent", "rescore", "resolve", "save", "search", "supersede",
        "sweep_firing", "upsert",
    }
)

# The sweep must see at least this much. Measured when this file was written:
# 120 distinct public method names across the SQL stores, 99 of them unique.
_MIN_METHOD_NAMES = 110
_MIN_UNIQUE_NAMES = 90


class _Method(NamedTuple):
    module: str
    cls: str
    name: str
    line: int
    is_property: bool


def _store_methods() -> dict[str, list[_Method]]:
    """Every public method on every class in `nora/infra/sql/`, keyed by name."""
    found: dict[str, list[_Method]] = defaultdict(list)
    for path in sorted(SQL.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
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


def _production_sources() -> list[tuple[Path, str]]:
    """Everything that could hold a production caller.

    `nora/testing/` is excluded — it is the fakes package, and a call there is
    exactly the "the contract suite is the gate" mistake this file is about.
    `nora/infra/sql/` IS included, because one store legitimately calling
    another is a production caller; a method's own defining module is excluded
    per-check so a docstring mention or self-call cannot vouch for it.
    """
    out: list[tuple[Path, str]] = []
    for path in (ROOT / "nora").rglob("*.py"):
        if ".venv" in path.parts or "testing" in path.parts:
            continue
        out.append((path, path.read_text(encoding="utf-8")))
    return out


def _find_caller(method: _Method, sources: list[tuple[Path, str]]) -> str | None:
    # A property is read as `.window_ms`, everything else called as `.name(`.
    suffix = r"\b" if method.is_property else r"\s*\("
    pattern = re.compile(r"\." + re.escape(method.name) + suffix)
    for path, blob in sources:
        if path.name == method.module and path.parent == SQL:
            continue  # its own module cannot vouch for it
        match = pattern.search(blob)
        if match is not None:
            rel = path.relative_to(ROOT)
            return f"{rel}:{blob[: match.start()].count(chr(10)) + 1}"
    return None


def test_every_uncalled_store_method_is_pinned() -> None:
    methods = _store_methods()
    sources = _production_sources()

    # POSITIVE CONTROL. An empty `unpinned` is also what a walk that found no
    # methods produces — a moved package, an AST change, a renamed directory.
    unique = {n: m[0] for n, m in methods.items() if len(m) == 1}
    assert len(methods) >= _MIN_METHOD_NAMES and len(unique) >= _MIN_UNIQUE_NAMES, (
        f"the sweep found {len(methods)} public store method names "
        f"({len(unique)} unique), below the {_MIN_METHOD_NAMES}/"
        f"{_MIN_UNIQUE_NAMES} this tree contains. The walk stopped matching — "
        "fix it before trusting a green run."
    )

    unpinned: list[str] = []
    for name, method in sorted(unique.items()):
        if name in _NAME_NOT_UNIQUE:
            continue
        if _find_caller(method, sources) is not None:
            continue
        if (method.module, name) in _NO_PRODUCTION_CALLER:
            continue
        unpinned.append(f"nora/infra/sql/{method.module}:{method.line} {method.cls}.{name}")

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
    sources = _production_sources()
    by_key = {
        (m[0].module, name): m[0] for name, m in methods.items() if len(m) == 1
    }

    now_called: list[str] = []
    missing: list[str] = []
    for key in sorted(_NO_PRODUCTION_CALLER):
        method = by_key.get(key)
        if method is None:
            missing.append(f"{key[0]}::{key[1]}")
            continue
        caller = _find_caller(method, sources)
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


def test_the_unresolvable_name_list_is_exactly_the_shared_names() -> None:
    """Derived, not hand-maintained: `_NAME_NOT_UNIQUE` must equal the set of
    method names that genuinely appear on more than one store class. A name
    that stops being shared becomes checkable and must leave this list, or it
    would silently exempt a real method forever."""
    methods = _store_methods()
    shared = {name for name, m in methods.items() if len(m) > 1}
    assert shared == _NAME_NOT_UNIQUE, (
        "_NAME_NOT_UNIQUE has drifted from the code.\n"
        f"  no longer shared (delete, they are checkable now): "
        f"{sorted(_NAME_NOT_UNIQUE - shared)}\n"
        f"  newly shared (add, they cannot be checked here): "
        f"{sorted(shared - _NAME_NOT_UNIQUE)}"
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
