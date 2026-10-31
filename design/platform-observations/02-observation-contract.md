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
  selected set, or aggregate snapshot. Empty *complete voter selection* means a
  withdrawn vote; omitted selection is unknown. Aggregate voters and option counts
  are separate; multi-select option counts cannot be summed into unique people.
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
