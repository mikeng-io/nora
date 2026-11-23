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
/v{major}/{contract}/blobs/{key}
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
| `POST …/operations` | every write but a blob's bytes | no | by `operation_id` |
| `GET …/operations/{id}` | the operation's current record | yes | yes |
| `PUT …/blobs/{key}` | bytes under an opaque key, with their hash (attachments, media) | no | by key and hash |

No `PATCH` and no `DELETE`: a partial update and a retirement are operations,
so they carry an actor, an idempotency id and an outcome like every other
write. Blob custody is the one write outside the operation lifecycle: bytes
are bound to the operation that names their key. Its `PUT` answers an
operation envelope that is settled at once: `complete` for new bytes and
again for the same key and hash, `rejected` for the same key with another
hash.

## The envelope

Every response body is one envelope: the outcome, the payload, and the facts
that make the outcome checkable. Three answers are not envelopes: the bytes
a blob `GET` returns, a stream's frames, and the root probes. A blob `GET`
answers its bytes as `application/octet-stream`, never JSON; when the blob
is absent or unreadable it answers a read envelope, `nothing_found` or
`unavailable`.

| part | read | operation |
|---|---|---|
| outcome | a read outcome | an operation outcome |
| payload | the resource, or the page | the applied effect, or the reason it was not applied |
| when | observed at, by the source's clock, and how that time was known | the record's state, and its started and settled times |
| from | the source, its health, and its coverage when partial | the target and the platform reference when one exists |
| scope | the scope the read resolved to, at its contract's level | the same, for the effect |
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

- A read over several sources is `ok` when any of them has the thing, with
  its coverage naming the sources that failed; `nothing_found` only when
  every one was healthy; otherwise `unavailable`.
- The outcome is the answer; the HTTP status is derived from it by these
  tables and never read instead of it. A 404 is a missing route, never
  `nothing_found`.
- Every status in the tables carries an envelope. A status that disagrees
  with its body's outcome is `unavailable`; a redirect is refused.
- Five statuses carry no envelope, because the request never became a read
  or an operation: `400` and `422` (malformed), `401` (no principal), `404`
  (no such route) and `409` (an `operation_id` reused for another effect).
- Reading an operation's record is a read: `ok` with the record and its
  operation outcome, `nothing_found` when no such record exists.

## Operations

- The caller mints the `operation_id`. Resubmitting the same id with the same
  effect returns the existing record; the same id with a different effect is
  `409` and changes nothing.
- A settled record is final, whatever its outcome. Trying again after an
  outcome that applied nothing (`denied`, `rejected`, `unsupported`,
  `unavailable`) is a new operation with a new id.
- An operation is typed: a closed `kind` per contract and a payload of that
  kind's shape, never an open mapping.
- The request names its subject; the server stamps the **actor** and the
  resolved **scope** on the record. An operation that changes stored state
  also names the revisions it observed, and a stale one is `rejected` with
  the current revision.
- Its record has one lifecycle: `prepared → started → settled`. Its outcome
  is `pending` while prepared or started; settling sets every other outcome.
- A caller whose response was lost reads the record back. An effect whose
  target cannot say whether it happened settles `uncertain`, never
  `complete` and never retried blindly.

## Collections and paging

- Pages are cursor-based: `limit` and an opaque `cursor` in, `next_cursor`
  out. No page numbers and no offsets.
- A page states whether the collection it walked was complete. A partial or
  failed walk never proves something is absent from the collection.
- Order is explicit in the contract, never the store's.

## Scope, actor and exposure

- Reachability is the network's. A principal is resolved once from the
  request: a service by its deployment credential, an identity by a viewer
  session from [managed identity](../managed-identity-access/management.md#web-app).
  The actor of an operation is the principal or the identity it verifiably
  acts for ([management](../managed-identity-access/management.md#one-application-boundary)),
  never a value in the body.
- The server resolves scope from the principal and the request's subject;
  a caller names a subject, never its own scope. Each contract declares the
  level it scopes at: a room, a unit, an agent, an identity, a workspace,
  the installation, or none for a public surface without a unit.
- A read whose scope cannot be checked because a dependency it needs, such
  as membership, cannot be read is `unavailable`, never `nothing_found`.
- A read resolves within its scope, never resolves and then filters: what
  the principal's scope or exposure does not reach answers `nothing_found`,
  exactly as what does not exist, and a page is complete for that scope.
- An effect whose scope cannot be resolved is `denied`, never widened or
  redirected. Intake is the exception that loses nothing: it
  places an event by the [observation rules](../cognition/observation.md#rules)
  and records one it cannot place as unresolved, never refusing it.
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
