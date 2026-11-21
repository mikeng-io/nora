"""The obligations a committed ingest raises downstream — the turn (tag or
`!!!member` command), participation candidate, memory feed, stimulus — as
ONE ordinal sequence per source event. `WorkRunner._run_ingest` owns the
verdict; this block owns the enqueues, kept at the same payload boundary
`bridge_payloads` opened.
"""

from __future__ import annotations

import uuid
from typing import Any

from nora.app.bridge_payloads import _decision_dict, _person_dict
from nora.platform.bridge.rehydrate import message_to_dict


async def enqueue_downstream(
    conn: Any, *, store: Any, work: Any, envelope: Any, message: Any,
    decision: Any, person: Any, row_id: Any, absorbed: bool, unit: Any,
    observation_status: str | None, room_command: Any, participation: Any,
    memory_feed: Any, stimulus: Any, downstream: dict[str, Any],
) -> bool:
    """Enqueue the turn/participation/memory-feed/stimulus obligations;
    returns whether `message` IS a room command (the caller stamps it)."""
    # ONE command-recognition rule for both ingress paths: the predicate the
    # live dispatcher is handed at composition, over the VERIFIED normalized
    # message — a bare `!!!member` never waits on the engage gate.
    command = room_command is not None and room_command(message)
    ordinal = 1
    if command or decision.engage:
        tag_id = uuid.uuid4().hex[:12]
        await store.set_event_state(
            conn, envelope.event_id, "done", tag_id=tag_id)
        await store.enqueue_work(
            conn, event_id=envelope.event_id, kind="turn",
            ordinal=ordinal, platform=work.platform,
            account_key=work.account_key,
            conversation_id=work.conversation_id,
            payload={
                "tag_id": tag_id,
                "stream": work.payload.get("stream"),
                "stream_id": work.payload.get("stream_id"),
                "message": message_to_dict(message),
                "message_row_id": row_id,
                "decision": _decision_dict(decision),
                # The explicit-command occasion survives durable rehydration.
                "occasion": "room_command" if command else "tag",
                "admission": None,
                **downstream,
            })
        ordinal += 1
    if not command and decision.relevance_gate and participation is not None:
        # The dispatcher's `note_message` never fires under bridge mode (no
        # live listener), so the durable obligation owns it.
        await store.enqueue_work(
            conn, event_id=envelope.event_id,
            kind="participation", ordinal=ordinal,
            platform=work.platform,
            account_key=work.account_key,
            conversation_id=work.conversation_id,
            payload={"note_message": message_to_dict(message),
                     **downstream})
        ordinal += 1
    if memory_feed is not None and person is not None:
        await store.enqueue_work(
            conn, event_id=envelope.event_id,
            kind="memory_feed", ordinal=ordinal,
            platform=work.platform,
            account_key=work.account_key,
            conversation_id=work.conversation_id,
            payload={"message": message_to_dict(message),
                     "person": _person_dict(person),
                     "absorbed": absorbed, "unit": unit,
                     **downstream})
        ordinal += 1
    # A committed sidecar's ordinal-90 obligation is the ONE stimulus for
    # the event; a refused/unscoped sidecar leaves the ordinary wake to raise.
    source_owned = observation_status == "ok" and bool(unit)
    if stimulus is not None and row_id is not None and not source_owned:
        await store.enqueue_work(
            conn, event_id=envelope.event_id,
            kind="stimulus", ordinal=ordinal,
            platform=work.platform,
            account_key=work.account_key,
            conversation_id=work.conversation_id,
            payload={"message": message_to_dict(message),
                     "row_id": row_id,
                     "person_id": person.person_id if person else None,
                     "absorbed": absorbed, "unit": unit,
                     **downstream})
        ordinal += 1
    return command
