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

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace

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
    subject: str, predicate: str, obj: str, message_ids: tuple[int, ...] = (1,),
) -> str:
    ids = await candidates.propose(
        key=WS,
        proposals=(_proposal(CandidateClaim(
            subject=subject, predicate=predicate, object=obj,
            message_ids=message_ids,
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
    every `message_ids` entry against the whole batch, so a row dropped from the render
    would make the set the model was shown smaller than the set `accept`
    accepts from it.

    Updated for design/dreamer-agent-loop/32 §3.3 point 1: the line is now
    ATTRIBUTED (`<id> [<ts>] <name> (<room>): <body>`) rather than the bare
    `id: body` this test used to assert, and a bodiless row renders its media
    placeholder (none here, so an empty body)."""
    adaptor = FactCandidateAdaptor(
        candidates=InMemoryFactCandidateStore(), workspace_for=lambda _: WS,
    )

    rendered = await adaptor.inputs(
        key=SCOPE, batch=(_row(41, "big walk 今晚?"), _row(42, None)),
    )

    assert "41 [1970-01-01 00:00:01 UTC] A (ws-main): big walk 今晚?" in rendered
    assert "42 [1970-01-01 00:00:01 UTC] A (ws-main): " in rendered


async def test_inputs_prefers_the_operator_authored_person_name():
    """DEC-80's ordering, applied to the dream transcript: `person_name` is
    operator-authored, `sender_name` is self-set and spoofable, so the render
    names the first and only falls back to the second."""
    adaptor = FactCandidateAdaptor(
        candidates=InMemoryFactCandidateStore(), workspace_for=lambda _: WS,
    )
    row = replace(_row(41, "a claim"), sender_name="self-set", person_name="Miles")

    rendered = await adaptor.inputs(key=SCOPE, batch=(row,))

    assert " Miles (ws-main): a claim" in rendered
    assert "self-set" not in rendered


async def test_inputs_renders_media_and_reply_markers():
    """32 §3.3 point 1: a bodiless row renders its media TYPE from
    `media_json` (a filename is never rendered — `nora.domain.media`'s own
    rule), and a reply row names the quoted target's timestamp."""
    adaptor = FactCandidateAdaptor(
        candidates=InMemoryFactCandidateStore(), workspace_for=lambda _: WS,
    )
    image = replace(
        _row(41, None),
        media_json='[{"sha256": "ab", "media_type": "image/jpeg", "size": 1,'
                   ' "filename": "secret-name.jpg"}]',
    )
    reply = replace(_row(42, "sure"), quoted_ts=2_000)

    rendered = await adaptor.inputs(key=SCOPE, batch=(image, reply))

    assert "41 [1970-01-01 00:00:01 UTC] A (ws-main): [image]" in rendered
    assert "secret-name" not in rendered
    assert "(reply to 1970-01-01 00:00:02 UTC)" in rendered


async def test_inputs_caps_the_render_but_keeps_the_whole_id_set():
    """32 §3.3 point 2: past MAX_RENDERED_MESSAGES the prompt switches to a
    summary line naming the omitted id range, while `accept` still validates
    against the FULL batch — the cap bounds text, not provenance."""
    adaptor = FactCandidateAdaptor(
        candidates=InMemoryFactCandidateStore(), workspace_for=lambda _: WS,
    )
    batch = tuple(_row(i, f"claim {i}") for i in range(1, 503))

    rendered = await adaptor.inputs(key=SCOPE, batch=batch)

    assert "claim 500" in rendered
    assert "claim 501" not in rendered
    assert "... and 2 more messages (ids 501..502) not shown" in rendered
    parsed = await adaptor.accept(key=SCOPE, items=(
        {"subject": "s", "predicate": "p", "object": "o",
         "message_ids": [502]},
    ))
    assert [c.message_ids for c in parsed] == [(502,)]  # type: ignore[attr-defined]


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
         "message_ids": [41]},
        {"subject": "big walk", "predicate": "costs", "object": "ten",
         "message_ids": [999]},
    ))

    assert [c.message_ids for c in parsed] == [(41,)]  # type: ignore[attr-defined]


async def test_a_claim_spanning_two_messages_keeps_both():
    """D31 makes an episode a conversational CHUNK, so a claim can be asserted
    across turns — somebody starting a sentence and finishing it. The singular
    `message_id` could not say so: the extractor would have had to pick one and
    the rest of the provenance would be dropped at the one point where it is
    free (21 §7.2's join runs off exactly these ids)."""
    store = InMemoryFactCandidateStore()
    store.entities = [_entity("big walk")]
    adaptor = FactCandidateAdaptor(candidates=store, workspace_for=lambda _: WS)
    await adaptor.inputs(
        key=SCOPE, batch=(_row(41, "big walk is"), _row(42, "unlocked by four")),
    )

    parsed = await adaptor.accept(key=SCOPE, items=(
        {"subject": "big walk", "predicate": "unlocked_by",
         "object": "four towers", "message_ids": [41, 42]},
    ))
    await adaptor.commit(
        key=SCOPE, proposals=tuple(_proposal(p) for p in parsed), now_ms=NOW,
    )

    rows = await store.pending(workspace=WS, limit=10)
    assert len(rows) == 1
    assert sorted(rows[0].observed_in) == ["41", "42"]


async def test_a_claim_with_one_invented_id_is_dropped_whole():
    """Not trimmed to its good ids. A claim half-attributed to messages that
    exist and half to messages that do not is fabricated provenance wearing a
    partial answer's clothes, and a later reader cannot tell which half it is
    holding. The sibling claim in the same batch survives, which is what makes
    this a drop rather than a batch failure."""
    adaptor = FactCandidateAdaptor(
        candidates=InMemoryFactCandidateStore(), workspace_for=lambda _: WS,
    )
    await adaptor.inputs(key=SCOPE, batch=(_row(41, "a"), _row(42, "b")))

    parsed = await adaptor.accept(key=SCOPE, items=(
        {"subject": "big walk", "predicate": "contains", "object": "a tower",
         "message_ids": [41, 999]},
        {"subject": "big walk", "predicate": "costs", "object": "ten",
         "message_ids": [42]},
    ))

    assert [c.message_ids for c in parsed] == [(42,)]  # type: ignore[attr-defined]


async def test_an_empty_message_ids_list_is_not_a_claim():
    """A claim with no message behind it has no provenance, and `observed_in`
    would be an empty array that reads as «seen nowhere» exactly like a row
    nobody wrote. Refused in the domain type, dropped here."""
    adaptor = FactCandidateAdaptor(
        candidates=InMemoryFactCandidateStore(), workspace_for=lambda _: WS,
    )
    await adaptor.inputs(key=SCOPE, batch=(_row(41, "a"),))

    parsed = await adaptor.accept(key=SCOPE, items=(
        {"subject": "big walk", "predicate": "contains", "object": "a tower",
         "message_ids": []},
    ))

    assert parsed == ()


async def test_a_boolean_message_id_is_refused_not_coerced():
    """`bool` is an `int` in Python and `int(True)` is 1 — a real message id in
    every conversation — so a model returning `[true]` would silently attribute
    a claim to message 1. The batch check would not catch it: message 1 is
    often IN the batch."""
    adaptor = FactCandidateAdaptor(
        candidates=InMemoryFactCandidateStore(), workspace_for=lambda _: WS,
    )
    await adaptor.inputs(key=SCOPE, batch=(_row(1, "a"),))

    parsed = await adaptor.accept(key=SCOPE, items=(
        {"subject": "big walk", "predicate": "contains", "object": "a tower",
         "message_ids": [True]},
    ))

    assert parsed == ()


async def test_the_operating_term_cap_is_the_injected_one_not_a_constant():
    """30 §5: every writer validates against the CURRENT operating value. The
    caps were module constants, so lowering `[facts] max_term_chars` changed
    nothing about what the extractor accepted — the config refused itself above
    its ceiling and enforced nothing below it.

    Driven at a value that is legal under the DEFAULT (200) and refused under
    the injected one, so a parser still reading the old constant is red."""
    store = InMemoryFactCandidateStore()
    adaptor = FactCandidateAdaptor(
        candidates=store, workspace_for=lambda _: WS, max_term_chars=20,
    )
    await adaptor.inputs(key=SCOPE, batch=(_row(41, "a"),))

    parsed = await adaptor.accept(key=SCOPE, items=(
        {"subject": "x" * 50, "predicate": "has", "object": "v",
         "message_ids": [41]},
        {"subject": "short", "predicate": "has", "object": "v",
         "message_ids": [41]},
    ))

    # The positive control is the sibling: a cap that refused everything would
    # satisfy the first assertion on its own.
    assert [c.subject for c in parsed] == ["short"]  # type: ignore[attr-defined]


async def test_the_operating_predicate_cap_is_injected_too():
    """Asserted separately from the term cap: one parameter threaded and the
    other left as a constant passes a test that only drives one of them."""
    store = InMemoryFactCandidateStore()
    adaptor = FactCandidateAdaptor(
        candidates=store, workspace_for=lambda _: WS, max_predicate_chars=5,
    )
    await adaptor.inputs(key=SCOPE, batch=(_row(41, "a"),))

    parsed = await adaptor.accept(key=SCOPE, items=(
        {"subject": "s", "predicate": "p" * 20, "object": "v",
         "message_ids": [41]},
        {"subject": "s", "predicate": "has", "object": "v",
         "message_ids": [41]},
    ))

    assert [c.predicate for c in parsed] == ["has"]  # type: ignore[attr-defined]


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
         "message_ids": [41]},
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
         "message_ids": [41]},
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
         "message_ids": [41]},
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
         "message_ids": [41]},
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


async def test_inputs_shows_a_handle_for_every_standing_fact_and_never_its_id():
    """`supersedes` names a HANDLE and the model may only name one it was
    shown, so a fact rendered without a handle is a fact it cannot correct —
    and a fact rendered with its UUID is one whose correction is DESTROYED
    before this adaptor sees it, because `dream_screen` drops any envelope item
    carrying an identifier.

    Both halves asserted: the handle is present, the id is absent."""
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
    assert standing.id not in rendered, (
        "the standing fact's UUID reached the prompt; a contradicted verdict "
        "naming it is screened before accept"
    )
    assert _handle_for(rendered, standing) == "1"
    assert "the page that established five" in rendered


def _handle_for(prompt: str, fact) -> str:
    """The per-tick ordinal `inputs` rendered this fact under.

    Read out of the PROMPT rather than out of `_shown`, because the prompt is
    what the model actually sees and the handle is only meaningful as the thing
    it reads. A test that reached into the adaptor's private map would still
    pass if the renderer stopped emitting handles altogether.

    The fact's UUID must NOT be in the prompt — an id there is one the model can
    echo into an envelope item, and `dream_screen` destroys any item carrying
    one. Asserted here so every caller gets the check for free.
    """
    assert fact.id not in prompt, (
        "the standing fact's UUID reached the prompt; a verdict naming it "
        "would be screened before accept ever saw it"
    )
    obj = fact.object_literal if fact.object_literal is not None else (
        fact.object_entity_id or ""
    )
    pattern = (
        rf"- \[(\d+)\] {re.escape(fact.subject_id)} \| "
        rf"{re.escape(fact.predicate)} \| {re.escape(obj)}"
    )
    match = re.search(pattern, prompt)
    assert match is not None, f"fact not rendered in the prompt: {pattern}"
    return match.group(1)


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
    prompt = await adaptor.inputs(key=WS, batch=())

    payloads = await adaptor.accept(key=WS, items=(
        _verdict_item(
            "big walk", "gourds_required", "three", "contradicted",
            supersedes=_handle_for(prompt, target),
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


async def test_a_supersedes_naming_a_handle_this_run_never_rendered_is_dropped():
    """The model can invent a handle. A supersession of a row nobody showed it
    is not a verdict, it is a guess with a delete attached — and it is checked
    against what THIS RUN rendered, not against what exists, because a real
    fact about another subject is still one nobody put in front of it.

    `unshown` exists in the store and is deliberately NOT rendered, so `"99"`
    is a handle no tick produced. Asserting the fact survives is what makes
    this a test of the refusal rather than of the parse."""
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
            supersedes="99",
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
    prompt = await adaptor.inputs(key=WS, batch=())

    payloads = await adaptor.accept(key=WS, items=(
        _verdict_item("big walk", "contains", "a shop", "not_covered"),
        _verdict_item(
            "big walk", "gourds_required", "three", "contradicted",
            supersedes=_handle_for(prompt, first),
        ),
        _verdict_item(
            "big walk", "unlocked_by", "four towers", "contradicted",
            supersedes=_handle_for(prompt, second),
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
    prompt = await adaptor.inputs(key=WS, batch=())

    payloads = await adaptor.accept(key=WS, items=(
        _verdict_item(
            "big walk", "gourds_required", "three", "contradicted",
            supersedes=_handle_for(prompt, target),
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
            message_ids=(n,),
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


async def test_a_contradicted_verdict_survives_the_dreamers_egress_screen():
    """**The test whose absence is why this shipped**, and the third adaptor to
    need it.

    `DreamerRuntime` runs `nora.agent.dream_screen.screen` over the model's raw
    envelope items BEFORE `accept`, and the screen drops the WHOLE item on any
    reachable identifier. `supersedes` carried a `fact.id` — a UUID — so every
    `contradicted` verdict was screened, always.

    **And it failed SELECTIVELY, which is why it survived three reviews.** Only
    `contradicted` carries `supersedes`; `supported` and `not_covered` flowed
    untouched, so the tier wrote facts, moved counters and looked healthy while
    never once recording a contradiction — never retiring a wrong standing
    fact, and re-adjudicating the same candidate every tick forever because a
    screened verdict never becomes a proposal and so never retires its
    candidate.

    Every other test in this file calls `accept` directly and therefore cannot
    see it. This one drives both verdicts through the real screen first, in the
    order the runtime does, then through `accept` and `commit`, and READS THE
    ROW BACK — the wrong fact is retired and its successor stands. Asserting
    that `accept` returned something would only prove the item was not dropped;
    the property is that a correction lands.

    **It takes the bracketed token out of the prompt verbatim rather than
    through `_handle_for`.** `_handle_for` asserts the id is absent, so a test
    built on it fails on THAT assertion when the renderer regresses — for the
    right outcome and the wrong reason. Reading whatever token the prompt
    actually carries makes this test fail at `rejected == ()`, which is the
    screen refusing the item, which is the property. A `supported`-only
    assertion would have passed against the bug entirely."""
    from nora.agent.dream_screen import screen

    facts, candidates = InMemoryFactStore(), InMemoryFactCandidateStore()
    target = await _standing_fact(
        facts, subject="big walk", predicate="gourds_required", obj="five",
    )
    candidates.facts = [target]
    candidates.entities = [_entity("big walk")]
    await _pending_candidate(
        candidates, subject="big walk", predicate="gourds_required", obj="three",
    )
    await _pending_candidate(
        candidates, subject="big walk", predicate="unlocked_by", obj="four towers",
    )
    adaptor = _adjudicator(facts, candidates)
    prompt = await adaptor.inputs(key=WS, batch=())

    # Whatever token the prompt renders for this fact — a handle now, a UUID if
    # the renderer regresses. Deliberately NOT `_handle_for`: see the docstring.
    # Anchored on the OBJECT as well, because a candidate and the standing
    # fact it contradicts share a subject and a predicate by definition — they
    # differ only in the object, and candidates render first. Without it this
    # matched `- [candidate-1] big walk | gourds_required | three` and tested
    # the wrong line.
    token = re.search(
        rf"- \[([^\]]+)\] {re.escape(target.subject_id)} \| "
        rf"{re.escape(target.predicate)} \| {re.escape(target.object_literal or '')}",
        prompt,
    )
    assert token is not None, "the standing fact was not rendered at all"

    items = [
        _verdict_item(
            "big walk", "gourds_required", "three", "contradicted",
            supersedes=token.group(1),
        ),
        _verdict_item("big walk", "unlocked_by", "four towers", "supported"),
    ]
    kept, rejected = screen(
        adaptor="fact-adjudication", items=items, metrics=Metrics(),
    )

    assert rejected == (), (
        "the screen dropped a verdict before accept — the fact tier cannot "
        "record a correction"
    )
    payloads = await adaptor.accept(key=WS, items=kept)
    written = await adaptor.commit(
        key=WS, proposals=tuple(_proposal(p) for p in payloads), now_ms=NOW,
    )

    # READ THE ROWS BACK. That the code path ran is not the property; that the
    # wrong fact is retired and the correction stands is.
    assert written == 2
    retired = await facts.fact_by_id(workspace=WS, fact_id=target.id)
    assert retired is not None and not retired.is_standing
    successor = await facts.fact_by_id(
        workspace=WS, fact_id=retired.superseded_by or "",
    )
    assert successor is not None and successor.object_literal == "three"


# ---------------------------------------------------------------------------
# pass 3 rides the tick
# ---------------------------------------------------------------------------


class _RecordingPromoter:
    """Records the calls `FactAdaptor` makes, and can be told to fail. Not a
    no-op double: a promoter that never raised could not show that a failure is
    caught rather than propagated, which is the property that decides whether a
    written fact reports as `unavailable, written=0`."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, int]] = []
        self._fail = fail

    async def promote(self, *, workspace: str, now_ms: int) -> object:
        self.calls.append((workspace, now_ms))
        if self._fail:
            raise RuntimeError("promotion exploded")
        return None


async def test_promotion_runs_after_a_tick_that_wrote_a_fact():
    """25 §4: promotion runs after any write to `fact`, because degree changes
    only when a fact lands."""
    facts, candidates = InMemoryFactStore(), InMemoryFactCandidateStore()
    promoter = _RecordingPromoter()
    candidates.entities = [_entity("big walk")]
    await _pending_candidate(
        candidates, subject="big walk", predicate="made_by", obj="hh",
    )
    adaptor = FactAdaptor(
        facts=facts, candidates=candidates, max_adjudications_per_tick=5,
        entities=promoter,
    )
    await adaptor.inputs(key=WS, batch=())
    verdicts = await adaptor.accept(key=WS, items=(
        {"subject": "big walk", "predicate": "made_by", "object": "hh",
         "status": "supported", "addresses_claim": True,
         "source_url": "https://x.test/a", "source_excerpt": "x"},
    ))

    written = await adaptor.commit(
        key=WS, proposals=tuple(_proposal(v) for v in verdicts), now_ms=NOW,
    )

    assert written == 1
    assert promoter.calls == [(WS, NOW)]


async def test_promotion_does_not_run_when_the_tick_wrote_nothing():
    """A tick that wrote no fact changed no degree. Promotion is an aggregate
    over the workspace — cheap, not free — and the Dreamer fires on its own
    clock over rooms that are mostly quiet."""
    facts, candidates = InMemoryFactStore(), InMemoryFactCandidateStore()
    promoter = _RecordingPromoter()
    adaptor = FactAdaptor(
        facts=facts, candidates=candidates, max_adjudications_per_tick=5,
        entities=promoter,
    )
    await adaptor.inputs(key=WS, batch=())

    await adaptor.commit(key=WS, proposals=(), now_ms=NOW)

    assert promoter.calls == []
    # The positive control, in THIS test: `== []` is also true of a `commit`
    # that never reaches `_promote` at all, so the same adaptor is driven once
    # more with a batch that DOES write, and the call appears.
    candidates.entities = [_entity("big walk")]
    await _pending_candidate(
        candidates, subject="big walk", predicate="made_by", obj="hh",
    )
    await adaptor.inputs(key=WS, batch=())
    verdicts = await adaptor.accept(key=WS, items=(
        {"subject": "big walk", "predicate": "made_by", "object": "hh",
         "status": "supported", "addresses_claim": True,
         "source_url": "https://x.test/a", "source_excerpt": "x"},
    ))
    await adaptor.commit(
        key=WS, proposals=tuple(_proposal(v) for v in verdicts), now_ms=NOW,
    )
    assert promoter.calls == [(WS, NOW)]


async def test_a_promotion_failure_does_not_fail_the_tick_that_wrote():
    """`_supersede_fact`'s reason, one verb later: the facts are ALREADY
    written by the time promotion runs, so letting the exception reach the
    runtime would report the whole run `unavailable` with `written=0` while
    rows sit in the table. Counted instead, so a promotion that never happened
    is visible rather than merely survived."""
    facts, candidates = InMemoryFactStore(), InMemoryFactCandidateStore()
    metrics = Metrics()
    promoter = _RecordingPromoter(fail=True)
    candidates.entities = [_entity("big walk")]
    await _pending_candidate(
        candidates, subject="big walk", predicate="made_by", obj="hh",
    )
    adaptor = FactAdaptor(
        facts=facts, candidates=candidates, max_adjudications_per_tick=5,
        entities=promoter, metrics=metrics,
    )
    await adaptor.inputs(key=WS, batch=())
    verdicts = await adaptor.accept(key=WS, items=(
        {"subject": "big walk", "predicate": "made_by", "object": "hh",
         "status": "supported", "addresses_claim": True,
         "source_url": "https://x.test/a", "source_excerpt": "x"},
    ))

    written = await adaptor.commit(
        key=WS, proposals=tuple(_proposal(v) for v in verdicts), now_ms=NOW,
    )

    assert written == 1
    assert metrics.get(
        "nora_fact_adjudications_skipped_total", reason="promotion_failed") == 1


class _RecordingSweeper:
    """Pass 4, seen from the tick. Records ORDER against the promoter, because
    `25` §5's "3 before 4, always" is a property of the pair rather than of
    either one."""

    def __init__(self, log: list[str], *, status: str = "ok") -> None:
        self._log = log
        self._status = status
        self.calls: list[tuple[str, int]] = []

    async def sweep(self, *, workspace: str, now_ms: int):
        self._log.append("embed")
        self.calls.append((workspace, now_ms))
        from nora.language.fact_embedder import Sweep
        return Sweep(embedded=1, stale=1, status=self._status)


class _OrderedPromoter:
    def __init__(self, log: list[str]) -> None:
        self._log = log
        self.calls: list[tuple[str, int]] = []

    async def promote(self, *, workspace: str, now_ms: int) -> object:
        self._log.append("promote")
        self.calls.append((workspace, now_ms))
        return None


async def _drive_one_write(adaptor, candidates):
    candidates.entities = [_entity("big walk")]
    await _pending_candidate(
        candidates, subject="big walk", predicate="made_by", obj="hh",
    )
    await adaptor.inputs(key=WS, batch=())
    verdicts = await adaptor.accept(key=WS, items=(
        {"subject": "big walk", "predicate": "made_by", "object": "hh",
         "status": "supported", "addresses_claim": True,
         "source_url": "https://x.test/a", "source_excerpt": "x"},
    ))
    return await adaptor.commit(
        key=WS, proposals=tuple(_proposal(v) for v in verdicts), now_ms=NOW,
    )


async def test_the_embedding_sweep_runs_after_promotion_and_not_before():
    """**3 before 4, always** (`25` §5). The section's original argument —
    promotion rewrites the triple, so embedding first wastes the call — stopped
    being true at migration 0039, which made promotion additive so the words
    the room used survive resolution. The ordering still holds for the reason
    that survives: promote-then-embed spends one pass, embed-then-promote
    leaves rows to re-embed next tick.

    Asserted as an ORDER rather than as two independent calls, because that is
    the property, and two `assert x.calls` would pass with the pair reversed.
    """
    facts, candidates = InMemoryFactStore(), InMemoryFactCandidateStore()
    order: list[str] = []
    promoter, sweeper = _OrderedPromoter(order), _RecordingSweeper(order)
    adaptor = FactAdaptor(
        facts=facts, candidates=candidates, max_adjudications_per_tick=5,
        entities=promoter, embedder=sweeper,
    )

    assert await _drive_one_write(adaptor, candidates) == 1

    assert order == ["promote", "embed"]
    assert sweeper.calls == [(WS, NOW)]


async def test_the_embedding_sweep_runs_even_on_a_tick_that_wrote_nothing():
    """**It must NOT match `_promote`'s trigger, and the first version did.**

    Promotion is driven by degree, which changes only when a fact lands.
    Staleness is `fingerprint(model, text)` — and the MODEL half moves with no
    write at all. Gated on `wrote`, a workspace that had said everything it
    was going to say would never re-embed after a model swap, and R-80 makes
    un-re-embedded rows INVISIBLE to retrieval: the stated degradation is
    «`nothing_found` shrinking for a while» and the while would be unbounded.

    Nothing measured it either, because a sweep that never fires emits no
    counter — the absence is invisible in exactly the way the presence would
    not be.

    A quiet sweep is affordable: the fingerprint predicate answers
    `nothing_found` after one indexed read and no model call, which is what
    makes running it every tick the cheap option rather than the thorough one.
    """
    facts, candidates = InMemoryFactStore(), InMemoryFactCandidateStore()
    order: list[str] = []
    sweeper = _RecordingSweeper(order)
    promoter = _OrderedPromoter(order)
    adaptor = FactAdaptor(
        facts=facts, candidates=candidates, max_adjudications_per_tick=5,
        entities=promoter, embedder=sweeper,
    )
    await adaptor.inputs(key=WS, batch=())

    await adaptor.commit(key=WS, proposals=(), now_ms=NOW)

    assert sweeper.calls == [(WS, NOW)]
    # The other half of the same decision, in the same test so the pair cannot
    # drift: PROMOTION still does not run on an empty tick, because degree
    # genuinely did not change. Two passes, two triggers, one hook.
    assert promoter.calls == []


async def test_an_incomplete_sweep_does_not_fail_the_tick_that_wrote():
    """The facts are already written by the time pass 4 runs. An
    `unavailable` sweep must leave `commit`'s count alone — reporting the run
    as failed with rows in the table is the inversion `_promote` already
    guards against, and it is worse here because the rows ARE correct, merely
    unembedded."""
    facts, candidates = InMemoryFactStore(), InMemoryFactCandidateStore()
    order: list[str] = []
    adaptor = FactAdaptor(
        facts=facts, candidates=candidates, max_adjudications_per_tick=5,
        entities=_OrderedPromoter(order),
        embedder=_RecordingSweeper(order, status="unavailable"),
    )

    assert await _drive_one_write(adaptor, candidates) == 1
