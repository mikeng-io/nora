"""Step 5 of `design/context-assembly/04-every-occasion.md` §5: a Standing or
culture head the withdrawal cascade marked `unsupported_at` stops rendering in
the Dreamer prompts that push one (design/cognition-loop/10-tiers.md §2 rule 7).

**The goldens in `tests/fixtures/context_assembly_step5_goldens.json` were
captured from `master` at `33975318`, BEFORE any of those prompts was touched**,
over synthetic fixtures. They are what master rendered, for every case in the
matrix below, and the file is never regenerated: a changed golden is a
model-visible change and belongs in a step that says so.

The matrix, per occasion that pushes a head (the relevance gate's `<standing>`
and the culture fold's `<standing>`):

* the head live (must stay byte-identical);
* the head withdrawn, for Standing and for culture, both, and neither;
* a fold whose OWN prior is withdrawn (the culture fold's `previous_account`,
  the tag fold's `standing_prior`), which must STILL
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
from typing import Any

import pytest

from nora.ingest import relevance_gate
from nora.language import culture_render, standing_render
from nora.testing import (
    InMemoryCultureStore,
)
from tests.unit import test_culture_adaptor as cul
from tests.unit import test_dreamer_assembly_goldens as g2a
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

#: A withdrawn head renders as master renders the SAME input with that head
#: absent (a room never folded): the block is gone, not replaced by anything
#: that says it was withdrawn. The twin is master's own capture, so the
#: comparison is against the reader's existing «no head» form, never against a
#: string this step wrote.
WITHDRAWN_IS_ABSENT = {
    "gate-standing-withdrawn": "gate-no-standing-row",
    "culture-fold-standing-withdrawn": "culture-fold-no-standing-row",
    "culture-fold-both-withdrawn": "culture-fold-previous-live-no-standing-row",
}

#: Words a model read on master and must not read now, as they stand in the
#: rendered prompt (escaped), so a case cannot pass by the twin alone.
LEAKS = {
    "gate-standing-withdrawn": ("planning a trip to Taipei",),
    "culture-fold-standing-withdrawn": ("they ask me for routes and nothing else",),
    "culture-fold-both-withdrawn": ("they ask me for routes and nothing else",),
}

#: A fold's OWN prior is read raw on purpose, and the withdrawn mark changes
#: nothing it renders: the case is master's bytes, equal to its live twin, and
#: the block is still there.
OWN_PRIOR_STILL_RENDERS = {
    "culture-fold-own-previous-account-withdrawn-must-still-render":
        ("culture-fold-standing-live-previous-live", "<previous_account"),
    "tag-fold-own-prior-withdrawn-must-still-render":
        ("tag-fold-own-prior-live", "<standing_prior"),
}

WITHDRAWN = set(WITHDRAWN_IS_ABSENT)
HELD = set(CASES) - WITHDRAWN


async def _run(name: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    fn = CASES[name]
    return await (fn(monkeypatch) if fn.__code__.co_argcount else fn())


def _text(rendered: Any) -> str:
    return json.dumps(rendered, ensure_ascii=False)


@pytest.mark.parametrize("name", sorted(HELD))
async def test_a_head_that_is_not_withdrawn_renders_byte_identical_to_master(
    name: str, monkeypatch: pytest.MonkeyPatch,
):
    """Live heads, absent heads and a fold's own prior, whatever its mark."""
    assert await _run(name, monkeypatch) == GOLDEN[name]


@pytest.mark.parametrize("name", sorted(WITHDRAWN))
async def test_a_withdrawn_head_renders_as_a_room_with_no_head(
    name: str, monkeypatch: pytest.MonkeyPatch,
):
    now = await _run(name, monkeypatch)
    assert now == GOLDEN[WITHDRAWN_IS_ABSENT[name]]
    # Not vacuous: master rendered something else for this input.
    assert now != GOLDEN[name]


@pytest.mark.parametrize("name", sorted(WITHDRAWN))
async def test_what_was_withdrawn_is_gone_from_the_prompt(
    name: str, monkeypatch: pytest.MonkeyPatch,
):
    now = _text(await _run(name, monkeypatch))
    for words in LEAKS[name]:
        assert words in _text(GOLDEN[name]), f"{words!r} was never in master's prompt"
        assert words not in now, f"{words!r} reached the model again"


@pytest.mark.parametrize("name", sorted(OWN_PRIOR_STILL_RENDERS))
async def test_a_folds_own_prior_still_renders_when_it_is_withdrawn(
    name: str, monkeypatch: pytest.MonkeyPatch,
):
    live_twin, block = OWN_PRIOR_STILL_RENDERS[name]
    now = await _run(name, monkeypatch)
    assert now == GOLDEN[name] == GOLDEN[live_twin]
    assert block in _text(now)


def test_every_case_has_a_golden_and_no_golden_is_orphaned():
    assert set(CASES) == set(GOLDEN)


def test_every_case_is_classified_once():
    assert set(CASES) >= WITHDRAWN
    assert set(OWN_PRIOR_STILL_RENDERS) <= HELD
    assert set(LEAKS) == WITHDRAWN
    assert set(WITHDRAWN_IS_ABSENT.values()) <= HELD
    assert all(twin in HELD for twin, _ in OWN_PRIOR_STILL_RENDERS.values())


def test_master_rendered_every_withdrawn_head_as_if_it_were_live():
    """The finding this step is built on, held in the fixture: before it, the
    mark changed nothing any of these prompts rendered (3 inputs)."""
    pairs = {
        "gate-standing-withdrawn": "gate-standing-live",
        "culture-fold-standing-withdrawn": "culture-fold-standing-live-previous-live",
        "culture-fold-both-withdrawn": "culture-fold-standing-live-previous-live",
    }
    for withdrawn, live in pairs.items():
        assert GOLDEN[withdrawn] == GOLDEN[live], withdrawn
