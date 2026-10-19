"""Persona writer input/output on the obtained graph; no model client here."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from nora.agent.dream_screen import text_offence
from nora.domain.culture import RoomMessage, character_key
from nora.domain.persona import Account, AddOp, PersonaCitable, Said
from nora.infra.sql import create_sql_engine
from nora.infra.sql.character import SqlCharacterEvidence
from nora.infra.sql.language import SqlLanguageStore
from nora.infra.sql.persona import SqlPersonaStore
from nora.obs.tracing import current_trace_id
from nora.subject.dreaming import _samples
from nora.subject.knowledge import arrive
from nora.subject.obtain import Subject


async def run_writer(
    subject: Subject, *, person: str, sources: list[dict[str, Any]],
    held_birthday: bool, print_input: bool, now_ms: int,
) -> dict[str, Any]:
    """Append declared invented arrivals, then drive the live person writer.

    The private case file supplies source timestamps and a real roster person.
    No restored source row is edited. Held state is created through the same
    append-only validator as every other Persona write.
    """
    runtime = subject.deps.dreamer_runtime
    chosen = next((a for a in subject.deps.dream_adaptors if a.name == "persona-person"), None)
    if runtime is None or chosen is None:
        raise LookupError("persona-person runtime/adaptor is unavailable")
    person = subject.deps.persons.subject_id_for(person)
    key = character_key(subject.unit, person)
    members = {ref.person_id for ref in
               subject.deps.persons.room_members(subject.listed_persons)}
    if key not in tuple(chosen.keys()) or person not in members:
        raise LookupError("person account is not resolvable in the case room")
    if not sources or any(
        set(s) != {"body", "ts"} or not isinstance(s["body"], str) or not s["body"].strip()
        or not isinstance(s["ts"], int) or s["ts"] > now_ms
        for s in sources
    ):
        raise ValueError("private persona sources require nonempty body and timestamp <= clock")
    if [s["ts"] for s in sources] != sorted(s["ts"] for s in sources):
        raise ValueError("private persona sources must be chronological")
    # This private counterfactual is a post-history session. Freeze and complete
    # the restored background BEFORE arrivals, using production's cursor rows.
    engine = create_sql_engine(subject.database_url)
    try:
        cursor = await SqlCharacterEvidence(engine).message_head()
        cursors = SqlLanguageStore(engine)
        for name in ("persona-person:history-boundary", "persona-person:history"):
            await cursors.advance_watermark(
                consolidator_name=name, scope_id=key, last_message_id=cursor,
            )
    finally:
        await engine.dispose()
    rows = []
    for source in sources:
        rows.append(await arrive(
            subject, source["body"], now_ms=source["ts"], person=person,
        ))
    engine = create_sql_engine(subject.database_url)
    try:
        store = SqlPersonaStore(engine, screen=text_offence)
        if await store.account_heads(key=key):
            raise ValueError("private writer case requires an empty Persona account")
        if held_birthday:
            result = await store.persona_write(
                account=Account(key=key, entity_kind="human", unit=subject.unit, subject=person),
                ops=(AddOp(part="identity", kind="attribute", text="Birthday is June 8",
                           mode="told", told_by=person, citations=(str(rows[0]),),
                           fact_key="birthday", fact_value={"month": 6, "day": 8}),),
                citable=PersonaCitable(messages={rows[0]: Said(
                    ts=sources[0]["ts"], person_id=person, room=subject.room[1],
                )}, listed_rooms=frozenset({subject.room[1]})),
                now_ms=now_ms, written_by_session="eval-synthetic-seed", apply_decay=False,
            )
            if result.status != "ok":
                raise ValueError(f"cannot seed held birthday: {result.refusals}")
        before = await store.account_state(key=key)
        held = await store.account_heads(key=key)
        batch, _ = await chosen.evidence.newest(
            key=key, after=cursor, limit=runtime.batch_limit_for(chosen.name) or 64,
        )
        # Check the real reader's adaptor input, never prompt text: even a
        # negative case cannot pass without every declared source as stimulus.
        observed = {m.row.id for m in batch if isinstance(m, RoomMessage)}
        missing = set(rows) - observed
        if missing:
            raise ValueError(
                f"declared synthetic sources missing from writer input: {sorted(missing)}"
            )
        if print_input:
            shown = await runtime.render_input(adaptor=chosen, key=key, batch=batch)
            return {"shown": shown}
        ok_before = _samples(subject, "ok")
        failed_before = _samples(subject, "unavailable")
        result = await runtime.run(adaptor=chosen, key=key, batch=batch)
        samples_ok = _samples(subject, "ok") - ok_before
        samples_failed = _samples(subject, "unavailable") - failed_before
        after = await store.account_state(key=key)
        heads = await store.account_heads(key=key)
        if (result.status == "unavailable" or samples_ok == 0
                or samples_failed or after[1] is None or after[1] == before[1]):
            raise ValueError(
                f"writer did not settle readably: {result}; revision {before} -> {after}"
            )
        return {
            "trace_id": current_trace_id(),
            "heads": [asdict(h) for h in heads], "held": [asdict(h) for h in held],
            "rows": rows, "timestamps": [s["ts"] for s in sources],
            "revision_before": before[0], "revision_after": after[0],
            "settled_at": after[1], "samples_ok": samples_ok,
            "samples_failed": samples_failed, "status": result.status,
            "budget": asdict(runtime.budget_for(chosen.name)),
            "samples_per_fire": runtime.samples_for(chosen.name),
        }
    finally:
        await engine.dispose()
