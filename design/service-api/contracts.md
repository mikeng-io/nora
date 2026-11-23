# Contracts

A contract is one namespace with one owner area. It lists who implements it
and who may call it; a caller outside that list has no route to the contract,
whatever the network allows.

## The contracts

| contract | owner | implemented by | called by | holds |
|---|---|---|---|---|
| `intake` | `ingest/` | the app's intake | bridges, collectors, wake gates, executors, the portal, any principal registered as a source | events in one envelope: messages, edits, reactions, deletions, summons, activity changes, social items, membership, source health |
| `platform` | `platform/` | every bridge | the app | delivery operations (send, react, typing), reads (self, roster, channels, members), attachment custody |
| `sources` | `social/` | each source adapter | the app's tools, the reflection loop | on-demand reads of one outside reference: an account's items, an identity's live activity |
| `graph` | `memory/` | the app | the portal, tools, MCP | scoped reads with the retrieval semantics |
| `management` | `people/` | the management service | the portal, in-room management, bridges' roster sync | the identity, membership and grant operations |
| `documents` | `documents/` | the app | the portal, tools | documents, revisions, artifacts and their media |
| `voice` | `voice/` | each audio controller, and the hub that owns its connection (a bridge or the app) | the app, a bridge that holds the connection's hub, and each audio controller for its reports only | the voice connection's operations, its status and the controller's reports |
| `computer` | `computer/` | computerd | the app | sandbox operations, browser sessions, exports |
| `operations` | `app/` | every Nora process | the operator, monitors, the portal | the process's own status, tool and skill catalogue and doctrine; config and policy reload, queue inspection and drain, loop pause and resume, replay |

- A bridge implements `platform` and its own `operations`, and calls
  `intake` and `management`. A platform-specific capability (voice, music, presence) is either a
  capability of `platform` declared by `self`, or another contract the bridge
  also implements; it is never a bridge-only route.
- `operations` controls a running process. Building, deploying and restarting
  services belong to the deployment, never to the API.
- The portal is a caller, not a contract. Its Next.js server is its
  backend-for-frontend: it holds the browser session, calls the contracts as
  the viewer's principal, and composes pages from their resources. The API
  is named for what it serves, never for a consumer; it returns resources,
  never a page's shape, and it checks the viewer's scope itself rather than
  trusting the portal to have done so.

## Where it runs

The service API is a convention and a set of contracts, not a service. Each
contract is served by the process that implements it, on that process's own
listener: the app serves `intake`, `graph`, `documents`, `management` and
`operations` on its one HTTP listener, and `voice` reports where it holds
a connection's hub; each bridge, controller, computerd
and the collector serves its own. There is no gateway: a gateway would be a
second hop and a second owner of every scope and actor check.

The MCP server is a separate entry point of the same image that calls the
contracts over HTTP like any other principal, so it cannot reach state the
contracts do not offer.

## Boundaries

```
 outside sources ─► adapters ──┐
 platforms ──────► bridges ────┼─► intake ─► items ─► the loops ─► admission ─► graph
 executors, wake gates,  portal┘                          │
                                                          ├─► platform  (deliveries, engagement loop only)
                                                          ├─► voice, computer, documents
                                                          └─► sources   (on-demand reads)
 portal, MCP, operator ─► graph, documents, management, operations
 bridges ───────────────► management (roster synchronisation)
```

- **Ingress is one direction.** Bridges and adapters record what happened
  only through `intake`; nothing outside the app writes a graph table, a
  presence head or a social row directly. A roster synchronisation is a
  `management` operation about authority, beside the membership item intake
  records of the same event.
- **Egress is one route.** Only the app calls `platform`, and every delivery
  is an act through the act envelope and the common guards
  ([boundaries](../cognition/boundaries.md#egress)); model-composed
  deliveries come only from the engagement loop. `documents` and `computer`
  effects and the `voice` connection operations pass the same envelope.
- **The graph has no write route.** `graph` is read-only. A caller that
  records that something happened is an `intake` source, registered with the
  item kinds it may submit; understanding is written only by the loops
  through admission. The portal is such a source for an identity's dispute
  and forget request: intake records the item and code hides what it names
  before answering ([storage](../cognition/storage.md#retention-and-forgetting)).
- **Scope is resolved, never widened.** A call names its subject and the
  server resolves the scope: a `sources` read for the reflection loop
  resolves to its unit, a portal read to its viewer's scope.
- **A contract never calls back up.** A bridge never reads the graph, an
  adapter never reads policy, and a controller never reads a transcript:
  what they need arrives in the operation they were given.

## Intake

Intake is the one ingress contract. Its envelope carries the version, a
stable event id derived from the source's own identity, the platform and
account, the kind, the source's time and how it was known, the receive and
enqueue times, the conversation or subject it concerns, and the trace context.

- The same envelope arrives over the durable stream or as a `record`
  operation of `intake`, idempotent on the event id; the transport changes
  delivery guarantees, never meaning.
- An event without its identity or version is retained as malformed, never
  guessed into a message: it becomes no item, its `record` operation is
  `rejected`, and it is kept once, under a hash of the bytes received, for
  inspection.
- Intake places each item by the observation rules
  ([observation](../cognition/observation.md#rules)); an adapter never names
  a unit.

## Voice

`voice` is the audio controller's control plane: one voice connection per
guild, and a player that plays what it is given
([audio ownership](../voice-channel/audio.md)).

- Its connection operations are join, leave, play a stream, stop, pause,
  resume, set volume and speak; its read is the connection's status. `speak` carries
  its bounded audio in the operation, and the record keeps the effect's
  hash, never the bytes.
- The hub that owns the connection calls the controller: the bridge where
  the bridge owns the gateway, the app otherwise. A bridge that holds the
  hub implements `voice` too, for the app: it relays the same connection
  operations under the same paths and answers `unsupported` for one it
  does not relay.
- Every connection operation but `join` names the connection generation it
  observed. A stale or absent generation is `rejected` with the current
  one, and a lifecycle or arbiter refusal is `rejected` with its reason;
  none is a 409 or a 404.
- A controller reports a connection's events (a track finished, the
  connection died) to the hub that owns the connection, as a `report`
  operation the hub implements and only a controller may call. The report
  carries the event's id and sequence, and the hub applies each event id
  once; a retry after `unavailable` is a new operation with the same event.
- The music queue (what plays next, skipping, reordering, resolving a
  source) belongs to the process that owns the player, never to `voice`
  ([music](../voice-channel/music.md)). When a bridge owns it, its commands
  are `platform` operations and its status is a `platform` read.
- A woken utterance is an intake event with the wake gate's own event id.
  One from a guild with no designated voice room is `rejected` before it
  is recorded, and its text is not kept
  ([receiving speech](../voice-channel/receive.md)).

## Deriving consumers

- **Clients.** The app's client for each contract is generated from the
  contract document; a bridge in another language is checked against it by
  the contract suite.
- **MCP.** Each MCP tool is one read or one operation of a contract,
  generated from the contract document with its scope and exposure fixed by
  the MCP principal. MCP adds no route, no write path and no semantics.
- **Portal.** Its API client is generated the same way; a portal page never
  reaches a route its viewer's scope could not.
