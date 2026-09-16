"""`CultureAdaptor` — the culture tier's only moving part (design/culture-tier).

Written around the four places this adaptor can be wrong without anything
saying so: it can hand the model a chronicle and then ask for an account, it can
pick arbitrarily between two proposed accounts, it can append a link carrying no
information, and it can fire in a room that said nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from nora.domain.culture import BODY_MAX_CHARS, CultureProposal, CultureRevision
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.ports import Proposal
from nora.language.culture import CULTURE_OUTCOMES, CultureAdaptor
from nora.obs.metrics import Metrics

T0 = 1_757_000_000_000
KEY = "discord/g1"


class _Store:
    def __init__(self, head: CultureRevision | None = None) -> None:
        self._head = head
        self.appended: list[dict] = []

    async def head(self, *, key: str) -> CultureRevision | None:
        return self._head

    async def append(self, **kw: object) -> CultureRevision:
        self.appended.append(kw)
        return CultureRevision(
            id="new", culture_key=str(kw["key"]), body=str(kw["body"]),
            written_at=int(kw["written_at"]),  # type: ignore[arg-type]
            established_by=str(kw["established_by"]),
        )


def _adaptor(store: _Store, metrics: Metrics | None = None) -> CultureAdaptor:
    return CultureAdaptor(culture=store, metrics=metrics)


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


async def test_the_previous_account_comes_FIRST_when_there_is_one():
    """It is how the batch is meant to be read. A batch handed over with no
    prior context produces a fresh snapshot every day, which is the thing the
    chain exists to be instead of."""
    out = await _adaptor(_Store(_revision("they decide by attrition"))).inputs(
        key=KEY, batch=[_row("hi")],
    )
    assert out.index("previous_account") < out.index("Messages in this batch")
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
    batch = out.split("Messages in this batch:")[1]
    assert "482" not in batch, "a row id reached the prompt"
    assert not re.search(r"\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}|\bago\b", batch), (
        f"a date, a clock time or an age reached the prompt: {batch!r}"
    )
    # The speaker separator is the ONLY colon, and the line is the whole line.
    assert batch.strip() == "Ada: 開車喇"


async def test_a_raw_sender_id_never_reaches_the_prompt():
    """R-18/DEC-80: the raw id is an E.164 or an ACI."""
    row = replace(_row("hi"), person_name=None, sender_name=None)
    out = await _adaptor(_Store()).inputs(key=KEY, batch=[row])
    assert "u1" not in out and "unknown" in out


# --- accept: one account, or none ------------------------------------------


async def test_one_well_formed_account_is_accepted():
    got = await _adaptor(_Store()).accept(key=KEY, items=[{"body": " an account "}])
    assert got == (CultureProposal(body="an account"),)


async def test_TWO_accounts_are_refused_ENTIRELY_never_picked_between():
    """A room has one account. Picking one of two would be an arbitrary choice
    made silently, on the row that claims to say how the room works."""
    m = Metrics()
    got = await _adaptor(_Store(), m).accept(
        key=KEY, items=[{"body": "one"}, {"body": "two"}],
    )
    assert got == ()
    assert m.get("nora_culture_fold_total", outcome="too_many") == 1


async def test_an_empty_envelope_is_a_real_answer_and_writes_nothing():
    """«I looked and found nothing» leaves the previous account standing, which
    the objective says is usually right."""
    m = Metrics()
    assert await _adaptor(_Store(), m).accept(key=KEY, items=[]) == ()
    assert m.get("nora_culture_fold_total", outcome="empty") == 1


@pytest.mark.parametrize("item", [{}, {"body": 7}, {"body": "   "}, {"account": "x"}])
async def test_a_malformed_item_is_dropped_not_raised(item):
    assert await _adaptor(_Store()).accept(key=KEY, items=[item]) == ()


async def test_an_oversize_account_is_refused_as_drift_back_into_chronicle():
    m = Metrics()
    got = await _adaptor(_Store(), m).accept(
        key=KEY, items=[{"body": "x" * (BODY_MAX_CHARS + 1)}],
    )
    assert got == ()
    assert m.get("nora_culture_fold_total", outcome="oversize") == 1


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
    assert CULTURE_OUTCOMES == {
        "written", "empty", "too_many", "unchanged", "oversize",
    }
