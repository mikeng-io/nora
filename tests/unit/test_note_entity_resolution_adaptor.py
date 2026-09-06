"""The note tier's judgement pass over the shared entity table (issue #100),
mirroring `tests/unit/test_entity_resolution_adaptor.py` — the fact tier's
identical pass over the identical table, tested the identical way.

The store is a double here rather than real Postgres, and deliberately: what
is under test is the adaptor's handle discipline and its refusals, not SQL.
`SqlEntityStore.unresolved_note_anchor_names` gets its own Postgres coverage
in `tests/infra/test_entity_store.py`.
"""

from __future__ import annotations

from nora.domain.ports import Proposal
from nora.language.note_entity_adaptor import (
    _ENTITY_HANDLES,
    _NAME_HANDLES,
    NoteEntityResolution,
    NoteEntityResolutionAdaptor,
)
from nora.obs.metrics import Metrics

WS = "ws-main"
NOW = 1_755_200_000_000


class _FakeEntities:
    """Matches production where the adaptor branches, and says where it does
    not — `_FakeEntities` in the fact tier's own suite, one method renamed:
    `unresolved_note_anchor_names` in place of `unresolved_names`, since that
    is the one verb this adaptor's Protocol does not share with the fact
    tier's."""

    def __init__(self, *, unresolved=(), similar=None, existing_aliases=()):
        self._unresolved = tuple(unresolved)
        self._similar = similar or {}
        self.attached: list[tuple[str, str]] = []
        self._existing = set(existing_aliases)

    async def unresolved_note_anchor_names(self, *, workspace: str, limit: int):
        return self._unresolved[:limit]

    async def similar_entities(self, *, workspace: str, name: str, limit: int):
        return tuple(self._similar.get(name, ()))[:limit]

    async def attach_alias(self, *, workspace: str, entity_id: str, alias: str):
        if (entity_id, alias) in self._existing:
            return False
        self.attached.append((entity_id, alias))
        self._existing.add((entity_id, alias))
        return True


def _proposal(payload: object) -> Proposal:
    return Proposal(
        payload=payload, instruction_sha256="a" * 64, model="m",
        established_by="run-1",
    )


async def test_inputs_renders_handles_and_never_an_entity_id():
    """The rule that caught three adaptors on the fact side: anything a model
    echoes back is an ordinal handle, never a stored identifier."""
    entities = _FakeEntities(
        unresolved=("Grant 婚禮",),
        similar={"Grant 婚禮": [
            ("0191f2c4-0000-7000-8000-0000000000aa", "Grant's wedding"),
            ("0191f2c4-0000-7000-8000-0000000000bb", "Grant Carver"),
        ]},
    )
    adaptor = NoteEntityResolutionAdaptor(entities=entities)

    rendered = await adaptor.inputs(key=WS, batch=())

    assert "[1] Grant 婚禮" in rendered
    assert "[a] Grant's wedding" in rendered
    assert "[b] Grant Carver" in rendered
    assert "0191f2c4" not in rendered


def test_the_two_handle_alphabets_are_disjoint():
    assert not (set(_NAME_HANDLES) & set(_ENTITY_HANDLES))


async def test_a_null_entity_is_a_decision_and_produces_nothing():
    """21 §5's default-to-split, identical to the fact tier's pass 6: saying
    no leaves two entries that might have been one, and somebody can join
    them later; saying yes has no way back."""
    metrics = Metrics()
    entities = _FakeEntities(
        unresolved=("Chi Ming",),
        similar={"Chi Ming": [("e-1", "Grant Carver")]},
    )
    adaptor = NoteEntityResolutionAdaptor(entities=entities, metrics=metrics)
    await adaptor.inputs(key=WS, batch=())

    parsed = await adaptor.accept(key=WS, items=({"name": "1", "entity": None},))

    assert parsed == ()
    assert metrics.get("nora_note_entity_resolutions_total", status="split") == 1


async def test_a_missing_entity_key_is_malformed_not_a_split():
    metrics = Metrics()
    entities = _FakeEntities(
        unresolved=("Chi Ming",),
        similar={"Chi Ming": [("e-1", "Grant Carver")]},
    )
    adaptor = NoteEntityResolutionAdaptor(entities=entities, metrics=metrics)
    await adaptor.inputs(key=WS, batch=())

    await adaptor.accept(key=WS, items=({"name": "1"},))

    assert metrics.get(
        "nora_note_entity_resolutions_total", status="malformed") == 1
    assert metrics.get("nora_note_entity_resolutions_total", status="split") == 0


async def test_a_handle_this_run_never_rendered_is_dropped():
    metrics = Metrics()
    entities = _FakeEntities(
        unresolved=("Chi Ming",),
        similar={"Chi Ming": [("e-1", "Grant Carver")]},
    )
    adaptor = NoteEntityResolutionAdaptor(entities=entities, metrics=metrics)
    await adaptor.inputs(key=WS, batch=())

    parsed = await adaptor.accept(key=WS, items=(
        {"name": "1", "entity": "z"},
        {"name": "9", "entity": "a"},
    ))

    assert parsed == ()
    assert metrics.get(
        "nora_note_entity_resolutions_total", status="unknown_handle") == 2


async def test_a_resolved_name_is_attached_as_an_alias():
    """The whole positive path, and the only write this pass has."""
    entities = _FakeEntities(
        unresolved=("Chi Ming",),
        similar={"Chi Ming": [("e-1", "Grant Carver")]},
    )
    adaptor = NoteEntityResolutionAdaptor(entities=entities)
    await adaptor.inputs(key=WS, batch=())
    parsed = await adaptor.accept(key=WS, items=({"name": "1", "entity": "a"},))

    written = await adaptor.commit(
        key=WS, proposals=tuple(_proposal(p) for p in parsed), now_ms=NOW,
    )

    assert written == 1
    assert entities.attached == [("e-1", "Chi Ming")]


async def test_a_name_with_no_near_misses_is_never_offered():
    entities = _FakeEntities(
        unresolved=("Chi Ming", "Grant 婚禮"), similar={},
    )
    adaptor = NoteEntityResolutionAdaptor(entities=entities)

    assert await adaptor.inputs(key=WS, batch=()) == ""


async def test_a_name_with_candidates_is_offered_beside_one_without():
    entities = _FakeEntities(
        unresolved=("Chi Ming", "Grant 婚禮"),
        similar={"Grant 婚禮": [("e-1", "Grant's wedding")]},
    )
    adaptor = NoteEntityResolutionAdaptor(entities=entities)

    rendered = await adaptor.inputs(key=WS, batch=())

    assert "Grant 婚禮" in rendered
    assert "[a] Grant's wedding" in rendered
    assert "Chi Ming" not in rendered


async def test_re_deciding_an_alias_already_present_counts_nothing():
    metrics = Metrics()
    entities = _FakeEntities(
        unresolved=("Chi Ming",),
        similar={"Chi Ming": [("e-1", "Grant Carver")]},
        existing_aliases={("e-1", "Chi Ming")},
    )
    adaptor = NoteEntityResolutionAdaptor(entities=entities, metrics=metrics)
    await adaptor.inputs(key=WS, batch=())
    parsed = await adaptor.accept(key=WS, items=({"name": "1", "entity": "a"},))

    written = await adaptor.commit(
        key=WS, proposals=tuple(_proposal(p) for p in parsed), now_ms=NOW,
    )

    assert written == 0
    assert metrics.get("nora_note_entity_aliases_attached_total") == 0


async def test_commit_refuses_a_payload_that_is_not_a_resolution():
    adaptor = NoteEntityResolutionAdaptor(entities=_FakeEntities())

    try:
        await adaptor.commit(
            key=WS, proposals=(_proposal("not a resolution"),), now_ms=NOW,
        )
    except TypeError as exc:
        assert "NoteEntityResolution" in str(exc)
    else:
        raise AssertionError("expected TypeError")


async def test_the_adaptor_has_no_tools():
    assert NoteEntityResolutionAdaptor.tools == frozenset()


async def test_the_offered_count_is_the_positive_control():
    metrics = Metrics()
    entities = _FakeEntities(
        unresolved=("Chi Ming",),
        similar={"Chi Ming": [("e-1", "Grant Carver")]},
    )
    adaptor = NoteEntityResolutionAdaptor(entities=entities, metrics=metrics)

    await adaptor.inputs(key=WS, batch=())

    assert metrics.get("nora_note_entity_resolution_offered_total") == 1


async def test_the_value_type_carries_no_negative_case():
    assert set(NoteEntityResolution.__annotations__) == {"name", "entity_id"}


async def test_accept_without_inputs_refuses_rather_than_keeping_raw_handles():
    """The identical self-attestation guard the fact tier's pass 6 carries:
    with no record of what this run rendered, a handle names nothing and
    must be dropped rather than kept as the model's own ordinal."""
    metrics = Metrics()
    adaptor = NoteEntityResolutionAdaptor(
        entities=_FakeEntities(), metrics=metrics,
    )

    parsed = await adaptor.accept(key=WS, items=[{"name": "1", "entity": "a"}])

    assert parsed == ()
    assert metrics.get(
        "nora_note_entity_resolutions_total", status="unknown_handle") == 1
    assert metrics.get("nora_note_entity_resolutions_total", status="merged") == 0


async def test_the_same_batch_after_inputs_is_kept_and_carries_real_values():
    entities = _FakeEntities(
        unresolved=("Chi Ming",),
        similar={"Chi Ming": [("e-1", "Grant Carver")]},
    )
    metrics = Metrics()
    adaptor = NoteEntityResolutionAdaptor(entities=entities, metrics=metrics)
    await adaptor.inputs(key=WS, batch=())

    parsed = await adaptor.accept(key=WS, items=[{"name": "1", "entity": "a"}])

    assert len(parsed) == 1
    resolution = parsed[0]
    assert isinstance(resolution, NoteEntityResolution)
    assert (resolution.name, resolution.entity_id) == ("Chi Ming", "e-1")
    assert metrics.get("nora_note_entity_resolutions_total", status="merged") == 1
