"""Step 5 of `design/context-assembly/04-every-occasion.md` §5: a Standing or
culture head the withdrawal cascade marked `unsupported_at` stops rendering in
the Dreamer prompts that push one (design/cognition-loop/10-tiers.md §2 rule 7).

**The goldens in `tests/fixtures/context_assembly_step5_goldens.json` were
captured from `master` at `33975318`, BEFORE any of those prompts was touched**,
over synthetic fixtures. They are what master rendered, for every case in the
matrix below, and the file is never regenerated: a changed golden is a
model-visible change and belongs in a step that says so.

The matrix, per occasion that pushes a head (the relevance gate's `<standing>`,
proactive's `<rooms>` lines and `<culture>` blocks, the culture fold's
`<standing>`, note-candidates' and the note curator's `<culture>`, the event
fold's `<culture>`):

* the head live (must stay byte-identical);
* the head withdrawn, for Standing and for culture, both, and neither;
* a withdrawn head beside a live head in ANOTHER room (the live one stays);
* a fold whose OWN prior is withdrawn (the culture fold's `previous_account`,
  the event fold's and the tag fold's `standing_prior`), which must STILL
  render: the fold reads what it wrote to supersede it, and the debrief's
  docstring says why (`nora.domain.withdrawal`).

What a withdrawn head rendered on master is therefore a fact in the fixture,
and the claim after the change is two-sided: the withdrawn case no longer
differs from master's own render of the SAME input with the head absent (the
twin in `WITHDRAWN_IS_ABSENT`), and the words that were withdrawn are gone.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nora.domain.models import NormalizedMessage
from nora.domain.standing import Standing
from nora.ingest import relevance_gate
from nora.ingest.stages import standing_event
from nora.language import culture_render, standing_render
from nora.obs.metrics import Metrics
from nora.testing import (
    FakeRecordStore,
    InMemoryCultureStore,
    InMemoryNoteStore,
    InMemoryStandingStore,
)
from tests.unit import test_culture_adaptor as cul
from tests.unit import test_dreamer_assembly_goldens as g2a
from tests.unit import test_note_candidates as cand
from tests.unit import test_note_curator as cur
from tests.unit import test_proactive_adaptor as pro
from tests.unit import test_relevance_gate as gate
from tests.unit import test_step2b_assembly_goldens as t2b

GOLDEN: dict[str, Any] = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "context_assembly_step5_goldens.json")
    .read_text(encoding="utf-8"),
)

CASES: dict[str, Callable[..., Awaitable[Any]]] = {}

#: When the cascade marked the rows. Any non-`None` value reads as withdrawn;
#: the renderers never print it.
_WITHDRAWN_AT = 1_790_000_000_000

_FROZEN_NOW = g2a._FROZEN_NOW


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _FROZEN_NOW if tz is None else _FROZEN_NOW.astimezone(tz)


@pytest.fixture(autouse=True)
def _frozen_wall_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every renderer here that reads the wall clock (`render_culture`,
    `render_standing`, the gate's `render_context`) reads this one."""
    for module in (culture_render, standing_render, relevance_gate):
        monkeypatch.setattr(module, "datetime", _FrozenDatetime)


def _case(name: str):
    def register(fn: Callable[..., Awaitable[Any]]):
        assert name not in CASES, name
        CASES[name] = fn
        return fn
    return register


# --- marking a head withdrawn: through the stores' own state ---------------


def _withdraw_standing(store: Any, platform: str, conversation_id: str) -> None:
    """`InMemoryStandingStore` holds `Standing` objects, as the SQL store's
    `read` builds them, so a withdrawn row is the same object with the mark."""
    rows = store._rows if hasattr(store, "_rows") else store.rows
    rows[(platform, conversation_id)] = replace(
        rows[(platform, conversation_id)], unsupported_at=_WITHDRAWN_AT,
    )


def _withdraw_culture(store: InMemoryCultureStore, key: str) -> None:
    head = store._revisions[store._heads[key]]
    store._revisions[head.id] = replace(head, unsupported_at=_WITHDRAWN_AT)


# =============================================================================
# the relevance gate: `<standing>`
# =============================================================================


async def _gate_prompt(*, standing: str | None, withdrawn: bool = False) -> str:
    store = gate._standing(standing)
    if withdrawn:
        _withdraw_standing(store, "discord", "g/1")
    client = gate._Client('{"verdict": "no_go", "relevance_score": 0.0}')
    await gate._gate(
        client, records=gate._Records(["anyone know the AWS date?"]), standing=store,
    ).decide(platform="discord", conversation_id="g/1")
    return client.prompts[0]


@_case("gate-standing-live")
async def _gate_live() -> str:
    return await _gate_prompt(standing="They are planning a trip to Taipei <soon>.")


@_case("gate-standing-withdrawn")
async def _gate_withdrawn() -> str:
    return await _gate_prompt(
        standing="They are planning a trip to Taipei <soon>.", withdrawn=True,
    )


@_case("gate-no-standing-row")
async def _gate_none() -> str:
    return await _gate_prompt(standing=None)


# =============================================================================
# proactive: `<rooms>` position lines and `<culture>` blocks
# =============================================================================


async def _pro_prompt(
    *, standing: InMemoryStandingStore | None, culture: InMemoryCultureStore | None,
    notes: bool = True, decisions: bool = True,
) -> str:
    return await pro._adaptor(
        notes=await g2a._pro_notes(count=2) if notes else InMemoryNoteStore(),
        standing=standing, culture=culture,
        rooms=g2a._PRO_ROOMS_DESCRIBED,
        decisions=await g2a._pro_decisions() if decisions else None,
    ).inputs(key=pro.WS, batch=())


async def _pro_standing_only(*rooms: str) -> InMemoryStandingStore:
    """`g2a._pro_standing`'s two rooms, keeping only `rooms` (by conversation)."""
    full = await g2a._pro_standing()
    out = InMemoryStandingStore()
    for (_platform, conversation_id), row in full._rows.items():
        if conversation_id in rooms:
            await out.write(standing=row)
    return out


async def _pro_culture_only(*keys: str) -> InMemoryCultureStore:
    full = await g2a._pro_culture()
    out = InMemoryCultureStore()
    for key in keys:
        head = full._revisions[full._heads[key]]
        await out.append(
            key=key, body=head.body, written_at=head.written_at,
            established_by=head.established_by,
        )
    return out


@_case("proactive-standing-and-culture-live")
async def _p_live() -> str:
    return await _pro_prompt(
        standing=await g2a._pro_standing(), culture=await g2a._pro_culture(),
    )


@_case("proactive-standing-withdrawn-culture-live")
async def _p_standing_withdrawn() -> str:
    standing = await g2a._pro_standing()
    _withdraw_standing(standing, "signal", "sig-1")
    _withdraw_standing(standing, "discord", "1/2")
    return await _pro_prompt(standing=standing, culture=await g2a._pro_culture())


@_case("proactive-standing-live-culture-withdrawn")
async def _p_culture_withdrawn() -> str:
    culture = await g2a._pro_culture()
    _withdraw_culture(culture, "sig-1")
    _withdraw_culture(culture, "1/2")
    return await _pro_prompt(standing=await g2a._pro_standing(), culture=culture)


@_case("proactive-standing-and-culture-both-withdrawn")
async def _p_both_withdrawn() -> str:
    standing = await g2a._pro_standing()
    _withdraw_standing(standing, "signal", "sig-1")
    _withdraw_standing(standing, "discord", "1/2")
    culture = await g2a._pro_culture()
    _withdraw_culture(culture, "sig-1")
    _withdraw_culture(culture, "1/2")
    return await _pro_prompt(standing=standing, culture=culture)


@_case("proactive-no-standing-row-culture-live")
async def _p_no_standing() -> str:
    return await _pro_prompt(
        standing=InMemoryStandingStore(), culture=await g2a._pro_culture(),
    )


@_case("proactive-standing-live-no-culture-row")
async def _p_no_culture() -> str:
    return await _pro_prompt(
        standing=await g2a._pro_standing(), culture=InMemoryCultureStore(),
    )


@_case("proactive-neither-row-exists")
async def _p_neither() -> str:
    return await _pro_prompt(
        standing=InMemoryStandingStore(), culture=InMemoryCultureStore(),
    )


@_case("proactive-standing-withdrawn-in-one-room-beside-a-live-one")
async def _p_standing_one() -> str:
    standing = await g2a._pro_standing()
    _withdraw_standing(standing, "signal", "sig-1")
    return await _pro_prompt(standing=standing, culture=None)


@_case("proactive-standing-only-the-other-room")
async def _p_standing_other() -> str:
    return await _pro_prompt(standing=await _pro_standing_only("1/2"), culture=None)


@_case("proactive-culture-withdrawn-in-one-room-beside-a-live-one")
async def _p_culture_one() -> str:
    culture = await g2a._pro_culture()
    _withdraw_culture(culture, "sig-1")
    return await _pro_prompt(standing=None, culture=culture)


@_case("proactive-culture-only-the-other-room")
async def _p_culture_other() -> str:
    return await _pro_prompt(standing=None, culture=await _pro_culture_only("1/2"))


@_case("proactive-only-withdrawn-standing-and-nothing-open-is-the-whole-prompt")
async def _p_only_withdrawn() -> str:
    standing = await g2a._pro_standing()
    _withdraw_standing(standing, "signal", "sig-1")
    _withdraw_standing(standing, "discord", "1/2")
    return await _pro_prompt(
        standing=standing, culture=None, notes=False, decisions=False,
    )


@_case("proactive-no-standing-row-and-nothing-open-is-blank")
async def _p_nothing() -> str:
    return await _pro_prompt(
        standing=InMemoryStandingStore(), culture=None, notes=False, decisions=False,
    )


# =============================================================================
# the culture fold: `<standing>`, and its own `<previous_account>`
# =============================================================================


def _cul_withdrawn_standing() -> cul._StandingStore:
    return cul._StandingStore({
        ("discord", "g1"): replace(cul._standing(), unsupported_at=_WITHDRAWN_AT),
    })


def _cul_prompt(
    *, previous: Any, standing: cul._StandingStore,
) -> Awaitable[str]:
    return cul._adaptor(
        cul._Store(previous), standing=standing,
        notes=cul._Notes((cul._note(),)),
    ).inputs(key=cul.KEY, batch=g2a._CUL_BATCH)


def _cul_revision() -> Any:
    return cul._revision("they decide by attrition & <never> cancel")


@_case("culture-fold-standing-live-previous-live")
async def _c_live() -> str:
    return await _cul_prompt(
        previous=_cul_revision(), standing=await g2a._cul_standing_store(),
    )


@_case("culture-fold-standing-withdrawn")
async def _c_standing_withdrawn() -> str:
    return await _cul_prompt(previous=_cul_revision(), standing=_cul_withdrawn_standing())


@_case("culture-fold-no-standing-row")
async def _c_no_standing() -> str:
    return await _cul_prompt(
        previous=_cul_revision(), standing=await g2a._cul_standing_store(present=False),
    )


@_case("culture-fold-own-previous-account-withdrawn-must-still-render")
async def _c_prior_withdrawn() -> str:
    return await _cul_prompt(
        previous=replace(_cul_revision(), unsupported_at=_WITHDRAWN_AT),
        standing=await g2a._cul_standing_store(),
    )


@_case("culture-fold-both-withdrawn")
async def _c_both() -> str:
    return await _cul_prompt(
        previous=replace(_cul_revision(), unsupported_at=_WITHDRAWN_AT),
        standing=_cul_withdrawn_standing(),
    )


@_case("culture-fold-previous-live-no-standing-row")
async def _c_previous_no_standing() -> str:
    return await _cul_prompt(
        previous=_cul_revision(), standing=await g2a._cul_standing_store(present=False),
    )


# =============================================================================
# note-candidates: `<culture>`
# =============================================================================


async def _cand_prompt(culture: InMemoryCultureStore) -> str:
    return await g2a._cand_adaptor(
        await g2a._cand_notebook(), description="a games room", culture=culture,
    ).inputs(key=cand.SCOPE, batch=g2a._cand_rows())


@_case("candidates-culture-live")
async def _k_live() -> str:
    return await _cand_prompt(await g2a._cand_culture())


@_case("candidates-culture-withdrawn")
async def _k_withdrawn() -> str:
    culture = await g2a._cand_culture()
    _withdraw_culture(culture, cand.SCOPE)
    return await _cand_prompt(culture)


@_case("candidates-no-culture-row")
async def _k_none() -> str:
    return await _cand_prompt(InMemoryCultureStore())


# =============================================================================
# the note curator: one `<culture room=...>` per room the shown notes came from
# =============================================================================


async def _cur_prompt(culture: InMemoryCultureStore) -> str:
    store = InMemoryNoteStore()
    for i, conv in enumerate(("conv-2", "conv-3", "conv-1", "elsewhere"), start=1):
        note = await store.create(
            unit=cur.WS, title=f"Arrangement {i}", description="one line",
            uncertainty="undecided", closing_condition="somebody decides",
            established_in=conv, body=f"body {i}", established_by="tag:miles",
            now_ms=cur.NOW, anchor_date=20260909 + i,
        )
        assert note is not None
    return await g2a._cur_adaptor(
        store, record=g2a._cur_record(), culture=culture, room_for=g2a._cur_room_for,
    ).inputs(key=cur.WS, batch=())


async def _cur_culture_only(*keys: str) -> InMemoryCultureStore:
    full = await g2a._cur_culture()
    out = InMemoryCultureStore()
    for key in keys:
        head = full._revisions[full._heads[key]]
        await out.append(
            key=key, body=head.body, written_at=head.written_at,
            established_by=head.established_by,
        )
    return out


@_case("curator-cultures-live")
async def _u_live() -> str:
    return await _cur_prompt(await g2a._cur_culture())


@_case("curator-one-culture-withdrawn-beside-a-live-one")
async def _u_one() -> str:
    culture = await g2a._cur_culture()
    _withdraw_culture(culture, "room-primary")
    return await _cur_prompt(culture)


@_case("curator-only-the-other-rooms-culture")
async def _u_other() -> str:
    return await _cur_prompt(await _cur_culture_only("room-two"))


@_case("curator-every-culture-withdrawn")
async def _u_all() -> str:
    culture = await g2a._cur_culture()
    _withdraw_culture(culture, "room-primary")
    _withdraw_culture(culture, "room-two")
    return await _cur_prompt(culture)


@_case("curator-no-culture-row")
async def _u_none() -> str:
    return await _cur_prompt(InMemoryCultureStore())


# =============================================================================
# the standing event fold: `<culture>`, and its own `<standing_prior>`
# =============================================================================


async def _event_fold(
    mp: pytest.MonkeyPatch, *, prior: Standing | None, culture: bool,
    withdraw_prior: bool = False, withdraw_culture: bool = False,
) -> list[list[str]]:
    """Drives `_event_fold` itself, so the fold's OWN read of its prior
    (`wiring.store.read`) is the one under test, not a prior handed in."""
    reader = None
    if culture:
        reader = InMemoryCultureStore()
        seed = t2b._culture()
        await reader.append(
            key="room-1", body=seed.body, written_at=seed.written_at,
            established_by=seed.established_by,
        )
        if withdraw_culture:
            _withdraw_culture(reader, "room-1")
    wiring, client = t2b._wiring(
        objective=t2b._OBJECTIVE, max_chars=1200, ws_items=5, ws_chars=600, culture=reader,
    )
    if prior is not None:
        await wiring.store.write(standing=replace(
            prior, unsupported_at=_WITHDRAWN_AT if withdraw_prior else None,
        ))
    record: FakeRecordStore = wiring.record_store
    for row in t2b._ordinary_rows():
        await record.append(
            message=NormalizedMessage(
                platform="signal", conversation_id="g1", conversation_type="group",
                sender_id="+852111", sender_name=row.sender_name or "Nora",
                timestamp=row.ts, body=row.body, raw={},
            ),
            is_nora=row.is_nora,
        )
    mp.setattr(standing_event, "time", SimpleNamespace(time=lambda: (t2b.T + 12 * t2b.H) / 1000))
    await standing_event._event_fold(
        key=("signal", "g1"), wiring=wiring, metrics=Metrics(),
    )
    return client.calls


def _event_prior() -> Standing:
    return replace(t2b._prior(), last_folded_row_id=1)


@_case("event-fold-culture-live-prior-live")
async def _e_live(mp) -> list[list[str]]:
    return await _event_fold(mp, prior=_event_prior(), culture=True)


@_case("event-fold-culture-withdrawn")
async def _e_culture_withdrawn(mp) -> list[list[str]]:
    return await _event_fold(
        mp, prior=_event_prior(), culture=True, withdraw_culture=True,
    )


@_case("event-fold-no-culture-row")
async def _e_no_culture(mp) -> list[list[str]]:
    return await _event_fold(mp, prior=_event_prior(), culture=False)


@_case("event-fold-own-prior-withdrawn-must-still-render")
async def _e_prior_withdrawn(mp) -> list[list[str]]:
    return await _event_fold(
        mp, prior=_event_prior(), culture=True, withdraw_prior=True,
    )


@_case("event-fold-both-withdrawn")
async def _e_both(mp) -> list[list[str]]:
    return await _event_fold(
        mp, prior=_event_prior(), culture=True, withdraw_prior=True, withdraw_culture=True,
    )


# =============================================================================
# the tag fold: its own `<standing_prior>` (the culture it is handed is read by
# the TAG path, which already applies the rule; see `test_engagement_context`)
# =============================================================================


@_case("tag-fold-own-prior-live")
async def _f_live(_mp) -> list[list[str]]:
    return await t2b._tag_fold(prior=t2b._prior(), culture=t2b._culture())


@_case("tag-fold-own-prior-withdrawn-must-still-render")
async def _f_withdrawn(_mp) -> list[list[str]]:
    return await t2b._tag_fold(
        prior=replace(t2b._prior(), unsupported_at=_WITHDRAWN_AT), culture=t2b._culture(),
    )


# =============================================================================
# the criterion
# =============================================================================


async def _run(name: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    fn = CASES[name]
    return await (fn(monkeypatch) if fn.__code__.co_argcount else fn())


@pytest.mark.parametrize("name", sorted(CASES))
async def test_the_prompt_is_byte_identical_to_master(
    name: str, monkeypatch: pytest.MonkeyPatch,
):
    assert await _run(name, monkeypatch) == GOLDEN[name]


def test_every_case_has_a_golden_and_no_golden_is_orphaned():
    assert set(CASES) == set(GOLDEN)
