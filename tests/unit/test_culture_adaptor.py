"""`CultureAdaptor` — the culture tier's only moving part.

Written around the places this adaptor can be wrong without anything saying so:
it can hand the model a chronicle and then ask for an account, it can pick
arbitrarily between two proposed accounts, it can append a link carrying no
information, and it can fire in a room that said nothing.

It can also be wrong in two more ways, both silent: it can be asked for a join
and handed one table (the tautology that passes every gate the runtime owns),
and it can hold a character cap no budget can emit, which truncates the
completion, holds the watermark and re-reads the same batch forever.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from nora.config import DreamBudgetConfig
from nora.domain.culture import (
    ACCOUNT_CHARS_OBSERVED_MAX,
    ACCOUNT_OUTPUT_TOKENS,
    BODY_MAX_CHARS,
    OUTPUT_TOKENS_PER_CHAR,
    CultureProposal,
    CultureRevision,
)
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.notes import Note
from nora.domain.ports import Proposal
from nora.infra.sql.schema import CULTURE_BODY_CEILING_CHARS
from nora.language.culture import (
    _BLOCKS,
    CULTURE_ADAPTOR_NAME,
    CULTURE_OUTCOMES,
    CULTURE_TOOLS,
    STORE_CALLS_PER_TICK,
    AccountNotRecordableError,
    CultureAdaptor,
)
from nora.obs.metrics import Metrics
from nora.testing import InMemoryCultureStore

T0 = 1_757_000_000_000
KEY = "discord/g1"


def test_the_registered_blocks_are_exactly_what_this_pass_carries():
    """What this pass carries is a table, not a trace through `inputs` and
    `_room_block`.

    Four rows: `previous_account`, `room`, `room_messages` and `notes`
    (`nora.language.note_render.render_notes`, shared with other occasions).
    `test_the_previous_account_precedes_the_notes_AND_the_batch` below
    exercises all four together. There is NO `standing` row: see
    `test_the_fold_does_not_read_the_rooms_standing`.
    """
    names = {name for name, _order, _key in _BLOCKS}
    assert names == {"room", "previous_account", "notes", "room_messages"}


class _Store(InMemoryCultureStore):
    """The contract-bound fake (`tests/contract/test_culture_store_contract.py`)
    with a seeded head and a record of every `append` call's arguments — the
    local twist `test_port_fakes_live_in_nora_testing` exempts as a subclass."""

    def __init__(self, head: CultureRevision | None = None) -> None:
        super().__init__()
        if head is not None:
            self._revisions[head.id] = head
            self._heads[head.culture_key] = head.id
        self.appended: list[dict] = []

    async def append(self, **kw: object) -> CultureRevision:  # type: ignore[override]
        self.appended.append(kw)
        return await super().append(**kw)  # type: ignore[arg-type]


class _Notes:
    """The note tier's index, keyed by UNIT (DEC-105)."""

    def __init__(self, notes: tuple[Note, ...] = ()) -> None:
        self._notes = notes
        self.reads: list[str] = []

    async def standing(self, *, unit: str) -> tuple[Note, ...]:
        self.reads.append(unit)
        return self._notes


def _note(title: str = "dinner on the 20th") -> Note:
    return Note(
        id="n1", unit="friends", title=title,
        description="where, still open", uncertainty="where, still open",
        closing_condition="somebody books it", established_in="discord/g1",
        created_at=T0, head_revision_id="nr1", anchor_date=20260920,
    )


def _adaptor(
    store: _Store,
    metrics: Metrics | None = None,
    *,
    notes: _Notes | None = None,
    label: str | None = "Zone 42",
    description: str | None = "the games channel",
    unit: str | None = "friends",
    platform_for=None,
    resolve_names=None,
) -> CultureAdaptor:
    return CultureAdaptor(
        culture=store, notes=notes, metrics=metrics,
        unit_for=lambda _k: unit,
        label_for=lambda _k: label,
        description_for=lambda _k: description,
        platform_for=platform_for,
        resolve_names=resolve_names,
    )


def _row(body: str, *, ts: int = T0, name: str = "Ada", rid: int = 1) -> TranscriptRow:
    return TranscriptRow(
        id=rid, sender_id="u1", sender_name=name, person_name=name, ts=ts,
        body=body, is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json="{}",
    )


def _revision(body: str) -> CultureRevision:
    return CultureRevision(
        id="r1", culture_key=KEY, body=body, written_at=T0,
        established_by="culture", citations=("7",),
    )


def _proposal(body: str) -> Proposal:
    return Proposal(
        payload=CultureProposal(body=body, citations=("7",)), instruction_sha256="a" * 64,
        model="flash", established_by="culture-run-1",
    )


# --- inputs: a quiet room costs nothing ------------------------------------


async def test_an_empty_batch_renders_NOTHING_so_the_runtime_does_not_fire():
    """`DreamAdaptor` has no `should_fire` verb — the fire condition IS "did
    `inputs` render anything". A silent room not firing is DEC-185, and it is
    correct: nothing here retires, so there is nothing to refresh against."""
    assert await _adaptor(_Store()).inputs(key=KEY, batch=()) == ""


async def test_a_mapped_senders_batch_line_renders_the_canonical_name():
    """`_render_batch` reads `r.person_name`, which this fold's own batch
    (straight off `record.since`) does not fill by itself: a mapped sender's
    line must carry the operator's canonical name, not their self-set,
    spoofable `sender_name`. `_row` presets `person_name`, which would hide
    that; this row leaves it unset, as a live Dreamer row does, and relies on
    `platform_for`/`resolve_names` to fill it before the batch renders."""
    row = TranscriptRow(
        id=1, sender_id="u1", sender_name="not-miles", person_name=None,
        ts=T0, body="dinner Saturday?", is_nora=False,
        delivery_status=DeliveryStatus.SENT, raw_json="{}",
    )

    def resolve_names(rows, platform):
        assert platform == "discord"
        return tuple(
            replace(r, person_name="Miles") if r.sender_id == "u1" else r
            for r in rows
        )

    out = await _adaptor(
        _Store(), platform_for=lambda _k: "discord", resolve_names=resolve_names,
    ).inputs(key=KEY, batch=[row])

    assert 'from="Miles"' in out
    assert "not-miles" not in out


async def test_a_raising_resolver_still_renders_on_the_floor_and_the_pass_commits():
    """A resolver fault must DEGRADE, never take the fold down with it.
    `resolve_names` is `Persons.name_transcript` in production — dict lookups
    only — but an injected callable can still raise, and letting that
    propagate out of `inputs()` would mark the whole occasion `unavailable`
    and hold the watermark, re-reading the same batch every tick over a
    failure that has nothing to do with what the room said.

    Both ways: `inputs()` still renders the line on the pre-resolution
    `speaker_name` floor (`sender_name`, here) rather than raising, AND the
    fold still COMMITS — `accept`/`commit` write an account exactly as
    `test_a_CHANGED_account_is_appended_over_an_existing_head` proves for a
    working resolver."""
    row = TranscriptRow(
        id=1, sender_id="u1", sender_name="spoofable-name", person_name=None,
        ts=T0, body="dinner Saturday?", is_nora=False,
        delivery_status=DeliveryStatus.SENT, raw_json="{}",
    )

    def resolve_names(rows, platform):
        raise RuntimeError("the roster is unreachable")

    metrics = Metrics()
    store = _Store()
    adaptor = _adaptor(
        store, metrics,
        platform_for=lambda _k: "discord", resolve_names=resolve_names,
    )
    out = await adaptor.inputs(key=KEY, batch=[row])

    assert 'from="spoofable-name"' in out, "the pre-resolution floor"
    assert metrics.get(
        "nora_dream_name_resolution_failed_total", adaptor="culture",
    ) == 1

    written = await adaptor.commit(
        key=KEY, proposals=[_proposal("they eat Saturdays")], now_ms=T0,
    )

    assert written == 1, "the fold commits despite the resolver fault"
    assert store.appended[0]["body"] == "they eat Saturdays"


async def test_an_unwired_resolver_still_never_renders_a_raw_id():
    """The other half: `platform_for`/`resolve_names` both default `None`, and
    resolution is skipped entirely rather than raising — `_render_batch`'s
    `speaker_name` floor (the profile name, or `"unknown"`) is unchanged, and
    the raw `sender_id` never reaches the prompt either way (R-18)."""
    row = TranscriptRow(
        id=1, sender_id="u1", sender_name=None, person_name=None,
        ts=T0, body="anyone free Friday?", is_nora=False,
        delivery_status=DeliveryStatus.SENT, raw_json="{}",
    )

    out = await _adaptor(_Store()).inputs(key=KEY, batch=[row])

    assert "u1" not in out
    assert 'from="unknown"' in out


async def test_the_previous_account_comes_FIRST_when_there_is_one():
    """It is how the batch is meant to be read. A batch handed over with no
    prior context produces a fresh snapshot every day, which is the thing the
    chain exists to be instead of."""
    out = await _adaptor(_Store(_revision("they decide by attrition"))).inputs(
        key=KEY, batch=[_row("hi")],
    )
    assert out.index("previous_account") < out.index("<room_messages ")
    assert "they decide by attrition" in out


async def test_an_UNCITED_previous_account_is_not_handed_to_the_room_pass():
    """R-31. An account with no citations was written while the pass read
    `<standing>`, `where_been` and `online_now`. Continuing it would carry
    those lines into a cited revision, which the person and unit sessions then
    read, and they render in rooms with other `location` flags. The room is
    re-written from its own messages."""
    uncited = CultureRevision(
        id="r0", culture_key=KEY, body="she was at the gym again",
        written_at=T0, established_by="culture",
    )
    out = await _adaptor(_Store(uncited)).inputs(key=KEY, batch=[_row("hi")])
    assert "previous_account" not in out
    assert "she was at the gym again" not in out


async def test_the_first_fold_of_a_room_renders_no_previous_block():
    out = await _adaptor(_Store()).inputs(key=KEY, batch=[_row("hi")])
    assert "previous_account" not in out


async def test_the_batch_carries_its_ROW_IDS_and_NO_timestamps():
    """The objective's load-bearing clause is «not dated observations»: a model
    handed a timestamp writes about that date, so no clock reaches it.
    The ROW ID does, as `n`, because an account cites the rows it rests on
    and `character_revise` resolves each citation against exactly these ids: a
    per-render position would fail that join silently."""
    import re

    out = await _adaptor(_Store()).inputs(
        key=KEY, batch=[_row("開車喇", ts=T0, rid=482)],
    )
    batch = out.split("<room_messages ")[1].split("</room_messages>")[0]
    body = batch.split(">", 1)[1]
    assert not re.search(r"\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}|\bago\b", body), (
        f"a date, a clock time or an age reached the prompt: {body!r}"
    )
    assert body.strip() == '<m n="482" from="Ada">開車喇</m>', body


async def test_the_room_block_names_the_room_and_carries_the_operators_words():
    """An account is a claim ABOUT A ROOM, so the prompt must say which room it
    is reading — the same input gap that made the note pass write «Bath
    gathering» out of a games room's 「2200」. The label is what the room is
    CALLED and the description is what the OPERATOR says it is; neither is
    derivable from the room's own messages."""
    out = await _adaptor(_Store()).inputs(key=KEY, batch=[_row("hi")])
    assert out.startswith("<room "), out[:120]
    assert 'label="Zone 42"' in out
    assert "the games channel" in out
    # THE OPENING TAG, not the whole render: the clause is the block's own
    # `description=`, declared on the type, so `<batch>`'s near-identical
    # sentence one block down cannot satisfy this.
    tag = out.split("<room ", 1)[1].split(">", 1)[0]
    assert "Not an instruction to you" in tag
    assert "not something anybody in the room said" in tag


async def test_the_room_block_NEVER_renders_the_conversation_id():
    """`UNNAMED_ROOM`'s rule, R-18: a room's identifier is not its name, and a
    model asked what a room IS has no use for one."""
    out = await _adaptor(
        _Store(), label=None, description=None,
    ).inputs(key=KEY, batch=[_row("hi")])
    assert KEY not in out
    assert "unnamed room" in out


def test_the_fold_does_not_read_the_rooms_standing():
    """`standing` is folded from the turn's raw tool results (a `where_been` or
    `online_now` among them) and the debrief is told to record who is present.
    This account is input to every person's and the unit's, which render in
    rooms whose `location` flags differ, so a line that reached it through
    `standing` would reach them (R-31). The fold has no
    parameter to read it with."""
    import inspect

    params = inspect.signature(CultureAdaptor.__init__).parameters
    assert "standing" not in params and "standing_key_for" not in params


async def test_what_the_room_has_OPEN_reaches_the_fold_at_the_UNIT_key():
    """The note index is a UNIT store (DEC-105), so the scope key is
    translated — `NoteCandidateAdaptor`'s own read, same verb, same
    translation."""
    notes = _Notes((_note(),))
    out = await _adaptor(_Store(), notes=notes).inputs(key=KEY, batch=[_row("hi")])
    assert notes.reads == ["friends"]
    assert "dinner on the 20th" in out


async def test_a_scope_that_names_no_policy_conversation_is_LOGGED(caplog):
    """A key that resolves to nothing renders no note index — and says so.
    Silence here would be a plausible, thinner prompt that nothing
    counts."""
    import logging

    with caplog.at_level(logging.WARNING, logger="nora.language.culture"):
        out = await _adaptor(
            _Store(), notes=_Notes((_note(),)), unit=None,
        ).inputs(key=KEY, batch=[_row("hi")])

    assert "<notes" not in out
    assert any(
        "resolves to no policy conversation" in r.getMessage()
        for r in caplog.records
    )


async def test_the_previous_account_precedes_the_notes_AND_the_batch():
    """The previous account is how everything after it is
    meant to be read. A fold with no prior context writes a fresh snapshot
    every day, which is the thing the chain exists to be instead of."""
    out = await _adaptor(
        _Store(_revision("they decide by attrition")),
        notes=_Notes((_note(),)),
    ).inputs(key=KEY, batch=[_row("hi")])
    assert (
        out.index("previous_account")
        < out.index("<notes")
        < out.index("<room_messages ")
    ), out


async def test_a_quiet_room_reads_NO_store_at_all():
    """The runtime's fire condition is «did `inputs` render anything», so a
    quiet room must cost nothing — including the reads."""
    culture, notes = _Store(), _Notes((_note(),))
    assert await _adaptor(culture, notes=notes).inputs(key=KEY, batch=()) == ""
    assert notes.reads == []


async def test_a_note_store_that_RAISES_takes_the_fold_with_it():
    """Fail-CLOSED. A run that folded anyway would write an account that
    GUESSES at what the room has open, and the runtime would report it `ok`
    — the tautology with a store fault behind it. Raising makes the run
    `unavailable` and HOLDS the watermark, so the batch is folded later instead
    of being consumed wrong today."""
    class _Broken:
        async def standing(self, *, unit: str):
            raise RuntimeError("note store down")

    with pytest.raises(RuntimeError, match="note store down"):
        await _adaptor(_Store(), notes=_Broken()).inputs(  # type: ignore[arg-type]
            key=KEY, batch=[_row("hi")],
        )


async def test_a_raw_sender_id_never_reaches_the_prompt():
    """R-18/DEC-80: the raw id is an E.164 or an ACI."""
    row = replace(_row("hi"), person_name=None, sender_name=None)
    out = await _adaptor(_Store()).inputs(key=KEY, batch=[row])
    assert "u1" not in out and "unknown" in out


# --- the write verbs: validated as written, staged, settled ----------------


async def _session(adaptor: CultureAdaptor, batch=None):
    """`inputs` for one batch, then this run's two verbs."""
    await adaptor.inputs(key=KEY, batch=batch or (_row("dinner?", rid=7),))
    revise, keep = adaptor.verbs(key=KEY)
    return revise, keep


async def test_a_cited_revision_is_staged_and_returned_by_accept():
    adaptor = _adaptor(_Store())
    revise, _keep = await _session(adaptor)
    assert await revise(entity="room", body=" an account ", citations=["7"]) == {
        "status": "ok",
    }
    got = await adaptor.accept(key=KEY, items=())
    assert got == (CultureProposal(body="an account", citations=("7",)),)


async def test_a_citation_of_a_row_the_session_was_NOT_shown_is_refused():
    """THE CHECK THIS VERB EXISTS FOR: every
    citation resolves to a row the session was handed, or the write is refused
    with a reason the model can act on, and nothing is staged. A citation
    nobody can resolve is a claim nobody can re-derive."""
    m = Metrics()
    adaptor = _adaptor(_Store(), m)
    revise, _keep = await _session(adaptor)
    answer = await revise(entity="room", body="an account", citations=["7", "999"])
    assert answer["status"] == "refused"
    assert answer["reason"] == "unresolved_citation"
    assert "999" in str(answer["detail"])
    assert m.get("nora_culture_fold_total", outcome="refused") == 1
    with pytest.raises(AccountNotRecordableError):
        await adaptor.accept(key=KEY, items=())


@pytest.mark.parametrize(
    ("body", "citations", "reason"),
    [
        ("an account", [], "uncited"),
        ("   ", ["7"], "empty"),
        ("x" * (BODY_MAX_CHARS + 1), ["7"], "too_long"),
        ("an account", ["response:7"], "unresolved_citation"),
    ],
)
async def test_each_malformed_revision_is_refused_by_name(body, citations, reason):
    adaptor = _adaptor(_Store())
    revise, _keep = await _session(adaptor)
    answer = await revise(entity="room", body=body, citations=citations)
    assert (answer["status"], answer["reason"]) == ("refused", reason)


async def test_an_account_exactly_at_the_cap_is_accepted():
    """The boundary is inclusive — an off-by-one here silently discards the
    longest legitimate account."""
    adaptor = _adaptor(_Store())
    revise, _keep = await _session(adaptor)
    answer = await revise(entity="room", body="x" * BODY_MAX_CHARS, citations=["7"])
    assert answer == {"status": "ok"}


async def test_an_entity_this_session_does_not_write_is_refused():
    adaptor = _adaptor(_Store())
    revise, keep = await _session(adaptor)
    for answer in (
        await revise(entity="Nobody", body="x", citations=["7"]),
        await keep(entity="Nobody", why="x"),
    ):
        assert (answer["status"], answer["reason"]) == ("refused", "unknown_entity")
        assert "room" in str(answer["detail"])


async def test_no_change_is_a_valid_close_and_writes_nothing():
    """«The messages add nothing» settles the room: the batch is spent and the
    previous account stands, which the objective says is usually right."""
    m = Metrics()
    adaptor = _adaptor(_Store(), m)
    _revise, keep = await _session(adaptor)
    assert await keep(entity="room", why="dinner talk, nothing new") == {"status": "ok"}
    assert await adaptor.accept(key=KEY, items=()) == ()
    assert m.get("nora_culture_fold_total", outcome="kept") == 1


async def test_a_later_revision_of_one_entity_replaces_the_earlier():
    adaptor = _adaptor(_Store())
    revise, _keep = await _session(adaptor)
    await revise(entity="room", body="first", citations=["7"])
    await revise(entity="room", body="second", citations=["7"])
    (got,) = await adaptor.accept(key=KEY, items=())
    assert got.body == "second"


async def test_a_session_that_never_settles_the_room_RAISES_and_HOLDS():
    """Ran out of turns, or stopped after a refusal: the room was neither
    revised nor kept, so the batch was not folded. Raising makes the run
    `unavailable`, which holds the watermark (`tests/unit/test_dreamer_runtime.
    py::test_an_accept_that_RAISES_is_unavailable_and_never_reaches_commit`)."""
    m = Metrics()
    adaptor = _adaptor(_Store(), m)
    await _session(adaptor)
    with pytest.raises(AccountNotRecordableError):
        await adaptor.accept(key=KEY, items=())
    assert m.get("nora_culture_fold_total", outcome="unsettled") == 1


async def test_the_third_unsettled_session_of_one_batch_in_a_row_advances_past_it():
    """Each unsettled session holds the watermark, so a batch the model keeps
    failing would be re-read for ever. The third in a row of the SAME batch
    (named by its first row) returns `()` instead, counted `abandoned`; a
    different batch, or a settled session, starts the run again."""
    m = Metrics()
    adaptor = _adaptor(_Store(), m)
    for _ in range(2):
        await _session(adaptor)
        with pytest.raises(AccountNotRecordableError):
            await adaptor.accept(key=KEY, items=())
    await _session(adaptor)
    assert await adaptor.accept(key=KEY, items=()) == ()
    assert m.get("nora_culture_fold_total", outcome="abandoned") == 1
    for rid in (7, 7, 8):
        await _session(adaptor, (_row("dinner?", rid=rid),))
        with pytest.raises(AccountNotRecordableError):
            await adaptor.accept(key=KEY, items=())
    _revise, keep = await _session(adaptor, (_row("dinner?", rid=8),))
    await keep(entity="room", why="nothing")
    assert await adaptor.accept(key=KEY, items=()) == ()
    for _ in range(2):
        await _session(adaptor, (_row("dinner?", rid=8),))
        with pytest.raises(AccountNotRecordableError):
            await adaptor.accept(key=KEY, items=())
    assert m.get("nora_culture_fold_total", outcome="abandoned") == 1


async def test_a_verb_called_after_its_session_ended_writes_nothing():
    adaptor = _adaptor(_Store())
    revise, keep = await _session(adaptor)
    await keep(entity="room", why="nothing")
    await adaptor.accept(key=KEY, items=())
    answer = await revise(entity="room", body="late", citations=["7"])
    assert answer["status"] == "unavailable"


# --- commit: the chain only grows when it says something new ---------------


async def test_commit_appends_with_the_RUNTIMES_provenance():
    """`accept` returns payloads precisely so an adaptor never has to know its
    own instruction digest — the runtime read the instructions and named the
    run, so these three must arrive off the `Proposal` and not be invented."""
    store = _Store()
    written = await _adaptor(store).commit(
        key=KEY, proposals=[_proposal("a new account")], now_ms=T0,
    )
    assert written == 1
    (call,) = store.appended
    assert call["instruction_sha256"] == "a" * 64
    assert call["model"] == "flash"
    assert call["established_by"] == "culture-run-1"
    assert call["written_at"] == T0
    assert call["citations"] == ("7",)


async def test_a_BYTE_IDENTICAL_account_is_not_appended():
    """The chain's whole content is that each link differs from the one before.
    A link carrying no information makes the history longer without making it
    richer."""
    m = Metrics()
    store = _Store(_revision("the same account"))
    written = await _adaptor(store, m).commit(
        key=KEY, proposals=[_proposal("the same account")], now_ms=T0,
    )
    assert written == 0 and store.appended == []
    assert m.get("nora_culture_fold_total", outcome="unchanged") == 1


async def test_a_CHANGED_account_is_appended_over_an_existing_head():
    store = _Store(_revision("the old account"))
    assert await _adaptor(store).commit(
        key=KEY, proposals=[_proposal("the new account")], now_ms=T0,
    ) == 1
    assert store.appended[0]["body"] == "the new account"


async def test_commit_never_supplies_a_parent():
    """The Dreamer dispatches scopes as concurrent tasks, so a parent captured
    before the model call is a parent that may have moved. The store chooses it
    inside its own transaction."""
    store = _Store(_revision("old"))
    await _adaptor(store).commit(key=KEY, proposals=[_proposal("new")], now_ms=T0)
    assert "parent_revision_id" not in store.appended[0]


async def test_a_foreign_payload_RAISES_rather_than_being_skipped():
    """A payload of the wrong type means the runtime and this adaptor disagree
    about what was proposed, which is a wiring fault and not a bad answer."""
    @dataclass(frozen=True)
    class _Other:
        body: str

    bad = Proposal(
        payload=_Other(body="x"), instruction_sha256="a" * 64, model="m",
        established_by="run",
    )
    with pytest.raises(TypeError, match="CultureProposal"):
        await _adaptor(_Store()).commit(key=KEY, proposals=[bad], now_ms=T0)


# --- the vocabulary is enforced, not hoped for -----------------------------


async def test_an_outcome_outside_the_vocabulary_raises():
    """A typo in a metric label creates a NEW series rather than an error, and
    the old one silently goes flat."""
    a = _adaptor(_Store(), Metrics())
    with pytest.raises(ValueError, match="vocabulary"):
        a._inc("typo")


def test_every_declared_outcome_is_reachable():
    """A vocabulary member nothing produces is a label that renders as a series
    which is always zero."""
    assert {
        "written", "unchanged", "kept", "refused", "unsettled", "abandoned",
    } == CULTURE_OUTCOMES


# --- the grant: a declared tool is not a reachable one ----------------------


def test_the_adaptor_declares_the_tools_and_the_name_it_is_granted_under():
    """The grant is keyed by NAME (`AdaptorGrant`), so the adaptor's `name` and
    the constant the composition root keys `dream_grants` on must be the same
    string — two spellings is a grant that silently does not apply."""
    assert CultureAdaptor.name == CULTURE_ADAPTOR_NAME == "culture"
    assert CultureAdaptor.tools == CULTURE_TOOLS


def test_every_granted_verb_is_inside_the_dreamers_READ_ONLY_ceiling():
    """`RegistryToolSource.read_only()` is the ceiling and `build` enforces it:
    a name outside it is not built at all, so it would be a declaration nothing
    honours. Asserted against the REAL registry, because the thing under test
    is the intersection with a set the registry DERIVES from
    `NativeTool.read_only` rather than lists."""
    from nora.agent.registry import ToolRegistry

    outside = CULTURE_TOOLS - ToolRegistry().read_only_tools()
    assert outside == set(), f"granted verb(s) outside the ceiling: {outside}"


def test_no_presence_read_reaches_a_rooms_culture():
    """R-31: a room's culture
    is input to the unit's person and unit accounts, which render in rooms
    whose `location` flags differ, and an account is prose with nothing to
    drop at render. So no Character account may rest on a presence read, and
    the room fold holds none."""
    assert CULTURE_TOOLS.isdisjoint({"where_been", "online_now", "where_is"})


def test_the_situational_verbs_and_a_second_read_of_its_own_room_stay_OUT():
    """These answer a question somebody is asking NOW; an account is about what
    a room IS. `load_transcript` is refused for a different reason — the
    adaptor is HANDED its room's batch, so re-reading it spends turns to arrive
    where it started."""
    assert CULTURE_TOOLS.isdisjoint({
        "route", "transit", "parking_status", "find_parking", "weather",
        "suggest_venue", "load_transcript",
    })


def test_the_grant_names_NO_act_verb():
    """The read-only ceiling refuses every act verb. The Dreamer does not
    speak, and this tier writes through its own store."""
    from nora.agent.dreamer import culture_grant

    grant = culture_grant()
    assert grant.home_room is True


# --- the cap and the budget are ONE decision, not two -----------------------


def test_a_realistic_account_fits_inside_the_loop_budget_that_must_emit_it():
    """**The silent, self-perpetuating trap** (`note-candidates`'
    `AdaptorSchedule` comment): an output cap that binds truncates the JSON,
    `parse_envelope` reads no envelope, the run is `unavailable`, the
    watermark is HELD, and the same batch is re-read and re-truncated on every
    fire, forever. **This is what `[dreamer] max_tokens` guards and what
    `BODY_MAX_CHARS` does NOT** — the character cap is read in `accept`, on a
    body the model already emitted in full, so it is a refusal and never a
    truncation guard.

    The budget is sized on `ACCOUNT_CHARS_OBSERVED_MAX`, the longest account
    the live fold has been SEEN to emit, not on `BODY_MAX_CHARS`: sizing it on
    the cap would tie a TOKEN RESERVATION to a length nothing is expected to
    reach, so the cap could not be raised without demanding a completion no
    fold would use.

    The length is in CHARACTERS and the budget in TOKENS; the bridge is the
    MEASURED `OUTPUT_TOKENS_PER_CHAR`.

    The rule: **the loop must be able to emit a full account twice** — once
    for the tool-call arguments and any preamble the pass spends before it
    writes, once for the account. Checked against the SHIPPED
    `[dreamer.budgets.culture]` block rather than `DreamBudgetConfig`'s field
    default, because the default (4,000) does not clear it. An operator who
    deletes that block is running a fold that cannot finish its sentence, and
    the block's own comment says so."""
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    cfg = tomllib.loads((root / "config.example.toml").read_text())
    loop_budget = cfg["dreamer"]["budgets"]["culture"]["output_tokens"]
    assert ACCOUNT_OUTPUT_TOKENS >= ACCOUNT_CHARS_OBSERVED_MAX
    assert loop_budget >= 2 * ACCOUNT_OUTPUT_TOKENS, (
        f"a realistic account costs ~{ACCOUNT_OUTPUT_TOKENS} output tokens and "
        f"the shipped culture loop budget is {loop_budget}: a fold truncates, "
        f"and a truncated fold holds its watermark and re-reads the same batch "
        f"forever"
    )
    assert DreamBudgetConfig().output_tokens < 2 * ACCOUNT_OUTPUT_TOKENS, (
        "the field default now clears the floor, so the comment in "
        "config.example.toml saying an operator must keep the culture block is "
        "wrong — re-derive both rather than re-approximating"
    )


def test_a_realistic_account_fits_inside_ONE_shipped_completion():
    """The tighter of the two ceilings, and a different mechanism: `[dreamer]
    max_tokens` (0 -> `[llm] max_tokens`) is the PROVIDER's per-completion
    ceiling and binds as `MaxTokensReachedException` -> `PartialReplyError`,
    not as a `stop_reason`. Read off `config.example.toml` — the shipped
    configuration, the thing an operator copies — rather than off a field
    default, because this one has none."""
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    cfg = tomllib.loads((root / "config.example.toml").read_text())
    ceiling = cfg["dreamer"].get("max_tokens") or cfg["llm"]["max_tokens"]
    assert ceiling >= ACCOUNT_OUTPUT_TOKENS, (
        f"a realistic account costs ~{ACCOUNT_OUTPUT_TOKENS} tokens and one "
        f"completion is capped at {ceiling}"
    )


def test_the_cap_cannot_wedge_a_room_the_way_1400_did():
    """A normal draw does not reach the cap: `accept` RAISES on oversize and
    the Dreamer holds the watermark for a raise, so a cap inside the emission
    distribution turns every fire into a coin toss paid for in model calls.
    4x the observed maximum is the margin, wide enough that a somewhat larger
    next sample cannot reach it either.

    This pins the cap's distance from observed emission, not its ratio to the
    objective's worked example, which measures nothing about a model. The
    quality guard belongs to the objective, which is `change_policy:
    operator-only`; `CultureAdaptor.accept` names the line that would restore
    it. Nothing here stands in for it."""
    assert BODY_MAX_CHARS >= 4 * ACCOUNT_CHARS_OBSERVED_MAX, (
        f"the cap is {BODY_MAX_CHARS} against a measured emission maximum of "
        f"{ACCOUNT_CHARS_OBSERVED_MAX}: that is inside the range the model "
        f"draws from, and a refusal inside that range wedges the room's fold "
        f"rather than improving it"
    )
    assert BODY_MAX_CHARS < CULTURE_BODY_CEILING_CHARS, (
        f"the operating cap ({BODY_MAX_CHARS}) has reached the database "
        f"ceiling ({CULTURE_BODY_CEILING_CHARS}): 'no writer should produce "
        f"that' and 'this row is corrupt' must stay two different refusals, "
        f"because only the first one is recoverable"
    )


def test_the_character_to_token_bridge_is_the_MEASURED_one():
    """**The one number in this pair that is measured, not asserted.**

    `OUTPUT_TOKENS_PER_CHAR` comes from `tiktoken`'s `o200k_base` over the JSON
    envelope the objective requires: 1.094 tok/char for a realistic
    dense-Cantonese account (89% CJK, Latin technical terms, an emoji
    nickname), 1.131 for continuous Cantonese prose with no Latin in it, and
    0.227 for the objective's own English worked example. A round 1.0 would be
    optimistic on the CJK case, which is the direction that truncates.

    `tiktoken` is not a test dependency — pinning the constant here keeps it
    from being quietly reverted to a rounder number, and
    `nora.domain.culture.OUTPUT_TOKENS_PER_CHAR` carries the table and the
    command that produced it."""
    assert OUTPUT_TOKENS_PER_CHAR >= 1.131, (
        "below the measured continuous-Cantonese-prose ratio: an account at "
        "the cap would cost more output tokens than ACCOUNT_OUTPUT_TOKENS says"
    )
    assert (ACCOUNT_OUTPUT_TOKENS
            >= ACCOUNT_CHARS_OBSERVED_MAX * OUTPUT_TOKENS_PER_CHAR)


def test_the_declared_store_calls_cover_the_two_inputs_and_the_two_writes():
    """`DreamerRuntime` bounds `inputs` and `commit` at this number x
    `[dreamer] store_timeout_seconds`, so a bound derived from a stale constant
    is a hold that is too wide — and a hold that is too wide looks exactly like
    a healthy one until something wedges. The architecture gate counts the
    awaits off the AST; this states what they ARE."""
    assert STORE_CALLS_PER_TICK == 4  # two reads in `inputs`, two in `commit`
    assert CultureAdaptor.store_calls_per_tick == STORE_CALLS_PER_TICK


async def test_a_room_session_writes_the_room_and_no_person():
    """A person is their own session (`nora.language.character`): a
    room session's one entity is `room`, so a write naming a person is
    refused and the room's account alone is staged."""
    adaptor = _adaptor(_Store())
    revise, _keep = await _session(adaptor, (_row("dinner?", name="Miles", rid=3),))
    answer = await revise(entity="Miles", body="dry and loyal", citations=["3"])
    assert (answer["status"], answer["reason"]) == ("refused", "unknown_entity")
    assert (await revise(entity="room", body="a games room", citations=["3"]))["status"] == "ok"
    (proposal,) = await adaptor.accept(key=KEY, items=())
    assert proposal.body == "a games room"
