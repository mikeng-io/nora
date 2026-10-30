---
type: design
title: "Platform observations: social evidence from capture to Nora's context"
description: "Operator-requested design for message metadata and missing social observations: ontology, capability gaps, Record/state/Rollup/Notebook ownership, incremental processing, shared automatic/tool projections, and acceptance. No runtime change."
timestamp: '2026-10-30'
edges:
  - {dst: 27-the-memory-is-a-graph, rel: extends, provenance: operator-requested}
  - {dst: 29-one-cognition-session, rel: extends, provenance: operator-requested}
  - {dst: 31-build-one-session, rel: informs, provenance: operator-requested}
  - {dst: ../platform-bridges/02-wire-and-platforms.md, rel: amends, provenance: proposed}
---

# 32 · Platform observations: capture, memory and model exposure

Status: **design amendment requested by the operator, 2026-10-30**. This document
owns the proposed social-data contract and acceptance; no collector, wire version,
table, tool, model occasion or live flag is changed by this document. Exact new
field/table/tool signatures are resolved against the implementation when built.

The operator asks for two connected deliverables: the actual memory-graph findings
in [27 §17](27-the-memory-is-a-graph.md#17-implementation-findings-and-longitudinal-understanding-2026-10-30),
and a complete path for useful platform metadata/data that Nora does not yet
collect or see. Typing indicators are explicitly unwanted. Adding Telegram should
add mappings and coverage, not a new platform-specific cognition workflow.

## 1. Owners and boundaries

[Platform bridges](../platform-bridges/00-contract.md) own transport capture,
recovery, ordering and delivery. Their current proposed inbound kind union names
message/edit/delete/reaction/summons and serializes NormalizedMessage. Non-message
observations below require an explicit typed domain/wire extension; do not smuggle
poll votes or membership changes through empty messages. Keep existing transport
choices; this document does not introduce a second bus or collector.

[The memory map](15-memory-map.md) owns Record, deterministic Rollup and interpreted
Notebook. [27](27-the-memory-is-a-graph.md) owns graph links/evidence; [29](29-one-cognition-session.md)
owns interpretation and action in the unified session. Existing context assembly
owns every model block. Reuse the existing Postgres stores, stimulus machinery,
media custody/perception, graph read service and tools before adding anything.

## 2. Ontology: objects, observations and interpretations

Use object kind and observation operation as independent dimensions. A message,
reaction, poll, room, membership, pin or shared artifact is an object. Created,
revised, withdrawn, added/removed transitions and observed snapshots describe
what the source reported. A snapshot is not a fabricated sequence of transitions.
An anonymous count has no inferred actor. An unexplained member departure has no
inferred intent. Actor, subject, target and source room remain distinct references.

| Group | Source evidence | Transcript / automatic context | Detail reads |
|---|---|---|---|
| Utterances and shares | Body, author, reply/quote, mentions, edit/withdrawal, shared object | Speaker lines plus structural annotations and existing media reading | Existing transcript/Record reads |
| Responses | Reaction transitions or replacement set; poll choice/vote changes or aggregate snapshot | Current target annotation; material off-window changes in context | Target response history; poll state/options/coverage |
| Collective choice | Poll definition, options, multiple-choice/anonymous flags, close/change state | Poll creation anchors conversation; current tally and source status | Bounded poll detail and supported voter data |
| Room state | Title/topic, membership, roles, permissions, pins, thread lifecycle | Relevant changes and current state with as-of | Existing room/member reads; bounded history when needed |
| Shared artifacts | Files, posts, reels, contacts or location shares when supported/admitted | Typed share and available reading; distinguish metadata, bytes and interpretation | Existing media/social/Record reads |
| Coverage | Capability, collection gap, snapshot completeness, freshness | State limitations beside the affected claim | Same coverage fields on all reads |
| Ephemeral signals | Typing | Ignore; do not persist, inject or wake on it | None |

Do not expand read receipts/online indicators into stored social evidence without
a concrete required journey. Existing presence capabilities remain independent.
A reaction can be observed without being interpreted as agreement; a vote is not
attendance; metadata is evidence only for the property it actually measures.

## 3. Locate every visibility gap

Platform support, configured account permission, bridge capture, app persistence,
projection and model exposure are different states. Maintain a source-verified
coverage table for each supported object/field, scoped to platform/account and
conversation mode (for example, group versus DM). It records:

- Source capability and permission prerequisites, including history/backfill limits.
- Capture mapping and fixture proving what the adapter emits.
- Durable Record owner and target/source reference mapping.
- Current-state/Rollup/graph projection and freshness/coverage semantics.
- Transcript/context/tool exposure and a named acceptance journey.

Mark each stage supported, unsupported, not implemented, disabled or unavailable
with its reason and inspected revision. Do not collapse these into one boolean.
The account's last successful observation time is separate from static capability.
This can start as the implementation's design/source coverage table; no standalone
capability service or second configuration system is required.

The earlier platform audit identified candidate gaps in polls, room details,
forward/embed metadata, media custody and update/history handling across Signal,
Discord, WhatsApp and Instagram. Those are an investigation queue, not a current
API-support promise. Re-check each against the receiving session's exact bridge
head and upstream documentation before marking it supported or impossible.
Telegram starts with the same table, including bot/client account mode and granted
updates; do not infer capabilities from the platform name alone.

### Earlier implementation audit: starting inventory

The following findings belong to the earlier source snapshot
`213d9f860` (2026-10-30), before the ongoing bridge work. They are historical
implementation observations, not upstream API limits or claims about the deployed
system. Revalidate them against the implementation head before selecting work.

| Platform | Observed gap / existing coverage | Stage to verify first |
|---|---|---|
| All four audited platforms | Reactions were parsed and target-linked but not ordinary transcript lines; downstream stimuli were conditional | Current-state projection, wake policy and automatic/tool exposure |
| Signal | DM input was dropped in the audited path; group metadata/poll coverage was not established by that audit | Admission/capture scope and object/field mapping; unknown is not unsupported |
| Discord | DM input was dropped; poll/embed/forward content reached message markers rather than a full typed object contract | Capture semantics and preservation through Record/rendering |
| WhatsApp | Poll/location/contact payloads were dropped; roster was not wired; media metadata existed without a downloader in main composition | Capture and media custody first, then projection/exposure |
| WhatsApp recovery | Notify-only input and a bounded 500-event buffer did not establish complete historical capture | Existing bridge recovery contract and explicit coverage gaps |
| Instagram | One attachment per DM was retained; receipt/group-change coverage had gaps; typing was discarded | Attachment multiplicity and room-state capture; keep typing ignored |
| Telegram | Not part of that implementation audit | Establish account-mode/permission coverage before adoption |

This inventory prevents the source-capture work disappearing behind graph-only
repairs. It is not a substitute for the stage-by-stage acceptance table above.

Classify the remedy by the missing stage: capture missing -> extend the existing
adapter; persisted but unprojected -> add the deterministic fold/link; projected
but invisible -> repair context/tool reads. Never solve all three by dumping raw
payloads into a model prompt. Unknown coverage is not zero reactions, zero votes,
absence, abstention or a negative social judgement.

## 4. Persistence: domain evidence, state, logs and traces

Use the current domain stores where they express the object; extend only missing
stores (for example poll definitions/options and vote/snapshot observations).
Avoid a universal JSON/EAV event table or a separate social-context database.
A private raw payload may be retained only for a supported reconstruction/debugging
need under existing admission, retention and deletion rules.

A normalized durable observation needs scoped source/object identity, target,
nullable actor with a reason when unavailable, source occurrence time when known,
receipt time, revision/transition identity or snapshot semantics, provenance and
field-level coverage. Preserve references needed for evidence reads. Source
identity is not a model-facing identifier or a trace ID.

| Representation | Owns | Does not establish |
|---|---|---|
| Bridge journal / delivery record | Durable handoff and replay identity | Social meaning or permanent application memory |
| Record observation | What the source actually reported, with correction/revision provenance | The speaker's sincerity or what later happened |
| Current-state projection | Rebuildable reaction set, poll tally, membership/pins | Unobserved intervening transitions or unknown voters |
| Rollup | Deterministic episodes/counts with time range and coverage | Group intent, commitment, attendance or reliability |
| Notebook + graph evidence | Revisable social account, relationships, conventions and ongoing matters | An independent second observation of its own conclusion |
| Log / trace / metric | Diagnostics, causality, outcomes and cost | Durable social evidence or complete source history |

Traces can be sampled or unavailable; application correctness and model evidence
must not require them. Carry the existing opaque event/work/observation references
through processing so a trace can join to authorized domain evidence. Preserve
producer/consumer trace propagation under the bridge design, without treating
traceparent as identity or storing private message payloads in telemetry. Use
existing observability conventions: counters for outcome/cost, bounded structured
logs for failures, generation-bearing traces for model work. No new trace pipeline.

## 5. Incremental processing and rollup

Normalize once at the existing platform boundary. The app resolves identity and
current policy, writes admitted Record observations and advances cheap state in
the current transaction boundary. Register required durable downstream stimulus
work there where the stores share ownership. Reuse bridge ACK/recovery rules;
media perception and model work remain in their existing downstream pipeline.

A deterministic fold follows source semantics: deltas update a set; complete
snapshots replace only their covered scope; partial snapshots cannot erase unknown
members/voters. Order by source revision/transition information, retaining arrival
order separately. A late event causes bounded affected-object reconstruction or a
visible unresolved state; naive wall-clock last-write-wins is insufficient.
Maintain enough source/version/watermark information to compare incremental state
with a full rebuild. Duplicate/replayed capture must not inflate counts.

Rollups summarize a bounded period or episode and retain coverage, references and
the relevant participants/objects. Do not build every possible aggregate first.
Graph projection connects Record and Rollup objects through the existing generic
entity/relation/evidence contract. New object/reference vocabulary is extended
coherently at its owner, not via a schema-free side channel.

Store, wake and inject are separate choices. Useful response changes can be cheaply
recorded without a model call. Existing wake rules coalesce stimuli; a material
change to an open matter can justify attention. Do not wake once per reaction.
Admitted new evidence remains available even when current Notebook understanding
does not predict its relevance.

## 6. What reaches Nora: automatic context and neutral tools

Both surfaces read the same scoped, current projection and graph semantics.
[27 §17](27-the-memory-is-a-graph.md#17-implementation-findings-and-longitudinal-understanding-2026-10-30)
F1/F2 must be addressed: richer data is ineffective if structural anchors disappear
or automatic recall walks different relations from the explicit graph tools.

1. **Transcript:** preserve actual utterances/shares as speaker lines. Annotate the
   target message with supported reactions, reply/quote, edit/withdrawal and poll
   state. A reaction/vote/member update is not a new utterance by a person.
2. **Declared context block:** through existing context assembly, show relevant
   off-window target changes, referenced active polls and meaningful room changes.
   Include object reference, as-of, state/version and coverage. Reuse graph context
   for curated understanding and evidence; distinguish it from source tally/state.
   Deduplicate against transcript. Reassemble from current heads each turn so a
   closed poll is not rendered from a stale open summary. Required but unavailable
   state must carry its limitation.
3. **Task-shaped tools:** retain memory_search/read for connected understanding;
   transcript/Record tools for source evidence; room/member tools for group state;
   media/social tools for artifact content. Add poll list/read only if existing
   verbs cannot express collective-choice detail coherently. Group by task/object,
   not platform, and avoid one universal execute/query tool. Exact signatures and
   routing evidence go through the existing tool procedure when implemented.

Tools expose the same state/version/coverage/references as automatic context, with
bounded history and explicit partial results. ok/nothing_found/unavailable remain
separate; an unavailable field does not turn into a negative fact. Resolve opaque
handles within server-bound room/unit scope on every read; model arguments do not
widen access. No new write/action authority is granted here.

Notebook curation supplies the longer account: prior relationships, conventions,
expectations and the underlying ongoing matter. It can connect a literal breakfast
poll to another activity, or support attendance expectations beyond explicit voters.
Preserve poll facts and interpreted expectations separately. Cross-platform passive
observations may inform this account only through already-authorized evidence
reads; this document enables no new location polling or unsolicited story viewing.

## 7. Acceptance and delivery boundary

Use generated protocol/code fixtures for deterministic guarantees and the existing
recorded-wake/held-out evaluation rules for model judgement. These requirements
are not evidence that the implementation or model has passed them:

- Replay a reaction add/remove/add sequence and a late edit; duplicate delivery
  preserves counts and references. Incremental state equals supported full rebuild.
- Reconcile complete and partial poll snapshots; anonymous totals never invent
  voter identities or abstention. An unavailable source stays visibly unavailable.
- Update a target outside the transcript window and close its poll. The next context
  shows the current version once, tools agree, and no fake speaker line appears.
- Follow a permitted room-state/artifact update through capture, persistence,
  projection and both read surfaces. Missing bytes and unknown membership retain
  their limitation. Typing produces no stored evidence, context or model work.
- Prove scope at retrieval and evidence reads; an object handle does not bypass it.
  Transport replay and trace sampling do not change stored domain state.
- Across anchored histories, retrieve the relevant curated account, associate an
  arrangement with its underlying matter, then revise an expectation when independent
  counterevidence arrives. Inspect the account and later assembled turn/answer.

Implementation should fill the per-stage coverage table and prove one complete
useful journey through the existing owners before broadening object coverage.
The graph/session work and observation-capture work can be owned separately, but
join on this typed evidence/projection contract. Neither can claim completion from
its local write alone. Delivery status and receipts belong with implementation;
this document owns architecture and acceptance.

This design holds unless source/contract evidence shows an existing owner already
provides the required semantics, or a measured journey shows a proposed projection
adds no useful context. Reuse that owner or omit that projection; do not duplicate it.
