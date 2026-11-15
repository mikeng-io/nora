# Scoped authority and access

## Identity is shared; authority is scoped

A person may belong to several units and rooms within the existing
[workspace/unit/room topology](../room-topology/10-topology.md). Canonical identity
does not imply a shared transcript, a cross-unit memory read or universal access.
Management reads are scoped too: recognising a known account must not reveal
its other platform accounts, private memberships or another unit's profile.

Keto models the canonical person, the workspace, the unit and the room as
distinct subjects/resources. Membership and management authority are distinct
relationships. Permission rules state which authority inherits to which
contained resources; membership alone never inherits operator powers. The
application verifies the resource's actual containment rather than trusting a
caller-supplied unit or workspace name.

The namespace model Nora declares is small and fixed: `Workspace`, `Unit` and
`Room` carry containment relations (`room` → `unit`, `unit` → `workspace` —
written by Nora after it validates the real containment), and `Person` is the
only subject. Relations: `member` on a scope, `admin` on a scope for delegated
authority, and the permits each consumer checks — `speak`, `view` and the
scoped-management permits (`manage_members`, `manage_grants`, `delegate`).
Inheritance is declared in OPL: a `member` of a `Room` may `speak`/`view`
there, a `Unit`'s `admin` may `manage_members` in contained rooms, and
membership confers no management permit anywhere. Revocation is tuple deletion;
the permits never widen from platform observations.

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
variant always answers 200 with the same body; Nora uses the mirror so a
transport-level redirect or proxy cannot launder a denial into a success
shape. There is no `/check/open`. `POST /relation-tuples/batch/check` is
available for several subjects in one call; `GET /relation-tuples/expand` is
diagnostic only. `POST /opl/syntax/check` validates an OPL document's syntax
(text/plain body), and `GET /namespaces` reports what the service actually
loaded.

Keto does not validate that a tuple's object exists or that a claimed room
really sits inside the named unit — a check over unknown objects simply
returns `allowed: false`, indistinguishable from a true denial. Containment
and existence are Nora's own validation at write time, and a malformed,
conflicting or unparsable response is `unavailable`, never allow.

The development service runs `keto serve` with a sqlite DSN and
`namespaces.location` pointing at the OPL file — the config key whose URI
holds the namespace source in the v26.2.0 schema, with
`experimental_strict_mode` left unset. The write and read APIs stay on
private listeners; only Nora's service holds the write side.

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
Global profile changes require holder authority; local nicknames belong to the
local scope. Initial installation authority is established out of band, never by
the first person to issue an in-room command.

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

Keto evaluates only the relationships Nora writes. A check answer is allow or
deny; a malformed request, an unknown object, an error or a timeout is
unavailable, never allow. Containment — that a room belongs to a unit — is a
Nora validation on write, not something a permission tuple proves.

Kratos, Nora's binding store and Keto do not share an application transaction.
Nora's management service owns completion and reconciliation: it records enough
operation identity and intended effect to retry an incomplete change safely.
Access that depends on an uncompleted identity/grant change remains blocked until
the required stores agree. A failed revocation blocks the affected authority
locally while permission reconciliation completes. Reporting success requires a
read-back of the resulting identity and permission state, not a queued write.

This retains an explicit unavailable state rather than masking service failure
with cached file grants. It trades temporary availability for preventing a
partial link or failed revocation from becoming an access bypass.
