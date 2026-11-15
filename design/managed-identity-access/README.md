# Managed identity and access

Nora recognises one person across platforms, rooms and units. Small installations
can maintain identity and grants in private TOML files; managed installations use
Ory and a private management API to register accounts, synchronise membership and
administer scoped access. Managed identity is an alternative backend, not a
replacement for the file backend.

**Status:** building — the shared contracts and the file backend's exact-account
view are shipped; managed runtime, linking and consumer integration are under
construction.

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Fmanaged-identity-access%2F%22)

## Parts

- [Identity](identity.md) defines the canonical person, platform-account bindings,
  the two backends and their sources of authority.
- [Account linking](account-linking.md) defines authenticated, bilateral consent
  and what changes when independently registered accounts become one identity.
- [Authorization](authorization.md) defines management authority, scoped grants
  and the boundary between common identity and private unit data.
- [Management](management.md) connects platform discovery, synchronisation,
  in-room commands and the Web App to one private application boundary.

## Architecture

Platform bridges supply authenticated account observations. Nora's identity
service resolves them to a canonical person; its authorization service evaluates
the person's rights within the requested scope. Management callers use the same
application operations, independent of whether the request arrived as an explicit
command, natural language, a Web App action or a platform synchronisation.

In managed mode, Kratos owns managed identity records and authentication;
Nora owns verified account bindings and link consent; Keto owns access grants
and evaluates the declared permission model. These responsibilities do not
require separate Nora processes. Ory administration credentials remain behind
the private service boundary.

The difficult boundary is evidence of common ownership: discovering an account
does not prove it belongs to an existing person. A platform account may be
registered without a browser login, but linking it to another identity requires
authenticated approval from both sides. Administrative authority is not a
substitute for either holder's consent.

## Relationship to existing design

The [identity and policy schema](../10-identity-and-policy-schema.md) owns the
file representation and its many-accounts-per-person semantics. The
[reload contract](../policy-reload/README.md) owns validated file publication.
The [room topology](../room-topology/10-topology.md) owns workspace, unit and
room containment. This bundle adds managed authority and lifecycle while keeping
those identity semantics and read boundaries.

Ory Kratos and Keto are the relevant components. Hydra's delegated token issuance
and Oathkeeper's HTTP proxy enforcement are not prerequisites for this model:
platform messages and internal application calls need the same enforcement even
when they do not traverse an HTTP proxy.

## Rationale

Pairwise platform mappings grow a second identity model for every integration.
One canonical identity with many account bindings makes a new platform an
additional binding rather than another user database. Scoped membership avoids
creating another person whenever someone joins a room or unit.

Keeping TOML available preserves the low-operation-cost installation. Keeping
one managed write boundary avoids file edits, platform events and model-generated
requests racing as competing authorities. The cost is operating Ory and handling
partial failures between independently committed stores, rather than treating a
remote identity provider as a replacement for Nora's domain logic.
