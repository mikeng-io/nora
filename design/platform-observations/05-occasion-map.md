# Occasion map: automatic input, tools and stimuli

Operator clarification, 2026-10-30. **Proposed implementation contract**, not
currently wired behavior. [03](03-model-projections.md) owns shared projection
semantics; this node owns the two explicit occasion maps and their examples.
[04](04-build-and-acceptance.md) remains the sole acceptance owner.

## Existing capability baseline

Source checked at `bf0b800c42aba8cb7b6cdb009913c53c5aa47f02`:

- `nora/agent/tools.py`, `record_query`: already exists with `start`, optional
  `end`, `scope=this_room|all_rooms`, `author`, `text`. It searches recorded
  messages; `nora/domain/record_query.py`, `RecordHit`, carries reactions,
  latest-edit transcript and delete state. `SqlRecordQuery` is the SQL reader.
- `nora/agent/registry.py`, `_withheld`: no configured Record reader means the
  tool is hidden. `config.example.toml` defaults `[record_query] enabled=false`.
  `nora/app/cognition_wiring.py` includes it in COGNITION_READ_CATALOGUE and
  FLAGGED_READS. Existing code is not evidence of live enablement; live config
  was not read. No flag flip is part of this design.
- The current tool reports `ambiguous` for input/name resolution as well as
  ok/nothing_found/unavailable. Preserve that existing resolution behavior;
  do not confuse it with a fourth data-availability state or silently change
  compatibility while adding observations.
- The current model-facing arguments do **not** include observation kind,
  target handle, history cursor or arbitrary poll/group-event queries. Those
  are proposed extensions, not existing capabilities.
- `room_read` reads another room's messages/thread; it is **not** a room-metadata
  reader. `group_members` reads current-conversation membership. Do not assign
  title/topic/permissions/pin-state queries to room_read by its name.
- Cognition's current catalogue includes record_query, social_read and graph
  reads, but not load_transcript, room_read, group_members, media_fetch or
  instagram_read. A registry addition does not automatically offer a tool to
  Cognition: its read catalogue, audit, scope binding and tests must change.

## List 1: automatic injection by object and occasion

Post means a shared post/reel; poll is a separate collective-choice object.
All inclusion is bounded by the current room/unit, source authorization, relevance
and budget. Collection does not imply that every object is shown on every turn.

| Object | Engagement transcript | Engagement social_context | Cognition agenda / context |
|---|---|---|---|
| Reaction | Annotate visible target with current supported responses, as-of and coverage — SHIPPED as `reactions`/`reactions_observed` attributes | Relevant off-window target/change; no duplicate full annotation | Agenda identifies change/target with the same current-state annotation; social_context supplies state; graph supplies existing account |
| Shared post/reel | Keep the actual share, sharer's words, artifact ref and available reading | Relevant off-window artifact/reading update; cover-only labelled | Existing message/social arrival in agenda; saved artifact content in context; graph connections |
| Poll | Creation message anchors question/options and bounded source state | Relevant off-window poll, closing/change and state; limited voter coverage explicit | Agenda identifies create/vote/close observation; context provides definition/state; interpretation stays Notebook |
| Group event: topic/title, membership/role, pin, thread lifecycle | Retain an actual platform service message as a labelled system event if present; never invent human speech | Relevant room changes/current state with subject, as-of and coverage | Agenda identifies new room observation; context provides relevant state, graph prior understanding |
| Operational trace | None | None as social evidence | None as social evidence; diagnostics use durable correlation refs |
| Typing | None | None | No stimulus or model work |

A system service message and its structured observation can share source identity;
render the event once, not both as a new utterance and a duplicated change.
Ordinary source messages retain original speaker, sequence, reply and media
semantics. Unknown historic metadata stays unknown; never backfill guessed zeros.

## Injection points and data flow

**Engagement:** `nora/engagement/context.py`, `TranscriptInjection.assemble`,
reads a scoped typed observation view while constructing TagContext. Add typed
observation/context fields there; do not let a pure renderer query stores.
Register
`social_context` in `nora/agent/prefill.py`'s `_BLOCKS` and render through assemble.
Perform shared object-read/dedup before rendering. Existing transcript tools use
that same enrichment/annotation contract, not a separate reaction formatter.

Correction recorded 2026-10-31 (independent audit at `3fe09ac7`): an earlier
draft described target annotation as already wired. At that head the only
target annotations anywhere were `replying_to`/`reply_to` and `edited`/`was`;
reaction-on-target annotation is NEW work extending that existing annotation
contract in BOTH renderers (`render_transcript` for the prompt and
`_transcript_messages` for `load_transcript`/`room_read`/`record_query`), not
a baseline to reuse. The first reactions slice shipped it on that contract:
the `message_reaction` rows fold once (`fold_reactions`, the definition
`record_query` established), `TranscriptRow.reactions` carries counts plus
newest recorded source-event time and `recorded` coverage, and the same
annotation reaches the prompt, the transcript tools and the Cognition agenda.
Ceilings kept: counts are recorded transitions only, never the complete
upstream set; no clear/snapshot capture, no per-target transition identity or
source_revision ordering is claimed; first slice covers admitted room modes
only — Signal and Discord drop DM input at parse today, so no DM widening.

The reaction read derives counts, event time and completeness from one SQL
statement snapshot. Concurrent appends cannot mix newer counts with older
provenance. The standing-set transfer is bounded; a target cut short carries
`partial` coverage and no counts. Prompt and agenda annotations budget their
details and mark omissions; transcript tools retain coverage/time and report
omitted details. Existing block field descriptions and tool result notes explain
that recorded transitions do not establish upstream completeness. Custom emoji
identities stay in storage; model surfaces use bounded display labels. Reaction
agenda items distinguish a removal from an addition beside the stimulus time.

**Cognition:** extend grounding for typed observation refs/state before
`CognitionSessionRunner._session_input` assembles the session. Agenda identifies
what changed. Register `social_context` in `nora/language/cognition_render.py`'s
`_BLOCKS`; its state summaries are separate from existing graph/Notebook judgement
and open Register items. Existing reaction/edit/delete agenda rendering is reused
and extended; it does not establish support for new poll/room payloads by itself.

`social_context` is the proposed common name, not a shipped block. Every model
occasion consuming these data must declare its input under existing context
assembly rules. Do not assume relevance/classifier inputs inherit TagContext:
trace each caller and add only the observation fields its judgement actually needs.

Read current heads at assembly time. A later tool result can be newer and carries
its own revision/as-of. Historical replay uses as-of state from the replay sandbox,
not today's state. Missing history is partial/unavailable. No DB transaction stays
open over model calls. Apply [03](03-model-projections.md)'s budget/dedup policy.

### Generated examples, not a live schema or production content

Engagement: M1 is visible; P1's original message is outside this window.

```xml
<group_transcript>
  <m ref="M1" from="Person A" at="09:00">
    Would this activity work for Saturday?
    <shared_post ref="S1" reading="activity introduction; content available"/>
    <reactions observed_at="09:10" coverage="partial">
      thumbs-up count=3; full reactor list unknown
    </reactions>
  </m>
</group_transcript>
<social_context as_of="09:10" projection_revision="example-v2">
  <poll ref="P1" target="M0" state="closed">
    options A=3, B=2; voter identities unavailable (anonymous)
    coverage: option counts complete; unique voter total unknown
  </poll>
  <room_change ref="O8" at="09:05">topic changed; old value unavailable</room_change>
</social_context>
```

Cognition: agenda identifies changes, context states what is currently known,
graph carries a revisable account. Object/state text is not repeated in graph.

```xml
<agenda>
  <observation ref="O7" object="P1" operation="closed" at="09:08"/>
  <observation ref="O8" object="R1" operation="topic_changed" at="09:05"/>
</agenda>
<social_context as_of="09:10" projection_revision="example-v2">
  P1: closed; options A=3, B=2; voter identities unavailable
  R1: current topic updated; historical coverage partial
</social_context>
<graph>
  P1 relates to E1; Notebook judgement with independent evidence handles
</graph>
```

Neither rendering means five attendees or a settled group decision. Literal poll
purpose and the ongoing matter can differ; Cognition interprets the connected account.

## List 2: tools and target occasion allowlists

Both columns below are **planned exposure**, conditional on configured readers,
per-turn capabilities and scope. They do not attest to deployment or current flags.
Existing tools retain their real permission and availability checks.

| Tool | Purpose and implementation delta | Engagement target | Cognition target |
|---|---|---|---|
| load_transcript | Existing dialogue-window read; add same target annotations as prompt | Retain/enrich | Do not add this round; source reads use record_query |
| record_query | Existing time/author/text message search; extend typed target/kind/history queries with compatibility preserved | Reuse/extend | Reuse/extend current catalogue entry |
| memory_search / memory_read | Connected accounts, matters and evidence | Retain scoped graph surface | Retain scoped graph surface |
| room_read | Existing other-room conversation/thread read; same annotation enrichment as transcript | Retain/enrich | Do not add this round; use scoped record_query |
| group_members | Existing current membership; label actual roster scope/coverage | Retain/extend | Add to read catalogue/audit and bind actual item room |
| room_state_read | Proposed current title/topic, supported roles/permissions/pins/thread state; current room or scoped handle, fields, as-of/coverage | Add only missing state capabilities | Add with scoped read audit; history stays record_query |
| poll_list | Proposed room-scoped discovery: state, limit, cursor | Add | Add |
| poll_read | Proposed handle, view=state/history, limit, cursor; definition/options/counts/supported voters/history | Add | Add |
| social_read | Saved social items/readings, not a live fetch | Reuse when registry reader is configured | Retain current catalogue entry |
| Existing artifact reads, e.g. media_fetch / instagram_read | Fetch content under existing rules; metadata/cover is not full content | Retain existing authority | Do not blanket-add live fetch this round; use saved content and expose unavailable gaps |

The record_query extension resolves target/kind/time/cursor into typed domain reads,
not SQL or an arbitrary query DSL. Keep existing message-mode calls/result shape
compatible; specify the typed observation result branch during implementation.
Use its existing bounds and explicit availability; no new reaction-specific tool
unless a concrete task cannot fit these reads. Non-message history cannot be
claimed complete before its storage/capture exists.

Poll tools are proposed signatures, not registered names by virtue of this file.
All new/changed tool surfaces follow the existing tool procedure and routing proof.
Opaque handles/cursors are scoped server-side; a caller cannot widen room/unit
access. Cognition tools bind to the relevant agenda item's room, not a fabricated
single default room for an entire multi-room wake.

## Stimulus delta and completion contract

Current `nora/domain/stimuli.py` defines message/social/play/rollup/time/self;
message item kinds already include reaction/edit/delete. Proposed changes:

- Reactions keep the existing message/reaction path, adding stable observation and
  target refs plus supported coverage; no second duplicate reaction stimulus.
- A shared post in chat keeps its message stimulus; collected feed items keep social.
- Add **one observation family** for non-message poll and room/member/pin/thread
  observations, with typed object/operation/target/source refs. No kind per platform.
- A poll creation delivered as a message plus structured observation has one durable
  source obligation with both references, not two independent wakes/interpretations.
  Distinct later transitions remain distinct evidence even if one agenda item batches them.

This requires updating the closed stimulus vocabulary/storage constraints, producer
transaction/dedupe, observed-input classification, wake evaluation, grounding/source
loader, agenda rendering, admitted-proposal coverage/consumption, retry/parking and
historical replay together. An observation cannot be consumed solely because its
message sibling was handled if its source evidence was not included or explicitly
covered. Reuse the existing memory proposal and wake completion discipline.

Stimuli accumulate under existing coalescing/wake rules; every callback does not
invoke a model. A configured group event may become relevant to a new understanding
without an existing note predicting it. Rendering and deterministic folds do not
classify sincerity, agreement, attendance or intent.
