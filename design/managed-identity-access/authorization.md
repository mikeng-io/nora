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
