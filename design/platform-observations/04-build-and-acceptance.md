# Build acceptance and accumulating evidence

Status: proposed design/build brief, no implementation in this change.
No requirement to wait for a dedicated semantic benchmark before collecting the
supported observations. Code contracts can ship with generated protocol fixtures;
real source-backed journeys accumulate after authorized use. Unknown model quality
must be reported as unknown, not used to claim success or block all collection.
Existing recorded-wake rules apply when claiming semantic evaluation results.

## Integrated metadata implementation map

Operator direction, 2026-11-02: build one coherent metadata cluster across the
existing owners. This supersedes the earlier family-by-family delivery order.
The lanes below are internal dependencies of one implementation, not separate
platform/family PRs, releases, gates or review rounds. This amendment is a build
map; it delivers no new runtime behavior.

### Scope and source admission

Include responses (the shipped reaction baseline and its missing semantics),
collective-choice polls, shared posts/artifacts and conversation events
(title/topic, membership/roles, pins and thread lifecycle). Each selected source
mapping must reach durable state and its model-facing read paths in the same
cluster; collection alone is not a completed family. Reuse the delivered reaction
annotations rather than rebuilding them.

Before implementation edits, refresh [01](01-coverage.md) against the actual build
head and pinned bridge dependencies. Freeze the selected platform/account/room
mode and payload/operation cells, their fixtures, limitations and acceptance in
that existing matrix. Its older census is historical, not today's capability
promise. Unknown source support requires inspection, not an unsupported label.
Do not make all four platforms support identical native features a prerequisite
for a useful integrated delivery. An unselected, unsupported or unavailable cell
remains explicitly undelivered; it is not counted as passing by omission. A
required selected cell that cannot be implemented returns to explicit scoping,
not silent removal from acceptance.

Source checkpoints read at `c537267ae4b5698110423f5b7c5c8f12b42fbf76`:
`TranscriptInjection.assemble` already gathers transcript and Standing into
TagContext; `CognitionSessionRunner._session_input` assembles the unified session;
Cognition's `_BLOCKS` still has no Standing declaration; stimulus kinds remain
message/social/play/rollup/time/self. These are integration seams, not proof the
proposed observation path exists. [05](05-occasion-map.md) owns its occasion and
tool map; its historical baseline must be refreshed before runtime edits.

### Dependency lanes in the same implementation

| Lane | Changes in existing owners | Required joined result |
|---|---|---|
| Capture and identity | Selected mappings in the four current bridges; typed wire/domain payloads under [02](02-observation-contract.md); explicit actor, source order, delta/snapshot and coverage semantics | Accepted observations survive the existing journal/queue path with stable identity; unsupported fields stay visible as gaps; no fake empty messages |
| Record and current state | Existing domain stores/folds plus only the missing typed poll and room/history ownership; shared artifact refs retain existing custody/perception ownership; durable downstream obligations at the existing commit boundary | Replay changes nothing twice; unresolved targets remain linkable; current state and supported rebuild/as-of reads agree; no generic JSON event lake or second outbox |
| Read model and presentation | One scoped read result for enriched transcript, relevant off-window Standing refs and detail reads; both Engagement and Cognition context assembly, with the reused Standing blocks explicitly declared for Cognition | A source object leaving the recent transcript stays available when relevant; later changes refresh its state; time/coverage/budget/dedup are consistent; no extra observation block |
| Tools and attention | Reuse/extend Record/transcript and saved-artifact reads; implement only the missing poll/room tasks in [05](05-occasion-map.md); update Cognition allowlists/audit/room binding and the complete observation-stimulus lifecycle together | Nora can discover and inspect old/new evidence through automatic input and bounded tools; an event is not consumed just because its message sibling was; callbacks coalesce without a model call each |

Normalize and serialize once before branching into these consumers. Sequence
shared wire/domain/store edits before their dependants; disjoint source mappings
can be developed together once that contract is fixed. An integrated cluster does
not require multiple agents, concurrent edits to shared files or concurrent gates.
Keep one integration owner and one candidate tree.

### Family obligations within the cluster

| Family | Transcript and continuity | Detail/attention obligations |
|---|---|---|
| Reaction | Reuse recorded target annotations; add only selected missing source identity/ordering/clear/snapshot/unresolved-target behavior; relevant old target can enter Standing | Existing Record reads and message/reaction agenda; coverage must distinguish the recorded subset from complete upstream state |
| Poll | Typed definition/change/vote/close events and current head; relevant poll ref/current state survives the recent window in Standing | Discovery/detail/history under [05](05-occasion-map.md); anonymous counts never invent voters; poll closure never settles the related matter |
| Shared post/artifact | Sharer's actual words, object ref, metadata and saved reading/bytes coverage; relevant off-window ref resolves through the same source read | Existing message/social stimuli and media/social/Record tools; no new feed crawler or blanket live-fetch authority |
| Group event | Typed room/member/pin/thread changes, actual source/actor/time quality and relevant supported current state | Scoped state/member/history reads and observation agenda; guild scope is not channel audience; a snapshot does not invent change history |

These obligations apply to the frozen source cells, not a claim every listed
operation is available on every platform. Non-message poll and room evidence use
one observation stimulus family as specified in [05](05-occasion-map.md), including
storage constraints, grounding, rendering, proposal coverage, retries and replay.
No per-platform stimulus or independent interpretation loop is introduced.

### Integration, verification and stopping boundary

Carry this map with the runtime changes on one integration branch and one PR.
Do not land the lanes as half-wired scaffolding or open a separate PR merely to
approve each family/map. In development, run the smallest meaningful checks for
the changed seam. Once joined, prove one generated mixed journey: poll/share/group
change, transcript-window eviction, later source update/closure, next Engagement
and Cognition assembly, and detail tools reading the same supported revision.
The controls below also cover the necessary ordering, anonymity, scope, budget,
unavailable and recovery failure cases; a mixed happy path alone is insufficient.

Run `make gate` once on the final integrated candidate, using the repository's
affected-test/receipt rules and normal local admission. `make full-gate` is not an
extra default step. Review the integrated change once at the applicable runtime
review tier (full for changed prompts/tools/routing), with an independent vendor;
[ORCHESTRATION.md](../../ORCHESTRATION.md) governs findings and the two-round ceiling.
A real defect or changed candidate still requires its relevant checks and renewed
final acceptance; batching is not a waiver of correctness. Hosted CI remains its
own required check, not another agent-triggered local gate.

Stop when every selected cell and joined acceptance obligation has evidence,
required checks/review are clear and the agreed delivery state is read back.
Source merge, deployment and authorized live acceptance remain separate receipts.
Keep unrelated shutdown/restart exercises, collector work and HTTP-client cleanup
outside this metadata cluster. A captured reaction that cannot link its target is
relevant only where it demonstrates a selected metadata-path defect. Telegram
adoption, new passive location/social collection and Notebook/profile semantic
redesign require their own explicit scope; this cluster adds no such authority.

## Proposed deterministic controls

Names below describe required test properties, not claimed existing tests.
Use generated data and real wrong-call shapes; no private chat fixtures.

| Property | Positive and failure controls |
|---|---|
| Contract parity | Native platform delta vs actor snapshot vs anonymous count snapshot map to distinct payloads; unknown versions fail retained |
| Identity/replay | add/remove/add stays three transitions; replay same input changes nothing; ambiguous revision does not fabricate history |
| Ordering/rebuild | Late event and older snapshot cannot erase newer state; compare fold with supported full replay; unordered gap becomes explicit |
| Targets | Response precedes message/poll definition; retained unresolved reference later links once; no empty synthetic utterance |
| Poll semantics | Complete empty voter selection withdraws; missing selection unknown; multi-select total differs from sum; anonymous count never creates identity |
| Room/snapshot | Partial member set never removes omitted accounts; guild scope never presented as channel audience |
| Exposure | Transcript annotation/event and Standing source enrichment deduplicate; close/edit appears on next assembly; newer tool state labelled with newer revision |
| Availability/budget | Source/field failure yields limitation; capped options are visibly incomplete with read handle; no guessed zero |
| Atomic recovery | Commit/ACK failure and deferred projection survive restart through existing obligations; retry does not duplicate Record or stimulus |
| Trace independence | Collector failure changes no domain state/work completion; source event can still be reconstructed from its durable owner |
| Scope/ignored signals | Cross-unit handle/evidence read refuses; typing creates no Record/context/wake/model call |

Use current repository contract fakes/SQL suites. Run affected checks once on the
final integrated implementation; a new test must fail on its relevant broken seam.
These checks establish computable guarantees, not sincerity/joke understanding.

## Accumulation and change discipline

01 is the versioned evidence/coverage home; 02 owns semantics; 03 owns model
projection; this file owns acceptance. Add findings to their owner with inspected
revision, source/caller, observed result, limitation and next acceptance property.
Mark superseded findings with replacement evidence rather than silently deleting
them. Do not copy current task status/receipts into a second tracking database.

Open questions live in _index.yaml with explicit unknowns. Resolve them from a
pinned protocol fixture or real authorized readback, not from platform reputation.
New data kinds need a useful journey and an owner, not automatically a new table,
tool, graph ontology or model call. A transport change updates the bridge contract
by reference; semantic changes update unified-memory without duplicating that spec.

This bundle briefs the integrated metadata cluster above; it does not claim
complete platform coverage. Exact DDL, registered tool text, token allocation,
installed collector changes and deployment evidence belong to implementation.


## Occasion-map acceptance amendment (2026-10-30)

[05](05-occasion-map.md) owns the injection/tool/stimulus map. Add these properties
to the integrated implementation's controls; they are not new tests in this change:

- Inspect actual Engagement and Cognition assembled inputs: visible target
  annotations, relevant off-window state, source time/coverage and existing graph
  account are present once through transcript, the existing Standing presentation
  and the existing graph surface; no separate observation block is required. Unavailable required state
  is explicit; no operational traces are interpreted as social evidence.
- The same object is enriched consistently in prompt and transcript tools.
  A later tool read labels its newer revision; historical replay cannot see future state.
- Test record_query's existing message calls/result compatibility and disabled-reader
  withholding, including existing ambiguous name resolution. Test new typed history
  separately, not by claiming current message search already reads every observation.
- Check each tool against both occasion allowlists; adding a registry entry alone
  cannot expose it to Cognition. Read tools bind a multi-room wake's actual item room.
- Admit/consume every observation family through the same durable wake/proposal
  contract. A co-delivered message cannot silently consume unseen poll/room evidence;
  replay creates no duplicate source obligations, and failure retains pending work.

## Transcript-window and Standing acceptance amendment (2026-11-01)

These properties apply across admitted message metadata, reactions, polls,
shared artifacts and room/member/pin/thread observations, not just poll tallies.
They specify future implementation checks, not a claim those families are built.

- **Beyond 40 entries:** create a relevant poll, push its creation outside a
  generated 40-entry transcript, then assemble both Engagement and Cognition.
  Standing still exposes its scoped reference and supported current state without
  requiring a model to remember to call a tool. No second poll-list block/store.
- **Freshness without new chat:** update/vote/close that old poll without a new
  human message. On the next eligible assembly, state/time/coverage match the
  domain head; the change is grounded by existing wake/coalescing rules. No stale
  copied tally and no model call required for every callback.
- **State is not settlement:** closing a poll leaves an unresolved activity matter
  visible. An explicit supported completion/cancellation can retire its current
  reference; source history remains readable. An unrelated old open poll is not
  permanently pinned into every prompt.
- **New evidence:** a newly admitted unlinked poll/room event can enter the scoped
  working context and Cognition grounding without an existing Notebook match.
- **Typed chronology:** service events have no invented speaker; unknown actor or
  occurrence time stays unknown. A snapshot is labelled as observed state. Current
  annotation on an old message is not presented as known at original send time.
- **One source result:** visible-target annotations, off-window Standing references
  and tool reads agree at the same revision; per-assembly source detail deduplicates.
  Later tool reads label their newer revision. Generated examples include a shared
  post reading update and a pin/membership change as well as a poll.
- **Budget/scope/failure:** overflow preserves limitation and bounded read handles;
  unavailable/partial state never becomes zero or a fabricated closed state. A
  Standing reference cannot widen scope. Replay sees only its as-of state; source
  withdrawal/retention gaps are reflected, not recovered from stale Standing prose.
- **Ownership:** transcript truncation or lost rendered text cannot delete canonical
  observations. Standing enrichment is derived from typed refs and source reads;
  no new model-driven ETL, Standing writer or operational-trace-to-social pipeline.


## Candidate evidence boundary (2026-11-02)

The integrated candidate implements the selected four-platform capture cells,
typed durable history/current heads, transactional stimulus obligations,
transcript/Standing projections and bounded task reads described in [01](01-coverage.md),
[02](02-observation-contract.md) and the current-candidate map in
[05](05-occasion-map.md). The original acceptance above remains binding; this is
not a declaration that every proposed extension or semantic property is complete.

Generated native fixtures, SQL/memory contracts, real WorkRunner recovery tests
and actual session-input/consumption tests measure deterministic behavior. They do
not prove real platform completeness, model tool choice, social interpretation or
production deployment. The final candidate gate and independent full review remain
the PR's acceptance evidence owners, not the lane reports.

Tool-routing measurement remains unavailable while the declared eval subject-config
failure is open ([issue 480](https://github.com/mikeng-io/nora/issues/480)); its fix
is not merged at the inspection snapshot. Do not bypass parity, invent a detail
handle or seed synthetic production history to make that measurement pass. The
new dinner-poll case requires discovery because its snapshot supplies no poll
handle; it does not establish `poll_read` detail routing. A source-backed detail
anchor and live routing evidence remain unmet. This does not waive the tool/eval
procedures or authorize a claim of routing quality from deterministic tests.

The candidate's automatic source-head working set is a bounded recency selection,
with explicit omissions and exact-handle reads. Ongoing matter-linked selection
and retirement must be verified against the Standing owner's actual typed refs;
no semantic lifecycle or lossless model compression is established by this slice.
