"""The fact adaptors' behaviour the shared contract suite cannot reach.

`tests/contract/test_dream_adaptor_contract.py` asserts what every
`DreamAdaptor` must do — parse and drop, `commit(key, ())` reaches the sink,
`tools` narrows the ceiling. It deliberately says nothing about a TIER's
vocabulary, and every property below is about this tier's vocabulary:

- a `contradicted` verdict supersedes THE FACT IT NAMED, and not another one;
- a `not_covered` verdict writes no fact AND leaves the standing fact standing
  (R-53/R-54 — the tier's whole point, and the shape §5.1 was derived from);
- a `supersedes` naming a fact this run never showed the model is dropped;
- §5.1's negative cache is actually written, so the same unprovable claim is
  not re-adjudicated every tick forever.

Built on `nora.testing`'s in-memory stores rather than on ad-hoc fakes: those
are real `DreamSink` implementations that pass the sink contract suite, and
they apply the same refusals the SQL stores do — a fake that accepted anything
would make every assertion here vacuous.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest

from nora.domain.facts import CandidateClaim, Fact, FactEntity
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.ports import Proposal
from nora.language.fact_adaptor import FactAdaptor, FactCandidateAdaptor
from nora.obs.metrics import Metrics
from nora.testing.fact_candidate_store import InMemoryFactCandidateStore
from nora.testing.fact_store import InMemoryFactStore

WS = "ws-main"
SCOPE = "signal:g1"
SHA = "b" * 64
NOW = 1_700_000_000_000


def _proposal(payload: object) -> Proposal:
    """The runtime's job, done by hand — provenance an adaptor cannot know."""
    return Proposal(
        payload=payload, instruction_sha256=SHA, model="deepseek-v4-flash",
        established_by="dream:test",
    )


def _row(row_id: int, body: str | None) -> TranscriptRow:
    return TranscriptRow(
        id=row_id, sender_id="p1", sender_name="A", ts=1_000, body=body,
        is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
    )


def _verdict_item(
    subject: str, predicate: str, obj: str, status: str, **extra: object
) -> Mapping[str, object]:
    item: dict[str, object] = {
        "subject": subject, "predicate": predicate, "object": obj,
        "status": status, "addresses_claim": status != "not_covered",
    }
    if status != "not_covered":
        item.setdefault("source_url", f"https://example.test/{predicate}")
        item.setdefault("source_excerpt", f"a sentence carrying {obj}")
    item.update(extra)
    return item


async def _standing_fact(
    facts: InMemoryFactStore, *, subject: str, predicate: str, obj: str,
) -> Fact:
    """Write one standing fact through the store's own verb, so the row is
    exactly what a previous tick would have left behind."""
    from nora.domain.facts import FactVerdict

    ids = await facts.propose(
        key=WS,
        proposals=(_proposal(FactVerdict(
            subject=subject, predicate=predicate, object=obj,
            status="supported", addresses_claim=True,
            source_url=f"https://example.test/standing/{predicate}",
            source_excerpt=f"the page that established {obj}",
        )),),
        now_ms=NOW - 1,
    )
    fact = await facts.fact_by_id(workspace=WS, fact_id=ids[0])
    assert fact is not None
    return fact


async def _pending_candidate(
    candidates: InMemoryFactCandidateStore, *,
    subject: str, predicate: str, obj: str, message_id: int = 1,
) -> str:
    ids = await candidates.propose(
        key=WS,
        proposals=(_proposal(CandidateClaim(
            subject=subject, predicate=predicate, object=obj,
            message_id=message_id,
        )),),
        now_ms=NOW - 1,
    )
    return ids[0]


def _entity(name: str) -> FactEntity:
    """`pending` drops an unresolvable subject seen once as one-off noise, so
    a candidate a test wants adjudicated needs an entity to resolve to."""
    return FactEntity(id=f"e-{name}", workspace=WS, canonical_name=name, created_at=0)


# ---------------------------------------------------------------------------
# Phase A — FactCandidateAdaptor
# ---------------------------------------------------------------------------


def test_the_extractor_holds_no_tools_at_all():
    """§5's cost decision, fixed as a test rather than left in a docstring.

    One cheap tool-free call per tick decides what is worth a research loop; a
    research loop per message is unbounded cost on a background clock nobody
    is watching. Granting this adaptor even one tool is the change that turns
    Phase A into Phase B for every message in the room."""
    adaptor = FactCandidateAdaptor(
        candidates=InMemoryFactCandidateStore(), workspace_for=lambda _: WS,
    )
    assert adaptor.tools == frozenset()


async def test_inputs_renders_every_row_including_one_with_no_body():
    """A media-only message has `body is None`, and the row is still rendered
    WITH ITS ID rather than skipped: `accept` validates a returned
    `message_id` against the whole batch, so a row dropped from the render
    would make the set the model was shown smaller than the set `accept`
    accepts from it."""
    adaptor = FactCandidateAdaptor(
        candidates=InMemoryFactCandidateStore(), workspace_for=lambda _: WS,
    )

    rendered = await adaptor.inputs(
        key=SCOPE, batch=(_row(41, "big walk 今晚?"), _row(42, None)),
    )

    assert "41: big walk 今晚?" in rendered
    assert "42: " in rendered


async def test_a_message_id_outside_the_batch_is_dropped():
    """The objective says "do not use a message id that is not in the batch".
    An invented id points a later reader at a message that does not exist —
    fabricated provenance, which `LexemeObserver._extract` drops for the same
    reason."""
    adaptor = FactCandidateAdaptor(
        candidates=InMemoryFactCandidateStore(), workspace_for=lambda _: WS,
    )
    await adaptor.inputs(key=SCOPE, batch=(_row(41, "a claim"),))

    parsed = await adaptor.accept(key=SCOPE, items=(
        {"subject": "big walk", "predicate": "contains", "object": "a tower",
         "message_id": 41},
        {"subject": "big walk", "predicate": "costs", "object": "ten",
         "message_id": 999},
    ))

    assert [c.message_id for c in parsed] == [41]  # type: ignore[attr-defined]


async def test_an_over_long_field_never_reaches_the_store():
    """The F14 shape: an unbounded model-authored string reaching a durable
    column — and here it would additionally become half of
    `fact_candidate_triple_key`, a key nobody can ever match a later sighting
    against."""
    adaptor = FactCandidateAdaptor(
        candidates=InMemoryFactCandidateStore(), workspace_for=lambda _: WS,
    )

    parsed = await adaptor.accept(key=SCOPE, items=(
        {"subject": "x" * 5_000, "predicate": "contains", "object": "a tower",
         "message_id": 41},
    ))

    assert parsed == ()


async def test_the_extractor_writes_its_claims_through_the_candidate_sink():
    store = InMemoryFactCandidateStore()
    # `pending` drops an unresolvable subject seen once as one-off noise, so
    # the entity is what makes the written row observable through the read
    # half rather than through the store's internals.
    store.entities = [_entity("big walk")]
    adaptor = FactCandidateAdaptor(candidates=store, workspace_for=lambda _: WS)
    await adaptor.inputs(key=SCOPE, batch=(_row(41, "a claim"),))
    payloads = await adaptor.accept(key=SCOPE, items=(
        {"subject": "big walk", "predicate": "contains", "object": "a tower",
         "message_id": 41},
    ))

    written = await adaptor.commit(
        key=SCOPE, proposals=tuple(_proposal(p) for p in payloads), now_ms=NOW,
    )

    assert written == 1
    assert (await store.pending(workspace=WS, limit=10))[0].object == "a tower"


async def test_candidates_are_filed_under_the_workspace_not_the_scope():
    """The extractor is SCOPE-keyed (a watermark and a message batch are per
    room) and `fact_candidate.workspace` is a WORKSPACE (DEC-105), which is
    what the workspace-keyed adjudicator reads. Filing a candidate under the
    scope id would put every room's claims in a "workspace" named after the
    room, where `pending()` never finds one — the two halves of the tier wired
    end to end and unable to see each other, silently, forever.

    Asserted through the READ the adjudicator actually makes, not through the
    write: `pending(workspace=SCOPE)` returning nothing is the half that
    matters."""
    store = InMemoryFactCandidateStore()
    store.entities = [_entity("big walk")]
    adaptor = FactCandidateAdaptor(candidates=store, workspace_for=lambda _: WS)
    await adaptor.inputs(key=SCOPE, batch=(_row(41, "a claim"),))
    payloads = await adaptor.accept(key=SCOPE, items=(
        {"subject": "big walk", "predicate": "contains", "object": "a tower",
         "message_id": 41},
    ))

    await adaptor.commit(
        key=SCOPE, proposals=tuple(_proposal(p) for p in payloads), now_ms=NOW,
    )

    assert len(await store.pending(workspace=WS, limit=10)) == 1
    assert await store.pending(workspace=SCOPE, limit=10) == ()


async def test_a_scope_with_no_workspace_writes_nothing_and_raises():
    """Fail closed, and loudly. Within one workspace a fact crosses rooms BY
    DESIGN (R-49/DEC-105), so a candidate filed under a guessed workspace is
    one social unit's claim entering another's standing facts. Raising makes
    the runtime report `unavailable`, which holds the watermark — the batch is
    retried rather than consumed by a tick that wrote nothing."""
    store = InMemoryFactCandidateStore()
    adaptor = FactCandidateAdaptor(candidates=store, workspace_for=lambda _: None)
    payloads = await adaptor.accept(key=SCOPE, items=(
        {"subject": "big walk", "predicate": "contains", "object": "a tower",
         "message_id": 41},
    ))

    with pytest.raises(LookupError):
        await adaptor.commit(
            key=SCOPE, proposals=tuple(_proposal(p) for p in payloads),
            now_ms=NOW,
        )

    assert store._by_triple == {}


# ---------------------------------------------------------------------------
# Phase B — FactAdaptor
# ---------------------------------------------------------------------------


def _adjudicator(
    facts: InMemoryFactStore, candidates: InMemoryFactCandidateStore,
    *, metrics: Metrics | None = None,
) -> FactAdaptor:
    return FactAdaptor(
        facts=facts, candidates=candidates, max_adjudications_per_tick=10,
        metrics=metrics,
    )


def test_the_adjudicators_tools_stay_inside_the_workspace_safe_set():
    """Every other read tool is bound to a CONVERSATION, and a workspace has
    no single room to bind one to. Measured against
    `nora.agent.dreamer.WORKSPACE_SAFE_TOOLS` rather than restated, so a tool
    added to this adaptor tomorrow is checked against the list that actually
    governs it."""
    from nora.agent.dreamer import WORKSPACE_SAFE_TOOLS

    adaptor = _adjudicator(InMemoryFactStore(), InMemoryFactCandidateStore())
    assert adaptor.tools <= WORKSPACE_SAFE_TOOLS
    assert adaptor.tools


async def test_inputs_shows_every_candidate_and_every_standing_fact_with_its_id():
    """`supersedes` names a fact id and the model may only name one it was
    shown, so a fact rendered without its id is a fact it cannot correct."""
    facts, candidates = InMemoryFactStore(), InMemoryFactCandidateStore()
    standing = await _standing_fact(
        facts, subject="big walk", predicate="gourds_required", obj="five",
    )
    candidates.facts = [standing]
    candidates.entities = [_entity("big walk")]
    candidate_id = await _pending_candidate(
        candidates, subject="big walk", predicate="gourds_required", obj="three",
    )

    rendered = await _adjudicator(facts, candidates).inputs(key=WS, batch=())

    assert candidate_id in rendered
    assert standing.id in rendered
    assert "the page that established five" in rendered


async def test_a_contradicted_verdict_supersedes_the_fact_it_named():
    """The tier's central write, and the assertion is that the OTHER standing
    fact is untouched — a supersession that retires the wrong row is silent,
    permanent and unauditable."""
    facts, candidates = InMemoryFactStore(), InMemoryFactCandidateStore()
    target = await _standing_fact(
        facts, subject="big walk", predicate="gourds_required", obj="five",
    )
    bystander = await _standing_fact(
        facts, subject="big walk", predicate="unlocked_by", obj="four towers",
    )
    candidates.facts = [target, bystander]
    candidates.entities = [_entity("big walk")]
    await _pending_candidate(
        candidates, subject="big walk", predicate="gourds_required", obj="three",
    )
    adaptor = _adjudicator(facts, candidates)
    await adaptor.inputs(key=WS, batch=())

    payloads = await adaptor.accept(key=WS, items=(
        _verdict_item(
            "big walk", "gourds_required", "three", "contradicted",
            supersedes=target.id,
        ),
    ))
    written = await adaptor.commit(
        key=WS, proposals=tuple(_proposal(p) for p in payloads), now_ms=NOW,
    )

    assert written == 1
    retired = await facts.fact_by_id(workspace=WS, fact_id=target.id)
    assert retired is not None and not retired.is_standing
    assert retired.supersede_reason == "wrong"
    successor = await facts.fact_by_id(
        workspace=WS, fact_id=retired.superseded_by or "",
    )
    assert successor is not None and successor.object_literal == "three"
    still = await facts.fact_by_id(workspace=WS, fact_id=bystander.id)
    assert still is not None and still.is_standing


async def test_a_not_covered_verdict_writes_no_fact_and_removes_none(caplog):
    """R-53/R-54, and the reason `FactVerdict` is shaped the way it is: a
    source that does not reach the question has not denied it. Nothing is
    written and nothing standing is removed — the outcome the whole tier is
    built to make expressible."""
    facts, candidates = InMemoryFactStore(), InMemoryFactCandidateStore()
    standing = await _standing_fact(
        facts, subject="big walk", predicate="contains", obj="a black tower",
    )
    candidates.facts = [standing]
    candidates.entities = [_entity("big walk")]
    await _pending_candidate(
        candidates, subject="big walk", predicate="contains", obj="no black tower",
    )
    adaptor = _adjudicator(facts, candidates)
    await adaptor.inputs(key=WS, batch=())

    payloads = await adaptor.accept(key=WS, items=(
        _verdict_item("big walk", "contains", "no black tower", "not_covered"),
    ))
    written = await adaptor.commit(
        key=WS, proposals=tuple(_proposal(p) for p in payloads), now_ms=NOW,
    )

    assert written == 0
    assert len(facts._facts) == 1
    survivor = await facts.fact_by_id(workspace=WS, fact_id=standing.id)
    assert survivor is not None and survivor.is_standing


async def test_a_not_covered_verdict_writes_the_negative_cache():
    """§5.1: without this the same unprovable claim is re-extracted and
    re-adjudicated every tick, forever, and it fails quietly because nobody is
    waiting on the Dreamer. Asserted through `pending` — the row must stop
    coming back — as well as through the retained row's status."""
    facts, candidates = InMemoryFactStore(), InMemoryFactCandidateStore()
    standing = await _standing_fact(
        facts, subject="big walk", predicate="contains", obj="a black tower",
    )
    candidates.facts = [standing]
    candidates.entities = [_entity("big walk")]
    candidate_id = await _pending_candidate(
        candidates, subject="big walk", predicate="contains", obj="no black tower",
    )
    adaptor = _adjudicator(facts, candidates)
    await adaptor.inputs(key=WS, batch=())

    payloads = await adaptor.accept(key=WS, items=(
        _verdict_item("big walk", "contains", "no black tower", "not_covered"),
    ))
    await adaptor.commit(
        key=WS, proposals=tuple(_proposal(p) for p in payloads), now_ms=NOW,
    )

    retired = await candidates.candidate_by_id(
        workspace=WS, candidate_id=candidate_id,
    )
    assert retired is not None and retired.status == "not_covered"
    assert retired.attempts == 1
    assert await candidates.pending(workspace=WS, limit=10) == ()


async def test_a_settled_verdict_retires_its_candidate_as_adjudicated():
    """The other half of the same mapping: a candidate that WAS settled must
    also stop coming back, and it is retired for a different reason than one
    nobody could settle."""
    facts, candidates = InMemoryFactStore(), InMemoryFactCandidateStore()
    candidates.entities = [_entity("big walk")]
    candidate_id = await _pending_candidate(
        candidates, subject="big walk", predicate="unlocked_by",
        obj="four towers",
    )
    adaptor = _adjudicator(facts, candidates)
    await adaptor.inputs(key=WS, batch=())

    payloads = await adaptor.accept(key=WS, items=(
        _verdict_item("big walk", "unlocked_by", "four towers", "supported"),
    ))
    await adaptor.commit(
        key=WS, proposals=tuple(_proposal(p) for p in payloads), now_ms=NOW,
    )

    retired = await candidates.candidate_by_id(
        workspace=WS, candidate_id=candidate_id,
    )
    assert retired is not None and retired.status == "adjudicated"


async def test_a_supersedes_naming_a_fact_this_run_never_showed_is_dropped():
    """The model can hallucinate a uuid. A supersession of a row nobody showed
    it is not a verdict, it is a guess with a delete attached — and it is
    checked against what THIS RUN rendered, not against what exists, because a
    real fact about another subject is still one nobody put in front of it."""
    facts, candidates = InMemoryFactStore(), InMemoryFactCandidateStore()
    unshown = await _standing_fact(
        facts, subject="other thing", predicate="costs", obj="ten",
    )
    candidates.entities = [_entity("big walk")]
    await _pending_candidate(
        candidates, subject="big walk", predicate="gourds_required", obj="three",
    )
    metrics = Metrics()
    adaptor = _adjudicator(facts, candidates, metrics=metrics)
    await adaptor.inputs(key=WS, batch=())

    payloads = await adaptor.accept(key=WS, items=(
        _verdict_item(
            "big walk", "gourds_required", "three", "contradicted",
            supersedes=unshown.id,
        ),
    ))

    assert payloads == ()
    assert metrics.get(
        "nora_fact_adjudications_skipped_total", reason="unknown_supersedes",
    ) == 1
    survivor = await facts.fact_by_id(workspace=WS, fact_id=unshown.id)
    assert survivor is not None and survivor.is_standing


async def test_a_verdict_that_breaks_a_batch_invariant_is_dropped_not_raised():
    """`FactVerdict.__post_init__` enforces R-27's invariants by RAISING,
    which is right for a constructor and fatal for a background tick: an
    exception here reaches the runtime as `unavailable`, holding the watermark
    over a batch that was in fact read. Caught, dropped, counted."""
    metrics = Metrics()
    adaptor = _adjudicator(
        InMemoryFactStore(), InMemoryFactCandidateStore(), metrics=metrics,
    )

    payloads = await adaptor.accept(key=WS, items=(
        # `supported` with no source at all.
        {"subject": "big walk", "predicate": "contains", "object": "a shop",
         "status": "supported", "addresses_claim": True},
    ))

    assert payloads == ()
    assert metrics.get(
        "nora_fact_adjudications_skipped_total", reason="invariant",
    ) == 1


async def test_each_contradiction_supersedes_with_its_own_new_fact_id():
    """The propose-id correspondence, which is the one thing in `commit` that
    cannot be got right by accident.

    `propose` returns ids only for rows it WROTE, so with a `not_covered`
    verdict FIRST in the batch a positional zip against `proposals` pairs
    every contradiction with the wrong successor — the first would retire its
    target naming the second's fact, and the second would retire nothing. Both
    halves are asserted: the right target, and the right successor on it."""
    facts, candidates = InMemoryFactStore(), InMemoryFactCandidateStore()
    first = await _standing_fact(
        facts, subject="big walk", predicate="gourds_required", obj="five",
    )
    second = await _standing_fact(
        facts, subject="big walk", predicate="unlocked_by", obj="three towers",
    )
    candidates.facts = [first, second]
    candidates.entities = [_entity("big walk")]
    for predicate, obj in (("gourds_required", "three"), ("unlocked_by", "four towers")):
        await _pending_candidate(
            candidates, subject="big walk", predicate=predicate, obj=obj,
        )
    adaptor = _adjudicator(facts, candidates)
    await adaptor.inputs(key=WS, batch=())

    payloads = await adaptor.accept(key=WS, items=(
        _verdict_item("big walk", "contains", "a shop", "not_covered"),
        _verdict_item(
            "big walk", "gourds_required", "three", "contradicted",
            supersedes=first.id,
        ),
        _verdict_item(
            "big walk", "unlocked_by", "four towers", "contradicted",
            supersedes=second.id,
        ),
    ))
    written = await adaptor.commit(
        key=WS, proposals=tuple(_proposal(p) for p in payloads), now_ms=NOW,
    )

    assert written == 2               # the not_covered verdict wrote no row
    for target, expected_object in ((first, "three"), (second, "four towers")):
        retired = await facts.fact_by_id(workspace=WS, fact_id=target.id)
        assert retired is not None and not retired.is_standing
        successor = await facts.fact_by_id(
            workspace=WS, fact_id=retired.superseded_by or "",
        )
        assert successor is not None
        assert successor.object_literal == expected_object


async def test_a_sink_returning_the_wrong_number_of_ids_supersedes_nothing():
    """Fail closed on the one branch where guessing costs a deletion.

    If `propose`'s ids and the writable subsequence ever disagree, the
    correspondence this module reconstructs is not recoverable — and
    superseding the wrong fact is silent, permanent and unauditable, so no
    count of rows written is worth risking one."""

    class _LosesAnId:
        def __init__(self, inner: InMemoryFactStore) -> None:
            self.inner = inner

        async def propose(
            self, *, key: str, proposals: Sequence[Proposal], now_ms: int,
        ) -> Sequence[str]:
            ids = await self.inner.propose(
                key=key, proposals=proposals, now_ms=now_ms,
            )
            return ids[:-1] if ids else ids

        def __getattr__(self, name: str) -> object:
            return getattr(self.inner, name)

    inner = InMemoryFactStore()
    target = await _standing_fact(
        inner, subject="big walk", predicate="gourds_required", obj="five",
    )
    candidates = InMemoryFactCandidateStore()
    candidates.facts = [target]
    candidates.entities = [_entity("big walk")]
    await _pending_candidate(
        candidates, subject="big walk", predicate="gourds_required", obj="three",
    )
    metrics = Metrics()
    adaptor = FactAdaptor(
        facts=_LosesAnId(inner),  # type: ignore[arg-type]
        candidates=candidates, max_adjudications_per_tick=10, metrics=metrics,
    )
    await adaptor.inputs(key=WS, batch=())

    payloads = await adaptor.accept(key=WS, items=(
        _verdict_item(
            "big walk", "gourds_required", "three", "contradicted",
            supersedes=target.id,
        ),
    ))
    await adaptor.commit(
        key=WS, proposals=tuple(_proposal(p) for p in payloads), now_ms=NOW,
    )

    survivor = await inner.fact_by_id(workspace=WS, fact_id=target.id)
    assert survivor is not None and survivor.is_standing
    assert metrics.get(
        "nora_fact_adjudications_skipped_total", reason="id_mismatch",
    ) == 1


async def test_the_three_states_are_counted_separately():
    """A high `not_covered` rate is the HONEST read — most sources do not
    address most claims — and reading it as a failure is the same mistake
    R-27 exists to prevent one layer down. It can only be read at all if the
    three states are counted apart."""
    metrics = Metrics()
    facts, candidates = InMemoryFactStore(), InMemoryFactCandidateStore()
    adaptor = _adjudicator(facts, candidates, metrics=metrics)

    payloads = await adaptor.accept(key=WS, items=(
        _verdict_item("a", "p", "o1", "supported"),
        _verdict_item("b", "p", "o2", "not_covered"),
    ))
    await adaptor.commit(
        key=WS, proposals=tuple(_proposal(p) for p in payloads), now_ms=NOW,
    )

    assert metrics.get("nora_fact_verdicts_total", status="supported") == 1
    assert metrics.get("nora_fact_verdicts_total", status="not_covered") == 1
    assert metrics.get("nora_facts_proposed_total") == 1


async def test_the_pending_read_is_capped_by_the_per_tick_budget():
    """§5's cap, which is the tick's budget: one research loop per surviving
    candidate is the expensive step, so the number that reach the prompt IS
    the cost decision."""
    facts, candidates = InMemoryFactStore(), InMemoryFactCandidateStore()
    candidates.entities = [_entity("big walk")]
    for n in range(5):
        await _pending_candidate(
            candidates, subject="big walk", predicate=f"p{n}", obj=f"o{n}",
            message_id=n,
        )
    adaptor = FactAdaptor(
        facts=facts, candidates=candidates, max_adjudications_per_tick=2,
    )

    rendered = await adaptor.inputs(key=WS, batch=())

    assert sum(line.startswith("- [") for line in rendered.splitlines()) == 2


async def test_an_unavailable_candidate_read_propagates_rather_than_looking_empty():
    """`FactCandidateReader.pending` raises by contract and `inputs` does not
    catch it: an empty candidate list would say «nothing to adjudicate» when
    the truth is «I could not check» — `unavailable` reported as
    `nothing_found`, the one substitution R-27 exists to forbid. The runtime's
    own try/except is what turns this into a counted `unavailable`."""

    class _UnreachableCandidates(InMemoryFactCandidateStore):
        async def pending(self, *, workspace: str, limit: int):  # type: ignore[override]
            raise ConnectionError("the candidate table could not be read")

    adaptor = _adjudicator(InMemoryFactStore(), _UnreachableCandidates())

    with pytest.raises(ConnectionError):
        await adaptor.inputs(key=WS, batch=())


async def test_the_adjudicator_renders_nothing_when_no_candidate_is_pending():
    """BLANK, not "(none)" — and the difference is one model call every 300s
    per workspace, forever.

    `DreamAdaptor` has no `should_fire` verb: the runtime's fire condition is
    "did `inputs` render anything to reason about". An adaptor that renders a
    polite empty form defeats it, and the runtime happily invokes an agent on a
    prompt that says there is nothing to do. Observed live 2026-08-21 —
    `invoke_agent dreamer:fact-adjudication` on every tick against an empty
    candidate table, which is exactly what the fire condition was added to
    prevent and did not."""
    adaptor = FactAdaptor(
        facts=InMemoryFactStore(), candidates=InMemoryFactCandidateStore(),
        max_adjudications_per_tick=10,
    )

    assert await adaptor.inputs(key=WS, batch=()) == ""


async def test_the_extractor_renders_nothing_for_an_empty_batch():
    """Same rule. A watermarked adaptor reaches an empty batch on the 24h age
    condition, which fires at `lag == 0` on purpose — so without this a dormant
    room bought one model call a day to be told there were no messages."""
    adaptor = FactCandidateAdaptor(
        candidates=InMemoryFactCandidateStore(), workspace_for=lambda _: WS,
    )

    assert await adaptor.inputs(key="g1", batch=()) == ""
