"""The Cognition session's topic-account verbs — `topic_create`,
`topic_revise`, `topic_set_status` — over the shared entry store: a subject
earns one account, cited to rows this session was shown (the
`graph_fetched` custody `memory_propose` is held to), revised against the
head it read, and moved active ↔ dormant → retired with nothing after it.
Unwired custody answers `unavailable`, never a write. Over in-memory stores
with a scripted agent (`tests/unit/cognition_rig.py`); names are invented
and no message is quoted."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from nora.app.cognition_grounding import build_grounder
from nora.domain.cognition_ground import Grounding
from nora.domain.cognition_session import AgendaItem
from nora.domain.entries import EntryContent
from nora.domain.stimuli import UnitView
from nora.language.comprehend_render import render_session_system
from nora.language.topic_render import render_topics
from nora.testing.cognition_grounding import StaticGrounder
from nora.testing.entries import FakeEntryStore
from tests.unit.cognition_rig import B as RIG_B
from tests.unit.cognition_rig import Stores, session
from tests.unit.test_cognition_grounding import (
    _PERSONS,
    _lineage,
)
from tests.unit.test_cognition_grounding import A as ROOM_A
from tests.unit.test_cognition_grounding import B as ROOM_B
from tests.unit.test_cognition_prompt_surface import rendered_session
from tests.unit.test_cognition_session_runner import (
    NOW,
    FakeCognitionAgentRunner,
    _pending,
    _runner,
    wake_memory,
)

DOCTRINE = "a" * 64
#: The provenance a revision must record; all four are wired or the write is off.
ARGS: dict[str, Any] = {
    "entries": FakeEntryStore(), "memory": None,
    "model_id": "generated-model", "doctrine_sha256": DOCTRINE,
}
CITE = [["message", "101"]]
BODY = "the generated subject this unit keeps returning to"
TOPIC_VERBS = {"topic_create", "topic_revise", "topic_set_status"}


def _kwargs(entries: FakeEntryStore | None = None, **overrides: Any) -> dict[str, Any]:
    return {
        **ARGS, "entries": entries if entries is not None else FakeEntryStore(),
        "memory": wake_memory(), **overrides,
    }


async def _one(call: str, entries: FakeEntryStore | None = None,
               stores: Stores | None = None, **arguments: Any) -> tuple[Any, dict[str, Any]]:
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by[call](**arguments))

    stores, _ = await session(behave, stores=stores, runner_kwargs=_kwargs(entries))
    return entries, answers[0]


async def test_topic_create_writes_an_active_account_with_session_provenance():
    entries = FakeEntryStore()
    _, answer = await _one(
        "topic_create", entries, subject="generated-racing", body=BODY,
        confidence="medium", citations=CITE)
    assert answer["status"] == "ok" and answer["entry_id"] and answer["revision_id"]
    (entry,) = await entries.held(unit="u")
    content = entry.head.content
    assert (entry.id, entry.head.id) == (answer["entry_id"], answer["revision_id"])
    assert content.about == (("topic", "generated-racing"),)
    assert content.state == "active" and content.citations == (("message", "101"),)
    assert content.writer == "cognition:u" and content.model == "generated-model"
    assert content.instruction_hash == DOCTRINE and content.session_id


async def test_topic_create_refuses_what_the_contract_and_custody_do():
    _, unshown = await _one(
        "topic_create", subject="x", body=BODY, confidence="low",
        citations=[["message", "999"]])
    _, malformed = await _one(
        "topic_create", subject="x", body=BODY, confidence="low",
        citations=["message:101"])
    _, uncited = await _one(
        "topic_create", subject="x", body=BODY, confidence="low", citations=[])
    _, blank_subject = await _one(
        "topic_create", subject=" ", body=BODY, confidence="low", citations=CITE)
    _, blank_body = await _one(
        "topic_create", subject="x", body=" ", confidence="low", citations=CITE)
    _, bad_confidence = await _one(
        "topic_create", subject="x", body=BODY, confidence="sure", citations=CITE)
    assert (unshown["reason"], malformed["reason"], uncited["reason"],
            blank_subject["reason"], blank_body["reason"], bad_confidence["reason"]) == (
        "unseen_citation", "bad_citation", "uncited",
        "bad_subject", "empty_body", "confidence")


async def test_a_held_subject_is_revised_never_started_a_second_time():
    entries = FakeEntryStore()
    await _one("topic_create", entries, subject="Karting", body=BODY,
               confidence="medium", citations=CITE)
    _, twin = await _one(
        "topic_create", entries, subject="karting", body=BODY,
        confidence="medium", citations=CITE)
    assert twin["status"] == "refused" and twin["reason"] == "duplicate"
    assert len(await entries.held(unit="u")) == 1


async def test_topic_verbs_are_unavailable_without_the_wiring_they_need():
    async def call(call_name: str, runner_kwargs: dict[str, Any]) -> dict[str, Any]:
        answers: list[Any] = []

        async def behave(by: dict[str, Any], tools: list[Any]) -> None:
            answers.append(await by[call_name](
                subject="x", body=BODY, confidence="low", citations=CITE))

        await session(behave, runner_kwargs=runner_kwargs)
        return answers[0]

    no_store = await call(
        "topic_create", {**ARGS, "entries": None, "memory": wake_memory()})
    no_custody = await call("topic_create", {**ARGS, "memory": None})
    no_hash = await call(
        "topic_create", {**ARGS, "memory": wake_memory(), "doctrine_sha256": None})
    for answer in (no_store, no_custody, no_hash):
        assert answer["status"] == "unavailable" and answer["reason"] == "unwired"


async def test_topic_revise_replaces_the_body_and_carries_its_citations():
    entries = FakeEntryStore()
    await _one("topic_create", entries, subject="racing", body=BODY,
               confidence="low", citations=CITE)
    (entry,) = await entries.held(unit="u")
    _, revised = await _one(
        "topic_revise", entries, entry_id=entry.id,
        base_revision_id=entry.head.id, body="the longer account",
        confidence="high")
    assert revised["status"] == "ok" and revised["revision_id"] != entry.head.id
    head = (await entries.get(unit="u", entry_id=entry.id)).head
    assert head.seq == 2 and head.base_revision_id == entry.head.id
    assert head.content.body == "the longer account"
    assert head.content.citations == (("message", "101"),)
    # A new citation is held to this session's shown rows the same way.
    _, unseen = await _one(
        "topic_revise", entries, entry_id=entry.id, base_revision_id=head.id,
        body="x", confidence="high", citations=[["message", "999"]])
    assert unseen["reason"] == "unseen_citation"


async def test_topic_revise_refuses_a_missing_or_stale_account():
    entries = FakeEntryStore()
    _, missing = await _one(
        "topic_revise", entries, entry_id="no-such", base_revision_id="r",
        body=BODY, confidence="low")
    await _one("topic_create", entries, subject="racing", body=BODY,
               confidence="low", citations=CITE)
    (entry,) = await entries.held(unit="u")
    _, stale = await _one(
        "topic_revise", entries, entry_id=entry.id, base_revision_id="r-not-head",
        body="x", confidence="low")
    assert missing["reason"] == "not_found" and stale["reason"] == "stale_base"
    assert len(await entries.revisions(unit="u", entry_id=entry.id)) == 1


async def test_the_lifecycle_ends_at_retired_and_nothing_writes_after_it():
    entries = FakeEntryStore()
    await _one("topic_create", entries, subject="racing", body=BODY,
               confidence="medium", citations=CITE)
    (entry,) = await entries.held(unit="u")

    async def set_status(status: str, base: str) -> dict[str, Any]:
        return (await _one(
            "topic_set_status", entries, entry_id=entry.id,
            base_revision_id=base, status=status))[1]

    dormant = await set_status("dormant", entry.head.id)
    entry = (await entries.get(unit="u", entry_id=entry.id))
    assert dormant["status"] == "ok" and entry.head.content.state == "dormant"
    assert entry.head.content.body == BODY  # a state move carries the account

    reactivated = await set_status("active", entry.head.id)
    entry = (await entries.get(unit="u", entry_id=entry.id))
    assert reactivated["status"] == "ok" and entry.head.content.state == "active"

    retired = await set_status("retired", entry.head.id)
    entry = (await entries.get(unit="u", entry_id=entry.id))
    assert retired["status"] == "ok" and entry.head.content.state == "retired"
    assert await entries.held(unit="u") == ()  # retired leaves the held list

    for answer in (
        await set_status("active", entry.head.id),
        (await _one("topic_revise", entries, entry_id=entry.id,
                    base_revision_id=entry.head.id, body="x", confidence="low"))[1],
    ):
        assert answer["status"] == "refused" and answer["reason"] == "lifecycle"
    heads = await entries.revisions(unit="u", entry_id=entry.id)
    assert len(heads) == 4 and heads[0].content.state == "retired"
    # The SUBJECT may be accounted again — a fresh entry, never a write on
    # the retired one (retired is not held, so it is no duplicate).
    _, again = await _one(
        "topic_create", entries, subject="racing", body="a new account",
        confidence="low", citations=CITE)
    assert again["status"] == "ok" and again["entry_id"] != entry.id
    assert len(await entries.revisions(unit="u", entry_id=entry.id)) == 4


async def test_held_topics_reach_the_grounding_and_the_block_with_citable_handles():
    entries = FakeEntryStore()
    entry = await entries.create(
        kind="topic", unit="u",
        content=EntryContent(
            (("topic", "generated-racing"),), BODY, (("message", "101"),),
            "medium", "active", "cognition", "s", "m", "a" * 64))
    retired = await entries.create(
        kind="topic", unit="u",
        content=EntryContent(
            (("topic", "generated-retired"),), BODY, (("message", "101"),),
            "low", "active", "cognition", "s", "m", "a" * 64))
    await entries.append(
        unit="u", entry_id=retired.id, base_revision_id=retired.head.id,
        content=replace(retired.head.content, state="retired"))

    async def nothing(unit: str) -> tuple:
        return ()

    async def no_events(unit: str, platform: str, room: str) -> tuple:
        return ()

    grounder = build_grounder(
        records=_EmptyRecords(), persons=_PERSONS, media_join=None,
        note_index=nothing, events_in=no_events, lineage=_lineage(),
        unit_rooms=lambda unit: set(), topic_index=lambda u: entries.held(unit=u))
    grounding = await grounder("u", (), ())
    assert [t.id for t in grounding.topics] == [entry.id]  # retired is not shown

    block = render_topics(grounding.topics, now_ms=NOW)
    body = block.body.text
    assert block.name == "topics" and f'id="{entry.id}"' in body
    assert f'revision="{entry.head.id}"' in body
    assert 'subject="generated-racing"' in body and BODY in body
    assert 'state="active"' in body and 'confidence="medium"' in body
    assert 'written="' in body  # every block says how old it is


class _EmptyRecords:
    async def stimuli_refs(self, stimuli):  # pragma: no cover - trivial
        return {}

    async def record_items(self, refs):  # pragma: no cover - trivial
        return {}


async def test_a_failed_topic_read_is_unavailable_never_empty():
    async def broken(unit: str):
        raise RuntimeError("generated outage")

    async def nothing(unit: str) -> tuple:
        return ()

    async def no_events(unit: str, platform: str, room: str) -> tuple:
        return ()

    grounder = build_grounder(
        records=_EmptyRecords(), persons=_PERSONS, media_join=None,
        note_index=nothing, events_in=no_events, lineage=_lineage(),
        unit_rooms=lambda unit: set(), topic_index=broken)
    grounding = await grounder("u", (), ())
    assert grounding.topics == () and "topics" in grounding.unavailable


async def test_the_historical_lane_holds_neither_the_verbs_nor_the_guidance():
    fake = FakeCognitionAgentRunner(script=[])
    runner, _, _ = _runner(agent_runner=fake, memory=wake_memory())
    await runner.replay(
        view=UnitView(unit="unit-1", pending=_pending("unit-1"),
                      last_activity_ms=None, last_wake=None), now_ms=NOW,
        historical=True)
    names = set(fake.last_tool_names)
    assert "memory_propose" in names and not names & TOPIC_VERBS
    historical = await rendered_session(historical=True)
    assert "topic_create" not in historical
    assert "keeps coming back across people or days" not in historical


async def test_the_live_lane_offers_the_verbs_and_their_guidance():
    fake = FakeCognitionAgentRunner(script=[])
    runner, _, _ = _runner(agent_runner=fake, memory=wake_memory())
    await runner.on_wake(
        unit="unit-1",
        view=UnitView(unit="unit-1", pending=_pending("unit-1"),
                      last_activity_ms=None, last_wake=None),
        now_ms=NOW, wake_id=1)
    assert set(fake.last_tool_names) >= TOPIC_VERBS
    live = await rendered_session(historical=False)
    assert "topic_create" in live
    assert "keeps coming back across people or days" in live
    assert "revise the account you hold" in live


def test_the_topic_guidance_is_gated_on_the_verbs_being_offered():
    with_verbs = render_session_system(
        "<!-- cognition-orient --><!-- cognition-act-skill -->", "N",
        tool_names=frozenset({"topic_create", "topic_revise", "topic_set_status"}))
    assert "keeps coming back across people or days" in with_verbs
    for names in (frozenset(), frozenset({"note_create", "memo_create"})):
        without = render_session_system(
            "<!-- cognition-orient --><!-- cognition-act-skill -->", "N",
            tool_names=names)
        assert "keeps coming back across people or days" not in without


async def test_cognition_is_the_live_topic_writer():
    """The session's verbs are the account's writer: create carries the
    session's provenance, and a held subject is revised, never doubled."""
    entries = FakeEntryStore()
    _, created = await _one(
        "topic_create", entries, subject="generated-racing", body=BODY,
        confidence="medium", citations=CITE)
    assert created["status"] == "ok"
    (entry,) = await entries.held(unit="u")
    content = entry.head.content
    assert (content.writer, content.model, content.instruction_hash) == (
        "cognition:u", "generated-model", DOCTRINE) and content.session_id
    _, twin = await _one(
        "topic_create", entries, subject="Generated-Racing", body=BODY,
        confidence="low", citations=CITE)
    assert twin["status"] == "refused" and twin["reason"] == "duplicate"
    _, revised = await _one(
        "topic_revise", entries, entry_id=entry.id,
        base_revision_id=entry.head.id, body="the revised account",
        confidence="high")
    assert revised["status"] == "ok" and len(await entries.held(unit="u")) == 1


async def test_an_account_is_shown_only_where_its_citations_lineage_permits():
    """Gated like memos: the account reaches a session whose agenda rooms its
    citations' lineage passes and is withheld elsewhere."""
    entries = FakeEntryStore(rooms={("message", "101"): "signal:room-a"})
    entry = await entries.create(
        kind="topic", unit="u",
        content=EntryContent(
            (("topic", "generated-racing"),), BODY, (("message", "101"),),
            "medium", "active", "cognition", "s", "m", DOCTRINE))

    async def nothing(unit: str) -> tuple:
        return ()

    async def no_events(unit: str, platform: str, room: str) -> tuple:
        return ()

    grounder = build_grounder(
        records=_EmptyRecords(), persons=_PERSONS, media_join=None,
        note_index=nothing, events_in=no_events, lineage=_lineage("on"),
        unit_rooms=lambda unit: {
            (ROOM_A.platform, ROOM_A.conversation_id),
            (ROOM_B.platform, ROOM_B.conversation_id)},
        topic_index=lambda u: entries.held(unit=u))

    def item(room: Any) -> AgendaItem:
        return AgendaItem("message", room, "generated", (), (), 0.0)

    a_side = await grounder("u", (item(ROOM_A),), ())
    assert [t.id for t in a_side.topics] == [entry.id]
    b_side = await grounder("u", (item(ROOM_B),), ())
    assert b_side.topics == ()


async def test_a_citation_must_be_shown_even_when_the_account_holds_it():
    """Custody binds the named citation to THIS session: carrying the account's
    own citations is only what omitting the argument does."""
    entries = FakeEntryStore()
    _, created = await _one(
        "topic_create", entries, subject="generated-racing", body=BODY,
        confidence="low", citations=CITE)
    assert created["status"] == "ok"
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["topic_revise"](
            entry_id=created["entry_id"],
            base_revision_id=created["revision_id"], body="a revised account",
            confidence="low", citations=[["message", "101"]]))

    # Room B's agenda shows message 201, never the account's own 101.
    await session(behave, rooms=(RIG_B,), runner_kwargs=_kwargs(entries))
    assert answers[0]["status"] == "refused"
    assert answers[0]["reason"] == "unseen_citation"


async def test_the_historical_lane_reads_and_renders_no_topics():
    """The lane has no as-of view: the held index is never called, a failed
    read is not marked, and the prompt carries no <topics> block."""
    entries = FakeEntryStore()
    entry = await entries.create(
        kind="topic", unit="u",
        content=EntryContent(
            (("topic", "generated-racing"),), BODY, (("message", "101"),),
            "medium", "active", "cognition", "s", "m", DOCTRINE))
    called = 0

    async def counting(unit: str) -> tuple:
        nonlocal called
        called += 1
        return await entries.held(unit=unit)

    async def nothing(unit: str) -> tuple:
        return ()

    async def no_events(unit: str, platform: str, room: str) -> tuple:
        return ()

    grounder = build_grounder(
        records=_EmptyRecords(), persons=_PERSONS, media_join=None,
        note_index=nothing, events_in=no_events, lineage=_lineage(),
        unit_rooms=lambda unit: set(), topic_index=counting)
    grounding = await grounder("u", (), (), historical=True)
    assert called == 0 and grounding.topics == () and grounding.historical
    assert "topics" not in grounding.unavailable

    held = StaticGrounder(Grounding(topics=(entry,)))
    fake = FakeCognitionAgentRunner(script=[])
    runner, _, _ = _runner(
        agent_runner=fake, memory=wake_memory(), grounder=held)
    await runner.replay(
        view=UnitView(unit="unit-1", pending=_pending("unit-1"),
                      last_activity_ms=None, last_wake=None),
        now_ms=NOW, historical=True)
    assert "<topic" not in fake.last_prompt

    live = FakeCognitionAgentRunner(script=[])
    runner, _, _ = _runner(
        agent_runner=live, memory=wake_memory(), grounder=held)
    await runner.on_wake(
        unit="unit-1",
        view=UnitView(unit="unit-1", pending=_pending("unit-1"),
                      last_activity_ms=None, last_wake=None),
        now_ms=NOW, wake_id=1)
    assert "<topic" in live.last_prompt
