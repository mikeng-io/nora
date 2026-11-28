"""The shared cognitive state: dedupe, projection, coverage, render.

`docs/design/cognition/graph.md`'s generated
scenarios, at unit grain: the same act in two rooms is ONE act (never a
citation-set match), an unread source is `unavailable` never
`nothing_found`, and a send the row could not record is `unknown`, never
`sent` and never retried silently.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from nora.agent.shared_state import read_shared
from nora.domain.cognition_remind import PendingRemind, RemindContext, check_remind

T0_MS = 1_780_000_000_000
T0 = datetime.fromtimestamp(T0_MS / 1000, UTC)
ROOM_A = ("signal", "room-a")
ROOM_B = ("signal", "room-b")
ROOMS = (ROOM_A, ROOM_B)


def _pending(
    *, platform: str = "signal", conversation_id: str = "room-a",
    subject: tuple[str, str] | None = ("note", "n-1"), action: str = "tell",
    rows: tuple[str, ...] = ("m1",),
) -> PendingRemind:
    return PendingRemind(
        reminder_ref=str(uuid.uuid4()), platform=platform,
        conversation_id=conversation_id, subject_rows=rows,
        subject=subject, action=action,
    )


def _check(**kw):
    args = {
        "subject_rows": ["m1"], "intent": "tell", "platform": "signal",
        "conversation_id": "room-a", "pending": (),
        "context": RemindContext(),
    }
    args.update(kw)
    return check_remind(**args)


def test_same_subject_and_action_in_another_room_is_a_conflict():
    """The incident case: the unit already waits to `tell` note:n-1 in
    room-b; a second room-a act for it is refused and names room-b."""
    refusal = _check(
        subject=("note", "n-1"),
        pending=[_pending(conversation_id="room-b")],
    )
    assert refusal is not None and refusal.reason == "conflict_other_room"
    assert "room-b" in refusal.detail


def test_an_act_pending_everywhere_asked_is_a_duplicate():
    refusal = _check(
        subject=("note", "n-1"),
        pending=[_pending(conversation_id="room-a")],
    )
    assert refusal is not None and refusal.reason == "duplicate_pending"


def test_explicit_multi_destination_is_the_producers_intent():
    """room-b already waits on this act; a bare room-a call is refused, and
    naming room-b in `also_rooms` IS the explicit intent that lets room-a
    join — while asking only for the already-pending destination is a plain
    duplicate."""
    refusal = _check(
        subject=("note", "n-1"),
        pending=[_pending(conversation_id="room-b")],
        extra_destinations=[("signal", "room-b")],
    )
    assert refusal is None
    refusal = _check(
        subject=("note", "n-1"), conversation_id="room-b",
        pending=[_pending(conversation_id="room-b")],
    )
    assert refusal is not None and refusal.reason == "duplicate_pending"


def test_a_different_action_on_the_same_subject_is_another_act():
    refusal = _check(
        subject=("note", "n-1"), intent="ask",
        pending=[_pending(conversation_id="room-b", action="tell")],
    )
    assert refusal is None


def test_citation_inequality_is_never_uniqueness_and_never_identity():
    """Different citation rows, same subject+action: still a conflict. Same
    citation rows, different subject: still another act."""
    refusal = _check(
        subject=("note", "n-1"), subject_rows=["m9", "m8"],
        pending=[_pending(conversation_id="room-b", rows=("m1", "m2"))],
    )
    assert refusal is not None and refusal.reason == "conflict_other_room"
    refusal = _check(
        subject=("decision", "d-7"), subject_rows=["m1"],
        pending=[_pending(subject=("note", "n-1"), rows=("m1",))],
    )
    assert refusal is None


def test_an_unresolved_new_row_falls_back_to_the_legacy_room_rule():
    refusal = _check(
        pending=[_pending(subject=None, rows=("m1",))],
    )
    assert refusal is not None and refusal.reason == "duplicate_pending"




def _send(message_id: int, at_ms: int, text: str = "already said it"):
    """The real `RecordHit` shape `own_sends` returns — the delivered fact
    lives under `row`, exactly as `SqlRecordQuery` rows it."""
    from nora.domain.models import TranscriptRow
    from nora.domain.record_query import RecordHit
    return RecordHit(
        platform="signal", conversation_id="room-a",
        row=TranscriptRow(
            id=message_id, sender_id="nora", sender_name="Nora", ts=at_ms,
            body=text, is_nora=True, delivery_status="sent", raw_json=None,
        ),
    )








class _Sends:
    def __init__(self, hits=()):
        self._hits = list(hits)

    async def own_sends(self, rooms, *, start_ms, end_ms, limit, **kw):
        room_set = set(rooms)
        return tuple(
            h for h in self._hits
            if (h.platform, h.conversation_id) in room_set
            and start_ms <= h.row.ts < end_ms
        )[:limit]


class _Broken:
    def __getattr__(self, name):
        async def _boom(*a, **k):
            raise RuntimeError("store is down")
        return _boom






async def test_a_source_that_raises_is_unavailable_never_nothing_found():
    """An unread premise must not degrade to «the unit is empty»: the
    source's own status names which read failed."""
    view = await read_shared(
        unit="u1", rooms=ROOMS, ai_peer="nora", now_ms=T0_MS,
        ledger=_Broken(),
    )
    assert view.status == "unavailable"
    assert ("ledger", "unavailable") in view.sources




async def test_a_destination_read_applies_the_real_ledger_permit():
    """The only narrower-than-unit rule is the ledger's own `ledger_permits`,
    run against a real `RenderRoom` and `LedgerEntry`: an unnamed listener
    in the destination room withholds a room-scoped ledger entry — existence,
    never content, and no raw room ids — while other kinds stay unit-visible."""
    from nora.agent.standing_ledger import ledger_permits
    from nora.domain.lineage import LiveRoom, RenderRoom, RoomFacts
    from nora.domain.standing_ledger import LedgerEntry

    class _Ledger:
        async def read(self, unit, platform, conversation_id):
            return (entry,) if (platform, conversation_id) == ROOM_B else ()

    entry = LedgerEntry(
        id="d1", kind="decision", unit="u1", scope="room",
        platform="signal", conversation_id="room-b",
        revision=1, status="open", subject="pick the venue",
        opened_at=T0_MS - 60_000, last_revised_at=T0_MS - 60_000,
        body="we lean toward the pier",
    )
    target = RenderRoom(
        RoomFacts(
            "signal", frozenset({"room-a"}), "u1",
            frozenset({"kit", "ana"}), primary_id="room-a", label="room-a",
        ),
        live=LiveRoom(frozenset({"kit", "ana"}), unnamed=1),
        mode="off",
    )

    def permit(e):
        return ledger_permits(e, target)

    view = await read_shared(
        unit="u1", rooms=ROOMS, ai_peer="nora", now_ms=T0_MS,
        room=ROOM_A, permit=permit,
        ledger=_Ledger(),
        record_query=_Sends([
            _send(10, T0_MS - 30_000),
            SimpleNamespace(platform="signal", conversation_id="room-b",
                            row=_send(11, T0_MS - 60_000).row),
        ]),
    )
    decision = next(e for e in view.entries if e.kind == "decision")
    assert decision.permitted is False
    # Unit-internal knowledge stays whole for the other kinds (DEC-231):
    # the send in the other room is readable to this unit's destination.
    send = next(e for e in view.entries if e.kind == "send")
    assert send.permitted is True


async def test_a_unit_read_sees_every_room():
    """The session's read is the unit itself (DEC-231): nothing is
    withheld — the same stores, one boundary wider."""
    room_b_send = _send(11, T0_MS - 60_000)
    room_b_send = SimpleNamespace(
        platform="signal", conversation_id="room-b", row=room_b_send.row,
    )
    view = await read_shared(
        unit="u1", rooms=ROOMS, ai_peer="nora", now_ms=T0_MS,
        record_query=_Sends([_send(10, T0_MS - 30_000), room_b_send]),
    )
    assert all(e.permitted for e in view.entries)


async def test_an_unresolved_remind_persists_until_a_newer_ok_discharges_it():
    """The Record is the carrier: an `unresolved` remind result persists
    until a NEWER `ok` for the same (subject, action) discharges it —
    across processes, because the trace store is the authority."""
    from nora.domain.tag_trace import TagTrace
    from nora.testing.tag_traces import FakeTagTraceStore

    store = FakeTagTraceStore()
    messages = (
        {"role": "assistant", "content": [
            {"toolUse": {"toolUseId": "r1", "name": "remind", "input": {
                "intent": "ask", "text": "ask when it is",
                "subject": "note:not-there", "action": "ask-time",
                "subject_rows": ["101"], "at": "2026-01-01T20:00:00Z"}}}]},
        {"role": "user", "content": [
            {"toolResult": {"toolUseId": "r1", "status": "success",
                            "content": [{"json": {
                                "status": "unresolved",
                                "reason": "subject_unresolved"}}]}}]},
    )
    await store.save(TagTrace(
        tag_id="t1", conversation_id="room-a", messages=messages,
        transcript=(), outcome="send_reply", created_at=T0_MS - 1000,
        unit="u1", occasion="cognition",
    ))
    view = await read_shared(
        unit="u1", rooms=ROOMS, ai_peer="nora", now_ms=T0_MS, traces=store,
    )
    assert len(view.unresolved) == 1 and "note:not-there" in view.unresolved[0]

    resolved_messages = (
        {"role": "assistant", "content": [
            {"toolUse": {"toolUseId": "r2", "name": "remind", "input": {
                "intent": "ask", "text": "ask when it is",
                "subject": "note:not-there", "action": "ask-time",
                "subject_rows": ["101"], "at": "2026-01-01T20:00:00Z"}}}]},
        {"role": "user", "content": [
            {"toolResult": {"toolUseId": "r2", "status": "success",
                            "content": [{"json": {"status": "ok",
                                                  "reminder_id": "x"}}]}}]},
    )
    await store.save(TagTrace(
        tag_id="t2", conversation_id="room-a", messages=resolved_messages,
        transcript=(), outcome="send_reply", created_at=T0_MS - 500,
        unit="u1", occasion="cognition",
    ))
    view2 = await read_shared(
        unit="u1", rooms=ROOMS, ai_peer="nora", now_ms=T0_MS, traces=store,
    )
    assert view2.unresolved == ()


async def test_a_filtered_fact_walk_finds_unresolved_past_irrelevant_turns():
    """R2-5: forty irrelevant newer traces cannot crowd an unresolved act out
    of the fact read — the server-side tool-name filter means the scan counts
    relevant turns, not chatter."""
    from nora.domain.tag_trace import TagTrace
    from nora.testing.tag_traces import FakeTagTraceStore

    store = FakeTagTraceStore()
    await store.save(TagTrace(
        tag_id="t-old", conversation_id="room-a", messages=(
            {"role": "assistant", "content": [
                {"toolUse": {"toolUseId": "r1", "name": "remind", "input": {
                    "intent": "ask", "text": "ask when",
                    "subject": "note:gone", "action": "ask-time",
                    "subject_rows": ["1"], "at": "2026-01-01T20:00:00Z"}}}]},
            {"role": "user", "content": [
                {"toolResult": {"toolUseId": "r1", "status": "success",
                                "content": [{"json": {
                                    "status": "unresolved",
                                    "reason": "subject_unresolved"}}]}}]},
        ), transcript=(), outcome="send_reply", created_at=T0_MS - 5000,
        unit="u1", occasion="cognition"))
    for i in range(40):
        await store.save(TagTrace(
            tag_id=f"t-chatter-{i}", conversation_id="room-a", messages=(
                {"role": "user", "content": [{"text": f"noise {i}"}]},),
            transcript=(), outcome="send_reply",
            created_at=T0_MS - 4000 + i, unit="u1", occasion="tag"))

    view = await read_shared(
        unit="u1", rooms=ROOMS, ai_peer="nora", now_ms=T0_MS, traces=store)
    assert view.unresolved and "note:gone" in view.unresolved[0]
    assert ("turns", "ok") in view.sources


async def test_closed_history_does_not_poison_the_fact_read():
    """R2-5: a long tail of `ok` results is CLOSED history — it cannot force
    `partial` or hide an older still-open fact."""
    from nora.domain.tag_trace import TagTrace
    from nora.testing.tag_traces import FakeTagTraceStore

    store = FakeTagTraceStore()
    await store.save(TagTrace(
        tag_id="t-open", conversation_id="room-a", messages=(
            {"role": "assistant", "content": [
                {"toolUse": {"toolUseId": "r1", "name": "remind", "input": {
                    "intent": "ask", "text": "ask when",
                    "subject": "note:gone", "action": "ask-time",
                    "subject_rows": ["1"], "at": "2026-01-01T20:00:00Z"}}}]},
            {"role": "user", "content": [
                {"toolResult": {"toolUseId": "r1", "status": "success",
                                "content": [{"json": {
                                    "status": "unresolved",
                                    "reason": "subject_unresolved"}}]}}]},
        ), transcript=(), outcome="send_reply", created_at=T0_MS - 9_000_000,
        unit="u1", occasion="cognition"))
    for i in range(205):
        await store.save(TagTrace(
            tag_id=f"t-c-{i}", conversation_id="room-a", messages=(
                {"role": "assistant", "content": [
                    {"toolUse": {"toolUseId": f"c{i}", "name": "decision_conclude",
                                 "input": {"subject": "d", "decision": f"c{i}"}}}]},
                {"role": "user", "content": [
                    {"toolResult": {"toolUseId": f"c{i}", "status": "success",
                                    "content": [{"json": {"status": "ok"}}]}}]},
            ), transcript=(), outcome="send_reply",
            created_at=T0_MS - 10_000 + i, unit="u1", occasion="cognition"))

    view = await read_shared(
        unit="u1", rooms=ROOMS, ai_peer="nora", now_ms=T0_MS, traces=store)
    assert view.status == "ok"
    assert view.unresolved and "note:gone" in view.unresolved[0]
    assert ("turns", "ok") in view.sources
async def test_an_expired_finding_and_an_excluded_position_stay_out():
    """R2-1: the 48h TTL and the section gate run on the shared read too —
    a 72h finding and an excluded position/finding are not presented."""
    from nora.agent.lineage import visible_sections
    from nora.domain.lineage import (
        BODY,
        HOLD_OPEN,
        I_OWE,
        WORKING_SET,
        LiveRoom,
        RenderRoom,
        RoomFacts,
        own_room_lineage,
    )
    from nora.domain.standing import Standing, WorkingSetItem
    from nora.testing import InMemoryStandingStore

    store = InMemoryStandingStore()
    stale = WorkingSetItem(what="expired option", source="old-tool",
                           found_at=T0_MS - 72 * 3_600_000, bears_on="topic")
    live = WorkingSetItem(what="current option", source="live-source",
                          found_at=T0_MS - 1000)
    excluded_pos = Standing(
        platform="signal", conversation_id="room-a",
        body="EXCLUDED POSITION", fold_count=1, updated_at=T0_MS,
        lineage=own_room_lineage("signal:room-a", (BODY, WORKING_SET)),
        exclude_persons=("listener-x",),
        working_set=(WorkingSetItem(what="EXCLUDED FINDING",
                                    source="s", found_at=T0_MS),),
    )
    await store.write(standing=Standing(
        platform="signal", conversation_id="room-a",
        body="the position", fold_count=1, updated_at=T0_MS,
        working_set=(stale, live)))
    view = await read_shared(
        unit="u1", rooms=ROOMS, ai_peer="nora", now_ms=T0_MS, standing=store)
    whats = [e.subject for e in view.entries if e.kind == "finding"]
    assert "current option" in whats and "expired option" not in whats
    src = next(e for e in view.entries if e.subject == "current option")
    assert "live-source" in src.detail and src.at_ms == live.found_at

    # Excluded position + finding: the real section gate over a real
    # RenderRoom with the listener present.
    store2 = InMemoryStandingStore()
    await store2.write(standing=excluded_pos)
    target = RenderRoom(
        room=RoomFacts("signal", frozenset({"room-a"}), "u1",
                       frozenset({"listener-x"}), primary_id="room-a"),
        live=LiveRoom(frozenset({"listener-x"})), mode="on",
        content_checks=True)
    def permits(pos):
        return visible_sections(
            pos.lineage, (BODY, HOLD_OPEN, I_OWE, WORKING_SET), target,
            origin="signal:room-a", exclude_persons=pos.exclude_persons)
    view2 = await read_shared(
        unit="u1", rooms=ROOMS, room=ROOMS[0], ai_peer="nora", now_ms=T0_MS,
        standing=store2, standing_permits=permits)
    bodies = [e.subject for e in view2.entries]
    assert "EXCLUDED POSITION" not in bodies
    assert "EXCLUDED FINDING" not in bodies


async def test_the_reader_keeps_an_unresolved_only_view():
    from nora.domain.tag_trace import TagTrace
    from nora.testing.tag_traces import FakeTagTraceStore

    traces = FakeTagTraceStore()
    await traces.save(TagTrace(
        tag_id="generated-open", conversation_id="room-a", unit="u1",
        transcript=(), outcome="silent", created_at=T0_MS - 3_600_000,
        messages=(
            {"role": "assistant", "content": [{"toolUse": {
                "toolUseId": "generated-call", "name": "remind", "input": {
                    "subject": "note:missing", "action": "ask", "text": "generated",
                },
            }}]},
            {"role": "user", "content": [{"toolResult": {
                "toolUseId": "generated-call", "content": [{"json": {"status": "unresolved"}}],
            }}]},
        ),
    ))
    view = await read_shared(unit="u1", rooms=ROOMS, ai_peer="nora", now_ms=T0_MS,
                             traces=traces)
    assert view.status == "ok"
    (unresolved,) = view.unresolved
    assert "note:missing" in unresolved
    assert "turn(t1)" in unresolved and "action=ask" in unresolved
    assert datetime.fromtimestamp((T0_MS - 3_600_000) / 1000, UTC).isoformat() in unresolved


async def test_a_delivered_message_without_text_is_still_a_delivery_fact():
    view = await read_shared(unit="u1", rooms=ROOMS, ai_peer="nora", now_ms=T0_MS,
                             record_query=_Sends([_send(9, T0_MS - 1000, "")]))
    assert view.status == "ok"
    (entry,) = view.entries
    assert (entry.kind, entry.id, entry.subject) == ("send", "9", "message without text")
