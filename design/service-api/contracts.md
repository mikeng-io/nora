# Contracts

A contract is one namespace with one owner area. It lists who implements it
and who may call it; a caller outside that list has no route to the contract,
whatever the network allows.

## The contracts

| contract | owner | implemented by | called by | holds |
|---|---|---|---|---|
| `intake` | `ingest/` | the app's intake | bridges, collectors, executors, any principal registered as a source | events in one envelope: messages, edits, reactions, deletions, summons, activity changes, social items, membership, source health |
| `platform` | `platform/` | every bridge | the app | delivery operations (send, react, typing), reads (self, roster, channels, members), attachment custody |
| `sources` | `social/` | each source adapter | the app's tools, the reflection loop | on-demand reads of one outside reference: an account's items, an identity's live activity |
| `graph` | `memory/` | the app | the portal, tools, MCP | scoped reads with the retrieval semantics |
| `management` | `people/` | the management service | the portal, in-room management, bridges' roster sync | the identity, membership and grant operations |
| `documents` | `documents/` | the app | the portal, tools | documents, revisions, artifacts and their media |
| `voice` | `voice/` | each audio controller | the app | join, leave, speak, play and their status |
| `computer` | `computer/` | computerd | the app | sandbox operations, browser sessions, exports |
| `operations` | `app/` | every Nora process | the operator, monitors | config and policy reload, queue inspection and drain, loop pause and resume, replay |

- A bridge implements `platform` and calls `intake`; it serves nothing else.
  A platform-specific capability (voice, music, presence) is either a
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
`operations` on its one HTTP listener; each bridge, controller, computerd
and the collector serves its own. There is no gateway: a gateway would be a
second hop and a second owner of every scope and actor check.

The MCP server is a separate entry point of the same image that calls the
contracts over HTTP like any other principal, so it cannot reach state the
contracts do not offer.

## Boundaries

```
 outside sources ─► adapters ──┐
 platforms ──────► bridges ────┼─► intake ─► items ─► the loops ─► admission ─► graph
 executors ────────────────────┘                          │
                                                          ├─► platform  (deliveries, engagement loop only)
                                                          ├─► voice, computer, documents
                                                          └─► sources   (on-demand reads)
 portal, MCP, operator ─► graph, documents, management, operations
```

- **Ingress is one direction.** Bridges and adapters write only to `intake`;
  nothing outside the app writes a graph table, a presence head or a social
  row directly.
- **Egress is one route.** Only the app calls `platform`, and every delivery
  is an act through the act envelope and the common guards
  ([boundaries](../cognition/boundaries.md#egress)); model-composed
  deliveries come only from the engagement loop. `voice`, `documents` and
  `computer` effects pass the same envelope.
- **The graph has no write route.** `graph` is read-only. A caller that
  records that something happened is an `intake` source, registered with the
  item kinds it may submit; understanding is written only by the loops
  through admission.
- **Scope travels, never widens.** Every call carries the agent, unit and
  room it acts within; a `sources` read for the reflection loop is keyed by
  its unit, a portal read by its viewer's scope.
- **A contract never calls back up.** A bridge never reads the graph, an
  adapter never reads policy, and a controller never reads a transcript:
  what they need arrives in the operation they were given.

## Intake

Intake is the one ingress contract. Its envelope carries the version, a
stable event id derived from the source's own identity, the platform and
account, the kind, the source's time and how it was known, the receive and
enqueue times, the conversation or subject it concerns, and the trace context.

- The same envelope arrives over the durable stream or over
  `POST /v1/intake/events`; the transport changes delivery guarantees, never
  meaning.
- An event without its identity or version is retained as malformed, never
  guessed into a message.
- Intake places each item by the observation rules
  ([observation](../cognition/observation.md#rules)); an adapter never names
  a unit.

## Deriving consumers

- **Clients.** The app's client for each contract is generated from the
  contract document; a bridge in another language is checked against it by
  the contract suite.
- **MCP.** Each MCP tool is one read or one operation of a contract,
  generated from the contract document with its scope and exposure fixed by
  the MCP principal. MCP adds no route, no write path and no semantics.
- **Portal.** Its API client is generated the same way; a portal page never
  reaches a route its viewer's scope could not.
