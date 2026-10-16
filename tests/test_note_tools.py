"""The four note tools and the read-before-write latch, from the tool layer's
side.

The STORE is already contract-tested (`tests/contract/test_note_store_contract.py`)
against both implementations, so nothing here re-asserts CAS or unit
scoping at the store. What this file tests is the layer above: title
resolution (the index carries no id, so the title is the handle),
the three-state R-27 answers each tool owes, and the latch, which is the one
piece that lives in `ReplyGuard` rather than in a tool closure.
"""

from __future__ import annotations

import json

import pytest

from nora.agent.guards import ReplyGuard
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.agent.tools import NATIVE_TOOLS
from nora.domain.models import DeliveryStatus, PersonRef, TranscriptRow
from nora.obs.metrics import Metrics
from nora.testing import FakeMemoryStore, FakeRosterSource
from nora.testing.note_store import InMemoryNoteStore
from tests.fakes import SinkSender

MILES = PersonRef(person_id="miles", name="Miles Novak", honcho_peer_id="miles")
GROUP = "signal:group-1"
UNIT = "household"
NOW = 1_770_000_000_000


class _NullStore:
    async def recent(self, *, conversation_id: str, limit: int):
        return []


class _FakePersons:
    def __init__(self, *refs: PersonRef) -> None:
        self.by_person = {ref.person_id: ref for ref in refs}

    def resolve(self, platform: str, sender_id: str):
        return None


def _room(*, tagger: PersonRef | None = MILES, conversation_id: str = GROUP) -> Room:
    source = FakeRosterSource(groups={conversation_id: (frozenset({"miles"}),)})
    return Room(
        platform="signal", conversation_id=conversation_id, roster_source=source,
        persons=_FakePersons(MILES), tagger=tagger,
    )


def _tools(
    *, notes: InMemoryNoteStore | None, unit: str | None = UNIT,
    metrics: Metrics | None = None, tagger: PersonRef | None = MILES,
    conversation_id: str = GROUP, rooms: dict[str, str] | None = None,
) -> dict:
    # `note_unit` RESOLVES a room to its unit rather than naming one.
    # The resolver here READS its arguments — a lambda ignoring them would make
    # every test below pass whether or not the tool consults the room, which is
    # exactly the decorative test CLAUDE.md names. `rooms` maps
    # conversation_id -> unit, the shape `policy.toml` actually has.
    book = rooms if rooms is not None else {conversation_id: unit}

    def _resolve(_platform: str, cid: str) -> str | None:
        return book.get(cid)

    registry = ToolRegistry(notes=notes, note_unit=_resolve)
    specs = registry.specs(
        store=_NullStore(), conversation_id=conversation_id,
        conversation_type="group",
        persons=_FakePersons(MILES), honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=metrics or Metrics(),
        room=_room(tagger=tagger, conversation_id=conversation_id),
        transcript=_WINDOW,
    )
    return {spec.name: spec.call for spec in specs}


#: The transcript the tools' turn rendered: position 1 is row 501, Miles's. A body's
#: `[#n]` is mapped to the row id at that position.
_WINDOW = (
    TranscriptRow(
        id=501, sender_id="+85200000001", sender_name="miles", ts=NOW - 60_000,
        body="dinner on the 9th?", is_nora=False,
        delivery_status=DeliveryStatus.SENT, raw_json=None,
        person_name="Miles Novak", person_id="miles",
    ),
)


def _b(text: str) -> str:
    """A tag-path body in the note's form, citing transcript position 1."""
    return f"## Decided\n## Open\n- {text} [#1]"


def _stored(text: str) -> str:
    """`_b(text)` as stored: the position mapped to its row id."""
    return f"## Decided\n## Open\n- {text} [501]"


async def _seed(
    store: InMemoryNoteStore, *, title: str, body: str,
    unit: str = UNIT, description: str = "one line about it",
):
    return await store.create(
        unit=unit, title=title, description=description,
        uncertainty="not settled yet", closing_condition="somebody decides",
        established_in=GROUP, body=body, established_by="tag:miles", now_ms=NOW,
        anchor_date=20261011,
    )


# ── the table ────────────────────────────────────────────────────────────────

def test_the_four_note_tools_carry_the_budgets_the_spec_sizes_them_at():
    """A revision costs two calls — read then update — and the second is
    what makes the first mandatory, so the numbers are load-bearing rather than
    bookkeeping: `note_create` at 1 is the per-turn cap on note spam."""
    assert NATIVE_TOOLS["note_read"].budget == 3
    assert NATIVE_TOOLS["note_search"].budget == 2
    assert NATIVE_TOOLS["note_create"].budget == 1
    assert NATIVE_TOOLS["note_update"].budget == 2


def test_no_note_tool_ends_the_turn():
    """None terminal — she still owes the room a reply. A terminal note
    verb would write the row and go silent, which is R-23's shape."""
    for name in ("note_read", "note_search", "note_create", "note_update"):
        assert NATIVE_TOOLS[name].terminal is False


# ── R-27: a wiring gap is `unavailable`, never `nothing_found` ───────────────

async def test_every_note_tool_answers_unavailable_when_no_store_is_wired():
    tools = _tools(notes=None)
    calls = (
        ("note_read", {"title": "Dinner"}),
        ("note_search", {"query": "hotel"}),
        ("note_create", {
            "title": "T", "description": "d", "uncertainty": "u",
            "closing_condition": "c", "body": _b("b"), "anchor_date": 20261011,
        }),
        ("note_update", {"title": "Dinner", "body": _b("b")}),
    )
    for name, kwargs in calls:
        assert (await tools[name](**kwargs))["status"] == "unavailable", name


async def test_a_store_with_no_unit_is_unavailable_not_empty():
    """The other half of the same wiring gap: a store wired without the
    unit it is keyed on cannot answer, and «there is no such note» would
    be a lie about a notebook that was never consulted."""
    tools = _tools(notes=InMemoryNoteStore(), unit="")
    assert (await tools["note_read"](title="Dinner"))["status"] == "unavailable"
    assert (await tools["note_search"](query="hotel"))["status"] == "unavailable"


# ── note_read: the TITLE is the handle ───────────────────────────────────────

async def test_note_read_resolves_the_title_the_index_shows_and_returns_the_body():
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner, 9 Sept", body="Nobody has booked yet.")
    tools = _tools(notes=store)

    out = await tools["note_read"](title="Dinner, 9 Sept")

    assert out["status"] == "ok"
    assert out["body"] == "Nobody has booked yet."
    assert out["title"] == "Dinner, 9 Sept"
    # The head revision id rides back so the compare-and-swap has something
    # to compare, even though the model never has to hand it to `note_update`.
    assert out["revision_id"] == note.head_revision_id


async def test_note_read_matches_a_title_case_insensitively():
    """`(unit, lower(title))` is what the partial unique index is on, so
    case is not part of a note's identity — and 「dinner」 typed back at the
    index's 「Dinner, 9 Sept」 must not be a miss."""
    store = InMemoryNoteStore()
    await _seed(store, title="Dinner, 9 Sept", body="Nobody has booked yet.")
    tools = _tools(notes=store)
    assert (await tools["note_read"](title="  dinner, 9 sept "))["status"] == "ok"


async def test_note_read_on_a_merged_title_reaches_its_survivor():
    """The turn's `<notes>` was rendered before a merge, so the
    model names the duplicate's title after it retired. «No note by that
    title» would be false of the question, which lives on in the survivor —
    the read forwards, and says which note it actually read."""
    from nora.domain.notes import NoteSupersedeReason

    store = InMemoryNoteStore()
    survivor = await _seed(store, title="Saturday dinner", body="time and place open")
    duplicate = await _seed(store, title="星期六飯局細節未定", body="asked twice")
    await store.supersede(
        key=UNIT, target=duplicate.id, replaced_by=survivor.id,
        because=NoteSupersedeReason.RETIRED_MERGED, source=None, now_ms=NOW + 1,
    )
    tools = _tools(notes=store)

    out = await tools["note_read"](title="星期六飯局細節未定")

    assert out["status"] == "ok"
    assert out["title"] == "Saturday dinner"
    assert out["body"] == "time and place open"


async def test_note_create_refuses_an_open_title():
    """Nothing in the schema stops two open notes sharing a
    title, and one is an amplifier on both write paths — so the tag path
    refuses to start one, under any spelling `note_handle` treats as the
    same, and points the model at the note that already carries it."""
    store = InMemoryNoteStore()
    await _seed(store, title="Saturday dinner", body="time and place open")
    tools = _tools(notes=store)

    out = await tools["note_create"](
        title="  saturday DINNER ", description="d", uncertainty="u",
        closing_condition="c", body=_b("b"), anchor_date=20261011,
    )

    assert out["status"] == "refused"
    assert "note_read" in out["note"]
    assert len(await store.standing(unit=UNIT)) == 1


async def test_note_read_answers_nothing_found_for_a_title_that_is_not_there():
    store = InMemoryNoteStore()
    await _seed(store, title="Dinner, 9 Sept", body="b")
    tools = _tools(notes=store)
    out = await tools["note_read"](title="Grant's wedding")
    assert out["status"] == "nothing_found"


async def test_note_read_refuses_an_ambiguous_title_and_names_both():
    """Ambiguity REFUSES, it never picks one. Guessing which note was
    meant is how a revision lands on the wrong body — and the refusal has to
    name the candidates or she cannot ask which was meant."""
    store = InMemoryNoteStore()
    # The database forbids this with a partial unique index; the store double
    # does not, which is exactly why the TOOL has to refuse rather than trust
    # the index to have made it impossible.
    await _seed(store, title="Wedding", body="Grant's")
    await _seed(store, title="wedding", body="Blake's")
    tools = _tools(notes=store)

    out = await tools["note_read"](title="Wedding")

    assert out["status"] == "refused"
    assert sorted(out["candidates"]) == ["Wedding", "wedding"]


async def test_note_read_cannot_reach_another_units_note():
    store = InMemoryNoteStore()
    await _seed(store, title="Dinner, 9 Sept", body="b", unit="somebody-else")
    tools = _tools(notes=store)
    assert (await tools["note_read"](title="Dinner, 9 Sept"))["status"] == "nothing_found"


# ── note_search: bodies, never titles ───────────────────────────────────────

async def test_note_search_reaches_into_a_body_the_title_says_nothing_about():
    store = InMemoryNoteStore()
    await _seed(store, title="Dinner, 9 Sept", body="Ada suggested a hotel buffet.")
    tools = _tools(notes=store)

    out = await tools["note_search"](query="hotel")

    assert out["status"] == "ok"
    assert [m["title"] for m in out["matches"]] == ["Dinner, 9 Sept"]
    assert "hotel" in out["matches"][0]["body"]


async def test_note_search_does_not_match_on_the_title_alone():
    """The injected index already shows every title, so a title search
    would duplicate the prompt and earn nothing. A hit on a word that appears
    ONLY in the title means the search is reading the wrong column."""
    store = InMemoryNoteStore()
    await _seed(store, title="Hotel for Grant", body="Nobody has decided.")
    tools = _tools(notes=store)
    assert (await tools["note_search"](query="Hotel"))["status"] == "nothing_found"


# ── note_create ──────────────────────────────────────────────────────────────

async def test_note_create_writes_the_note_and_its_first_revision():
    store = InMemoryNoteStore()
    tools = _tools(notes=store)

    out = await tools["note_create"](
        title="Grant's wedding", description="Nobody knows the venue",
        uncertainty="which venue, and who is going",
        closing_condition="Grant says where it is",
        body=_b("Ada thinks it might be in Sai Kung."),
        anchor_date=20261011, anchor_entities=["grant"],
    )

    assert out["status"] == "ok"
    assert out["title"] == "Grant's wedding"
    (note,) = await store.standing(unit=UNIT)
    assert note.anchor_date == 20261011
    assert note.anchor_entity_ids == ("grant",)
    assert note.established_in == GROUP
    assert store.revisions[note.head_revision_id].body == (
        _stored("Ada thinks it might be in Sai Kung.")
    )
    assert store.revisions[note.head_revision_id].established_by == "tag:miles"


async def test_note_create_surfaces_the_domain_refusal_instead_of_swallowing_it():
    """The four fields are the point of the tier, and three of them are only
    load-bearing because the wrong thing is UNREPRESENTABLE. A tool that
    reported a `ValueError` as a bare failure would hand the model no way to
    fix the call — so the constructor's own sentence is the refusal."""
    store = InMemoryNoteStore()
    tools = _tools(notes=store)

    no_anchor = await tools["note_create"](
        title="We should hang out", description="d", uncertainty="u",
        closing_condition="c", body=_b("b"),
    )
    assert no_anchor["status"] == "refused"
    assert "anchor" in no_anchor["note"]

    long_title = await tools["note_create"](
        title="x" * 61, description="d", uncertainty="u",
        closing_condition="c", body=_b("b"), anchor_date=20261011,
    )
    assert long_title["status"] == "refused"
    assert "60 characters" in long_title["note"]

    assert await store.standing(unit=UNIT) == ()


async def test_note_create_refuses_when_it_does_not_know_who_is_asking():
    """`established_by` distinguishes a tag-path revision from the curator's
    own, so a revision written under nobody's name breaks the strike
    reset. Same refusal `remind` makes for the same reason."""
    tools = _tools(notes=InMemoryNoteStore(), tagger=None)
    out = await tools["note_create"](
        title="T", description="d", uncertainty="u", closing_condition="c",
        body=_b("b"), anchor_date=20261011,
    )
    assert out["status"] == "refused"


# ── note_update: the CAS and the tool-layer backstop ─────────────────────────

async def test_note_update_appends_a_revision_and_moves_the_head():
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner, 9 Sept", body="Nobody has booked.")
    tools = _tools(notes=store)
    await tools["note_read"](title="Dinner, 9 Sept")

    out = await tools["note_update"](
        title="Dinner, 9 Sept", body=_b("Nobody has booked. Ada says 8pm."),
    )

    assert out["status"] == "ok"
    head = store.notes[note.id].head_revision_id
    assert head != note.head_revision_id
    assert store.revisions[head].body == _stored("Nobody has booked. Ada says 8pm.")
    assert store.revisions[head].parent_revision_id == note.head_revision_id
    # Append-only: the revision she read is still there.
    assert store.revisions[note.head_revision_id].body == "Nobody has booked."


async def test_note_update_refuses_when_this_turn_never_read_that_note():
    """The tool-layer half of the latch. `ReplyGuard` vetoes this call before it
    runs; this is the backstop for the one case the guard cannot see — a title
    that resolved to a DIFFERENT note between the read and the write."""
    store = InMemoryNoteStore()
    await _seed(store, title="Dinner, 9 Sept", body="Nobody has booked.")
    tools = _tools(notes=store)

    out = await tools["note_update"](title="Dinner, 9 Sept", body=_b("Ada says 8pm."))

    assert out["status"] == "refused"
    assert "note_read" in out["note"]


async def test_a_head_that_moved_under_her_is_unavailable_never_applied_on_top():
    """R-27's three states at the store layer: «I could not do it», not
    «there was nothing to do» and never a silent overwrite of the arrangement
    the intervening revision made."""
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner, 9 Sept", body="Nobody has booked.")
    tools = _tools(notes=store)
    await tools["note_read"](title="Dinner, 9 Sept")

    # The note-candidates gate appends to it mid-turn, or a parallel turn in
    # the other room of the same unit revises it.
    moved = await store.append_revision(
        unit=UNIT, note_id=note.id, body="Ada booked it.",
        parent_revision_id=note.head_revision_id, established_by="curator",
        now_ms=NOW + 1,
    )

    out = await tools["note_update"](title="Dinner, 9 Sept", body=_b("Nobody has booked."))

    assert out["status"] == "unavailable"
    assert "read it again" in out["note"]
    assert store.notes[note.id].head_revision_id == moved.id


async def test_a_second_update_in_one_turn_writes_against_what_she_just_wrote():
    """Budget 2 makes two revisions in one turn reachable, and the second must
    CAS against the head the FIRST one left — not against the body she read,
    which is no longer the head."""
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner, 9 Sept", body="Nobody has booked.")
    tools = _tools(notes=store)
    await tools["note_read"](title="Dinner, 9 Sept")
    await tools["note_update"](title="Dinner, 9 Sept", body=_b("Ada says 8pm."))

    out = await tools["note_update"](
        title="Dinner, 9 Sept", body=_b("Ada says 8pm, Sai Kung."),
    )

    assert out["status"] == "ok"
    assert store.revisions[store.notes[note.id].head_revision_id].body == (
        _stored("Ada says 8pm, Sai Kung.")
    )


async def test_a_read_that_found_nothing_licenses_no_revision_at_the_tool_layer():
    store = InMemoryNoteStore()
    await _seed(store, title="Dinner, 9 Sept", body="Nobody has booked.")
    tools = _tools(notes=store)
    read = await tools["note_read"](title="Grant's wedding")
    assert read["status"] == "nothing_found"

    out = await tools["note_update"](title="Dinner, 9 Sept", body=_b("Ada says 8pm."))
    assert out["status"] == "refused"


# ── nora_note_update_refused_total: three reasons, two diagnoses ────────────
#
# `not_read` is a reading about the MODEL — it tried to rewrite a body it had
# not seen. `title_moved` and `stale_head` are readings about CONTENTION — the
# note it reached was not the note it read, or the head moved under the call.
# Each reason is its own label on one series, so a query can separate «the
# latch is working» from «two writers are fighting».


async def test_a_stale_head_is_counted_as_a_refusal_with_its_own_reason():
    store = InMemoryNoteStore()
    metrics = Metrics()
    note = await _seed(store, title="Dinner, 9 Sept", body="Nobody has booked.")
    tools = _tools(notes=store, metrics=metrics)
    await tools["note_read"](title="Dinner, 9 Sept")
    await store.append_revision(
        unit=UNIT, note_id=note.id, body="Ada booked it.",
        parent_revision_id=note.head_revision_id, established_by="curator",
        now_ms=NOW + 1,
    )

    out = await tools["note_update"](title="Dinner, 9 Sept", body=_b("Nobody has booked."))

    assert out["status"] == "unavailable"
    assert metrics.get("nora_note_update_refused_total", reason="stale_head") == 1
    # Not filed under either of the other two: a CAS loss is a curator racing
    # the tag path, and reading it as a model that skipped its read would send
    # somebody to fix the wrong thing.
    assert metrics.get("nora_note_update_refused_total", reason="not_read") == 0
    assert metrics.get("nora_note_update_refused_total", reason="title_moved") == 0


async def test_a_title_that_resolved_to_another_note_is_counted_as_title_moved():
    """The tool-layer backstop, which the guard cannot see: the guard's latch is
    keyed on the TITLE and this one on the resolved note ID, so what reaches
    here is a title that now answers to a different note than the one read."""
    store = InMemoryNoteStore()
    metrics = Metrics()
    await _seed(store, title="Dinner, 9 Sept", body="Nobody has booked.")
    tools = _tools(notes=store, metrics=metrics)
    assert (await tools["note_read"](title="Grant's wedding"))["status"] == "nothing_found"

    out = await tools["note_update"](title="Dinner, 9 Sept", body=_b("Ada says 8pm."))

    assert out["status"] == "refused"
    assert metrics.get("nora_note_update_refused_total", reason="title_moved") == 1
    assert metrics.get("nora_note_update_refused_total", reason="stale_head") == 0


async def test_an_update_that_lands_counts_no_refusal():
    store = InMemoryNoteStore()
    metrics = Metrics()
    await _seed(store, title="Dinner, 9 Sept", body="Nobody has booked.")
    tools = _tools(notes=store, metrics=metrics)
    await tools["note_read"](title="Dinner, 9 Sept")

    out = await tools["note_update"](title="Dinner, 9 Sept", body=_b("Ada says 8pm."))

    assert out["status"] == "ok"
    assert metrics.get("nora_note_update_refused_total") == 0


# ── R-55: the latch, measured through the veto ───────────────────────────────
#
# Every test below asserts through `evaluate`'s refusal rather than by reading
# the latch set. The set is invisible to the model; the veto is where a miss
# costs something, and a test that reads the set goes green against a latch
# that never vetoes anything.

BUDGETS = ToolRegistry().budgets()
TERMINAL = ToolRegistry().terminal_tools()


def _guard(metrics: Metrics | None = None) -> ReplyGuard:
    return ReplyGuard(conversation_id=GROUP, budgets=BUDGETS, terminal=TERMINAL,
                      metrics=metrics or Metrics())


class _AfterEvent:
    """What Strands really passes to `AfterToolCallEvent` — the tool's own dict
    wrapped in `{"content": [{"text": json.dumps(...)}]}`, which is the only
    place the tool's REAL status survives (see `_unwrap_tool_status`)."""

    def __init__(self, name, payload, *, exception=None, cancel_message=None,
                 tool_input=None):
        self.tool_use = {"name": name, "input": tool_input or {}}
        self.exception = exception
        self.cancel_message = cancel_message
        self.result = {"content": [{"text": json.dumps(payload)}]}


def _read_happened(guard, title, **kw):
    guard._after_tool(_AfterEvent(
        "note_read", {"status": "ok", "title": title},
        tool_input={"title": title}, **kw,
    ))


def test_r55_a_revision_is_vetoed_when_this_turn_never_read_the_note():
    guard = _guard()
    veto = guard.evaluate("note_update", {"title": "Dinner, 9 Sept", "body": "x"})
    assert veto is not None
    assert "note_read" in veto


def test_r55_a_revision_is_allowed_once_the_read_succeeded():
    guard = _guard()
    _read_happened(guard, "Dinner, 9 Sept")
    assert guard.evaluate("note_update", {"title": "Dinner, 9 Sept", "body": "x"}) is None


def test_r55_the_veto_is_counted_under_its_own_reason_as_well_as_its_rule():
    """Two series, two questions. `nora_guard_vetoes_total{rule}` answers «which
    rule fired» across every guard; `nora_note_update_refused_total{reason}`
    answers «why did a note_update not land», and its other two reasons live in
    the tool and never reach a veto at all."""
    metrics = Metrics()
    guard = _guard(metrics)

    assert guard.evaluate("note_update", {"title": "Dinner, 9 Sept", "body": "x"})

    assert metrics.get("nora_note_update_refused_total", reason="not_read") == 1
    assert metrics.get("nora_guard_vetoes_total", rule="R-55") == 1


def test_r55_an_allowed_revision_counts_no_refusal():
    metrics = Metrics()
    guard = _guard(metrics)
    _read_happened(guard, "Dinner, 9 Sept")

    assert guard.evaluate("note_update", {"title": "Dinner, 9 Sept", "body": "x"}) is None

    assert metrics.get("nora_note_update_refused_total") == 0


def test_r55_reading_one_note_does_not_license_revising_another():
    guard = _guard()
    _read_happened(guard, "Dinner, 9 Sept")
    veto = guard.evaluate("note_update", {"title": "Grant's wedding", "body": "x"})
    assert veto is not None


def test_r55_a_read_that_raised_licenses_nothing():
    """`_after_tool` returns early on `exception is not None`, and the latch
    write is BELOW that return on purpose: `release()` means «this call
    finished» and is true on every path, the latch means «this call
    SUCCEEDED» and is true on one. Hoisting the latch to sit with `release`
    would license a revision off a call that never produced a body."""
    guard = _guard()
    _read_happened(guard, "Dinner, 9 Sept", exception=RuntimeError("boom"))
    assert guard.evaluate(
        "note_update", {"title": "Dinner, 9 Sept", "body": "x"},
    ) is not None


def test_r55_a_read_the_guard_cancelled_licenses_nothing():
    guard = _guard()
    _read_happened(
        guard, "Dinner, 9 Sept", cancel_message="You already called note_read.",
    )
    assert guard.evaluate(
        "note_update", {"title": "Dinner, 9 Sept", "body": "x"},
    ) is not None


def test_r55_a_read_that_found_no_such_note_licenses_nothing():
    """The case the latch exists to catch, and the one a «did the call finish?»
    check would miss: reading a note that ISN'T THERE tells her nothing about a
    body, so it must not become a licence to append to one."""
    guard = _guard()
    guard._after_tool(_AfterEvent(
        "note_read", {"status": "nothing_found"},
        tool_input={"title": "Dinner, 9 Sept"},
    ))
    assert guard.evaluate(
        "note_update", {"title": "Dinner, 9 Sept", "body": "x"},
    ) is not None


def test_r55_an_ambiguous_read_licenses_nothing():
    guard = _guard()
    guard._after_tool(_AfterEvent(
        "note_read", {"status": "refused", "candidates": ["Wedding", "wedding"]},
        tool_input={"title": "Wedding"},
    ))
    assert guard.evaluate("note_update", {"title": "Wedding", "body": "x"}) is not None


def test_r55_does_not_apply_to_note_create():
    """The index is uncapped, so every title is already in front of her
    and whether a note already covers this ground is checkable from the prompt
    alone. Create needs no read."""
    guard = _guard()
    assert guard.evaluate("note_create", {"title": "New", "body": "x"}) is None


def test_a_vetoed_revision_does_not_spend_its_budget():
    """DEC-33's mechanism, reused rather than re-invented: `_after_tool` never
    `record`s a cancelled call, so the model's corrected second attempt — read
    first, then revise — is not refused by R-7 for a call that never ran."""
    guard = _guard()
    event = type("E", (), {})()
    event.tool_use = {"name": "note_update", "input": {"title": "D", "body": "x"}}
    event.invocation_state = {"conversation_id": GROUP}
    event.cancel_tool = None
    guard._before_tool(event)
    assert event.cancel_tool is not None
    guard._after_tool(_AfterEvent(
        "note_update", {"status": "refused"}, cancel_message=event.cancel_tool,
    ))
    _read_happened(guard, "D")
    assert guard.evaluate("note_update", {"title": "D", "body": "x"}) is None


async def test_two_rooms_in_different_units_see_different_notebooks():
    """The leak this resolver exists to prevent, and it fails if the unit
    stops being read from the ROOM.

    `policy.toml` puts the operator's own rooms in `ridgeline` and the group in
    `friends`, deliberately never paired (DEC-105/R-49 — a unit is the
    boundary `send_to` may not cross). A process-wide unit string hands
    one room the OTHER's notebook: `note_read` answers from it, `note_update`
    writes into it, past the unit boundary.

    Both calls below go through the SAME resolver and the same store; only the
    room differs. That is what makes this red when the tool stops consulting
    the room — with one unit configured, every other test in this file
    passes either way.
    """
    store = InMemoryNoteStore()
    await store.create(
        unit="friends", title="Group dinner", description="undecided",
        uncertainty="where", closing_condition="somebody picks",
        established_in="signal:friends-room", body="three places",
        established_by="miles", now_ms=1, anchor_date=20260909,
    )
    book = {"friends-room": "friends", "ridgeline-room": "ridgeline"}

    in_friends = await _tools(
        notes=store, rooms=book, conversation_id="friends-room",
    )["note_read"](title="Group dinner")
    in_ridgeline = await _tools(
        notes=store, rooms=book, conversation_id="ridgeline-room",
    )["note_read"](title="Group dinner")

    assert in_friends["status"] == "ok"
    # Same store, same title, different room: not there, and not readable.
    assert in_ridgeline["status"] == "nothing_found"


async def test_a_room_policy_cannot_name_gets_unavailable_never_a_guess():
    """An unlisted room has no unit, and the verbs must say so.

    Defaulting to any particular notebook would write a note into one the room
    is not part of — and `standing` would then render it to people who never
    saw the conversation it came from. `unavailable` is «I could not check»;
    `nothing_found` would be the lie.
    """
    store = InMemoryNoteStore()
    out = await _tools(notes=store, unit=None)["note_read"](title="anything")
    assert out["status"] == "unavailable"


# ── ADR 0015: how to report a status rides in the result ────────────────────

async def test_every_non_ok_note_status_carries_a_note():
    """The four descriptions only NAME their statuses; what to say or fix is
    the result's `note`. Every non-ok path the fakes reach is driven here, so
    one that loses its note goes red.

    One note is exempt from the dash check: a domain refusal is
    `nora.domain.notes`' own sentence, which the curator reads too."""
    store = InMemoryNoteStore()
    await _seed(store, title="Dinner, 9 Sept", body="Nobody has booked.")
    await _seed(store, title="Wedding", body="Grant's")
    await _seed(store, title="wedding", body="Blake's")
    tools = _tools(notes=store)
    stale_store = InMemoryNoteStore()
    stale = await _seed(stale_store, title="Dinner, 9 Sept", body="Nobody has booked.")
    stale_tools = _tools(notes=stale_store)
    await stale_tools["note_read"](title="Dinner, 9 Sept")
    await stale_store.append_revision(
        unit=UNIT, note_id=stale.id, body="Ada booked it.",
        parent_revision_id=stale.head_revision_id, established_by="curator",
        now_ms=NOW + 1,
    )
    create = {
        "title": "T", "description": "d", "uncertainty": "u",
        "closing_condition": "c", "body": _b("b"), "anchor_date": 20261011,
    }
    cases = {
        "unavailable/read_no_store": (_tools(notes=None)["note_read"], {"title": "x"}),
        "nothing_found/read": (tools["note_read"], {"title": "Grant's wedding"}),
        "refused/read_ambiguous": (tools["note_read"], {"title": "Wedding"}),
        "refused/search_empty": (tools["note_search"], {"query": "  "}),
        "nothing_found/search": (tools["note_search"], {"query": "hotel"}),
        "refused/create_no_tagger": (
            _tools(notes=InMemoryNoteStore(), tagger=None)["note_create"], create,
        ),
        "refused/create_domain": (
            tools["note_create"], {**create, "anchor_date": None},
        ),
        "refused/update_no_tagger": (
            _tools(notes=store, tagger=None)["note_update"],
            {"title": "Dinner, 9 Sept", "body": _b("b")},
        ),
        "refused/update_not_read": (
            tools["note_update"], {"title": "Dinner, 9 Sept", "body": _b("b")},
        ),
        "refused/update_ambiguous": (tools["note_update"], {"title": "Wedding", "body": _b("b")}),
        "nothing_found/update": (tools["note_update"], {"title": "Nope", "body": _b("b")}),
        "unavailable/update_stale": (
            stale_tools["note_update"], {"title": "Dinner, 9 Sept", "body": _b("b")},
        ),
    }
    exempt = {"refused/create_domain"}
    for case, (call, kwargs) in cases.items():
        out = await call(**kwargs)
        assert out["status"] == case.split("/")[0], (case, out)
        assert isinstance(out.get("note"), str) and out["note"].strip(), case
        if case not in exempt:
            assert "—" not in out["note"] and "–" not in out["note"], case


async def test_a_missing_title_note_says_to_check_the_index_not_to_guess_variants():
    """The rule «the titles in <notes> are the only ones there are, so check it
    rather than trying variations» rides on the `nothing_found` it is about,
    not in note_read's description."""
    store = InMemoryNoteStore()
    out = await _tools(notes=store)["note_read"](title="Grant's wedding")
    assert out["status"] == "nothing_found"
    assert "<notes>" in out["note"] and "variations" in out["note"]


# ── the body's form ─────────────────────────────────────────────────────────


async def test_note_create_maps_positions_to_row_ids_and_stores_the_normalised_body():
    """She cites what she sees (position 1), the store keeps ids (501)."""
    store = InMemoryNoteStore()
    tools = _tools(notes=store)

    out = await tools["note_create"](
        title="Dinner", description="where is open", uncertainty="where",
        closing_condition="a place is booked",
        body=(
            "## decided\n* dinner is on 2026-10-09 Fri (Miles Novak) [#1]\n\n## Open\n"
            "- where [#1]\n## Owed\n- Miles Novak -> book a table -> no date given [#1]"
        ),
        anchor_date=20261009,
    )

    assert out["status"] == "ok"
    [note] = await store.standing(unit=UNIT)
    assert store.revisions[note.head_revision_id].body == (
        "## Decided\n- dinner is on 2026-10-09 Fri (Miles Novak) [501]\n## Open\n- where [501]"
        "\n## Owed\n- Miles Novak -> book a table -> no date given [501]"
    )


@pytest.mark.parametrize(("rule", "body"), [
    ("not_a_line", "Miles wants dinner on the 9th."),
    ("unknown_id", "## Decided\n## Open\n- where [#2]"),
    ("unknown_id", "## Decided\n## Open\n- where [501]"),
    ("misattributed", "## Decided\n- dinner (Ada) [#1]\n## Open\n- where [#1]"),
    ("unknown_owner",
     "## Decided\n## Open\n- where [#1]\n## Owed\n- Nora -> book -> no date given [#1]"),
    ("bad_date", "## Decided\n- on 2026-10-09 Sat (Miles Novak) [#1]\n## Open\n- where [#1]"),
], ids=["prose", "position_out_of_range", "bare_id_not_carried", "misattributed",
        "nora_owes", "bad_date"])
async def test_note_create_refuses_a_body_out_of_form_and_names_the_rule(rule, body):
    """Refused, not repaired, with the rule in the note so she can fix
    the call; nothing is written."""
    store = InMemoryNoteStore()
    tools = _tools(notes=store)

    out = await tools["note_create"](
        title="Dinner", description="where is open", uncertainty="where",
        closing_condition="a place is booked", body=body, anchor_date=20261009,
    )

    assert out["status"] == "refused"
    assert rule in out["note"]
    assert await store.standing(unit=UNIT) == ()


async def test_note_update_may_keep_a_bare_id_the_head_already_cites():
    """A restatement carries the head's own ids forward as bare ids; any
    other bare id is one she could only have invented."""
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner, 9 Sept", body="## Decided\n## Open\n- where [777]")
    tools = _tools(notes=store)
    await tools["note_read"](title="Dinner, 9 Sept")

    kept = await tools["note_update"](
        title="Dinner, 9 Sept",
        body="## Decided\n- Miles booked it (Miles Novak) [#1, 777]\n## Open",
    )
    assert kept["status"] == "ok"
    head = store.notes[note.id].head_revision_id
    assert store.revisions[head].body == (
        "## Decided\n- Miles booked it (Miles Novak) [501, 777]\n## Open"
    )

    invented = await tools["note_update"](
        title="Dinner, 9 Sept", body="## Decided\n- booked (Miles Novak) [#1, 778]\n## Open",
    )
    assert invented["status"] == "refused" and "unknown_id" in invented["note"]
    assert store.notes[note.id].head_revision_id == head


def test_the_body_argument_names_the_grammar():
    """She learns the form from the `body` argument, in two sentences,
    and from the refusal; nothing else teaches it on the tag path."""
    tools = _tools(notes=InMemoryNoteStore())
    create = tools["note_create"].__doc__ or ""
    update = tools["note_update"].__doc__ or ""
    create_body = create[create.index("    body:"):]
    assert "## Decided and ## Open" in create_body and "[#n]" in create_body
    assert "[#n]" in update[update.index("    body:"):]
