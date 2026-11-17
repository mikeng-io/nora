# Scoped authority and access

## Identity is shared; authority is scoped

An identity may belong to several units and rooms within the existing
[workspace/unit/room topology](../room-topology/10-topology.md). Canonical identity
does not imply a shared transcript, a cross-unit memory read or universal access.
Management reads are scoped too: recognising a known account must not reveal
its other platform accounts, private memberships or another unit's profile.

Keto models the canonical identity, the workspace, the unit and the room as
distinct subjects/resources. Membership and management authority are distinct
relationships. Permission rules state which authority inherits to which
contained resources; membership alone never inherits operator powers. The
application verifies the resource's actual containment rather than trusting a
caller-supplied unit or workspace name.

The namespace model Nora declares is small and fixed: `Workspace`, `Unit` and
`Room` carry containment relations (`room` → `unit`, `unit` → `workspace` —
written by Nora after it validates the real containment). The canonical identity
is the permission subject. Relations: `member` on a scope, `admin` on a scope for the full
delegated operator, `restricted` for an explicit deny, and separate
`grant_<permit>` / `deny_<permit>` relations for every
access and management permit. A speak-only restriction denies only speak;
`restricted` is the explicit blanket deny. Grants and matching restrictions
inherit downward from workspace to unit to room. Room membership grants
speak/view only in that room; parent membership grants neither. The
permits each consumer checks are `speak`, `view` and the scoped-management
permits (`manage_members`, `manage_grants`, `delegate`). Inheritance is
declared in OPL: a `member` of a `Room` may `speak`/`view` there, a `Unit`'s
`admin` may `manage_members` in contained rooms, a restriction denies at its
scope and everywhere below it — deny always wins — and membership confers no
management permit anywhere. The declared source is
`deploy/ory/keto/namespaces.ts`. Revocation is tuple deletion; the permits
never widen from platform observations.

## Pinned Keto surface (v26.2.0)

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

## Who may change what

- A room administrator manages only the room operations delegated to them.
- A unit operator manages the unit's admitted rooms, membership and grants within
  their delegated powers.
- A workspace operator manages the contained units within their delegated powers.
- Identity holders approve their own cross-platform links and account ownership
  changes. Room/unit administration cannot replace this approval.
- Installation authority controls backend selection, initial management authority
  and global identity lifecycle operations.

Registration, granting access and delegating administrative power are separate
operations. Permission to add a member is not permission to appoint an operator,
edit the operator's own authority or change the global owner of an account.
Global profile changes require holder authority. A local nickname is descriptive
metadata Nora owns for a canonical identity inside a room, unit or workspace;
it never proves account ownership or changes the global Kratos profile.
Scoped operators update it through the same authenticated management boundary.
A scoped name view uses the nearest nickname on its room-to-workspace ancestry,
then the global name. Unscoped views use the global name; siblings never borrow
each other's local names, and an unreadable scoped source stays unavailable.
Local names follow the same disclosure boundary as the context that uses them.
Transcript tools resolve names through each read's actual room. External-read
screening retains names already shown to the session across later renames;
a deleted message's visible author label does not make its body citable.
Authority handoff includes this state or refuses an unrepresentable transfer.

Initial installation authority is established out of band, never by the first
identity to issue an in-room command.

Platform roles and rosters are observations. An installation may explicitly
delegate bounded authority to a platform role; no such delegation is inferred
from being a guild owner or platform administrator. A delegated role's removal
removes the authority derived from it.

## Admission and disclosure

Both room admission and sender permission remain necessary. An explicitly open
room may admit an unlinked or unregistered sender under its approved rule;
managed mode does not introduce a universal browser-registration requirement.
It does not widen the room's read or disclosure scope.

Tool use, outbound delivery, background work and Web App reads enforce their
corresponding current authority, not only the initial inbound message gate. A
natural-language request never supplies its own actor identity, operator role or
proof of permission. The service derives those from the authenticated invocation.

The relevant identity and permission revision is bound to a management operation.
The service checks authority again when applying the mutation so a revoked
operator or changed target cannot use an earlier interpretation as a grant.
Revocation also invalidates any authorization cache used by subsequent protected
operations; an affected operation cannot proceed on a known stale grant.

## Failure and independent stores

An explicit refusal and an inability to check authority are different results.
Neither authorizes the operation. Registration or membership discovery cannot
turn an unavailable Keto decision into an implicit grant.

Keto evaluates only the relationships Nora writes. Its check answer is allow
or deny — an unknown object or a missing tuple is an ordinary deny, never an
error — and Nora validates the named scope exists before the check, so
"Keto denied" and "Nora could not resolve the scope" stay distinct outcomes.
A malformed request, a transport error, a timeout or a verdict whose status
and body disagree is `unavailable`, never allow. Containment — that a room
belongs to a unit — is a Nora validation on write, not something a
permission tuple proves.

Kratos, Nora's binding store and Keto do not share an application transaction.
Nora's management service owns completion and reconciliation: it records enough
operation identity and intended effect to retry an incomplete change safely.
The managed runtime resumes the owning journals at startup and periodically,
independently of platform discovery configuration. Each owner retains its
revalidation and publication holds until recovery confirms the recorded effect.
Access that depends on an uncompleted identity/grant change remains blocked until
the required stores agree. A failed revocation blocks the affected authority
locally while permission reconciliation completes. Reporting success requires a
read-back of the resulting identity and permission state, not a queued write.

This retains an explicit unavailable state rather than masking service failure
with cached file grants. It trades temporary availability for preventing a
partial link or failed revocation from becoming an access bypass.
