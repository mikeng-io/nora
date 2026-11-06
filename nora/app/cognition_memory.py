"""One wake's frozen graph input and in-call admission. No model driver."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Any

from pydantic import TypeAdapter, ValidationError
from sqlalchemy.exc import NoResultFound

from nora.agent.egress import scan_identifiers
from nora.app.comprehend import (
    MemoryProposalAdmission,
    Proposal,
    Statement,
    _shape_errors,
    proposal_scope,
)
from nora.domain.comprehend import (
    ComprehendItem,
    Preview,
    WithheldSourceError,
    item_window,
    preview_seeds,
    resolution_candidates,
)
from nora.domain.culture import character_key, nora_character_key, unit_culture_key
from nora.domain.extraction import partition_claims
from nora.domain.memory_graph import (
    CATCH_UP_PREFIX,
    ENTRY,
    GraphNode,
    Key,
    Source,
    Subgraph,
    Walk,
    WorkItem,
)
from nora.domain.profile import (
    Account,
    ProfileCitable,
    Said,
    handles_for,
    parse_ops,
)
from nora.language.comprehend_render import preview_text


def historical_graph(graph: Subgraph, at: datetime) -> Subgraph:
    """Keep asserted structure, withholding heads without historical versions."""
    edges = tuple(
        replace(edge, relation=replace(
            edge.relation,
            valid_to=(edge.relation.valid_to if edge.relation.valid_to is None
                      or edge.relation.valid_to <= at else None),
            created_at=None, superseded_by=None,
        ), evidence_count=0, evidence=(), sources=())
        for edge in graph.relations
        if edge.relation.asserted_at <= at
        and edge.relation.src[0] not in ENTRY
        and edge.relation.dst[0] not in ENTRY
    )
    return Subgraph(graph.status, tuple(
        GraphNode(node.key) for node in graph.nodes if node.key[0] not in ENTRY
    ), edges)


def _source_payload(sources: Iterable[Source]) -> list[dict[str, Any]]:
    rows = [{**asdict(source), "persons": sorted(source.persons)} for source in sources]
    return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True, default=str))


_STATEMENT: TypeAdapter[Statement] = TypeAdapter(Statement)


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

    async def prepare(
        self, state: Any, stimuli: Any, grounding: Any, *, agenda_format: str = "A",
    ) -> None:
        """Freeze exactly what the agenda shows, including each non-message
        source. `agenda_format` is the experiment's candidate; D renders the
        preview's relation rows in statement shape."""
        at = datetime.fromtimestamp(state.now_ms / 1000, UTC)
        anchors: list[Key] = []
        aliases = {} if state.historical else await self.graph.aliases(unit=state.unit)
        for index, agenda in state.items_by_index.items():
            sources: list[Source] = []
            stimulus_inputs: dict[str, str] = {}
            rows: list[dict[str, Any]] = []
            loaded_items = []
            for sid in agenda.stimulus_ids:
                stimulus = stimuli[sid]
                start = len(sources)
                row = grounding.records.get(stimulus.ref)
                if row is not None and row.text is not None:
                    sources.append(Source(("message", str(row.message_id)), "record", state.unit,
                                          (row.person_id,) if row.person_id else (), row.text))
                    # `records.load` answers TODAY's projected source — its
                    # text/reply/media fold can postdate the evidence instant
                    # and would silently overwrite the as-of Source above on
                    # dedup. A historical session takes the grounding's as-of
                    # source alone (same ceiling as media: no as-of fold
                    # exists to offer instead).
                    loaded = None if state.historical else await _load_message(
                        self.records, sid, row, state, at)
                    if loaded is not None:
                        loaded_items.append(loaded)
                        sources.extend(loaded.sources)
                        rows.extend({**r, "source_key": loaded.snapshot.get("source_keys", {}).get(
                            str(r["id"]), ["message", str(r["id"])])}
                            for r in loaded.snapshot.get("messages", [])
                            + loaded.snapshot.get("context", []))
                    rows.append({"id": row.message_id, "body": row.text, "ts": row.at_ms,
                                 "person_id": row.person_id, "author": row.author})
                elif stimulus.kind == "message":
                    continue
                elif stimulus.kind in ("social", "play"):
                    kind, _, ref = stimulus.ref.partition(":")
                    work = WorkItem(f"stimulus:{sid}", (kind, ref), state.unit, "", at,
                                    historical=state.historical)
                    try:
                        loaded = await self.records.load(work)
                    except WithheldSourceError:
                        continue  # gone or hidden: never read, never cited
                    loaded_items.append(loaded)
                    sources.extend(loaded.sources)
                    rows.extend({**r, "source_key": loaded.snapshot.get("source_keys", {}).get(
                        str(r["id"]), ["message", str(r["id"])])}
                        for r in loaded.snapshot.get("messages", [])
                        + loaded.snapshot.get("context", []))
                else:
                    line = grounding.rollups.get(stimulus.ref)
                    text = json.dumps(asdict(line), default=str) if line else stimulus.ref
                    source_persons = (stimulus.person_id,) if stimulus.person_id else ()
                    if stimulus.kind == "time" and stimulus.item.startswith(CATCH_UP_PREFIX):
                        selected = json.loads(stimulus.item.removeprefix(CATCH_UP_PREFIX))
                        if selected["unit"] != state.unit:
                            raise ValueError("catch-up selection is outside the unit")
                        text = stimulus.item
                        source_persons = tuple(sorted({
                            person for relation in selected["relations"]
                            for person in relation["persons"]}))
                        anchors.extend(
                            (str(key[0]), str(key[1]))
                            for relation in selected["relations"]
                            for key in (relation["src"], relation["dst"]))
                    sources.append(Source((stimulus.kind, str(sid)), "rollup", state.unit,
                                          source_persons, text))
                stimulus_inputs[str(sid)] = hashlib.sha256(json.dumps(
                    [state.unit, stimulus.kind, stimulus.item, stimulus.ref,
                     stimulus.at_ms, _source_payload(sources[start:])],
                    sort_keys=True).encode()).hexdigest()
            sources = list({s.key: s for s in sources}.values())
            original_rows: dict[Key, Any] = {}
            for r in rows:
                key = tuple(r.get("source_key", ["message", str(r["id"])]))
                original_rows[key] = {**original_rows.get(key, {}), **r}
            rows = []
            for source in sources:
                if source.layer != "record" or scan_identifiers(
                        source.text, content_checks=self.content_checks):
                    continue
                origin = original_rows.get(source.key, {})
                if source.key[0] == "media":
                    parent = ("message", source.key[1].partition(":")[0])
                    origin = original_rows.get(parent, {})
                if source.key[0] == "social":
                    loaded = next(v for v in loaded_items if source in v.sources)
                    stamp = (loaded.snapshot.get("metadata") or {}).get("taken_at")
                    if isinstance(stamp, str):
                        stamp = int(datetime.fromisoformat(stamp).timestamp() * 1000)
                    origin = {"ts": stamp, "person_id": loaded.author}
                if not isinstance(origin.get("ts"), int):
                    continue  # no assertion clock: do not invent one for a reading
                rows.append({**origin, "source_key": list(source.key), "body": source.text,
                             "mentions": origin.get("mentions", []),
                             "author": origin.get("author") or origin.get("person_id")})
            digest = hashlib.sha256(json.dumps(_source_payload(sources),
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
                                  author=next((str(r["person_id"]) for r in rows
                                               if r.get("person_id")), ""),
                                  snapshot={"messages": [], "context": [],
                                            "record_rows": rows, "source_keys": {},
                                            "stimulus_inputs": stimulus_inputs,
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
        persons = frozenset(
            p for item in state.memory_items.values()
            for s in item.sources for p in s.persons)
        seeds = tuple(dict.fromkeys(anchors))
        graph = await self.graph.walk(seeds, Walk(unit=state.unit, persons=persons, depth=2,
                                                # Unversioned heads are withheld below.
                                                layers=("record", "rollup", "notebook"),
                                                valid_at=at,
                                                known_at=at if state.historical else None))
        if graph.status == "unavailable":
            raise RuntimeError("wake graph unavailable")
        if state.historical:
            graph = historical_graph(graph, at)
        candidates: list[Subgraph] = []
        for item in state.memory_items.values():
            candidates.extend(await resolution_candidates(
                self.graph, item, aliases, seeds, item_window(item.text, at)))
        if state.historical:
            candidates = [historical_graph(candidate, at) for candidate in candidates]
        # `shown_keys`/`shown_relations` are the renderer's own emitted-rows
        # provenance: a row the budget omits was never shown, whatever the walk
        # returned. Emitted edge ids are the only targets a correction may name.
        shown_keys: set[Key] = set()
        shown_relations: set[str] = set()
        state.graph_text = preview_text(
            Preview(seeds, graph, tuple(candidates), (at, at)),
            shown_keys=shown_keys,
            shown_relations=shown_relations,
            statements=agenda_format == "D")
        if scan_identifiers(state.graph_text, content_checks=self.content_checks):
            # A withheld block contributes nothing: its keys and its correction
            # catalogue are withheld with it.
            state.graph_text = ""
            shown_keys.clear()
            shown_relations.clear()
            self.metrics.inc("nora_errors_total", stage="wake_prefetch")
        state.shown_keys.update(shown_keys)
        state.shown_relations = shown_relations

    def register_shown(self, state: Any) -> None:
        """The citable graph sources are exactly the rows the agenda emitted:
        `render_agenda` has rewritten each item's `source_keys` to the rows it
        actually rendered (after clipping/dropping); only those hydrate, and
        with the emitted body — a tail the budget clipped was never shown.
        Code keeps the Source's key/layer/unit/persons; only a later explicit
        tool read may grant a larger snapshot."""
        for item in state.memory_items.values():
            emitted = {
                str(r["id"]): r["body"]
                for r in item.snapshot.get("messages", [])
            }
            by_key = {s.key: s for s in item.sources}
            for n, key in item.snapshot.get("source_keys", {}).items():
                source = by_key.get(tuple(key))
                if source is not None:
                    state.graph_fetched.show(
                        replace(source, text=emitted.get(n, source.text)))

    async def propose(
        self, state: Any, *, items: list[int], statements: list[Any],
        nothing_here: Iterable[int] = (),
    ) -> dict[str, Any]:
        # `memory_items` is what `prepare` built; `probe` never prepares.
        if not items or any(i not in state.memory_items for i in items):
            return {"status": "refused", "admitted": [], "statements": [],
                    "refused": [{"reason": "no_such_item"}]}
        if any(i not in state.memory_items for i in nothing_here):
            return {"status": "refused", "admitted": [], "statements": [],
                    "refused": [{"reason": "no_such_item"}]}
        # Statements are validated individually at the admission boundary:
        # one malformed entry takes only its own slot, never a sibling's.
        valid: list[Statement] = []
        indices: list[int] = []
        pre_refused: dict[int, dict[str, Any]] = {}
        for i, raw in enumerate(statements):
            try:
                valid.append(_STATEMENT.validate_python(raw))
                indices.append(i)
            except ValidationError as error:
                pre_refused[i] = {"status": "refused", "kind": "schema",
                                  "reason": "schema",
                                  "errors": _shape_errors(error)}
        output = Proposal(statements=valid)
        # Each proposal has one evidence scope. A multi-item proposal is validated
        # against their union, then consumes only the explicitly named items.
        inputs = [state.memory_items[i] for i in items]
        base = inputs[0]
        from dataclasses import replace
        combined = replace(base, sources=tuple(s for v in inputs for s in v.sources), snapshot={
            "messages": [r for v in inputs for r in v.snapshot["messages"]], "context": [],
            "source_keys": {k: source for v in inputs
                            for k, source in v.snapshot.get("source_keys", {}).items()},
            "completion": [
                {"work_id": v.work.id, "input_hash": v.work.input_hash,
                 "inputs": v.snapshot.get("stimulus_inputs", {})}
                for v in inputs],
        })
        combined = replace(combined, work=replace(combined.work, input_hash=hashlib.sha256(
            json.dumps(_source_payload(combined.sources),
                       sort_keys=True).encode()).hexdigest()))
        # `shown_persons` is emitted-row authorship, never a source's scope:
        # every item's rendered rows (a clipped or withheld row contributes
        # nothing) plus the rows the read tools handed out (`on_rows`).
        scope = proposal_scope(
            inputs, state.graph_fetched,
            shown_keys=getattr(state, "shown_keys", frozenset()),
            shown_relations=getattr(state, "shown_relations", frozenset()),
            shown_persons={
                str(r["person_id"])
                for item in state.memory_items.values()
                for r in item.snapshot.get("messages", ())
                if r.get("person_id")
            } | set(getattr(state, "shown_persons", ())),
            admitted=set(getattr(state, "admitted_relations", ())))
        result, local_refusals, aux = await self.admission._admit(
            combined.work, output, state.graph_fetched, state.session_id, scope,
            indices)
        report: dict[int, dict[str, Any]] = aux["report"]
        cited_items: dict[int, set[int]] = aux["cited_items"]
        claims: list[dict[str, Any]] = []
        claim_of: dict[int, dict[str, Any]] = {}
        for index, claim in aux["claims"].items():
            claim_valid, drops = partition_claims(
                {"claims": [claim]}, combined.snapshot)
            if drops:
                await self.admission._refuse(state.session_id, "reading", drops[0])
                report[index] = {"status": "refused", "kind": "reading",
                                 "reason": drops[0]}
            else:
                claims.extend(claim_valid)
                claim_of[index] = claim_valid[0]
                report[index] = {"status": "saved", "kind": "reading"}
        # Claims persist before coverage is decided: an input is done only
        # when its saved statement's claim is actually stored (the writer
        # merges additional claims idempotently for the same input), or the
        # model explicitly declared it nothing_here.
        persisted = (await self.records.readings(
            combined.work, state.session_id,
            replace(combined, snapshot={**combined.snapshot, "completion": []}),
            claims) or []) if claims else []
        covered = set(nothing_here)
        for index, entry in report.items():
            if entry["status"] != "saved":
                continue
            if index in claim_of and claim_of[index] not in persisted:
                continue
            covered.update(items[i] for i in cited_items.get(index, set()))
        admitted = sorted(covered)
        refused = [*[asdict(r) for r in result.refused], *local_refusals,
                   *({"kind": "schema", "reason": "schema"}
                     for _i in pre_refused)]
        if admitted:
            record_item = replace(combined, snapshot={
                **combined.snapshot,
                "completion": [
                    {"work_id": v.work.id, "input_hash": v.work.input_hash,
                     "inputs": v.snapshot.get("stimulus_inputs", {})}
                    for v in (state.memory_items[i] for i in admitted)],
            })
            await self.records.readings(
                combined.work, state.session_id, record_item, [])
            state.admitted_indices.update(admitted)
        seeds = tuple(dict.fromkeys(k for r in result.relations for k in (r.src, r.dst)))
        graph = await self.graph.walk(seeds, Walk(unit=state.unit, depth=1,
            persons=frozenset(p for s in combined.sources for p in s.persons),
            valid_at=combined.work.as_of,
            known_at=combined.work.as_of if state.historical else None))
        if graph.status == "unavailable":
            raise RuntimeError("connected graph unavailable")
        if state.historical:
            graph = historical_graph(graph, combined.work.as_of)
        state.shown_keys.update(n.key for n in graph.nodes)
        state.shown_keys.update(
            k for e in graph.relations for k in (e.relation.src, e.relation.dst))
        state.shown_keys.update(k for r in result.relations for k in (r.src, r.dst))
        # Relations the connected walk actually returned were shown to the
        # session: they become correction-eligible alongside preview rows.
        if getattr(state, "shown_relations", None) is None:
            state.shown_relations = set()
        state.shown_relations.update(e.relation.id for e in graph.relations)
        state.admitted_relations.update({r.id: r for r in result.relations})
        state.admitted_evidence.extend(result.evidence)
        # Per-statement results: saved, or refused with ONE primary reason;
        # dependent cascade diagnostics are recorded, never returned.
        statements_report = [
            {"index": i, **(pre_refused.get(i) or report.get(
                i, {"status": "refused", "reason": "ungrounded"}))}
            for i in range(len(statements))
        ]
        primary = [
            {"statement": index, "kind": entry["kind"], "reason": entry["reason"]}
            for index, entry in report.items() if entry["status"] == "refused"
        ] + [
            {"statement": i, "kind": "schema", "reason": "schema"}
            for i in pre_refused
        ] + [
            {"statement": index, **part}
            for index, entry in report.items() if entry["status"] == "saved"
            for part in entry.get("refused_parts", [])
        ]
        committed = [r.id for r in result.relations]
        note = "Read the connected state before deciding whether to act."
        if refused and committed:
            note = "Valid statements were saved (see committed). " + note
        return {"status": "ok" if admitted else "refused",
                "admitted": admitted,
                "open": [i for i in items if i not in covered],
                "statements": statements_report,
                "refused": refused, "primary_refusals": primary,
                "committed": committed,
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


async def _load_message(
    records: Any, sid: int, row: Any, state: Any, at: Any,
) -> Any:
    """`records.load` for one message stimulus — the LIVE path only
    (`prepare` skips it for `state.historical`; see the call site). A
    projection miss is ordinary (not yet projected): the grounding row's
    own text still stands."""
    try:
        return await records.load(WorkItem(
            f"stimulus:{sid}", ("message", str(row.message_id)),
            state.unit, "", at, historical=state.historical))
    except NoResultFound:
        return None
