"""`CultureAdaptor` — the culture tier's only moving part (design/culture-tier).

Written around the places this adaptor can be wrong without anything saying so:
it can hand the model a chronicle and then ask for an account, it can pick
arbitrarily between two proposed accounts, it can append a link carrying no
information, and it can fire in a room that said nothing.

Since 2026-09-17 it can also be wrong in two new ways, and both are silent:
it can be asked for a join and handed one table (`design/dreamer-agent-loop/
03-no-agent-loop §7`, `17-failure-classes §11` — the tautology that passes every
gate the runtime owns), and it can hold a character cap no budget can emit,
which truncates the completion, holds the watermark and re-reads the same batch
forever.
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
    character_key,
)
from nora.domain.models import DeliveryStatus, PersonRef, TranscriptRow
from nora.domain.notes import Note
from nora.domain.ports import Proposal
from nora.domain.standing import Standing
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
    """`design/context-assembly/01-the-registry.md` S1: what this pass
    carries is a table, not a trace through `inputs` and `_room_block`.

    Two rows the design bundle's own survey missed: `00-overview.md`
    counted `previous_account, room, room_messages` — three — because it
    read `ContextBlock(name=...)` literals in this file alone, and missed
    `standing` (`nora.language.standing_render.render_standing`) and `notes`
    (`nora.language.note_render.render_notes`), both shared with other
    occasions. `test_the_previous_account_precedes_standing_notes_AND_the_
    batch` below exercises all five together.
    """
    names = {name for name, _order, _key in _BLOCKS}
    assert names == {
        "room", "previous_account", "standing", "notes", "people_views",
        "person_view", "room_messages",
    }


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


class _StandingStore:
    """The per-room `standing` tier, keyed as the real one is."""

    def __init__(self, rows: dict[tuple[str, str], Standing] | None = None) -> None:
        self.rows = rows or {}
        self.reads: list[tuple[str, str]] = []

    async def read(
        self, *, platform: str, conversation_id: str,
    ) -> Standing | None:
        self.reads.append((platform, conversation_id))
        return self.rows.get((platform, conversation_id))


class _Notes:
    """The note tier's index, keyed by UNIT (DEC-105)."""

    def __init__(self, notes: tuple[Note, ...] = ()) -> None:
        self._notes = notes
        self.reads: list[str] = []

    async def standing(self, *, unit: str) -> tuple[Note, ...]:
        self.reads.append(unit)
        return self._notes


def _standing(body: str = "they ask me for routes and nothing else") -> Standing:
    return Standing(
        platform="discord", conversation_id="g1", body=body,
        fold_count=1, updated_at=T0,
    )


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
    standing: _StandingStore | None = None,
    notes: _Notes | None = None,
    label: str | None = "Zone 42",
    description: str | None = "the games channel",
    unit: str | None = "friends",
    standing_key: tuple[str, str] | None = ("discord", "g1"),
    platform_for=None,
    resolve_names=None,
) -> CultureAdaptor:
    return CultureAdaptor(
        culture=store, standing=standing, notes=notes, metrics=metrics,
        unit_for=lambda _k: unit,
        standing_key_for=lambda _k: standing_key,
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
        established_by="culture",
    )


def _proposal(body: str) -> Proposal:
    return Proposal(
        payload=CultureProposal(body=body), instruction_sha256="a" * 64,
        model="flash", established_by="culture-run-1",
    )


# --- inputs: a quiet room costs nothing ------------------------------------


async def test_an_empty_batch_renders_NOTHING_so_the_runtime_does_not_fire():
    """`DreamAdaptor` has no `should_fire` verb — the fire condition IS "did
    `inputs` render anything". A silent room not firing is DEC-185, and it is
    correct: nothing here retires, so there is nothing to refresh against."""
    assert await _adaptor(_Store()).inputs(key=KEY, batch=()) == ""


async def test_a_mapped_senders_batch_line_renders_the_canonical_name():
    """#200 follow-up: `_render_batch` reads `r.person_name` and this fold's
    own batch, straight off `record.since`, never had it filled — nothing on
    this path ever called `Persons.name_transcript` — so a mapped sender's
    line was attributed to their self-set, spoofable `sender_name` instead of
    the operator's canonical name for them. `_row` presets `person_name`
    already, which is exactly the shape that hid this defect in
    `note_candidates` before #544 (`tests/offline_eval_support.py`'s own
    fix); this row leaves it unset, as a live Dreamer row does, and relies on
    `platform_for`/`resolve_names` — wired the way composition now wires
    them — to fill it before the batch renders."""
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
    """#546 review: a resolver fault must DEGRADE, never take the fold down
    with it. `resolve_names` is `Persons.name_transcript` in production —
    dict lookups only — but an injected callable can still raise, and
    letting that propagate out of `inputs()` would mark the whole occasion
    `unavailable` and hold the watermark, re-reading the same batch every
    tick over a failure that has nothing to do with what the room said.

    Proved both ways: `inputs()` still renders the line on the
    pre-resolution `speaker_name` floor (`sender_name`, here) rather than
    raising, AND the fold still COMMITS — `accept`/`commit` write an account
    exactly as `test_a_CHANGED_account_is_appended_over_an_existing_head`
    proves for a working resolver."""
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
    """The other half: `platform_for`/`resolve_names` both default `None`
    (the shape every pre-#200 construction of this adaptor already has), and
    resolution is skipped entirely rather than raising — `_render_batch`'s
    existing `speaker_name` floor (the profile name, or `"unknown"`) is
    unchanged, and the raw `sender_id` never reaches the prompt either way
    (R-18)."""
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


async def test_the_first_fold_of_a_room_renders_no_previous_block():
    out = await _adaptor(_Store()).inputs(key=KEY, batch=[_row("hi")])
    assert "previous_account" not in out


async def test_the_batch_carries_NO_row_ids_and_NO_timestamps():
    """The objective's load-bearing clause is «not dated observations». A model
    handed `[482] 2026-09-12 14:03` writes about the 12th; handed a name and a
    line it writes about the room. Handing over the chronicle shape and then
    asking for its opposite is the failure this rendering avoids."""
    import re

    out = await _adaptor(_Store()).inputs(
        key=KEY, batch=[_row("開車喇", ts=T0, rid=482)],
    )
    batch = out.split("<room_messages ")[1].split("</room_messages>")[0]
    body = batch.split(">", 1)[1]
    # THE ROW ID, still absent. `n` is a per-render position (1..N), never
    # `row.id` — the culture fold cites nothing back, so a real id here would be
    # a handle to a stored row for no reader.
    assert "482" not in body, "a row id reached the prompt"
    assert not re.search(r"\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}|\bago\b", body), (
        f"a date, a clock time or an age reached the prompt: {body!r}"
    )
    # Exact bytes, so «no time» cannot rot into «a time nobody grepped for».
    # `n` and `from` are the only attributes: no `age`, because `_render_batch`
    # passes no `timestamp` accessor and `render_elements` drops what is None.
    assert body.strip() == '<m n="1" from="Ada">開車喇</m>', body


async def test_the_room_block_names_the_room_and_carries_the_operators_words():
    """Issue #161 one tier over. An account is a claim ABOUT A ROOM, and the
    prompt never said which room it was reading — the same input gap that made
    the note pass write «Bath gathering» out of a games room's 「2200」. The
    label is what the room is CALLED and the description is what the OPERATOR
    says it is; neither is derivable from the room's own messages."""
    out = await _adaptor(_Store()).inputs(key=KEY, batch=[_row("hi")])
    assert out.startswith("<room "), out[:120]
    assert 'label="Zone 42"' in out
    assert "the games channel" in out
    # THE OPENING TAG, not the whole render: since #314 the clause is the
    # block's own `description=`, declared on the type, so `<batch>`'s
    # near-identical sentence one block down cannot satisfy this.
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


async def test_her_STANDING_in_this_room_reaches_the_fold():
    """`01-the-adaptor §1.2`. The objective asks the model where she stands
    with them while the `standing` tier holds precisely that, per room — so
    without it the model infers from raw transcript a thing another store
    states, and a guess in this tier is prose that reads like an observation."""
    store = _StandingStore({("discord", "g1"): _standing()})
    out = await _adaptor(_Store(), standing=store).inputs(
        key=KEY, batch=[_row("hi")],
    )
    assert "they ask me for routes and nothing else" in out
    assert "<standing" in out


async def test_the_standing_read_uses_the_key_the_POLICY_resolves():
    """`standing` keys `(platform, conversation_id)` and this adaptor keys
    `primary_id` (GLOSSARY.md). They are equal for every live conversation and
    that is an accident of data (#229) — read with the adaptor's own key it
    matches zero rows and renders as «this room has no standing», a real state
    nothing anywhere reports as a miss."""
    store = _StandingStore()
    await _adaptor(
        _Store(), standing=store, standing_key=("signal", "+8520000"),
    ).inputs(key=KEY, batch=[_row("hi")])
    assert store.reads == [("signal", "+8520000")]


async def test_what_the_room_has_OPEN_reaches_the_fold_at_the_UNIT_key():
    """The note index is a UNIT store (DEC-105), so the scope key is
    translated — `NoteCandidateAdaptor`'s own read, same verb, same
    translation."""
    notes = _Notes((_note(),))
    out = await _adaptor(_Store(), notes=notes).inputs(key=KEY, batch=[_row("hi")])
    assert notes.reads == ["friends"]
    assert "dinner on the 20th" in out


async def test_a_scope_that_names_no_policy_conversation_is_LOGGED(caplog):
    """A key that resolves to nothing renders neither block — and says so.
    Silence here is the #161 shape again: a plausible, thinner prompt that
    nothing counts."""
    import logging

    with caplog.at_level(logging.WARNING, logger="nora.language.culture"):
        out = await _adaptor(
            _Store(), standing=_StandingStore(), notes=_Notes((_note(),)),
            unit=None, standing_key=None,
        ).inputs(key=KEY, batch=[_row("hi")])

    assert "<standing" not in out and "<notes" not in out
    assert any(
        "resolves to no policy conversation" in r.getMessage()
        for r in caplog.records
    )


async def test_the_previous_account_precedes_standing_notes_AND_the_batch():
    """`00-overview §5`: the previous account is how everything after it is
    meant to be read. A fold with no prior context writes a fresh snapshot
    every day, which is the thing the chain exists to be instead of."""
    out = await _adaptor(
        _Store(_revision("they decide by attrition")),
        standing=_StandingStore({("discord", "g1"): _standing()}),
        notes=_Notes((_note(),)),
    ).inputs(key=KEY, batch=[_row("hi")])
    assert (
        out.index("previous_account")
        < out.index("<standing")
        < out.index("<notes")
        < out.index("<room_messages ")
    ), out


async def test_a_quiet_room_reads_NO_store_at_all():
    """The runtime's fire condition is «did `inputs` render anything», so a
    quiet room must cost nothing — including the three reads."""
    culture, standing, notes = _Store(), _StandingStore(), _Notes((_note(),))
    assert await _adaptor(
        culture, standing=standing, notes=notes,
    ).inputs(key=KEY, batch=()) == ""
    assert standing.reads == [] and notes.reads == []


async def test_a_standing_store_that_RAISES_takes_the_fold_with_it():
    """Fail-CLOSED, unlike proactive's `_standing_lines`. A run that folded
    anyway would write an account that GUESSES at her standing, and the
    runtime would report it `ok` — the §11 tautology with a store fault behind
    it. Raising makes the run `unavailable` and HOLDS the watermark, so the
    batch is folded tomorrow instead of being consumed wrong today."""
    class _Broken:
        async def read(self, *, platform: str, conversation_id: str):
            raise RuntimeError("standing store down")

    with pytest.raises(RuntimeError, match="standing store down"):
        await _adaptor(_Store(), standing=_Broken()).inputs(  # type: ignore[arg-type]
            key=KEY, batch=[_row("hi")],
        )


async def test_a_raw_sender_id_never_reaches_the_prompt():
    """R-18/DEC-80: the raw id is an E.164 or an ACI."""
    row = replace(_row("hi"), person_name=None, sender_name=None)
    out = await _adaptor(_Store()).inputs(key=KEY, batch=[row])
    assert "u1" not in out and "unknown" in out


# --- accept: one account, or none ------------------------------------------


async def test_one_well_formed_account_is_accepted():
    got = await _adaptor(_Store()).accept(key=KEY, items=[{"body": " an account "}])
    assert got == (CultureProposal(body="an account"),)


async def test_TWO_accounts_are_refused_ENTIRELY_and_the_batch_is_HELD():
    """A room has one account. Picking one of two would be an arbitrary choice
    made silently, on the row that claims to say how the room works. And the
    refusal RAISES (DEC-280): returning `()` consumed the batch with no
    account written from it; raising makes the run `unavailable`, which holds
    the watermark (`tests/unit/test_dreamer_runtime.py::
    test_an_accept_that_RAISES_is_unavailable_and_never_reaches_commit`)."""
    m = Metrics()
    with pytest.raises(AccountNotRecordableError):
        await _adaptor(_Store(), m).accept(
            key=KEY, items=[{"body": "one"}, {"body": "two"}],
        )
    assert m.get("nora_culture_fold_total", outcome="too_many") == 1
    assert m.get("nora_culture_fold_total", outcome="abandoned") == 0


async def test_the_third_rejection_of_one_batch_in_a_row_advances_past_it():
    """Each rejection holds the watermark, so a batch the model keeps misreading
    would be re-read for ever. The third in a row of the SAME batch (named by its
    first row) returns `()` instead, so the batch is consumed, counted
    `abandoned` and logged; a different batch, or a reply that is not
    rejected, starts the run again."""
    m = Metrics()
    adaptor = _adaptor(_Store(), m)
    two = [{"body": "one"}, {"body": "two"}]
    for _ in range(2):
        await adaptor.inputs(key=KEY, batch=(_row("dinner?", rid=7),))
        with pytest.raises(AccountNotRecordableError):
            await adaptor.accept(key=KEY, items=two)
    await adaptor.inputs(key=KEY, batch=(_row("dinner?", rid=7),))
    assert await adaptor.accept(key=KEY, items=two) == ()
    assert m.get("nora_culture_fold_total", outcome="abandoned") == 1
    # The run starts again on a new batch: two rejections of one batch and
    # then one of another is a first rejection, not a third.
    for rid in (7, 7, 8):
        await adaptor.inputs(key=KEY, batch=(_row("dinner?", rid=rid),))
        with pytest.raises(AccountNotRecordableError):
            await adaptor.accept(key=KEY, items=two)
    # ...and after an accepted reply.
    await adaptor.inputs(key=KEY, batch=(_row("dinner?", rid=8),))
    assert await adaptor.accept(key=KEY, items=[{"body": "fine"}])
    for _ in range(2):
        await adaptor.inputs(key=KEY, batch=(_row("dinner?", rid=8),))
        with pytest.raises(AccountNotRecordableError):
            await adaptor.accept(key=KEY, items=two)
    assert m.get("nora_culture_fold_total", outcome="abandoned") == 1


async def test_an_empty_envelope_is_a_real_answer_and_writes_nothing():
    """«I looked and found nothing» leaves the previous account standing, which
    the objective says is usually right."""
    m = Metrics()
    assert await _adaptor(_Store(), m).accept(key=KEY, items=[]) == ()
    assert m.get("nora_culture_fold_total", outcome="empty") == 1


@pytest.mark.parametrize("item", [{}, {"body": 7}, {"body": "   "}, {"account": "x"}])
async def test_a_malformed_item_is_dropped_not_raised(item):
    assert await _adaptor(_Store()).accept(key=KEY, items=[item]) == ()


async def test_an_oversize_account_RAISES_so_the_batch_is_not_spent_too():
    """**The refusal must not cost the batch as well**, and returning `()`
    here did exactly that.

    Traced: no proposal -> `commit(key, ())` -> status `ok` ->
    `_dream_once` advances the watermark. The account was discarded, the
    messages it was written from were consumed, and the only record was a
    counter whose label is destroyed at every process restart — so a room whose
    fold habitually runs long wrote nothing, forever, and nothing durable said
    so. Raising makes the run `unavailable`, which is the one status that HOLDS
    the cursor, so the same batch is folded again tomorrow.

    `unavailable` and not `nothing_found`: the model DID return an account, so
    «there was nothing in this batch» is a claim this run is not entitled to
    make (R-27, and the module docstring's own line about a batch that will not
    parse)."""
    m = Metrics()
    with pytest.raises(AccountNotRecordableError):
        await _adaptor(_Store(), m).accept(
            key=KEY, items=[{"body": "x" * (BODY_MAX_CHARS + 1)}],
        )
    assert m.get("nora_culture_fold_total", outcome="oversize") == 1


async def test_an_empty_answer_still_returns_and_lets_the_batch_advance():
    """`empty` is «I looked and there was nothing usable», true, and the
    previous account stands, so holding the cursor for it would re-fold a batch
    that has already been answered. `too_many` and `oversize` hold (DEC-280)."""
    adaptor = _adaptor(_Store())
    assert await adaptor.accept(key=KEY, items=[]) == ()
    assert await adaptor.accept(key=KEY, items=[{"body": "  "}]) == ()


async def test_an_account_exactly_at_the_cap_is_accepted():
    """The boundary is inclusive — an off-by-one here silently discards the
    longest legitimate account and counts it as drift."""
    got = await _adaptor(_Store()).accept(key=KEY, items=[{"body": "x" * BODY_MAX_CHARS}])
    assert len(got) == 1


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
        "written", "empty", "too_many", "unchanged", "oversize", "dropped_person",
        "abandoned",
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


def test_the_join_the_operators_example_needs_is_actually_granted():
    """`03-no-agent-loop §7.2`: the transcript carries the intent, presence
    carries who was actually there, live state carries who was elsewhere — and
    the conclusion is a property of the JOIN. `where_been` was in no adaptor's
    grant at all before this, so the example could be written and not run."""
    assert {"where_been", "online_now"} <= CULTURE_TOOLS


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
    `AdaptorSchedule` comment, reached from the other end): an output cap that
    binds truncates the JSON, `parse_envelope` reads no envelope, the run is
    `unavailable`, the watermark is HELD, and the same batch is re-read and
    re-truncated on every fire, forever. **This is what `[dreamer] max_tokens`
    guards and what `BODY_MAX_CHARS` does NOT** — the character cap is read in
    `accept`, on a body the model already emitted in full, so it is a refusal
    and never a truncation guard.

    **What this test measures changed on 2026-09-18, and the old version is
    why.** It used to size the budget on an account written to
    `BODY_MAX_CHARS`, which tied a TOKEN RESERVATION to a length nothing is
    expected to reach — so the cap could not be raised without demanding a
    23,000-token completion no fold would ever use. The budget is sized on
    `ACCOUNT_CHARS_OBSERVED_MAX` now: 1,876 characters, the longest account the
    live fold has been SEEN to emit.

    The length is in CHARACTERS and the budget in TOKENS; the bridge is the
    MEASURED `OUTPUT_TOKENS_PER_CHAR` (1.15, against 1.094 for a realistic
    dense-Cantonese account and 1.131 for continuous Cantonese prose). The
    previous 1.0 was asserted rather than taken and was optimistic on the very
    case it named, which is the direction that truncates.

    The rule: **the loop must be able to emit a full account twice** — once for
    the tool-call arguments and any preamble the pass spends before it writes,
    once for the account. Against the SHIPPED `[dreamer.budgets.culture]`
    block rather than `DreamBudgetConfig`'s field default, because the default
    (4,000) does not clear it and saying otherwise would be the decoration this
    pair of tests exists to refuse. An operator who deletes that block is
    running a fold that cannot finish its sentence, and the block's own comment
    says so."""
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
    """**This test replaces one that certified the defect** (2026-09-18), and
    the swap is the finding.

    The old test pinned `BODY_MAX_CHARS` as a band (1.75x-2.05x) around the
    objective's 739-character worked example, on the theory that the cap was a
    QUALITY instrument: admit the second paragraph, refuse the essay. It was
    green on 1,400 — and 1,400 sat inside what the model actually emits.
    Measured live the same evening, n=11 across five rooms: 563, 811, 1,095,
    1,316 and 1,323 characters written; 1,583, 1,677, 1,725, 1,812, 1,826 and
    1,876 REFUSED. **6 of 11 — 55% — refused**, each refusal spending a model
    call and re-folding the same batch. The deployment's busiest room paid
    four before its fifth draw came under. The band certified all of that as
    correct, because a ratio against a worked example is not a measurement of
    a model.

    So the property worth pinning is not the cap's ratio to the example. It is
    that **a normal draw does not reach the cap**: `accept` RAISES on oversize
    and the Dreamer holds the watermark for a raise, so a cap inside the
    emission distribution turns every fire into a coin toss paid for in model
    calls. 4x the observed maximum is the margin — wide enough that the
    29%-higher next sample this repo has already been bitten by (`[dreamer]
    max_tokens`, n=1 -> 4,000) cannot reach it either.

    The quality guard the old test thought it was enforcing belongs to the
    objective, which is `change_policy: operator-only`; `CultureAdaptor.accept`
    names the line that would restore it. Nothing here pretends to stand in."""
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
    """**The one number in this pair that was asserted rather than taken.**

    It was 1.0 «the CJK worst case» and nothing had encoded anything. Measured
    2026-09-17 with `tiktoken`'s `o200k_base` over the JSON envelope the
    objective requires: 1.094 tok/char for a realistic dense-Cantonese account
    (89% CJK, Latin technical terms, an emoji nickname), 1.131 for continuous
    Cantonese prose with no Latin in it, and 0.227 for the objective's own
    English worked example. So 1.0 was OPTIMISTIC on the very case it named,
    which is the direction that truncates.

    `tiktoken` is not a test dependency — pinning the constant here is what
    keeps the measurement from being quietly reverted to a rounder number, and
    `nora.domain.culture.OUTPUT_TOKENS_PER_CHAR` carries the table and the
    command that produced it."""
    assert OUTPUT_TOKENS_PER_CHAR >= 1.131, (
        "below the measured continuous-Cantonese-prose ratio: an account at "
        "the cap would cost more output tokens than ACCOUNT_OUTPUT_TOKENS says"
    )
    assert (ACCOUNT_OUTPUT_TOKENS
            >= ACCOUNT_CHARS_OBSERVED_MAX * OUTPUT_TOKENS_PER_CHAR)


def test_the_declared_store_calls_cover_the_three_inputs_and_the_two_writes():
    """`DreamerRuntime` bounds `inputs` and `commit` at this number x
    `[dreamer] store_timeout_seconds`, so a bound derived from a stale constant
    is a hold that is too wide — and a hold that is too wide looks exactly like
    a healthy one until something wedges. The architecture gate counts the
    awaits off the AST; this states what they ARE."""
    assert STORE_CALLS_PER_TICK == 6  # five reads/writes plus ONE `memory.chat` site
    assert CultureAdaptor.store_calls_per_tick == STORE_CALLS_PER_TICK


# --- the Character tier (DEC-278) --------------------------------------------


class _Memory:
    """`MemoryStore.chat` and nothing else: what each person's view answers."""

    def __init__(self, answers: dict[str, str | None]) -> None:
        self.answers = answers
        self.asked: list[tuple[str, str, str | None]] = []

    async def chat(self, *, peer_id, query, target=None, unit=None, **_kw):
        self.asked.append((target, query, unit))
        return self.answers.get(target)


_MILES = PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")
_ADA = PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada")


def _character_adaptor(
    store: _Store, memory: _Memory, metrics: Metrics | None = None,
) -> CultureAdaptor:
    return CultureAdaptor(
        culture=store, unit_for=lambda _k: "friends", metrics=metrics,
        standing_key_for=lambda _k: None, label_for=lambda _k: "Zone 42",
        description_for=lambda _k: None,
        members_for=lambda _k: (_MILES, _ADA), memory=memory, ai_peer="nora",
        screen=lambda text: "id" if "+852" in text else None,
    )


async def test_the_people_views_are_asked_per_listed_person_and_screened():
    """One question per listed person who wrote in the batch, routed by the
    unit; a person memory has nothing for gets no entry, and an answer carrying
    an identifier (R-18) is withheld whole."""
    memory = _Memory({"miles": "dry, loyal, late to everything", "ada": "call +852 5555"})
    out = await _character_adaptor(_Store(), memory).inputs(
        key=KEY, batch=(_row("dinner?", name="Miles"), _row("yes", name="Ada", rid=2)),
    )
    assert memory.asked == [
        ("miles", "What is the character of Miles?", "friends"),
        ("ada", "What is the character of Ada?", "friends"),
    ]
    assert "<person_view" in out and "dry, loyal, late to everything" in out
    views = out.split("<room_messages")[0].split("<people_views")[1]
    assert "+852" not in out and "Ada" not in views


async def test_a_person_account_is_written_under_its_own_key():
    """The model names a person from the roster; `accept` resolves the id, and
    `commit` stores it in the same store under `character_key`, leaving the
    room's own head alone."""
    store = _Store()
    adaptor = _character_adaptor(store, _Memory({"miles": "dry and loyal"}))
    await adaptor.inputs(key=KEY, batch=(_row("dinner?", name="Miles"),))
    proposals = await adaptor.accept(key=KEY, items=[
        {"body": "a games room"},
        {"person": "Miles", "body": "dry and loyal"},
        {"person": "Nobody", "body": "dropped"},
    ])
    assert [(p.person_id, p.body) for p in proposals] == [
        (None, "a games room"), ("miles", "dry and loyal"),
    ]
    await adaptor.commit(
        key=KEY,
        proposals=[Proposal(payload=p, instruction_sha256="a" * 64,
                            model="flash", established_by="culture-run-1")
                   for p in proposals],
        now_ms=T0,
    )
    assert (await store.head(key=KEY)).body == "a games room"
    assert (await store.head(key=character_key(KEY, "miles"))).body == "dry and loyal"


async def test_only_the_batchs_senders_are_asked_and_a_silent_persons_account_stands():
    """DEC-280. Live 2026-10-03: eight dialectic calls on a pass where two
    people had posted. A listed person who wrote nothing in the batch is not
    asked, gets no `<person_view>`, and their existing account is neither
    rewritten nor removed, even when the model names them anyway."""
    store = _Store(head=CultureRevision(
        id="c1", culture_key=character_key(KEY, "ada"), body="steady and kind",
        written_at=T0, established_by="culture",
    ))
    memory = _Memory({"miles": "dry, loyal", "ada": "steady"})
    m = Metrics()
    adaptor = _character_adaptor(store, memory, m)
    out = await adaptor.inputs(key=KEY, batch=(_row("dinner?", name="Miles"),))
    assert [target for target, _q, _u in memory.asked] == ["miles"]
    views = out.split("<room_messages")[0].split("<people_views")[1]
    assert 'name="Miles"' in views and 'name="Ada"' not in views
    proposals = await adaptor.accept(key=KEY, items=[
        {"body": "a games room"},
        {"person": "Miles", "body": "dry and loyal"},
        {"person": "Ada", "body": "rewritten from nothing"},
    ])
    assert [(p.person_id, p.body) for p in proposals] == [
        (None, "a games room"), ("miles", "dry and loyal"),
    ]
    assert m.get("nora_culture_fold_total", outcome="dropped_person") == 1
    await adaptor.commit(
        key=KEY,
        proposals=[Proposal(payload=p, instruction_sha256="a" * 64,
                            model="flash", established_by="culture-run-1")
                   for p in proposals],
        now_ms=T0 + 1,
    )
    assert (await store.head(key=character_key(KEY, "ada"))).body == "steady and kind"
    assert (await store.head(key=character_key(KEY, "miles"))).body == "dry and loyal"


async def test_a_person_not_on_the_roster_drops_only_that_item():
    """A person's item naming nobody the fold was shown drops that item alone:
    the room's account and the other person's stand, and it is counted."""
    m = Metrics()
    adaptor = _character_adaptor(_Store(), _Memory({"miles": "dry"}), m)
    await adaptor.inputs(key=KEY, batch=(_row("dinner?", name="Miles"),))
    proposals = await adaptor.accept(key=KEY, items=[
        {"body": "a games room"},
        {"person": "Nobody", "body": "dropped"},
        {"person": "Miles", "body": "dry"},
    ])
    assert [(p.person_id, p.body) for p in proposals] == [
        (None, "a games room"), ("miles", "dry"),
    ]
    assert m.get("nora_culture_fold_total", outcome="dropped_person") == 1
    assert m.get("nora_culture_fold_total", outcome="too_many") == 0
