# Service API

**Every interface between Nora's services is one canonical API: one set of
conventions, one envelope, two outcome families and one machine-readable
contract, from which every client, bridge, portal route and MCP tool is
derived.**

The API is what Nora's processes say to each other: a bridge handing in an
event, the app sending through a bridge, a collector recording activity, the
portal reading a document, an operator reloading policy, a tool reading the
graph. Who can reach a listener is the deployment's network, never the API's.
What the API owns is the shape of every call, the scope it resolves to, the
actor it names and the outcome it reports.

## Parts

| part | is | defined in |
|---|---|---|
| **conventions** | the path, method, envelope, outcome, time, paging, idempotency, versioning and trace rules every route follows | [conventions](conventions.md) |
| **contracts** | the namespaces: intake, platform, sources, graph, management, documents, voice, computer, operations; who implements and who calls each | [contracts](contracts.md) |
| **source adapters** | the plugin form for outside sources: item and activity ports, collectors, registration, sessions | [adapters](adapters.md) |

## Terms

- **Contract** — a namespace of resources and operations with one owner area.
  Any number of services may implement one contract; every bridge implements
  `platform`.
- **Operation** — every write: a typed, idempotent request with an actor and a
  scope, answered with a record that can be read back.
- **Read** — every GET-shaped answer: `ok`, `nothing_found` or `unavailable`,
  with when and from what it was observed.
- **Principal** — the authenticated caller. The **actor** is who an operation
  acts for; a principal never nominates another actor.
- **Adapter** — the code that turns one outside source into items through
  intake and serves its on-demand reads.

## Invariants

- One contract per subject. A route that fits an existing contract joins it;
  a new namespace is a reviewed architecture change.
- The contract is machine-readable and generated from the domain types; a
  non-Python service, the portal client and the MCP server are checked against
  it, never hand-copied.
- Every write is an operation; no route mutates state on a GET or outside the
  operation lifecycle.
- A read's absence and a read's failure never share a shape; an unavailable
  dependency is `unavailable`, never an empty success.
- Every request resolves one scope (agent, unit, room) that Nora validates
  against real containment; a caller-supplied scope name is never trusted.
- Platform identifiers, phone numbers, handles and free text never appear in
  a path or query string.
- Items reach the graph only through intake; inferred and committed rows only
  through admission ([surface](../cognition/surface.md#admission)). No
  contract offers another write path.
- A caller that forwards answers to an outside model is egress: its principal
  carries an exposure, and the [egress rules](../cognition/boundaries.md)
  apply to every answer it receives.

## Why this shape

- **Contracts, not services**: four bridges already share one wire; naming
  the contract rather than the process lets a platform, source or worker be
  added without a new dialect.
- **Every write an operation**: the management operations and the bridge
  actions are already the same shape (idempotent id, actor, typed outcome,
  read-back of an uncertain result); generalising it removes the per-route
  state machines.
- **Two outcome families, not one**: a read answers whether something exists;
  a write answers whether an effect happened. Forcing one vocabulary onto
  both blurs `nothing_found` with `rejected`.
- **Generated contract**: a rule copied by hand into three languages drifts;
  a generated document makes drift a failing check.
- **Network for reach, API for authority**: reachability is infrastructure;
  scope, actor and exposure travel with the call so that a reachable caller
  still reads only its scope.

## Scope

This bundle owns the shape of every internal interface. The subjects behind
the contracts keep their own bundles: [platform bridges](../platform-bridges/00-contract.md)
own event custody and delivery durability, [managed identity](../managed-identity-access/management.md)
owns the management operations and their authority, and
[cognition](../cognition/README.md) owns what the graph contract reads and
admits. Those bundles describe their routes in this bundle's conventions.
