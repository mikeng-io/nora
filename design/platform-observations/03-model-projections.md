# Model projections and neutral tools — proposed

One scoped read result supplies transcript annotations, a declared social-context
block and detail tools. Existing context assembly owns all rendering. The graph
retrieval repairs in unified-memory 27 §17 remain prerequisites for consistent
connected understanding; this bundle does not create another retrieval service.

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

## Transcript and context contract

Actual utterances/shares remain speaker lines. The target's annotation can include
reply/quote, edit/withdrawal and response state; poll creation can anchor an
utterance. Never fabricate speaker lines for votes/reactions/membership.

The new declared social-context block covers material off-window updates to
referenced objects, active polls and relevant room changes. Its typed input has:

- objects: bounded state summaries using the shared read model;
- changes: source-supported transition summaries and their time window;
- coverage: source gaps and omitted detail/more references;
- related matters: handles into the existing graph preview, not a second copy of
  Notebook prose or an independent truth inferred by the renderer.

Selection starts from actual message/reply/mention/shared-object references, the
current room and existing open matters within scope. New admitted observations
remain discoverable even if no existing matter references them. Curated graph
context informs Cognition's attention; an ETL keyword classifier must not decide
which unknown patterns can ever be discovered.

Preserve existing prompt budgets. Share a per-assembly rendered-object set across
transcript/context renderers: annotate visible targets once, then use references
for off-window changes. Keep kind, time, source/interpretation label, open/closed
state and coverage before optional details. If material objects exceed the budget,
mark omitted detail and provide bounded read handles; never silently truncate a
poll's options into an apparently complete choice. Exact token allocation is
measured with the existing assembler at implementation time.

Illustrative **proposed**, generated render (handles are placeholders):

```text
[social context · observed 09:10 · more available]
poll P1 on message M1: closed; option O1 "A" count 3, O2 "B" count 2
voter identities: unavailable (anonymous); unique-voter total: unknown
source coverage: latest snapshot complete for option counts only
related ongoing matter: E1 (Notebook judgement; read graph evidence)
```

It does not mean five attendees, or that the literal poll title defines E1.
A missing vote is not an absence fact. New source state is assembled each turn;
no persisted pre-rendered context block remains authoritative after a close/edit.

## Tool groups and proposed signatures

Reuse existing verbs for transcript/Record evidence, graph understanding, room
membership and media/social content. Add only capabilities they cannot express.
No platform-specific model workflow and no universal SQL/query/execute tool.

| Task | Surface and bounded arguments | Result |
|---|---|---|
| Connected account/evidence | Existing memory_search/read | Scoped graph preview/full fields and supporting/contradicting evidence |
| Conversation/observation history | Existing Record/transcript reads extended with typed target refs/time/cursor | Actual source observations, not guessed transitions |
| Room/member state | Existing room/member reads | Supported current scope/roles/coverage, not inferred channel audience |
| Collective-choice discovery | Proposed poll_list(scope=server-bound current room, state, limit, cursor) | Poll handles, questions, state/as_of/coverage |
| Collective-choice detail | Proposed poll_read(handle, view=state or history, limit, cursor) | Definition/options, supported counts/voters or bounded source history |

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
