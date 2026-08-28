"""Pass 6 — the half of entity resolution that needs judgement (21 §5).

The deterministic half runs in the sink on every write and handles the
overwhelming majority for free. What reaches this adaptor is what exact
matching cannot settle, and the property that matters most is what it CANNOT
do: it attaches an alias and never merges two entity rows, because a split is
provisional and a merge has already absorbed both sets of edges.

The store is a double here rather than real Postgres, and deliberately: what is
under test is the adaptor's handle discipline and its refusals, not SQL. The
store's own three verbs are tested against Postgres in
`tests/infra/test_entity_store.py`. The double is faithful where this adaptor
BRANCHES — an unknown handle, an empty candidate list, an alias already
present — and nowhere else, which is the only place it could hide a defect.
"""

from __future__ import annotations

from nora.domain.ports import Proposal
from nora.language.fact_adaptor import (
    _ENTITY_HANDLES,
    _NAME_HANDLES,
    EntityResolution,
    FactEntityResolutionAdaptor,
)
from nora.obs.metrics import Metrics

WS = "ws-main"
NOW = 1_755_200_000_000


class _FakeEntities:
    """Matches production where the adaptor branches, and says where it does
    not. `similar_entities` returns whatever was seeded for a name — including
    the empty tuple, which is the case that decides whether a name is offered
    at all."""

    def __init__(self, *, unresolved=(), similar=None, existing_aliases=()):
        self._unresolved = tuple(unresolved)
        self._similar = similar or {}
        self.attached: list[tuple[str, str]] = []
        self._existing = set(existing_aliases)

    async def unresolved_names(self, *, workspace: str, limit: int):
        return self._unresolved[:limit]

    async def similar_entities(self, *, workspace: str, name: str, limit: int):
        return tuple(self._similar.get(name, ()))[:limit]

    async def attach_alias(self, *, workspace: str, entity_id: str, alias: str):
        # Returns False for one already present, exactly as the store does —
        # the branch `commit`'s count depends on.
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
    """The rule that caught three adaptors: anything a model echoes back is an
    ordinal handle, never a stored identifier. `dream_screen` drops any
    envelope item carrying a uuid, silently, while the tier's own counters look
    healthy."""
    entities = _FakeEntities(
        unresolved=("HKO",),
        similar={"HKO": [
            ("0191f2c4-0000-7000-8000-0000000000aa", "Hong Kong Observatory"),
            ("0191f2c4-0000-7000-8000-0000000000bb", "HK Observatory"),
        ]},
    )
    adaptor = FactEntityResolutionAdaptor(entities=entities)

    rendered = await adaptor.inputs(key=WS, batch=())

    assert "[1] HKO" in rendered
    assert "[a] Hong Kong Observatory" in rendered
    assert "[b] HK Observatory" in rendered
    assert "0191f2c4" not in rendered


def test_the_two_handle_alphabets_are_disjoint():
    """This is the property, not a style choice.

    Numbers name the thing being decided and letters name what it might be.
    Upstream numbers BOTH, which puts two integer spaces in one prompt, makes
    `1` ambiguous at a glance, and — the part that matters — makes a handle
    from the wrong space a legal key in the other space's map, so it resolves
    silently to the wrong row instead of failing.

    Disjointness is what makes that unrepresentable rather than merely
    discouraged: a number can never key into a letter-keyed map. Asserted
    directly because the parse-level check is redundant WITH this and would
    otherwise look like the guard when it is the belt."""
    assert not (set(_NAME_HANDLES) & set(_ENTITY_HANDLES))


async def test_a_handle_from_the_wrong_space_resolves_to_nothing():
    entities = _FakeEntities(
        unresolved=("HKO",),
        similar={"HKO": [("e-1", "Hong Kong Observatory")]},
    )
    adaptor = FactEntityResolutionAdaptor(entities=entities)
    await adaptor.inputs(key=WS, batch=())

    parsed = await adaptor.accept(key=WS, items=(
        {"name": "a", "entity": "1"},          # both handles, both wrong space
        {"name": "1", "entity": "1"},          # valid name, entity from numbers
    ))

    assert parsed == ()
    # The positive control: the same shapes in the RIGHT spaces do resolve, so
    # the empty result above is about the spaces and not a parser that refuses
    # everything.
    assert len(await adaptor.accept(
        key=WS, items=({"name": "1", "entity": "a"},))) == 1


async def test_a_null_entity_is_a_decision_and_produces_nothing():
    """21 §5's default-to-split, and the objective's own wording already
    carries it. Saying no leaves two entries that might have been one and
    somebody can join them later; saying yes merges two things that were not
    one and there is no way back.

    It produces no VALUE — there is nothing to write, because the name simply
    stays as it was — but it IS counted, because a pass that decided no forty
    times and a pass that never ran are otherwise the same reading."""
    metrics = Metrics()
    entities = _FakeEntities(
        unresolved=("HKO",), similar={"HKO": [("e-1", "Hong Kong Observatory")]},
    )
    adaptor = FactEntityResolutionAdaptor(entities=entities, metrics=metrics)
    await adaptor.inputs(key=WS, batch=())

    parsed = await adaptor.accept(key=WS, items=(
        {"name": "1", "entity": None},
    ))

    assert parsed == ()
    assert metrics.get("nora_fact_resolutions_total", status="split") == 1


async def test_a_missing_entity_key_is_malformed_not_a_split():
    """An explicit `null` is the model answering; an absent key is the model
    not answering. Collapsing them would report every malformed item as a
    considered decision to keep two things apart."""
    metrics = Metrics()
    entities = _FakeEntities(
        unresolved=("HKO",), similar={"HKO": [("e-1", "Hong Kong Observatory")]},
    )
    adaptor = FactEntityResolutionAdaptor(entities=entities, metrics=metrics)
    await adaptor.inputs(key=WS, batch=())

    await adaptor.accept(key=WS, items=({"name": "1"},))

    assert metrics.get("nora_fact_resolutions_total", status="malformed") == 1
    assert metrics.get("nora_fact_resolutions_total", status="split") == 0


async def test_a_handle_this_run_never_rendered_is_dropped():
    """`_Shown`'s rule, one pass over: the model may only name something it was
    shown. A resolution naming a row nobody put in front of it is a guess with
    a merge attached."""
    metrics = Metrics()
    entities = _FakeEntities(
        unresolved=("HKO",), similar={"HKO": [("e-1", "Hong Kong Observatory")]},
    )
    adaptor = FactEntityResolutionAdaptor(entities=entities, metrics=metrics)
    await adaptor.inputs(key=WS, batch=())

    parsed = await adaptor.accept(key=WS, items=(
        {"name": "1", "entity": "z"},          # letter, but never rendered
        {"name": "9", "entity": "a"},          # number, but never rendered
    ))

    assert parsed == ()
    assert metrics.get(
        "nora_fact_resolutions_total", status="unknown_handle") == 2


async def test_a_resolved_name_is_attached_as_an_alias():
    """The whole positive path, and the only write this pass has."""
    entities = _FakeEntities(
        unresolved=("HKO",), similar={"HKO": [("e-1", "Hong Kong Observatory")]},
    )
    adaptor = FactEntityResolutionAdaptor(entities=entities)
    await adaptor.inputs(key=WS, batch=())
    parsed = await adaptor.accept(key=WS, items=(
        {"name": "1", "entity": "a"},
    ))

    written = await adaptor.commit(
        key=WS, proposals=tuple(_proposal(p) for p in parsed), now_ms=NOW,
    )

    assert written == 1
    assert entities.attached == [("e-1", "HKO")]


async def test_a_name_with_no_near_misses_is_never_offered():
    """There is nothing for the model to choose between, and asking would
    invite it to invent one. Blank input also means the runtime skips the model
    entirely, which is the difference between a quiet workspace costing nothing
    and costing one call per tick forever."""
    entities = _FakeEntities(unresolved=("HKO", "big walk"), similar={})
    adaptor = FactEntityResolutionAdaptor(entities=entities)

    assert await adaptor.inputs(key=WS, batch=()) == ""


async def test_a_name_with_candidates_is_offered_beside_one_without():
    """The positive control for the test above: a store returning nothing for
    every name would satisfy it while making the pass structurally unable to
    run."""
    entities = _FakeEntities(
        unresolved=("HKO", "big walk"),
        similar={"big walk": [("e-2", "Big Walk")]},
    )
    adaptor = FactEntityResolutionAdaptor(entities=entities)

    rendered = await adaptor.inputs(key=WS, batch=())

    assert "big walk" in rendered
    assert "[a] Big Walk" in rendered
    assert "HKO" not in rendered


async def test_re_deciding_an_alias_already_present_counts_nothing():
    """A background pass legitimately re-fires over work it already did.
    Counting the repeat would make a loop that re-runs look like a loop that is
    learning."""
    metrics = Metrics()
    entities = _FakeEntities(
        unresolved=("HKO",),
        similar={"HKO": [("e-1", "Hong Kong Observatory")]},
        existing_aliases={("e-1", "HKO")},
    )
    adaptor = FactEntityResolutionAdaptor(entities=entities, metrics=metrics)
    await adaptor.inputs(key=WS, batch=())
    parsed = await adaptor.accept(key=WS, items=({"name": "1", "entity": "a"},))

    written = await adaptor.commit(
        key=WS, proposals=tuple(_proposal(p) for p in parsed), now_ms=NOW,
    )

    assert written == 0
    assert metrics.get("nora_fact_aliases_attached_total") == 0


async def test_commit_refuses_a_payload_that_is_not_a_resolution():
    """`Proposal.payload` is `object` at the port on purpose, so the store is
    the one place that can catch a tier mismatch."""
    adaptor = FactEntityResolutionAdaptor(entities=_FakeEntities())

    try:
        await adaptor.commit(
            key=WS, proposals=(_proposal("not a resolution"),), now_ms=NOW,
        )
    except TypeError as exc:
        assert "EntityResolution" in str(exc)
    else:
        raise AssertionError("expected TypeError")


async def test_the_adaptor_has_no_tools():
    """Deciding whether two names mean one thing is a judgement about the two
    names, not research. A pass that could search would be tempted to settle
    `HKO` by reading a page, which is the adjudicator's job on a CLAIM and not
    this one's on a NAME."""
    assert FactEntityResolutionAdaptor.tools == frozenset()


async def test_the_offered_count_is_the_positive_control():
    """Every other series here is compatible with a pass that was never asked
    anything."""
    metrics = Metrics()
    entities = _FakeEntities(
        unresolved=("HKO",), similar={"HKO": [("e-1", "Hong Kong Observatory")]},
    )
    adaptor = FactEntityResolutionAdaptor(entities=entities, metrics=metrics)

    await adaptor.inputs(key=WS, batch=())

    assert metrics.get("nora_fact_resolution_offered_total") == 1


async def test_the_value_type_carries_no_negative_case():
    """A `null` verdict produces nothing rather than a row saying "no".
    Representing the negative would make it indistinguishable from a decision
    to keep two things apart FOREVER, and 21 §5's whole argument is that a
    split is provisional."""
    assert set(EntityResolution.__annotations__) == {"name", "entity_id"}


async def test_accept_without_inputs_refuses_rather_than_keeping_raw_handles():
    """**The one self-attesting write in the tier, and it reached a uuid
    column.**

    With no record of what this run rendered, `accept` used to keep
    `EntityResolution(name="1", entity_id="a")` — the model's own ordinal
    handles — and count it `merged`. `commit` then handed `"a"` to
    `attach_alias` against `fact_entity.id`, a `pg.UUID`: an invalid-UUID
    error, the tick reporting `unavailable`, and the merge counter already
    moved. Every other path in this tier enforces «the model may only name
    something it was shown»; this was the one that did not.

    Counted `unknown_handle`, which is the same label the not-in-the-map miss
    uses, because in both cases the handle names nothing this run rendered.
    """
    metrics = Metrics()
    adaptor = FactEntityResolutionAdaptor(
        entities=_FakeEntities(), metrics=metrics,
    )

    parsed = await adaptor.accept(
        key=WS, items=[{"name": "1", "entity": "a"}],
    )

    assert parsed == ()
    assert metrics.get(
        "nora_fact_resolutions_total", status="unknown_handle") == 1
    assert metrics.get("nora_fact_resolutions_total", status="merged") == 0


async def test_the_same_batch_after_inputs_is_kept_and_carries_real_values():
    """The positive control for the refusal above — and the half that proves
    it is a refusal rather than a broken parse. Same items, same adaptor
    class; the only difference is that `inputs` ran, so a map exists.

    It also fixes the property the refusal protects: what comes back is the
    stored NAME and the stored uuid, never the handles that were echoed.
    """
    entities = _FakeEntities(
        unresolved=("HKO",),
        similar={"HKO": [
            ("0191f2c4-0000-7000-8000-0000000000aa", "Hong Kong Observatory"),
        ]},
    )
    metrics = Metrics()
    adaptor = FactEntityResolutionAdaptor(entities=entities, metrics=metrics)
    await adaptor.inputs(key=WS, batch=())

    parsed = await adaptor.accept(key=WS, items=[{"name": "1", "entity": "a"}])

    assert parsed == (
        EntityResolution(
            name="HKO", entity_id="0191f2c4-0000-7000-8000-0000000000aa",
        ),
    )
    assert metrics.get("nora_fact_resolutions_total", status="merged") == 1
