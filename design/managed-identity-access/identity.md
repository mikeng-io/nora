# Canonical identity and backends

## One person, many accounts

A canonical person is the stable identity recognised throughout one Nora
installation. A platform account is an account that person controls. An account
may have several identifier forms, and a person may control several accounts
on the same platform. Rooms and units refer to the person; they do not create
local copies of that identity.

The existing person identity remains stable when a managed backend is selected.
A Kratos identity is bound to that person, not used to silently re-key historical
messages, reminders or ownership. The Web App resolves an authenticated account
through the same binding as messaging; it does not invent a separate portal user.

Account keys use stable, provider-qualified identifiers, including the issuing
scope when a provider's identifiers are application-scoped. Alternate identifier
forms belong to an account; mutable handles, display names and nicknames are
metadata, never ownership evidence. A matchable account identifier has one owner,
enforced at the binding store's write boundary. Repeated and concurrent discovery
returns the existing registration rather than creating another person.

Discovery may register an unlinked account under an independently established
person. Until [linking](account-linking.md) establishes common ownership, two
accounts remain distinct even when names, phone claims or shared rooms suggest
otherwise. Automated registration grants neither operator authority nor access
to another person's data.

The representation every backend shares is that same shape: a person holds
platform accounts, and an account holds typed matchable identifiers plus
operator-authored labels that never resolve. Resolving any matchable
identifier returns the exact account it names — never a re-selected preferred
account — because that account is the unit an account-control proof binds.
The identifier's kind carries its issuer scope: a Signal `uuid` is not a
`phone`, and an Instagram `fbid` is a different, app-scoped number than the
same account's `igid`. A second issuer for one kind would extend the
identifier, not collapse the kinds.

## Sources of authority

| Responsibility | File backend | Managed backend |
|---|---|---|
| Person roster and descriptive profile | Private identity file | Kratos identity, bound to Nora's person |
| Platform-account ownership | Validated identity file | Nora's verified binding store |
| Access grants and management roles | Private policy file and operator-controlled administration | Keto relationships and permission model |
| Room addressing and unit/workspace topology | Nora configuration | Nora configuration |
| Link consent and account verification | Operator-maintained bindings | Nora's bilateral link service |

The file backend remains suitable for single-user and small-group installations,
including installations with several platforms or units. Its operator-maintained
mapping does not need an Ory service. In-room managed writes do not silently edit
private TOML files; file mode reports that management is operator-maintained.

A deployment selects its identity/access backend explicitly. A fact has one
authoritative writer: managed grants are not merged with file allowlists, and a
failure of Ory does not fall back to a permissive or stale file policy. Read
projections do not become independent writable authorities. Nora configuration
continues to own room metadata, destinations and topology rather than encoding
all configuration as permission relationships.

## Ory's responsibility

Kratos owns the managed identity record, authentication credentials and identity
lifecycle. Its Admin API can create an identity without login credentials. Nora
uses a schema appropriate to platform-originated people, without inventing email
addresses or treating every platform account as an OIDC login credential.

Nora's service controls the person binding and verified account associations.
They are not user-editable profile traits. Kratos metadata is not a replacement
for uniqueness constraints or authorization at Nora's binding store. Public
identity views do not enumerate private account identifiers or memberships.

Keto answers scoped permission questions over canonical people and resources.
It does not discover accounts, prove ownership or decide that two people are the
same. Identity state, ownership and permission decisions remain distinct inputs
to admission.

The managed backend is pinned to self-hosted Ory Kratos v26.2.0 and Ory Keto
v26.2.0 — OSS builds, with no OEL or Ory Network capabilities assumed. The
versioned API specifications are the contract reference:
[Kratos](https://raw.githubusercontent.com/ory/kratos/v26.2.0/spec/api.json),
[Keto](https://raw.githubusercontent.com/ory/keto/v26.2.0/spec/api.json).

## Backend selection and lifecycle

Selecting managed mode admits a validated mapping of existing people and grants
under operator authority. It preserves person references and rejects ambiguous
account ownership. Selection alone neither infers links nor broadens grants.
Returning to file mode requires an explicit validated authority handoff; a stale
file is not an emergency authorization source.

Leaving a room or unit ends that membership, not the global identity. Revoking an
account binding removes that account's ability to act as the person. Global
identity suspension is a separate authorized operation checked by all entry
points, including existing Web App sessions. A Kratos login-state change alone
is not assumed to stop messages authenticated by another platform.

## Sources

- [Ory identity model](https://www.ory.com/docs/kratos/manage-identities/overview)
- [Administrative identity creation](https://www.ory.com/docs/kratos/manage-identities/create-users-identities)
- [Keto permission model](https://www.ory.com/docs/keto/modeling/create-permission-model)
