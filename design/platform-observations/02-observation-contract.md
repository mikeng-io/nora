# Typed observation and persistence contract — proposed

This specifies domain semantics for implementation, not shipped classes or DDL.
At the `3fe09ac7` audit snapshot the bridge union was proposed, not built.
Integration with master `23ee4a5ed` brings the v1 `Envelope` in
`nora/platform/bridge/envelope.py`: its kinds are message/edit/delete/reaction/
summons. `rehydrate.py` serializes and restores `NormalizedMessage`/`Touch`,
including the reaction removal bit. This is now the transport owner of those
payloads; the first reaction visibility slice changes their read projection,
not the wire. The sibling poll/room observation payloads below remain proposed
extensions at that same owner. Keep NormalizedMessage for utterances; a shared
event header does not mean every payload is a message or shares one table.

## Header and identity

| Field | Required meaning |
|---|---|
| version, event_id | Wire schema version and bridge-owned stable event identity; reuse bridge dedupe contract |
| platform, account_key, conversation_ref | Private platform/account/room scope, including thread where applicable |
| object_ref, target_ref | Typed source object and affected parent/message; nullable parent only when source provides none |
| operation | create, revise, withdraw, add, remove, clear, snapshot; validated against payload kind |
| source_revision | Opaque comparable source order only within its declared scope, or absent |
| occurred_at, received_at, time_quality | Source time when known, receipt time, and source/arrival/unknown quality; never fake source time |
| actor_ref, actor_status | Known account, anonymous, not supplied, or not applicable; subject may differ from actor |
| coverage | Per-field and covered-set/window completeness, observation time, gap reason |
| provenance | Source event/key and parser contract revision; raw custody reference only if retained |

Event identity distinguishes transitions; object identity stays stable across
revisions. Actor+target+emoji is a state key, not sufficient event dedupe for
add/remove/add. Use bridge sequence/replay ambiguity rules when source revisions
are absent. No invented timestamp/body hash turns ambiguous replay into a new fact.
Internal IDs are scoped and private; model handles are resolved inside server scope.
Native room-field references such as parent/owner IDs remain in Record. Projection
uses the resolved owner name when available, otherwise an opaque reference; source
facts are retained without presenting raw identifiers to the model.

## Payload union

- **message metadata:** reply/quote target and attribution, mentions, forward origin
  when supplied, typed shared objects, expiry/view-once flags. Preserve flags as
  source constraints; they do not authorize durable media capture beyond policy.
- **reaction:** target, emoji identity (Unicode or platform custom reference), actor
  when known; add/remove delta, clear with its covered emoji/actor scope, or snapshot
  of counts/known reactors. Do not impose one-reaction-per-user across all platforms.
- **poll definition:** stable poll ref, parent message ref, question, option refs/text,
  choice constraints, anonymity, open/closed/unknown state, source close time if known.
  Option IDs stay stable; changed labels are not new options by positional guess.
- **poll response:** delta for a specific option, replacement of one known voter's
  selected set, or aggregate snapshot. A voter's selection is a set: repeated
  option refs count once, including in a covered voter snapshot. Empty *complete
  voter selection* means a withdrawn vote; omitted selection is unknown. Aggregate voters and option counts
  are separate; multi-select option counts cannot be summed into unique people.
  Retained per-voter selections report as their own labelled recorded-subset
  tally — never summed with a source aggregate and never read as a complete
  upstream total.
- **room change:** changed typed title/topic/permissions/thread lifecycle fields;
  absent fields unchanged, explicit null follows source semantics.
- **membership:** subject account, role/state delta or scoped snapshot. Actor/cause
  can be unknown. Removal does not establish voluntary departure or relationship.
- **pin:** target reference, pin/unpin or scoped pin-set snapshot.
- **shared artifact:** typed reference, source metadata, custody/bytes availability
  and perception reference are separate. A preview/cover never stands for full content.

Unrecognized wire versions are retained failures under bridge rules. A known
payload with unsupported optional fields records coverage gaps; do not silently
mark full support. Transport decoding happens once before domain processing.

## Store mapping and atomicity

| Domain | Existing owner / minimal proposed extension |
|---|---|
| Message/edit/delete | Existing message, edit and withdrawal owners; preserve source anchors |
| Reactions | Existing MESSAGE_REACTION history plus missing transition identity/clear/snapshot representation and deterministic read fold |
| Collective-choice poll | Proposed poll definition/revision, response observations, current head; references to existing message/room |
| Room/member/pin | Existing current roster reads remain; proposed typed room observations/history and rebuildable current projection where missing |
| Artifact | Existing media/social stores; observation only connects references, never duplicates blob custody |
| Provenance/work | Existing bridge input/work/action records, plus observation reference; no new scheduler/event bus |

Final table/column names and migrations are allocated from the actual implementation
head; this mapping is binding ownership, not an additional generic JSON event store.
Typed variant payloads may use JSON columns inside their owning store if that is the
existing pattern, with validated shape and indexed identity/target/time columns.

In the existing app transaction: source mapping/dedupe, Record append, cheap current
head update, graph projection or durable obligation, and any required stimulus are
committed together where they share a DB. Never ACK while a required projection
exists only as a detached task. Retried work resumes its durable obligation.
Cross-store expensive processing uses the bridge's existing deferred completion
contract; no second outbox mechanism is introduced here.

Unknown targets retain a scoped unresolved reference for later linking. They do not
become fabricated messages or disappear. Target arrival re-links the same observation
idempotently. Unsupported reconciliation remains visible as partial/unavailable.

## Presentation references are not duplicate storage

[03](03-model-projections.md) owns the transcript-first and Standing-continuity
contract. A transcript entry is a typed view of a message or platform event, not
a requirement to store every observation as a message row. Standing references
objects/matters; assembly reads their state from these domain stores. Tallies,
source revisions and coverage are not maintained by a second Standing prose fold.
Leaving the recent transcript window does not delete observations or their links;
source retention/deletion rules still apply. Missing retained history is a visible
gap, not reconstructable from a remembered summary. Source deletion/withdrawal must
also invalidate affected derived presentation on its next authorized assembly.

## Folding, late arrival and correction

Delta operations apply once in source order. A complete snapshot replaces only its
specified covered set at its source position. Partial snapshots merge known facts
without clearing unknown members/votes. Never overlay an old snapshot onto a newer
known delta; replay from the nearest supported prior state, or expose unresolved
ordering when source evidence cannot order them. Receipt time is not global causality.
A response before its definition remains unresolved, not an empty poll.

Current state carries applied source position(s), observation references, as-of,
coverage and projection revision. Concurrent updates to one object serialize using
existing DB locking/transaction patterns. Rebuild uses supported retained observations;
when retention leaves a gap, mark it, do not claim full-history equivalence.

Rollup contains bounded deterministic counts/episodes and coverage; Notebook owns
social interpretation. Graph links Record/Rollup and Notebook accounts with existing
vocabularies extended in code where needed. Attendance/commitment is not inferred
inside this fold. Corrections preserve provenance and revise derived state under the
existing memory policy; this document does not introduce a withdrawal cascade.

Retention/deletion mappings must be read from current policy before enabling stores.
Do not expand collection to private raw archives, view-once media or permanent
location history. This is an explicit implementation prerequisite, not a new policy.

---

## Implemented contract (foundation head `0144_platform_observation`)

The frozen shared surface. Capture lanes emit it; the app lane consumes it;
exposure lanes read `platform_object_state` heads and `platform_observation`
history — nobody else parses source JSON again.

### Imports and construction

```python
from nora.domain.observations import (
    OBSERVATION_CONTRACT,            # currently 1
    OBJECT_KINDS, OPERATIONS, ACTOR_STATUSES, KIND_OPERATIONS,
    SourceScope, Coverage, Observation,
    # payload variants — the discriminated union
    ReactionPayload, MessageMetadataPayload, PollPayload, RoomPayload,
    MembershipPayload, MembershipEntry, PinPayload, ArtifactPayload,
    # the one fold + its rows/head
    StoredObservation, ObservationHead, fold_observation, rebuild_head,
    target_key, target_from_key,    # EditTarget <-> stored "id:x"/"ts:y"
    payload_from_dict,              # typed payload from {"kind", payload}
)
from nora.platform.bridge.envelope import Envelope, V1_KINDS, KINDS, \
    canonical_source_key, canonical_digest, event_id
from nora.platform.bridge.rehydrate import (
    observation_to_dict, observation_from_dict,
    message_to_dict, message_from_dict,
)
from nora.domain.ports import ObservationStore
from nora.infra.sql.observations import SqlObservationStore
from nora.testing.observations import FakeObservationStore
```

An `Observation` validates at construction (`__post_init__`): the operation
must be legal for its `object_kind` (`KIND_OPERATIONS`), `actor_status`
`known` requires `actor_id`, `upstream` time requires `occurred_ms`,
`source_position` requires `revision_scope`, `object_ref` is required. Payload
validation is per operation (e.g. a poll `add` needs `voter_ref`+`option_ref`;
a voter `revise` needs `selected`, where `()` is a withdrawn vote).

### Wire form

`Envelope.observation` carries `observation_to_dict()`'s output — the typed
record, `contract` versioned separately from the envelope's `v`:

```json
{
  "v": 2, "kind": "poll", "platform": "signal", "account_key": "acct",
  "conversation_id": "g1", "message": null,
  "observation": {
    "contract": 1, "kind": "poll",
    "scope": {"platform": "signal", "account_key": "acct",
              "conversation_id": "g1", "thread": ""},
    "object_ref": "poll:1700000000001",
    "target": {"message_id": "", "sent_ts": 1700000000001,
               "mentions_known": true},
    "operation": "revise",
    "subject_ref": "", "actor_id": "+15550100",
    "actor_status": "known",
    "occurred_ms": 1700000000050, "received_ms": 1700000000099,
    "time_quality": "upstream",
    "source_revision": "g:9", "source_position": 9,
    "revision_scope": "signal.group_revision",
    "coverage": {"complete": true, "covered": "event", "gaps": [],
                 "reason": ""},
    "payload": {"voter_ref": "+15550100", "selected": ["o1"]}
  }
}
```

### Version and compatibility rules (the mirror requirement)

- `Envelope.version()` emits `v=2` iff the record carries an `observation`
  sidecar OR a kind outside `V1_KINDS` (`message`, `edit`, `delete`,
  `reaction`, `summons`); otherwise `v=1`. The version describes the shape
  used, not the library — a new bridge keeps emitting `v=1` for ordinary
  messages so an old app accepts them through a mixed-version rollout.
- `Envelope.from_fields` accepts `v in (1, 2)`. `v=1` requires a `message`
  and no `observation`; `v=2` requires `message` or `observation` (a pure
  observation event has `message=None` — never a fake utterance) and a kind
  in `KINDS`. Any other `v` raises — a retained failure at the boundary.
- The observation's own `contract` field gates the payload union;
  `observation_from_dict` raises on any `contract != OBSERVATION_CONTRACT`.
- `observation_from_dict(d, expect_scope=SourceScope(...))` raises when the
  observation's declared scope differs from the envelope's — the wrong-scope
  control; the observation's scope must equal the envelope's.
- Producers and consumers share this module — bridge lanes serialize with
  `observation_to_dict`, app lanes rehydrate with `observation_from_dict`
  and never reinterpret raw bytes.

### Selected operation meanings (binding)

- **poll:** `create`/`revise`/`withdraw` carry definition fields
  (`question`, `options` as `(ref, label)` pairs — refs are stable identity,
  a relabelled option is the same option — `multi_select`, `anonymous`,
  `state`, `close_ms`, `parent_ref`). A poll close is `revise` with
  `state="closed"`. Response deltas: `add`/`remove` carry
  `voter_ref`+`option_ref`; `revise` with `voter_ref`+`selected` replaces
  that voter's COMPLETE selection (`()` = withdrawn; Signal
  `pollVote.optionIndexes` maps here — a per-voter set replacement, not a
  per-option delta). `snapshot` carries `option_counts`, `voter_count` and
  optionally a covered `voters` map — aggregates never synthesize voter
  identities. A COMPLETE snapshot replaces exactly its covered set: a
  covered `option_counts` claim replaces the whole count table — a covered
  EMPTY table (e.g. WA `pollResultSnapshot` with `voters={}`,
  `voter_count=0`) is a real "no recorded votes", never a merge with
  earlier counts. Counts supplied outside a complete covered claim merge
  as partial evidence and never erase omitted options; a `voters`-only
  coverage leaves the aggregate table untouched. `clear` empties the
  recorded vote set in covered scope.
- **reaction:** `add`/`remove` per-actor deltas; `clear` with covered
  emoji/actor scopes (empty scope = all observed); `snapshot` of
  `counts`/`reactors`. Deltas with unknown actors accumulate as unattributed
  counts, never invented identities. The existing `Touch`/`message_reaction`
  path is unchanged; this payload is the missing clear/snapshot semantics.
- **membership:** the OBJECT is the member collection `object_ref` names —
  `channel:C1` vs `guild:G1` are different objects, so a guild roster can
  never fold into a channel audience. `add`/`remove`/`revise` are subject
  deltas (`subject_ref` + payload `role`/`state`); `snapshot` carries the
  `members` set with `covered` — complete replaces the collection, partial
  merges and never removes omitted accounts. The folded collection keeps
  every recorded native state (`member`, `left`, `invited`, `banned`,
  `unknown`, and request states a source reports); CURRENT membership is
  derived from the native active state only — pending, departed and
  unrecognized states report under their own labels, never as members.
- **room:** `object_ref` names the channel or thread; `revise`/`create`
  patch only reported `fields` (absent field = unchanged, explicit `null` =
  cleared; Signal `groupInfo` name/revision lands here — it is not member
  history). `snapshot` replaces only its covered field set.
- **pin:** the OBJECT is the room's pin set; `add`/`remove` carry one
  `EditTarget` `message`, `snapshot` carries the covered `pins` set.
- **artifact:** `object_ref` is the artifact's own ref; `create`/`revise`/
  `withdraw` carry `artifact_kind`, `metadata`, `custody`
  (declared/held/unavailable — never the bytes), `perception_ref`,
  `preview`.
- **message_metadata:** `object_ref` is the message's own handle; the
  message's forward origin, expiry, view-once, shared refs as source
  constraints.

### Storage, identity and the fold

- `platform_observation`: one row per admitted observation, unique on
  `(platform, account_key, source_digest)` — a replayed source event is a
  no-op (`append` returns `None`, the `StimulusStore.append` contract).
  `event_id` + `source_digest` are provenance BY VALUE — no FK to the live
  `bridge_event` journal. `target_key` stores `target_key(target)`;
  `target_message_id` is the resolved `messages.id` (FK `SET NULL` — a
  pruned message re-opens the unresolved link, never erases the row).
- `platform_object_state`: the fold's compare-and-swap head per
  `(platform, account_key, conversation_id, thread, kind, object_ref)` —
  `state` JSONB + `applied`, `head_observation_id`, `source_position`,
  `revision_scope`, `observed_ms`, `ordering` (`ordered`/`arrival`/`gap`),
  `coverage`, `projection_rev`. Both tables keep `row_history` versions.
- `fold_observation(head, stored)` is THE one fold — incremental head,
  `as_of`, `rebuild_head` and the fake all share it. Deltas apply once;
  a positioned-stale delta is retained and the head flags `ordering="gap"`;
  a positioned-stale snapshot is retained but never overlays newer state
  (`coverage.superseded_snapshots`); a response before its definition flags
  `coverage.awaiting_definition` until the definition folds in.
  `rebuild_head` replays in `source_position` order when every retained row
  carries one in a common `revision_scope`, else arrival order — no
  manufactured order for sources that prove none (WA callback streams carry
  `None` + no scope, honestly `arrival`).
- `ObservationStore` methods: `append(observation=..., event_id=...,
  source_digest=..., target_message_id=None, tx_hook=None)`,
  `current(scope=..., kind=..., object_ref=...)`, `heads(scope=...,
  kind=None, limit=100)`, `history(scope=..., kind=..., object_ref=...,
  after_id=0, limit=1000)`, `as_of(..., through_id=...)`,
  `resolve_targets(scope=..., target_key=..., message_id=...)`,
  `unresolved_targets(scope=..., limit=200)`, `aclose()`. `tx_hook` runs
  `(conn, observation_id)` inside the append transaction.

### What the lanes call next

- **Capture/source-mapping lanes** (parsers, `deploy/*` bindings): build an
  `Envelope` with `kind` = the object kind (or `message` when an utterance
  rode along) and `observation=observation_to_dict(obs)`; `message=None`
  when there is no utterance. Scope in the observation must equal the
  envelope's. Where the source proves no order (WA), leave
  `source_position`/`revision_scope` empty — ambiguity stays ambiguity;
  upstream dedupe identity is the source's own claim, not a manufactured
  sequence. Uncertain native operations stay unmapped until evidence.
- **App ingest lane** (`bridge_queue` ingest worker): currently raises for
  observation-only envelopes — wire `observation_from_dict(env.observation,
  expect_scope=SourceScope(platform=env.platform, account_key=
  env.account_key, conversation_id=env.conversation_id, thread=""))` then
  `ObservationStore.append` inside the existing transaction (resolving
  `target` to `target_message_id` when the Record already has it), plus any
  required stimulus through `tx_hook`. A message+observation envelope
  ingests the utterance AND appends the observation in one commit.
- **Exposure lanes** (transcript/Standing/tools): read
  `ObservationStore.current`/`heads`/`history` and `unresolved_targets` —
  `coverage`/`ordering` are part of the answer, never dropped. No model
  block exists yet; none is wired in this stage.


### Timestamp-addressed target authors

A Signal target is `(author, sent timestamp)`, not timestamp alone. The typed
header and wire carry `target_author_id` separately from actor/subject; the
stored target key includes it for timestamp targets, and target relinking uses
that complete key. A missing author leaves the target unresolved rather than
choosing an arbitrary same-timestamp message. Message-ID targets retain their
existing platform-scoped addressing. This additive contract detail is part of
the same unshipped cluster, including its initial migration and shared store tests.


## Integrated candidate: delivery and recording provenance

The candidate extends the existing bridge envelope to v2 for typed observations;
v1 utterances/touches remain readable. `platform_observation` and
`platform_object_state` retain source history and its folded head. Selected capture
cells are in [01](01-coverage.md); [04](04-build-and-acceptance.md) owns acceptance.

A co-delivered utterance and an affected message are different relationships.
`delivered_message_id` identifies the utterance that carried the typed fact;
`target_message_id` identifies the affected message and remains nullable until
its independently addressed target resolves. A pin notice can carry words while
pinning an older message. Both links survive in durable history without making the
notice the pin target. Neither link depends on the live bridge journal surviving.

Reaction reads merge legacy per-actor deltas and typed clears/snapshots through
one bounded fold. SQL returns metadata and bounded state in one statement snapshot;
truncated, unattributable or unorderable state withholds exact counts. Database
insertion stamps define the recorded-arrival fallback across these two tables.
They are not transaction commit timestamps and prove no upstream ordering;
source revisions remain scoped to the native stream that supplied them.
