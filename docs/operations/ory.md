---
class: operations
subject: the pinned Ory Kratos and Keto surfaces the managed identity backend runs against
owns: [Ory pinned surface]
links:
  - {to: docs/design/people/identity.md, rel: uses}
  - {to: docs/design/people/authority.md, rel: uses}
---

# Ory pinned surface

The managed identity backend runs against self-hosted Ory Kratos and Ory Keto. What
each owns, and why, is in the people design; this file holds the pinned versions and
the API surface the integration uses.

## Kratos

Kratos owns the managed identity record, authentication credentials and identity
lifecycle. Its Admin API can create an identity without login credentials. Nora
uses a schema appropriate to platform-originated identities, without inventing email
addresses or treating every platform account as an OIDC login credential.

The v26.2.0 surface Nora uses is the Admin identity lifecycle plus session
inspection: `POST /admin/identities` to create (schema id, traits, `external_id`,
state, metadata — credentials optional and normally absent for
platform-originated identities), `GET /admin/identities/{id}` and
`GET /admin/identities/by/external/{externalID}` to read back,
`PATCH /admin/identities/{id}` (JSON Patch) to update traits or state,
`GET`/`DELETE /admin/identities/{id}/sessions` to inspect and invalidate an
identity's sessions, `DELETE /admin/sessions/{id}` for one session, and
`GET /sessions/whoami` to validate a presented session (200 with the session and
its identity; 401/403 when it is not valid). `GET /schemas/{id}` exposes the
configured identity schema.

`external_id` is identity correlation, not ownership evidence: it is unique across
identities, and a duplicate create answers 409 — Nora reads back the existing
identity by `external_id` and never assumes a colliding registration intended
the same identity. Nora's own canonical key survives; the Kratos
identity id is stored as a binding, not substituted into historical references.

The initial file admission goes further: each identity it creates carries
`metadata_admin` naming the import and its intended effect, stamped on the
create call itself. That admin-only marker is the durable command evidence an
interrupted import recovers through — a collision whose record lacks it is
foreign and refused untouched, and neither `external_id` equality nor
holder-editable traits substitute for it. It marks which command created the
identity; it never decides account ownership.

The managed identity schema carries descriptive traits only — a display name, the
social-memory opt-out and the non-matchable labels — because a
platform-originated identity may have no email, password or OIDC credential at
all. The declared schema source lives at
`deploy/ory/kratos/person.schema.json`: credentialless, closed to additional
properties and carrying no account ownership in mutable traits. No fake email
is manufactured to satisfy a schema; batch upsert and credential attachment
are not part of this integration.

The `social` trait is the managed source for the social opt-out file mode
declares on an identity block. Registration writes `social = true` explicitly —
the existing file default — so a new identity never relies on a reader
inferring an absent value, and a registration retry reads the identity back
rather than overwriting a stored preference. The read folds the canonical
identity with every retired identity an alias preserves: an opt-out recorded on
either independently registered identity applies after linking, and a retired
(inactive) identity's traits still answer without reactivation. An identity
whose trait is absent or malformed is unavailable to the reader — never
inferred enabled. Background evidence reads and input assembly consult this
same preference, including retained batches and retired identities. A changed
preference after evidence reads prevents that input from reaching a model.
No path grants authority to clear another holder's preference.

Identity `state` is `active` or `inactive`. The v26.2.0 specification text
claims the value "has currently no effect", but the v26.2.0 source enforces it:
`Identity.IsActive` checks state, `Session.IsActive` requires the identity to
be active, `ManagerHTTP.FetchFromRequest` refuses an inactive session, and
`ActivateSession` refuses to mint a session for an inactive identity. Nora
therefore suspends an identity by setting `state=inactive` plus invalidating their
sessions — and still enforces the suspension on its own derived access rather
than trusting the login surface alone.

Nora's service controls the identity binding and verified account associations.
They are not user-editable profile traits. Kratos metadata is not a replacement
for uniqueness constraints or authorization at Nora's binding store. Public
identity views do not enumerate private account identifiers or memberships.

Keto answers scoped permission questions over canonical identities and resources.
It does not discover accounts, prove ownership or decide that two identities are the
same. Identity state, ownership and permission decisions remain distinct inputs
to admission.

The managed backend is pinned to self-hosted Ory Kratos v26.2.0 and Ory Keto
v26.2.0 — OSS builds, with no OEL or Ory Network capabilities assumed. The
versioned API specifications are the contract reference:
[Kratos](https://raw.githubusercontent.com/ory/kratos/v26.2.0/spec/api.json),
[Keto](https://raw.githubusercontent.com/ory/keto/v26.2.0/spec/api.json).
The pinned image digests are an implementation obligation of the service
integration (Task 2): they must be recorded from verified registry metadata at
pin time, not guessed. Development instances run the OSS servers' own
development modes — Kratos `serve --dev` with a sqlite DSN and a mounted
identity schema; Keto `serve` with a sqlite DSN and the OPL namespace file at
`namespaces.location` — each reachable only from Nora's service, with the Admin
API kept off any public listener. The pinned development configs live at
`deploy/ory/kratos/kratos.dev.yaml` and `deploy/ory/keto/keto.dev.yaml`;
`kratos migrate sql` and `keto migrate up` are the one-time prerequisites
before the first `serve`.


## Keto

Nora writes and deletes relationships on the write API — `PUT
/admin/relation-tuples` (201 on create; a malformed body is 400) and `DELETE
/admin/relation-tuples` (204 on matching deletes) — and reads on the read API:
`GET /relation-tuples` lists tuples by namespace/object/relation/subject
filters with `page_size`/`page_token`, and `GET`/`POST
/relation-tuples/check` answers one check. The check mirror maps the verdict
into the status: `allowed: true` answers 200, `allowed: false` answers
**403** — a denied verdict arrives as an error status, never a bare 200, so
the client reads `allowed` from the body on both. The `/check/openapi`
variant always answers 200 with the same body. Nora uses the mirror and
validates that status and body agree — a 200 whose body does not allow is
`unavailable`, and a redirect is refused rather than followed — so a proxy
or redirect cannot launder a denial into a success shape. There is no
`/check/open`. `POST /relation-tuples/batch/check` is
available for several subjects in one call; `GET /relation-tuples/expand` is
diagnostic only. `POST /opl/syntax/check` validates an OPL document's syntax
(text/plain body), and `GET /namespaces` reports what the service actually
loaded.

Keto does not validate that a tuple's object exists or that a claimed room
really sits inside the named unit — a check over objects with no matching
relationships answers deny, not error. Existence and containment are
therefore Nora's own validations — at write time for the tuples it
publishes and at request time for the scope a caller names — and a
malformed, conflicting or unparsable check response is `unavailable`,
never allow.

The development service runs `keto serve` with a sqlite DSN and
`namespaces.location` pointing at the OPL file — the config key whose URI
holds the namespace source in the v26.2.0 schema, with
`experimental_strict_mode: true`. Computed permits are authoritative: even a
direct tuple named `manage_members` cannot bypass its restriction rewrite.
Management writes the separate grant/deny relations, never computed permits.
The pinned engine implements this mode; it is a required part of the model,
not an optional availability choice. `limit.max_read_depth: 16` accommodates
three-scope inheritance and the per-permit denial rewrites; the default depth
of five cannot evaluate the complete declared hierarchy. The pinned development config is
`deploy/ory/keto/keto.dev.yaml`; `keto migrate up` is the one-time
prerequisite. The write and read APIs stay on private listeners; only
Nora's service holds the write side.


## Sources

- [Ory identity model](https://www.ory.com/docs/kratos/manage-identities/overview)
- [Administrative identity creation](https://www.ory.com/docs/kratos/manage-identities/create-users-identities)
- [Keto permission model](https://www.ory.com/docs/keto/modeling/create-permission-model)

