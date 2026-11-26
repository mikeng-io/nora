# Observation evidence

## Identity and scope

Each observation names its platform, account and conversation scope, the object
it concerns, any affected target and the operation reported. Source occurrence
time and receipt time remain separate, with their quality stated. A scoped
source revision may order evidence only inside the stream that supplied it;
arrival order is a fallback, never fabricated global causality.

Object identity survives revision. Transition identity distinguishes an
add/remove/add sequence from replay. Actor identity can be known, anonymous,
not supplied or not applicable; a membership subject or poll voter is not
necessarily the actor who caused the change. Native identifiers remain private
in evidence, while model surfaces receive scoped opaque handles and resolved
safe labels.

An utterance delivered beside an observation has two different links: the
message that carried the fact and the target the fact affects. A pin notice,
for example, does not become the pinned message. Both references survive
independently of the live bridge journal.

Confirmed multi-post delivery preserves every native post handle in send
order. Any confirmed handle can resolve the same audited outbound message
within its room. Rejected, failed or uncertain delivery mints no handles and
claims no delivered count.

## Object semantics

- **Message metadata** preserves reply, forward, mention, expiry, view-once and
  shared-object facts as source constraints. It does not authorize retention or
  media capture beyond existing policy.
- **Reactions** support actor deltas, covered clears and observed sets or
  counts. Unknown actors remain unattributed. A clear or snapshot need not
  invent per-user deletion history.
- **Polls** keep definitions, stable option identity, voter selection sets,
  aggregate snapshots and open, closed or unknown state. A complete empty voter
  selection withdraws that vote; an omitted selection is unknown. Aggregate
  option counts cannot establish unique people or abstention.
- **Rooms and threads** patch only reported fields. Absent fields remain
  unchanged, while an explicit source null has its native clearing meaning.
  Thread observations stay inside the parent room rather than creating a room.
- **Membership and roles** retain subject, native state and covered collection
  scope. Partial snapshots never remove omitted accounts. Pending, departed,
  banned and unknown states are not current membership.
- **Pins** change a room's covered pin set through target deltas or snapshots.
- **Shared artifacts** connect source metadata, custody, previews and perception
  without duplicating media ownership. A thumbnail or preview is not full
  content. Contact and event responses remain per responder, and an unreadable
  response is not a withdrawal or attendance claim.

## Persistence and folding

The observation Record is append-oriented source evidence with scoped replay
identity and provenance. The current head is a rebuildable projection with its
applied source position, observation time, ordering and coverage. There is no
untyped event store standing in for message, media, graph or Notebook owners.

One deterministic fold serves incremental updates, current reads, historical
as-of reads and full rebuilds. Deltas apply once. Complete snapshots replace
only their covered sets; partial snapshots merge without erasing unknowns. A
stale snapshot cannot overlay a newer delta. If supported evidence cannot order
changes, the head exposes an ordering gap.

A response before its definition remains unresolved rather than creating an
empty poll. Later arrival links the same observation idempotently. Source
withdrawal and retention gaps affect authorized derived presentation; missing
history cannot be reconstructed from a model-authored summary.

Capture, Record append, cheap current projection and durable downstream work
commit at the existing transaction boundary. Expensive perception and model
work use existing deferred obligations. Acknowledgement never depends on a
detached projection task with no durable owner.

## Outbound and derived meaning

Rollups provide bounded deterministic episodes and counts with coverage.
Notebook and graph accounts may interpret relationships, conventions and
ongoing matters, but they are not another observation of their own conclusions.
Source state, recorded work progress and curated interpretation stay visibly
different.

Observation does not expand raw-payload retention, view-once capture, location
history or permanent voter archives. Source deletion and retention follow the
existing owners. Operational logs and traces correlate failures and cost but
are never required to reconstruct application truth.
