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
    ATTRIBUTE_PART_REPAIR,
    REFUSAL_REPAIR,
    ComprehendItem,
    Preview,
    WithheldSourceError,
    item_window,
    preview_seeds,
    resolution_candidates,
)
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
                 private_names: tuple[str, ...] = (), content_checks: bool = True) -> None:
        self.metrics, self.content_checks = metrics, content_checks
        self.private_names = private_names
        self.graph, self.records = graph, records
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

    async def completed(self, state: Any) -> None:
        """Record the inputs of a normally ended historical session for resume."""
        await self.records.complete_session(state.unit, state.session_id, {
            sid: digest for item in state.memory_items.values()
            for sid, digest in item.snapshot.get("stimulus_inputs", {}).items()
        })

    async def prepare(self, state: Any, stimuli: Any, grounding: Any) -> None:
        """Freeze exactly what the agenda shows, including each non-message source."""
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
                # A reaction's actor does not author its target's words.
                if row is not None and row.kind != "reaction" and row.text is not None:
                    sources.append(Source(("message", str(row.message_id)), "record", state.unit,
                                          (row.person_id,) if row.person_id else (), row.text))
                    # `records.load` answers TODAY's projected source — its fold
                    # can postdate the evidence instant and would silently
                    # overwrite the as-of Source on dedup; a historical session
                    # takes the grounding's as-of source alone (same ceiling).
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
            item = ComprehendItem(work, tuple(sources), agenda.room.platform,
                                  agenda.room.conversation_id,
                                  author=next((str(r["person_id"]) for r in rows
                                               if r.get("person_id")), ""),
                                  snapshot={"messages": [], "context": [],
                                            "record_rows": rows, "source_keys": {},
                                            "stimulus_inputs": stimulus_inputs})
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
            shown_relations=shown_relations)
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

    async def propose(self, state: Any, *, statements: list[Any]) -> dict[str, Any]:
        if not state.memory_items:
            return {"status": "unavailable", "statements": [], "note": "No shown input."}
        from nora.obs.tracing import eval_capture_enabled, record_eval_event
        if eval_capture_enabled():
            catalogue = proposal_scope(
                list(state.memory_items.values()), state.graph_fetched).catalogue
            record_eval_event("nora.proposal.sources", {"body": json.dumps({
                str(n): {"key": list(key), "text": state.graph_fetched.sources[key].text}
                for n, key in catalogue.items() if key in state.graph_fetched.sources
            })})
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
        # Every statement is validated against the session's shown evidence.
        inputs = list(state.memory_items.values())
        base = inputs[0]
        from dataclasses import replace
        combined = replace(base, sources=tuple(s for v in inputs for s in v.sources), snapshot={
            "messages": [r for v in inputs for r in v.snapshot["messages"]], "context": [],
            "source_keys": {k: source for v in inputs
                            for k, source in v.snapshot.get("source_keys", {}).items()},
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
            indices, confirmed=getattr(state, "possible_existing", frozenset()))
        report: dict[int, dict[str, Any]] = aux["report"]
        claims: list[dict[str, Any]] = []
        for index, claim in aux["claims"].items():
            claim_valid, drops = partition_claims(
                {"claims": [claim]}, combined.snapshot)
            if drops:
                await self.admission._refuse(state.session_id, "reading", drops[0])
                local_refusals.append({"kind": "reading", "reason": drops[0]})
                report[index] = {"status": "refused", "kind": "reading",
                                 "reason": drops[0]}
            else:
                claims.extend(claim_valid)
                report[index] = {"status": "saved", "kind": "reading"}
        if claims:
            await self.records.readings(combined.work, state.session_id, combined, claims)
        refused = [*[asdict(r) for r in result.refused], *local_refusals,
                   *({"kind": "schema", "reason": "schema"} for _i in pre_refused)]
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
        # Per-statement results: saved, or refused with ONE primary reason,
        # the field it rejected, its repair and the cited rows; dependent
        # cascade diagnostics are recorded, never returned.
        def cite_ids(index: int) -> list[Any]:
            """The cited row ids of one raw statement — only when its `cite`
            is a list of citation objects; a malformed container contributes
            no ids and never breaks the report the saved siblings earned."""
            raw = statements[index] if 0 <= index < len(statements) else None
            if not isinstance(raw, dict):
                return []
            cites = raw.get("cite")
            if not isinstance(cites, (list, tuple)):
                return []
            return [c["source"] for c in cites
                    if isinstance(c, dict) and "source" in c]

        def repair_of(entry: dict[str, Any]) -> dict[str, str]:
            # An attribute part gets ONE generic, true message — the admission
            # result cannot say reliably whether the attribute saved, so the
            # message claims nothing about it. It keeps its own attribute
            # name and cite ids.
            if entry.get("attribute"):
                return {
                    "field": f"attributes.{entry['attribute']}",
                    "repair": ATTRIBUTE_PART_REPAIR.format(
                        reason=entry.get("reason", "")),
                }
            spec = REFUSAL_REPAIR.get(entry.get("reason", ""))
            if spec is None:
                return {}
            return {"field": entry.get("detail") or spec["field"],
                    "repair": spec["repair"]}

        statements_report = []
        for i in range(len(statements)):
            entry = dict(pre_refused.get(i) or report.get(
                i, {"status": "refused", "reason": "ungrounded"}))
            out = {"index": i, **entry}
            if entry.get("status") == "refused":
                out.update(repair_of(entry))
                out["cite"] = entry.get("cite", cite_ids(i))
            if entry.get("refused_parts"):
                out["refused_parts"] = [
                    {**part, **repair_of(part),
                     "cite": part.get("cite", cite_ids(i))}
                    for part in entry["refused_parts"]
                ]
            statements_report.append(out)
        primary = [
            {"statement": index, "kind": entry["kind"], "reason": entry["reason"],
             **({"part": entry["part"]} if entry.get("part") else {}),
             **({"attribute": entry["attribute"]}
                if entry.get("attribute") else {}),
             **repair_of(entry),
             "cite": entry.get("cite", cite_ids(index))}
            for index, entry in report.items() if entry["status"] == "refused"
        ] + [
            {"statement": i, "kind": "schema", "reason": "schema",
             **repair_of({"reason": "schema"}), "cite": cite_ids(i)}
            for i in pre_refused
        ] + [
            {"statement": index, **part, **repair_of(part),
             "cite": part.get("cite", cite_ids(index))}
            for index, entry in report.items() if entry["status"] == "saved"
            for part in entry.get("refused_parts", [])
        ]
        committed = [r.id for r in result.relations]
        note = "Read the connected state before deciding whether to act."
        if refused and committed:
            note = "Valid statements were saved (see committed). " + note
        # What a returned `possible_existing` card showed is shown state —
        # the ONLY keys and relation ids registered are the ones the cards
        # emit; the refusal itself is what `new.distinct` may later confirm.
        # Registered here, after every fallible step, so a call that raises
        # returns no cards and grants nothing.
        state.shown_keys.update(aux.get("candidate_keys", ()))
        if aux.get("candidate_relations"):
            if getattr(state, "shown_relations", None) is None:
                state.shown_relations = set()
            state.shown_relations.update(aux["candidate_relations"])
        prior = getattr(state, "possible_existing", None)
        if prior is None:
            state.possible_existing = prior = set()
        prior.update(aux.get("possible_existing", ()))
        return {"status": "ok" if not refused or any(
                    row["status"] == "saved" for row in statements_report) else "refused",
                "statements": statements_report,
                "refused": refused, "primary_refusals": primary,
                "committed": committed,
                "connected": json.loads(json.dumps(asdict(graph), default=str)),
                "note": note}


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
