"""The culture adaptor WALKS `assemble`, so the table's `order` is the order the
model reads, and the text it reads is byte-identical to the hand-joined form.

**The goldens in `tests/fixtures/context_assembly_step2a_goldens.json` were
captured from the hand-joined adaptor**, over synthetic fixtures, and are the
criterion. What is compared is the adaptor's
whole `inputs()` text, not a substring of it, over the cases a hand join and
`assemble` could disagree on:

* a block present and the same block absent (every optional block), and the
  whole prompt blank where the adaptor returns `""`;
* no operator room description (culture's self-closing `<room label="..."/>`,
  which `assemble` would DROP as an empty body), and a description made only of
  characters XML cannot carry, which escapes to an empty body without being
  falsy;
* blocks built by a shared builder (`render_notes`, `render_culture`) and
  restamped with the occasion's own row;
* long content: a long account, a batch of many rows.

Nothing here regenerates a golden. Changing one is a model-visible change.
The culture adaptor does not read the room's `standing` (R-31: its account is
input to accounts shown in every room of the unit), so no culture golden
carries a `<standing>` block.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nora.language import culture_render
from nora.testing import (
    InMemoryCultureStore,
)
from tests.unit import test_culture_adaptor as cul

GOLDEN: dict[str, str] = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "context_assembly_step2a_goldens.json")
    .read_text(encoding="utf-8"),
)

CASES: dict[str, Callable[[], Awaitable[str]]] = {}


#: The wall clock the culture block's `written=` age is measured against.
#: `render_culture` defaults to `datetime.now()` (production wants exactly
#: that), so a golden recording "391d ago" would go stale as days pass. The
#: fixed clock is 391 days and half a day after the culture fixtures' `T0`
#: (`cul.T0`), so every age lands well inside its bucket.
_FROZEN_NOW = datetime.fromtimestamp((cul.T0 + 391 * 86_400_000 + 43_200_000) / 1000, tz=UTC)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _FROZEN_NOW if tz is None else _FROZEN_NOW.astimezone(tz)


@pytest.fixture(autouse=True)
def _frozen_wall_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(culture_render, "datetime", _FrozenDatetime)


def _case(name: str):
    def register(fn: Callable[[], Awaitable[str]]):
        assert name not in CASES, name
        CASES[name] = fn
        return fn
    return register


# =============================================================================
# culture
# =============================================================================

_CUL_BATCH = (
    cul._row("dinner Saturday? <b>&</b> 開車喇", ts=cul.T0, name="Ada", rid=11),
    cul._row("sure\nsecond line", ts=cul.T0 + 60_000, name="Bo]: x", rid=12),
    cul._row("2200", ts=cul.T0 + 120_000, name="Ada", rid=13),
)


@_case("culture-full")
async def _culture_full() -> str:
    return await cul._adaptor(
        cul._Store(cul._revision("they decide by attrition & <never> cancel")),
        notes=cul._Notes((cul._note(), cul._note("trip & <away>"))),
    ).inputs(key=cul.KEY, batch=_CUL_BATCH)


@_case("culture-no-operator-description")
async def _culture_no_description() -> str:
    return await cul._adaptor(
        cul._Store(cul._revision("account")), description=None,
        notes=cul._Notes((cul._note(),)),
    ).inputs(key=cul.KEY, batch=_CUL_BATCH)


@_case("culture-empty-string-description")
async def _culture_empty_description() -> str:
    return await cul._adaptor(cul._Store(), description="").inputs(
        key=cul.KEY, batch=_CUL_BATCH,
    )


@_case("culture-description-of-forbidden-characters-only")
async def _culture_control_description() -> str:
    """Truthy, and escapes to an EMPTY body: not the same input as `None`."""
    return await cul._adaptor(cul._Store(), description="\x0b\x00").inputs(
        key=cul.KEY, batch=_CUL_BATCH,
    )


@_case("culture-no-label")
async def _culture_no_label() -> str:
    return await cul._adaptor(
        cul._Store(cul._revision("account")), label=None, description=None,
    ).inputs(key=cul.KEY, batch=_CUL_BATCH)


@_case("culture-first-fold-nothing-else-wired")
async def _culture_bare() -> str:
    return await cul._adaptor(cul._Store(), notes=None).inputs(
        key=cul.KEY, batch=_CUL_BATCH[:1],
    )


@_case("culture-previous-account-only")
async def _culture_previous_only() -> str:
    return await cul._adaptor(
        cul._Store(cul._revision("account")), notes=None,
    ).inputs(key=cul.KEY, batch=_CUL_BATCH)


@_case("culture-notes-only")
async def _culture_notes_only() -> str:
    return await cul._adaptor(
        cul._Store(), notes=cul._Notes((cul._note(),)),
    ).inputs(key=cul.KEY, batch=_CUL_BATCH)


@_case("culture-wired-but-empty-notes")
async def _culture_wired_empty() -> str:
    return await cul._adaptor(
        cul._Store(), notes=cul._Notes(()),
    ).inputs(key=cul.KEY, batch=_CUL_BATCH)


@_case("culture-key-names-no-policy-conversation")
async def _culture_unresolved() -> str:
    return await cul._adaptor(
        cul._Store(cul._revision("account")), unit=None,
        notes=cul._Notes((cul._note(),)),
    ).inputs(key=cul.KEY, batch=_CUL_BATCH)


@_case("culture-empty-batch-is-blank")
async def _culture_empty_batch() -> str:
    return await cul._adaptor(cul._Store(cul._revision("account"))).inputs(
        key=cul.KEY, batch=(),
    )


@_case("culture-long")
async def _culture_long() -> str:
    batch = tuple(
        cul._row(f"message {i} " + "word " * 40, ts=cul.T0 + i * 60_000,
                 name="Ada" if i % 2 else "Bo", rid=100 + i)
        for i in range(30)
    )
    return await cul._adaptor(
        cul._Store(cul._revision("a long account " * 60)),
        notes=cul._Notes(tuple(cul._note(f"arrangement {i}") for i in range(12))),
    ).inputs(key=cul.KEY, batch=batch)


# =============================================================================
# the criterion
# =============================================================================


@pytest.mark.parametrize("name", sorted(CASES))
async def test_the_prompt_is_byte_identical_to_master(name: str):
    assert await CASES[name]() == GOLDEN[name]


def test_every_case_has_a_golden_and_no_golden_is_orphaned():
    assert set(CASES) == set(GOLDEN)


#: What each golden holds and does not, so «every optional block present and
#: absent» is a checked property of the fixtures and not a claim about them.
_PRESENT = {
    "culture-full": ("room", "previous_account", "notes", "room_messages"),
    "culture-first-fold-nothing-else-wired": ("room", "room_messages"),
}
_ABSENT = {
    "culture-first-fold-nothing-else-wired": ("previous_account", "notes"),
    "culture-previous-account-only": ("notes",),
    "culture-notes-only": ("previous_account",),
    # NO golden holds `<standing`: the fold does not read it (R-31).
}


def test_the_goldens_vary_the_blocks_this_step_is_about():
    for name, tags in _PRESENT.items():
        for tag in tags:
            assert f"<{tag} " in GOLDEN[name], (name, tag)
    for name, tags in _ABSENT.items():
        for tag in tags:
            assert f"<{tag} " not in GOLDEN[name], (name, tag)
    # The empty-body shape `assemble` would change, in its golden.
    assert '<room label="Zone 42" description=' in GOLDEN["culture-no-operator-description"]
    assert "/>" in GOLDEN["culture-no-operator-description"].split("<room ", 1)[1].split("\n", 1)[0]
    assert GOLDEN["culture-empty-batch-is-blank"] == ""


# =============================================================================
# the one place the text is NOT byte-identical, pinned so nobody calls it so
# =============================================================================


async def test_a_body_that_escapes_to_nothing_is_dropped_like_the_tag_path():
    """A block whose body is non-empty but escapes to NOTHING (control
    characters only; `\\x01\\x08` is storable, `\\x00` is not in Postgres) renders
    no empty `<previous_account>` pair: `assemble` drops an empty body, as the
    tag turn does, where a hand join would keep every string it is handed. This
    differs from the hand-joined form for input no person produces, and the
    adaptor carries no `when_empty` flag for blocks that can come out empty.
    The goldens above cover none of these inputs, so this pins the behaviour."""
    store = InMemoryCultureStore()
    await store.append(
        key=cul.KEY, body="\x01\x08", written_at=cul.T0, established_by="t",
    )
    text = await cul._adaptor(store).inputs(key=cul.KEY, batch=(cul._row("hi"),))
    assert "<previous_account" not in text
    assert "<room " in text  # the rest of the prompt is still there
