# Conventions

Every route in every contract follows these rules. A route that cannot is a
finding against the route, never an exception to the convention.

## Paths

```
/v{major}/{contract}/{collection}
/v{major}/{contract}/{collection}/{id}
/v{major}/{contract}/{collection}/{id}/{sub-collection}
/v{major}/{contract}/operations
/v{major}/{contract}/operations/{operation_id}
/v{major}/{contract}/queries
/health   /ready   /metrics
```

- The version leads, then the contract, then the resource. Every service
  serves only the contracts it implements, under the same paths as every
  other implementation.
- Collections are plural nouns in kebab-case; ids are Nora's opaque ids,
  never a platform identifier, a phone number, a handle or a name.
- No verbs in a path. A change of state is an operation, not a
  `/{id}/approve` route.
- At most one sub-collection level. Deeper relations are a query or a
  separate collection filtered by its parent.
- `/health`, `/ready` and `/metrics` sit at the root, unversioned, on every
  service: liveness only, readiness to serve, Prometheus. Richer state, such
  as a bridge's queue, is a read of `operations`.
- A byte stream (audio, live media) is a resource an operation opens; its
  frames are the stream's own protocol, not envelopes.

## Methods

| method | use | safe | idempotent |
|---|---|---|---|
| `GET` | read a resource or a collection page | yes | yes |
| `POST …/queries` | a read whose filter names a platform identifier, free text or more than a page key | yes | yes |
| `POST …/operations` | every write | no | by `operation_id` |
| `GET …/operations/{id}` | the operation's current record | yes | yes |
| `PUT …/{collection}/{key}` | content-addressed bytes only (attachments, media) | no | by key and hash |

No `PATCH` and no `DELETE`: a partial update and a retirement are operations,
so they carry an actor, an idempotency id and an outcome like every other
write.

## The envelope

Every response body is one envelope: the outcome, the payload, and the facts
that make the outcome checkable.

| part | read | operation |
|---|---|---|
| outcome | a read outcome | an operation outcome and its state |
| payload | the resource, or the page | the applied effect, or the reason it was not applied |
| when | observed at, by the source's clock, and how that time was known | started and settled times |
| from | the source, its health, and its coverage when partial | the target and the platform reference when one exists |
| scope | the agent, unit and room the read resolved to | the same, for the effect |
| trace | the trace id | the trace id |

Field names are `snake_case`; times are integer epoch milliseconds named
`*_ms`; durations are `*_ms` or `*_s`, never bare numbers.

## Outcomes

**Reads**

| outcome | means | HTTP |
|---|---|---|
| `ok` | the source answered and the thing exists | 200 |
| `nothing_found` | every source needed was healthy and none has it | 200 |
| `unavailable` | a source failed, is stale or was never wired; absence is unknown | 503 |

**Operations**

| outcome | means | HTTP |
|---|---|---|
| `complete` | the whole effect is applied and confirmed by its target | 200 |
| `pending` | accepted; the record moves on and is read back | 202 |
| `denied` | the actor lacks the authority; nothing was applied | 403 |
| `rejected` | the target refused the effect; nothing was applied | 200 |
| `unsupported` | this implementation does not offer the operation | 200 |
| `incomplete` | part of the effect committed; reconciliation is owed | 200 |
| `uncertain` | the effect may have happened; never retried blindly, read back first | 200 |
| `unavailable` | not attempted: a dependency the operation needs is down | 503 |

- The HTTP status says whether the API answered; the outcome says what
  happened. A 404 is a missing route, never `nothing_found`.
- The client reads the outcome from the body for every status, and a status
  that disagrees with its body is `unavailable`. A redirect is refused.
- Transport faults that are the caller's own (a malformed body, an unknown
  field, a missing credential) are `400`, `422` and `401`, with no outcome.

## Operations

- The caller mints the `operation_id`. Resubmitting the same id with the same
  effect returns the existing record; the same id with a different effect is
  `409` and changes nothing.
- An operation is typed: a closed `kind` per contract and a payload of that
  kind's shape, never an open mapping.
- It names its **actor** and its **scope**, and the revisions it observed of
  the state it changes. A stale revision is `rejected` with the current one.
- Its record has one lifecycle: `prepared → started → settled`. Its outcome
  is `pending` while prepared or started; settling sets every other outcome. A record that cannot be found after a lost response is
  `uncertain` until the target's own evidence settles it.

## Collections and paging

- Pages are cursor-based: `limit` and an opaque `cursor` in, `next_cursor`
  out. No page numbers and no offsets.
- A page states whether the collection it walked was complete. A partial or
  failed walk never proves something is absent from the collection.
- Order is explicit in the contract, never the store's.

## Scope, actor and exposure

- Reachability is the network's. A principal is resolved once from the
  request: a service by its deployment credential, an identity by a viewer
  session from [managed identity](../managed-identity-access/management.md#web-app); the actor of an operation is the principal or the human it
  verifiably acts for, never a value in the body.
- Every request resolves one agent, unit and room scope. Nora validates its
  containment; an unknown or unresolvable scope is `denied`, never widened or
  redirected.
- A principal's exposure bounds what any read returns to it: internal rows
  never reach an outside-forwarding principal; operator rows reach operator
  principals only.

## Versions and the contract document

- The major version changes only for a breaking change; additions are
  backwards-compatible within a major. A new major is served beside the old
  one until its last caller moves.
- The contract document is OpenAPI, generated from the domain types that own
  each contract. Every service, the portal client and the MCP tool list are
  generated from or checked against it, and each contract's suite in
  `tests/contract/` runs every implementation against it.

## Tracing and observation

- Every request and every intake event carries W3C trace context; a service
  continues it, never starts a new root for a call it received.
- Every route records one counter with `contract`, `operation or read` and
  `outcome` labels, so an `unavailable` is as visible as an `ok`.
