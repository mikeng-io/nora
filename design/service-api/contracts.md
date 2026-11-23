# Contracts

A contract is one namespace with one owner area. It lists who implements it
and who may call it; a caller outside that list has no route to the contract,
whatever the network allows.

## The contracts

| contract | owner | implemented by | called by | holds |
|---|---|---|---|---|
| `intake` | `ingest/` | the app's intake | bridges, collectors, executors | events in one envelope: messages, edits, reactions, deletions, summons, activity changes, social items, membership, source health |
| `platform` | `platform/` | every bridge | the app | delivery operations (send, react, typing), reads (self, roster, channels, members), attachment custody |
| `sources` | `social/` | each source adapter | the app's tools, the reflection loop | on-demand reads of one outside reference: an account's items, a person's live activity |
| `graph` | `memory/` | the app | the portal, tools, MCP | scoped reads with the retrieval semantics; observation submissions |
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
- The portal is a caller, not a contract: its pages read `graph`,
  `documents` and `management`, so the portal and Nora read the same rows
  with the same scope rules.

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
- **Egress is one route.** Only the engagement loop calls `platform`
  delivery operations, after the guards ([boundaries](../cognition/boundaries.md#egress));
  `voice`, `documents` and `computer` effects pass the same act envelope.
- **The graph has no write route.** `graph` submissions are observations: a
  caller records that something happened, and intake records it with the
  caller as its source. Understanding is written only by the loops through
  admission.
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
