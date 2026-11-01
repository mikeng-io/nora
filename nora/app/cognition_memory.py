"""One wake's frozen graph input and in-call admission. No model driver."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import NoResultFound

from nora.agent.egress import scan_identifiers
from nora.app.comprehend import GraphWrite, MemoryProposalAdmission, _shape_errors
from nora.domain.comprehend import ComprehendItem, Preview, preview_seeds
from nora.domain.culture import character_key, nora_character_key, unit_culture_key
from nora.domain.extraction import partition_claims
from nora.domain.memory_graph import Key, Source, Walk, WorkItem
from nora.domain.profile import (
    Account,
    ProfileCitable,
    Said,
    handles_for,
    parse_ops,
)
from nora.language.comprehend_render import preview_text

#: Characters of each row's body kept in the reading slot map.
READING_CLIP = 160


class WakeMemory:
    def __init__(self, *, graph: Any, records: Any, metrics: Any,
                 private_names: tuple[str, ...] = (), content_checks: bool = True,
                 profile: Any = None, listed_rooms: Any = None) -> None:
        self.metrics, self.content_checks = metrics, content_checks
        self.private_names = private_names
        self.graph, self.records = graph, records
        # The promoted typed-account writer (`SqlProfileStore`, `index` set):
        # `None` under every flag combination other than `[memory.profile]
        # write && read`, where `profile_revise` keeps recording shadow
        # receipts and the Dreamer adaptors remain the account writers.
        self.profile_store = profile
        # `(unit, person) -> the room ids listing the person` (35 §3's
        # citable.listed_rooms); `None` answers for collectives by rule.
        self.listed_rooms = listed_rooms or (lambda unit, person: None)
        self.admission = MemoryProposalAdmission(graph=graph, records=records, metrics=metrics)

    async def act_key(self, state: Any, name: str, arguments: Any) -> str:
        payload = json.dumps([state.unit, sorted(v.work.id for v in state.memory_items.values()),
                              name, arguments], sort_keys=True, default=str)
        return "act:" + hashlib.sha256(payload.encode()).hexdigest()

    async def link_result(self, state: Any, result: Any, *, targets: Any = None) -> None:
        from nora.app.register_graph import RegisterGraphLinks
        for field, kind in (("note_id", "note"), ("memo_id", "memo"), ("event_id", "event_entry")):
            if field not in result:
                continue
            base = next(iter(state.memory_items.values())).work
            from dataclasses import replace
            work = replace(base, id=f"register:{kind}:{result[field]}",
                           source=(kind, str(result[field])))
            item = await self.records.load(work)
            await RegisterGraphLinks(graph=self.graph, records=self.records).project(
                item, state.session_id, selected=None if targets is None else tuple(targets))

    async def parked(self, unit: str, pending: Any) -> set[int]:
        return {s.id for s in pending if await self.records.failures(unit, s.id) >= 5}

    async def failed(self, unit: str, ids: Any, session: int | None) -> None:
        for sid in ids:
            await self.records.fail_stimulus(unit, sid, session)

    async def prepare(self, state: Any, stimuli: Any, grounding: Any) -> None:
        """Freeze exactly what the agenda shows, including each non-message source."""
        at = datetime.fromtimestamp(state.now_ms / 1000, UTC)
        anchors: list[Key] = []
        aliases = await self.graph.aliases(unit=state.unit)
        for index, agenda in state.items_by_index.items():
            sources = []
            rows = []
            loaded_items = []
            for sid in agenda.stimulus_ids:
                stimulus = stimuli[sid]
                row = grounding.records.get(stimulus.ref)
                if row is not None and row.text is not None:
                    sources.append(Source(("message", str(row.message_id)), "record", state.unit,
                                          (row.person_id,) if row.person_id else (), row.text))
                    try:
                        loaded = await self.records.load(WorkItem(f"stimulus:{sid}",
                            ("message", str(row.message_id)), state.unit, "", at,
                            historical=state.historical))
                    except NoResultFound:
                        # Not projected yet: the message's own text above still stands.
                        # ponytail: social and play stimuli still need their projection.
                        loaded = None
                    if loaded is not None:
                        loaded_items.append(loaded)
                        sources.extend(loaded.sources)
                        rows.extend(loaded.snapshot.get("messages", []))
                        rows.extend(loaded.snapshot.get("context", []))
                    rows.append({"id": row.message_id, "body": row.text, "ts": row.at_ms,
                                 "person_id": row.person_id, "mentions": []})
                elif stimulus.kind in ("social", "play"):
                    kind, _, ref = stimulus.ref.partition(":")
                    work = WorkItem(f"stimulus:{sid}", (kind, ref), state.unit, "", at,
                                    historical=state.historical)
                    loaded = await self.records.load(work)
                    loaded_items.append(loaded)
                    sources.extend(loaded.sources)
                    rows.extend(loaded.snapshot.get("messages", []))
                    rows.extend(loaded.snapshot.get("context", []))
                else:
                    line = grounding.rollups.get(stimulus.ref)
                    text = json.dumps(asdict(line), default=str) if line else stimulus.ref
                    sources.append(Source((stimulus.kind, str(sid)), "rollup", state.unit,
                        (stimulus.person_id,) if stimulus.person_id else (), text))
            sources = list({s.key: s for s in sources}.values())
            original_rows: dict[int, Any] = {}
            for r in rows:
                original_rows[r["id"]] = {**r, **original_rows.get(r["id"], {})}
            rows = []
            source_keys = {}
            next_id = max([int(s.key[1]) for s in sources if s.key[0] == "message"] + [0]) + 1
            for source in sources:
                if source.layer != "record":
                    continue
                row_id = int(source.key[1]) if source.key[0] == "message" else next_id
                next_id += 1
                rows.append({**original_rows.get(row_id, {}), "id": row_id, "body": source.text,
                    "ts": original_rows.get(row_id, {}).get("ts", state.now_ms),
                    "person_id": original_rows.get(row_id, {}).get("person_id")
                    or (source.persons[0] if len(source.persons) == 1 else ""),
                    "mentions": original_rows.get(row_id, {}).get("mentions", [])})
                source_keys[str(row_id)] = list(source.key)
            digest = hashlib.sha256(json.dumps([asdict(s) for s in sources],
                                              sort_keys=True).encode()).hexdigest()
            work = WorkItem(f"stimuli:{','.join(map(str, agenda.stimulus_ids))}",
                            ("stimulus", str(agenda.stimulus_ids)), state.unit, digest, at,
                            historical=state.historical)
            source_provenance = {
                f"{source.key[0]}:{source.key[1]}": {
                    "metadata": loaded.snapshot.get("metadata") or {},
                    "author": loaded.author,
                }
                for loaded in loaded_items for source in loaded.sources
                if source.key[0] == "social"
            }
            item = ComprehendItem(work, tuple(sources), agenda.room.platform,
                                  agenda.room.conversation_id,
                                  author=next((r.get("person_id") for r in rows
                                               if r.get("person_id")), ""),
                                  snapshot={"messages": rows, "context": [],
                                            "source_keys": source_keys,
                                            "source_provenance": source_provenance})
            from dataclasses import replace
            if loaded_items:
                item = replace(item, author=item.author or loaded_items[0].author,
                    reply_parent=next((v.reply_parent for v in loaded_items
                        if v.platform == item.platform and v.room == item.room), None),
                    urls=tuple(dict.fromkeys(u for v in loaded_items for u in v.urls)),
                    accounts=tuple(dict.fromkeys(a for v in loaded_items for a in v.accounts)),
                    faces=tuple(dict.fromkeys(p for v in loaded_items for p in v.faces)),
                    mentions=tuple(dict.fromkeys(p for v in [item, *loaded_items]
                        for p in (v.author, *v.mentions, *v.faces) if p)), snapshot=item.snapshot)
            anchors.extend(preview_seeds(item, aliases))
            for loaded in loaded_items:
                anchors.extend(preview_seeds(loaded, aliases))
            state.memory_items[index] = item
            for source in sources:
                state.graph_fetched.show(source)
        persons = frozenset(p for s in state.graph_fetched.sources.values() for p in s.persons)
        seeds = tuple(dict.fromkeys(anchors))
        graph = await self.graph.walk(seeds, Walk(unit=state.unit, persons=persons, depth=2,
                                                layers=("record", "rollup", "notebook"),
                                                valid_at=at))
        if graph.status == "unavailable":
            raise RuntimeError("wake graph unavailable")
        # `shown_keys` is the renderer's own emitted-rows provenance: a row the
        # budget omits was never shown, whatever the walk returned.
        state.graph_text = preview_text(Preview(seeds, graph, (), (at, at)),
                                        shown_keys=state.shown_keys)
        # The slot map the model cites by row id. The agenda already renders the
        # text, so each body is clipped to identify the row, never repeat it.
        # ponytail: fixed clip; count it into RenderBudget if busy wakes overflow.
        state.graph_text += "\nreading_sources=" + json.dumps(
            {i: {**v.snapshot, "messages": [
                {**r, "body": str(r.get("body") or "")[:READING_CLIP]}
                for r in v.snapshot["messages"]]}
             for i, v in state.memory_items.items()}, default=str)
        if scan_identifiers(state.graph_text, content_checks=self.content_checks):
            state.graph_text = ""
            self.metrics.inc("nora_errors_total", stage="wake_prefetch")

    async def propose(
        self, state: Any, *, items: list[int], payload: dict[str, Any],
    ) -> dict[str, Any]:
        # `memory_items` is what `prepare` built; `probe` never prepares.
        if not items or any(i not in state.memory_items for i in items):
            return {"status": "refused", "admitted": [], "refused": [{"reason": "no_such_item"}]}
        try:
            output = GraphWrite.model_validate(payload)
        except ValidationError as error:
            return {"status": "refused", "admitted": [],
                    "refused": [{"reason": "schema", "errors": _shape_errors(error)}]}
        if any(e.kind in ("person", "room", "unit", "nora") for e in output.entities):
            return {"status": "refused", "admitted": [],
                    "refused": [{"reason": "identity_pointer"}]}
        admitted: list[int] = []
        refused: list[dict[str, Any]] = []
        # Each proposal has one evidence scope. A multi-item proposal is validated
        # against their union, then consumes only the explicitly named items.
        inputs = [state.memory_items[i] for i in items]
        base = inputs[0]
        from dataclasses import replace
        combined = replace(base, sources=tuple(s for v in inputs for s in v.sources), snapshot={
            "messages": [r for v in inputs for r in v.snapshot["messages"]], "context": [],
            "source_keys": {k: source for v in inputs
                            for k, source in v.snapshot.get("source_keys", {}).items()}})
        claims, drops = partition_claims({"claims": [r.model_dump() for r in output.readings]},
                                         combined.snapshot)
        if drops:
            return {"status": "refused", "admitted": [],
                    "refused": [{"reason": r} for r in drops]}
        result, local_refusals = await self.admission._admit(
            combined.work, output, state.graph_fetched, state.session_id)
        refused = [*[asdict(r) for r in result.refused], *local_refusals]
        if not refused:
            await self.records.readings(combined.work, state.session_id, combined, claims)
            admitted = items
        seeds = tuple(dict.fromkeys(k for r in result.relations for k in (r.src, r.dst)))
        graph = await self.graph.walk(seeds, Walk(unit=state.unit, depth=1,
            persons=frozenset(p for s in combined.sources for p in s.persons),
            valid_at=combined.work.as_of))
        if graph.status == "unavailable":
            raise RuntimeError("connected graph unavailable")
        state.shown_keys.update(n.key for n in graph.nodes)
        state.shown_keys.update(
            k for e in graph.relations for k in (e.relation.src, e.relation.dst))
        state.shown_keys.update(k for r in result.relations for k in (r.src, r.dst))
        state.admitted_indices.update(admitted)
        state.admitted_relations.update({r.id: r for r in result.relations})
        state.admitted_evidence.extend(result.evidence)
        # Admission commits the clean parts in the same call, so a refusal can
        # still have saved relations: say so rather than read as nothing saved.
        committed = [r.id for r in result.relations]
        note = "Read the connected state before deciding whether to act."
        if refused and committed:
            note = ("Some relations were saved (see committed); the item is not "
                    "admitted until a proposal has no refusals. " + note)
        return {"status": "ok" if admitted else "refused", "admitted": admitted,
                "refused": refused, "committed": committed,
                "connected": json.loads(json.dumps(asdict(graph), default=str)),
                "note": note}

    def _account_for(self, state: Any, entity: list[str]) -> Account | None:
        """The shown `[kind, id]` subject's typed account, or `None` when it
        names no account-holding subject of this unit (a topic is a graph
        subject, not an account)."""
        kind = entity[0] if entity else ""
        ref = entity[1] if len(entity) > 1 else ""
        prefix = f"{state.unit}:{kind}:"
        if ref.startswith(prefix):
            ref = ref[len(prefix):]
        unit = state.unit
        if kind == "person" and ref in getattr(
                self.records, "persons_by_unit", {}).get(unit, set()):
            return Account(key=character_key(unit, ref), entity_kind="human",
                           unit=unit, subject=ref)
        if kind == "room" and any(
            ref == rid for _, rid in getattr(self.records, "rooms_by_unit", {}).get(unit, ())
        ):
            return Account(key=ref, entity_kind="room", unit=unit)
        if kind == "unit" and ref == unit:
            return Account(key=unit_culture_key(unit), entity_kind="unit", unit=unit)
        if kind == "nora":
            return Account(key=nora_character_key(unit), entity_kind="nora", unit=unit)
        return None

    async def account_view(self, state: Any, entity: list[str]) -> dict[str, Any]:
        """`profile_read`: one account's current heads as handles, its open
        disputes and its revision — what `profile_revise` writes against."""
        if self.profile_store is None:
            return {"status": "unavailable",
                    "note": "Profile accounts are not wired in this deployment."}
        account = self._account_for(state, entity)
        if account is None:
            return {"status": "refused", "reason": "unknown_account",
                    "note": "That subject has no profile account here; a topic "
                            "is a graph subject, not an account."}
        revision, _ = await self.profile_store.account_state(key=account.key)
        heads = await self.profile_store.account_heads(key=account.key)
        handles = handles_for(heads)
        disputes = await self.profile_store.open_disputes(key=account.key, limit=10)
        state.profile_accounts[account.key] = {
            "account": account, "heads": heads, "handles": handles,
            "revision": revision, "disputes": disputes,
        }
        by_id = {h.id: h for h in heads}
        return {"status": "ok", "account": {
            "entity_kind": account.entity_kind, "revision": revision,
            "lines": [
                {"handle": handle, "part": line.part, "kind": line.kind,
                 "mode": line.mode, "state": line.state, "text": line.text,
                 "content": dict(line.content),
                 "verdict": line.verdict, "told_by": line.told_by,
                 "citations": list(line.citations),
                 "draws_on": list(line.draws_on),
                 "support_count": line.support_count,
                 "last_supported_at": line.last_supported_at,
                 "as_of": line.as_of, "written_at": line.written_at,
                 "valid_from": line.valid_from, "valid_until": line.valid_until,
                 "fact_key": line.fact_key, "fact_value": line.fact_value,
                 "prediction": line.prediction,
                 "prediction_observable": line.prediction_observable,
                 "prediction_window_end": line.prediction_window_end}
                for handle, line_id in handles.items()
                for line in (by_id[line_id],)],
            "disputes": [
                {"disputed_by": d.disputed_by, "kind": d.kind, "claim": d.claim,
                 "line": next((h for h, lid in handles.items() if lid == d.line_id), None)}
                for d in disputes],
        }}

    def _citable_for(self, state: Any, account: Account) -> ProfileCitable:
        """What this session's profile write may cite: every source key the
        session was shown, resolved to `(ts, person, room)` from the frozen
        snapshot rows (unresolvable keys stay out, so a citation of one is
        `unresolved_citation`), plus the admitted relations as premises."""
        # Only sources whose REAL loader provenance resolves may cite — a
        # missing date/author stays unavailable rather than being stamped
        # with the wake's own clock or the source's sole person.
        said: dict[str, Said] = {}
        social: dict[str, Said] = {}
        for item in state.memory_items.values():
            provenance = item.snapshot.get("source_provenance") or {}
            for source_key in item.snapshot.get("source_keys", {}).values():
                kind, ref = source_key[0], source_key[1]
                citation = f"{kind}:{ref}"
                if kind in ("message", "edit"):
                    record = next(
                        (r for r in state.records
                         if str(getattr(r, "message_id", "")) == str(ref)), None)
                    if record is None:
                        continue
                    said[citation] = Said(
                        ts=record.at_ms, person_id=record.person_id or None,
                        room=getattr(record.room, "conversation_id", None),
                        occasion=citation)
                elif kind == "social":
                    origin = provenance.get(citation) or {}
                    taken_at = (origin.get("metadata") or {}).get("taken_at")
                    if isinstance(taken_at, str):
                        taken_at = int(
                            datetime.fromisoformat(taken_at).timestamp() * 1000)
                    if not isinstance(taken_at, int):
                        continue
                    author = origin.get("author")
                    social[ref] = Said(
                        ts=taken_at, person_id=author, room=None,
                        occasion=citation, medium="social")
        listed = (self.listed_rooms(account.unit, account.subject)
                  if account.entity_kind == "human" else None)
        return ProfileCitable(
            sources=said, social=social,
            social_subjects=frozenset(
                getattr(self.records, "persons_by_unit", {}).get(account.unit, set())),
            listed_rooms=frozenset(listed) if listed is not None else None,
            relations=frozenset(state.admitted_relations),
        )

    def _unshown(self, state: Any, parsed: Any) -> list[str]:
        shown_cites = {f"{k[0]}:{k[1]}" for k in state.graph_fetched.sources}
        supports = {(e.relation_id, e.source) for e in state.admitted_evidence}
        out = []
        for op in parsed:
            if (getattr(op, "part", None) == "facts"
                    or getattr(op, "mode", None) == "told"):
                out.append("facts are rendered by code from the graph and told "
                           "lines belong to the turn path; this session writes "
                           "traits and guesses")
                continue
            cites = getattr(op, "citations", ()) or ()
            for cite in cites:
                if cite not in shown_cites:
                    out.append(f"citation {cite!r} was not shown in this session")
            for draw in (getattr(op, "draws_on", None) or ()):
                if not draw.startswith("relation:"):
                    continue
                rid = draw[len("relation:"):]
                if rid not in state.admitted_relations:
                    out.append(f"premise {draw!r} is not an admitted relation")
                elif not any(
                        (rid, tuple(c.split(":", 1))) in supports for c in cites):
                    out.append(f"premise {draw!r} is not supported by the "
                               "op's cited evidence")
        return out

    async def profile(
        self, state: Any, entity: list[str], ops: list[dict[str, Any]],
        *, act_key: str | None = None,
    ) -> dict[str, Any]:
        if act_key is not None:
            # Identical replay returns the recorded result: the ok receipt is
            # bound into the write's own transaction (the Register pattern),
            # refused/conflict results are recorded after the call.
            existing = await self.records.act_result(act_key, state.session_id)
            if existing is not None:
                return dict(existing)
        item = next(iter(state.memory_items.values()), None)
        if item is None:
            return {"status": "refused", "reason": "no_items"}
        if self.profile_store is None:
            # Shadow staging (any flag combination other than write&&read):
            # the same typed ops are parsed and their references checked
            # against what the session was shown, then RECEIPTED, never applied.
            parsed, bad = parse_ops(ops)
            refusals = [{"reason": r.reason, "detail": r.detail, "op": r.op_index}
                        for r in bad]
            if not refusals:
                refusals = [{"reason": "unshown", "detail": u}
                            for u in self._unshown(state, parsed)]
            if refusals:
                return {"status": "refused", "refusals": refusals}
            await self.records.shadow(item.work, state.session_id, tuple(entity),
                                      ops, state.session_id)
            result = {"status": "ok", "note": "Profile proposals recorded in shadow."}
            if entity and entity[0] == "topic":
                activity = await self.graph.topic_activity(unit=state.unit, topic=tuple(entity),
                                                           at=item.work.as_of)
                result["topic_activity"] = json.loads(json.dumps(asdict(activity), default=str))
            return result
        account = self._account_for(state, entity)
        if account is None:
            return {"status": "refused", "reason": "unknown_account",
                    "note": "That subject has no profile account here; a topic "
                            "is a graph subject, not an account."}
        shown = state.profile_accounts.get(account.key)
        if shown is None:
            return {"status": "refused", "reason": "read_first",
                    "note": "Call profile_read on this account first; a write "
                            "rests on the lines and revision it was shown."}
        parsed, bad = parse_ops(ops)
        if bad:
            return {"status": "refused", "refusals": [
                {"reason": r.reason, "detail": r.detail, "op": r.op_index} for r in bad]}
        unshown = self._unshown(state, parsed)
        if unshown:
            return {"status": "refused", "refusals": [
                {"reason": "unshown", "detail": u, "op": None} for u in unshown]}
        citable = self._citable_for(state, account)
        now_ms = (int(item.work.as_of.timestamp() * 1000)
                  if item is not None else state.now_ms)

        async def receipt(conn: Any, payload: dict[str, Any]) -> None:
            await self.records.act_result_on(conn, act_key, state.session_id, payload)

        # A store failure propagates to `_counted`, which answers `unavailable`
        # (`reason: error`) — a write may have landed part-way, so refusal
        # wording would lie.
        result = await self.profile_store.profile_write(
            account=account, ops=parsed, citable=citable, now_ms=now_ms,
            written_by_session=(
                f"cognition:profile:{state.session_id}"),
            apply_decay=True, expected_revision=shown["revision"],
            receipt=receipt if act_key is not None else None)
        out: dict[str, Any]
        if result.status == "conflict":
            state.profile_accounts.pop(account.key, None)
            out = {"status": "conflict",
                   "note": "The account changed under you; call profile_read "
                           "again before revising it."}
        elif result.status == "refused":
            out = {"status": "refused", "refusals": [
                {"reason": r.reason, "detail": r.detail, "op": r.op_index}
                for r in result.refusals]}
        else:
            out = {"status": "ok", "revision": result.revision,
                   "written": list(result.written),
                   "note": "Handles changed: call profile_read again before "
                           "the next write to this account."}
            # A write renumbers the account's handles — drop the shown view
            # rather than bind the model to P-numbers it never re-saw; the
            # next write requires a fresh profile_read.
            state.profile_accounts.pop(account.key, None)
        return out
