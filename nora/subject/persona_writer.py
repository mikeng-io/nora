"""Persona writer input/output on the obtained graph; no model client here."""
from __future__ import annotations

import hashlib
import os
from copy import copy
from dataclasses import asdict
from typing import Any

from nora.agent.dream_screen import text_offence
from nora.domain.culture import RoomMessage, character_key
from nora.domain.media import MediaBlob
from nora.domain.persona import Account, AddOp, PersonaCitable, Said
from nora.domain.persona_inputs import PersonSourceRevision
from nora.infra.sql import create_sql_engine
from nora.infra.sql.character import SqlCharacterEvidence
from nora.infra.sql.language import SqlLanguageStore
from nora.infra.sql.media_objects import SqlMediaObjectStore
from nora.infra.sql.persona import SqlPersonaStore
from nora.infra.sql.persona_inputs import SqlPersonaInputs
from nora.obs.tracing import current_trace_id
from nora.subject.dreaming import _samples
from nora.subject.knowledge import arrive
from nora.subject.obtain import Subject

SOURCE_VECTORS_V2 = ("profile_endorsed_late", "profile_media_only_late",
                     "profile_media_only_missing")

SOURCE_VECTORS = ("profile_cached", "profile_missing", "profile_late")


def validate_sources(sources: object, now_ms: int, *, vector: str | None = None) -> None:
    """Closed synthetic message grammar, checked before obtaining a model."""
    if type(now_ms) is not int or not isinstance(sources, list) or not sources:
        raise ValueError("private persona sources require a clock and nonempty sources")
    for source in sources:
        if (not isinstance(source, dict) or set(source) not in (
                {"body", "ts"}, {"body", "ts", "attachment"})
                or not isinstance(source["body"], str)
                or (not source["body"].strip() and "attachment" not in source)
                or type(source["ts"]) is not int or source["ts"] > now_ms):
            raise ValueError("private persona sources require nonempty body and timestamp <= clock")
        if "attachment" in source:
            a = source["attachment"]
            if (not isinstance(a, dict) or set(a) != {"reading", "late"}
                    or type(a["late"]) is not bool
                    or (a["reading"] is not None and
                        (not isinstance(a["reading"], str) or not a["reading"].strip()))
                    or (a["late"] and a["reading"] is None)):
                raise ValueError("attachment requires reading (text or null) and late bool")
    if vector in {"profile_media_only_late", "profile_media_only_missing"}:
        if (len(sources) != 2 or any(s["body"] != "" or "attachment" not in s for s in sources)
                or sources[0]["ts"] == sources[1]["ts"]
                or any(s["attachment"]["late"] != (vector == "profile_media_only_late")
                       for s in sources)
                or sum(s["attachment"]["reading"] is None for s in sources)
                   != (1 if vector.endswith("missing") else 0)):
            raise ValueError("media-only vector requires two premises and declared readings")
    elif vector in (*SOURCE_VECTORS, "profile_endorsed_late"):
        if (len(sources) != 2 or any(not s["body"].strip() for s in sources)
                or "attachment" not in sources[0]
                or "attachment" in sources[1]
                or sources[0]["attachment"]["late"] != (
                    vector in {"profile_late", "profile_endorsed_late"})
                or (sources[0]["attachment"]["reading"] is None) != (vector == "profile_missing")):
            raise ValueError("source vector requires declared media treatment and caption control")
    elif vector is not None and any("attachment" in source for source in sources):
        raise ValueError("original persona vectors require text sources only")
    if [s["ts"] for s in sources] != sorted(s["ts"] for s in sources):
        raise ValueError("private persona sources must be chronological")


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
    validate_sources(sources, now_ms)
    exposure = os.environ.get("NORA_EVAL_PERSONA_MEDIA_EXPOSURE", "cached")
    if exposure not in {"cached", "caption_only"}:
        raise ValueError("media exposure must be cached or caption_only")
    if exposure == "caption_only":
        # Fixed eval-only exposure ablation; all owning-store seeds stay identical.
        chosen = copy(chosen)
        chosen._media_join = None
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
    engine = create_sql_engine(subject.database_url)
    try:
        media = SqlMediaObjectStore(engine)
        inputs = SqlPersonaInputs(engine)
        delayed = []
        for source in sources:
            attachment = source.get("attachment")
            blob = (MediaBlob(sha256=hashlib.sha256(
                (source["body"] + str(source["ts"])).encode()).hexdigest(),
                media_type="image/png", size=1) if attachment else None)
            body = source["body"] + ((" " if source["body"] else "")
                                     + f"[image {blob.sha256[:6]}]" if blob else "")
            rows.append(await arrive(subject, body, now_ms=source["ts"], person=person,
                                     media_blobs=(blob,) if blob else ()))
            if attachment and attachment["late"]:
                # The actual pre-upgrade consumer already saw this caption.
                initial = await inputs.messages(key=key, rooms=(subject.room,),
                                                person=person, after=cursor, limit=64)
                initial = [r for r in initial if r.source == f"message:{rows[-1]}"]
                if len(initial) != 1 or initial[0].cache_ready:
                    raise ValueError("late fixture lacks its uncached initial revision")
                await inputs.settle(key=key, revisions=initial)
                cursor = rows[-1]
                cursors = SqlLanguageStore(engine)
                for name in ("persona-person", "persona-person:history-boundary",
                             "persona-person:history"):
                    await cursors.advance_watermark(consolidator_name=name, scope_id=key,
                                                    last_message_id=cursor)
                    if await cursors.watermark_for(consolidator_name=name, scope_id=key) != cursor:
                        raise ValueError("late fixture cursor seed readback failed")
                pending = await inputs.messages(key=key, rooms=(subject.room,),
                                                person=person, after=cursor, limit=64)
                if any(r.source == initial[0].source for r in pending):
                    raise ValueError("late fixture initial revision did not settle")
            if attachment and blob is not None and attachment["reading"] is not None:
                delayed.append((blob, attachment))
        # Consume every uncached late source before publishing any reading.
        for blob, attachment in delayed:
            if not await media.comprehend(sha256=blob.sha256, title="Synthetic profile",
                    description=attachment["reading"], content="", model="eval-cached-fixture"):
                raise ValueError("cached reading seed unavailable")
            obj = await media.get(sha256=blob.sha256)
            if obj is None or obj.description != attachment["reading"]:
                raise ValueError("cached reading seed readback failed")
    finally:
        await engine.dispose()
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
        observed = {m.row.id for item in batch for m in (
            item.roots if isinstance(item, PersonSourceRevision)
            else (item,) if isinstance(item, RoomMessage) else ())}
        missing = set(rows) - observed
        if missing:
            raise ValueError(
                f"declared synthetic sources missing from writer input: {sorted(missing)}"
            )
        if any(isinstance(r, PersonSourceRevision) and r.state == "unavailable" for r in batch):
            raise ValueError("writer source domain unavailable")
        if print_input:
            shown = await runtime.render_input(adaptor=chosen, key=key, batch=batch)
            return {"shown": shown, "rows": rows, "exposure": exposure}
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
            "trace_id": current_trace_id(), "exposure": exposure, "person": person,
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
