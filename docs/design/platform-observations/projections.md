# Shared projections

## One read result

Automatic context and task reads use the same scoped source projection. An
object result identifies its kind and safe handle, current or historical state,
observation time, projection revision, coverage and evidence handles. A
collection is bounded and reports omissions and a continuation path where one
exists.

Found, nothing found and unavailable remain different outcomes. Partial
describes readable evidence with limitations, not a fourth availability state.
An unknown field is not zero, false, empty or withdrawn. Unsupported optional
fields and stored-but-unprojected metadata remain visible as bounded coverage
omissions rather than leaking raw JSON.

A projection revision is local consistency information, not an upstream global
order. All fragments for one object in an assembly use the same read result and
appear once at full detail. A later tool result may be newer and must label its
own time and revision; no database transaction stays open across model calls to
pretend time stopped.

Historical assembly reads the as-of projection. A current annotation on an old
message carries the observation's own later time, so it cannot look like
knowledge available when the message was sent.

## Presentation references

The transcript presents actual speech and typed source events chronologically.
The ongoing-matter presentation retains scoped source references and resolves
fresh state at assembly. Neither is another persisted tally or social-summary
store.

Matter-linked references live on the existing Note owner as bounded typed
pointers, updated under its ordinary revision concurrency control. Cognition
may attach only unambiguous handles actually shown in the same room; forged,
cross-room or unresolvable references fail closed. Note reads return the
handles needed for later revision.

Assembly resolves those references under their original scope, labels the
tracking matter and reports unresolved rows rather than silently dropping
them. Duplicate discovery and retained presentations collapse to one detailed
source fragment. Retiring the matter removes its current references without
deleting source history; closing a poll changes source state only.

Recency selection can admit new evidence before any Notebook matter exists.
It is not evidence of semantic relevance, and an old open poll is not pinned
into every prompt forever. Relevance and matter retirement remain with the
existing Notebook and Standing owners.

## Artifact and response detail

Artifact metadata has one normalized, bounded projection for summaries,
assembled detail and task reads. Safe names, descriptions, times, counts,
response state and availability are rendered deliberately; native addressing,
coordinates, credentials and arbitrary raw fields are not.

Responses remain keyed by responder where identity is known and by source
revision where it is not. A removal or source-declared empty response affects
only that responder. Unread values do not overwrite another responder's answer
or become a negative RSVP. Counts derived from recorded responses never claim
attendance.

Reaction annotations own the tally for a visible target; Standing references
that presentation instead of repeating it. Off-window reactions and other
relevant objects use the same current-head projection with their coverage and
read handles.

## Scope and evidence

Handles and cursors are resolved within server-bound room and unit scope on
every read. Possession of a handle does not authorize another room, and typed
queries cannot turn a current-room subset into a claimed unit-wide answer.

Graph accounts and recalled interpretation remain labelled as interpretation,
not a second independent observation. Operational logs and traces can locate
capture, projection and rendering failures through safe correlation references,
but they do not enter this read model as social facts.
