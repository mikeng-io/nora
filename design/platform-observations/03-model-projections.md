# Model projections and neutral tools — proposed

One scoped read result supplies enriched transcript entries, source-backed state
in the existing Standing presentation, and detail tools. Existing context assembly
owns all rendering. The graph retrieval repairs in unified-memory 27 §17 remain prerequisites for consistent
connected understanding; this bundle does not create another retrieval service.

Recorded 2026-10-31: an independent source audit (head `3fe09ac7`) verified the
F1/F2 MECHANISMS are repaired at that head — `WakeMemory.prepare` restores the
structural anchors and seeds via `preview_seeds`, and automatic prefetch calls
the same `MemorySearch.read`/`graph.walk` retrieval as the tools. That is
source-mechanism evidence, not a live probe or model-quality receipt. One
limitation stands: automatic prefetch and tool reads share the retrieval
mechanism, but the per-turn prefetch cache in `context.py` is local — a shared
cache between automatic and tool reads is not proven at that head.

## Shared read model

Every object result carries a model-safe handle, kind, source scope label, source
state, as_of, projection_revision, coverage and evidence handles. Collections carry
bounded pagination/more and explicit partial reasons. Status stays ok/nothing_found/
unavailable; partial describes readable data, not a fourth success state.

Poll details include question/options, state, supported tally and voter coverage.
Reaction details include the target and source-supported set/counts. Room results
state whether they describe a guild, channel, thread or conversation; permission
visibility and membership are different claims. Missing fields carry reasons rather
than zero/false. Current state and historical as-of reads are labelled separately.

A projection revision is local consistency/version information, not a globally
ordered upstream revision. All fragments for one object in a turn reuse the same
read result. A later tool read can be newer; label it with its own as_of/revision
rather than pretending it is identical to the earlier prompt. Do not hold a DB
transaction open across model calls just to freeze time.

## Transcript-first presentation and Standing continuity

Operator amendment, 2026-11-01: the proposed `social_context` block/name is
withdrawn. Social context is Nora's connected understanding of people, relationships,
conventions and matters; it is not a name for platform metadata. Use **platform
observations** for source-reported events and **current state** for their supported
fold. No replacement block or dedicated poll-list block is introduced by this
amendment. These are proposed presentation requirements, not shipped Standing or
poll behavior. The first recorded-reaction target annotations were delivered by
PR #478; their documented capture/ordering ceilings remain.

The default for admitted message metadata and conversation events is the enriched
transcript. Actual utterances/shares remain speaker lines; reply/quote, edits,
reactions and shared-artifact metadata annotate their target. Poll creation and
room/member/pin/thread changes can be typed platform-event entries, with their
real source identity, target, actor when supplied and time quality. Never fabricate
a human utterance or an actor for a service event. This display contract does not
put every payload in the message table: [02](02-observation-contract.md) retains
typed domain ownership and storage.

A source-reported transition and a snapshot read are different. A snapshot says
what was observed as of a time; it does not invent the intervening events or their
occurrence times. Current state annotated onto an older message carries its own
observation time; it must not look like information available when that message
was originally sent. Historical assembly reads the replay's as-of state.

### Surviving the transcript window

A bounded recent transcript (40 entries in the operator's example, not a new
hard-coded limit) is a presentation window, not retention or the whole working
context. Falling out of that window must not make a still-relevant poll, shared
artifact or room change invisible. Durable Record ownership alone, or a passive
tool nobody knows to call, does not satisfy this visibility requirement.

Use the **existing Standing presentation** for a compact working set of relevant
matters and referenced objects. Standing retains scoped object/matter references
and supported progress; context assembly resolves them through the shared read
model and attaches fresh, bounded source state. This is not another persisted
poll summary, a new free-standing working-set store, or repeated model compression
of tallies into prose. Re-derivable detail remains in its domain store; the
[Standing ledger](../standing-ledger/10-what-standing-holds.md) keeps pointers to
what Record can return. The Standing owner retains its update/render lifecycle;
this bundle specifies observation references and source-state enrichment, not a
parallel Standing writer. Cognition decides and acts; code projects recorded
facts and never declares attendance, agreement or a settled arrangement from a
poll tally or close event.

Selection combines actual message/reply/mention/shared-object references, relevant
room observations and existing ongoing matters within scope. Newly admitted
observations remain discoverable even before Notebook has a matching matter.
Existing stimulus/wake grounding admits them; an ETL keyword classifier or a
requirement for a pre-existing graph link must not permanently hide new evidence.
A new change on an old target enters the recent typed-event view with a scoped
handle and enough target summary to understand it, even if the parent message is
outside the window. This does not require restoring the whole old conversation.

Keep relevant objects while the underlying matter still needs them. A poll's
source open/closed state and the matter's progress are separate fields: closing
the poll cannot automatically settle or drop the matter. Completion, cancellation
or supported loss of current relevance can remove a reference from the rendered
working set without deleting its history. An open poll is not an instruction to
keep it in every prompt forever. Budget omissions, unavailable reads and missing
history remain explicit; no guessed zeros and no claim of lossless model prose.

### Authority, deduplication and budgets

Label source state (tally, membership/pin status, as-of and coverage) separately
from recorded decisions/work progress and curated interpretation. Culture carries
long-term group conventions; Notebook/graph carry revisable relationships and
underlying matters. They are not duplicate stores of live poll counts. Operational
logs/traces never enter this social evidence path.

Deduplicate by scoped object identity across transcript and Standing: render a
full source-state fragment once, and use references elsewhere. Preserve a
`recalled`/graph account of that same object when relevant, labelled as interpreted
understanding rather than a second independent observation. A Standing reference
must not widen room/unit access to the target or its evidence.

Every assembly reads supported current heads; a later tool read can be newer and
carries its own as-of/revision. No DB transaction spans a model call. Budgets bound
both transcript events and Standing enrichment. Keep object/kind, source time,
open/closed or unknown state, coverage and a read handle before optional details.
Collapse noisy transitions into a labelled supported summary where appropriate;
keep the history available. Overflow reports omitted items/details and a scoped
read path, never a silently complete-looking list or a truncated choice set.

Illustrative **proposed**, generated presentation (placeholder handles):

```text
transcript window: recent 40 entries; original poll message is outside it
  09:08 platform event: poll P1 closed; target M1 (Saturday breakfast choice)

standing: Saturday activity arrangement — awaiting confirmation (recorded matter)
  related source object P1 on M1: closed as of 09:10
  option O1 "A" count 3; option O2 "B" count 2
  anonymous: voter identities unavailable; unique-voter total unknown
  coverage: snapshot complete for option counts only
  Nora owes: check transport; result not yet delivered (recorded commitment)
  detail/history: read P1; graph evidence for the underlying matter: E1
```

In an actual assembly the duplicated P1 state is shown once, with references in
other fragments. This is a presentation sketch, not an XML/DDL/tool signature.
It does not mean five attendees or that the literal breakfast question defines E1.

## Tool groups and proposed signatures

Reuse existing verbs for transcript/Record evidence, graph understanding, room
membership and media/social content. Add only capabilities they cannot express.
No platform-specific model workflow and no universal SQL/query/execute tool.

The planned per-tool **Engagement/Cognition allowlist** and the existing
record_query capability boundary are in [05](05-occasion-map.md). It also owns
per-object injection maps, generated examples and the proposed stimulus extension.
Do not treat the shared projection contract as automatic exposure on every occasion.

These signatures specify intent, not registered tools. At implementation follow the
existing tool skill, naming conventions and measured routing. Do not add another
reaction-specific tool when existing target/Record reads can supply the detail.
Opaque cursors bind query/scope and cannot widen server authorization. Validate all
object/evidence reads against current unit and allowed source access, including
when resolving a referenced message outside the visible transcript.

## Attention, interpretation and operational traces

Record useful evidence cheaply, then let existing stimulus coalescing/wake rules
control model occasions. A poll close or changed open matter may need attention;
every reaction need not. Typed stimulus references point to the same observation/
object keys read by tools. Do not add a parallel model-driven ETL loop.

Operational traces are never injected as social evidence. Existing lifecycle
logs/spans can locate capture/normalization/persistence/projection/render failures
through event, observation, attempt and turn references. A source observation can
be absent from a turn because of missing capture, projection, selection or rendering;
record a bounded reason at the relevant existing seam. Aggregate coverage/outcomes
belong in metrics; safe per-event correlation belongs in receipts/logs/traces under
platform-bridges 04. No content/IDs in metric labels or raw payloads in telemetry.
