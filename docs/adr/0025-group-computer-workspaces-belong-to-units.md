# 0025 · Group computer workspaces belong to policy units

- **Status:** accepted (operator decisions in the merged computer-unit-scope contract, 2026-10-21)
- **Date:** 2026-10-21
- **Supersedes, in part:** [0019](0019-noras-computer-runs-in-an-sbx-microvm.md), Decision 4's room hash. The shared VM, opaque scopes, per-scope Unix ownership and working-disk limits remain.
- **Index:** a DEC number is taken from origin/master at implementation merge; no number is reserved.

## Context

The merged [contract](../../design/computer-unit-scope/00-contract.md) requires a
job started in one group transport to be resumed from the unit's other groups.
Room-hashed working disks, browser profiles and session reads prevented that.
The existing unit policy is the trust boundary; room membership remains separate.
This is the operator's settled scope choice, not a new runtime or storage service.

## Decision

Hash UTF-8 `unit:<exact policy unit name>` to 32 hex characters, once from the
turn's code-bound unit. Unknown rooms and private DMs get no group workspace.
Use that opaque key for the workspace, browser, context and session selection,
and `scope:<key>` for every non-job mutating call. Jobs retain `job:<id>` and
report to their launching room. Audit and session creation-room provenance stay.

Migrate old workspaces whole to `rooms/<old-scope>/`, copying and verifying
inventory, SHA-256 digests, modes and new ownership before separately retiring
the sources into root-only quarantine. Preserve symlinks without following them.
Keep old browser profiles as data and start a fresh unit profile. The offline
[runbook](../../design/computer-unit-scope/01-migration.md) gives the dry-run,
backups, process drain, metadata binding, verification and recovery order.

Session metadata uses the existing `stopped_at_detail.workspace_scope` and
`resume_prefix` fields. Original `scope`, IDs, objective, timestamps, actor,
status and audit rows stay unchanged. The new `workspace_scope` property and
`for_scope` selector read the effective workspace. Among multiple migrated open
sessions, reuse the most recently active (UUID descending breaks ties); retain
all the other tasks and states. The existing unique index continues to enforce
one newly opened session per scope; no schema or automatic data migration runs.
Unit reassignment does not transfer the old unit's shared disk.

## Consequences

**Better:** files, sessions and browser state are shared across a unit's group
transports; different units keep the existing Unix isolation. A single admission
key prevents two room/session identities splitting the computer lock.

**Worse:**

- Every group in the unit can read its files and browser logins regardless of
  different membership. A room needing privacy requires its own unit.
- Cutover needs paused admission, drained jobs/processes, temporary duplicate
  disk space, verified backups and explicit metadata binding. Running browser
  and tmux state does not transfer.
- Migrated absolute paths remain historical text; resumption uses the displayed
  room prefix. A legacy browser login requires an explicit operator choice.
- Multiple migrated open tasks remain visible; only the most recently active
  is continued by a new call. Jobs retain their existing shared-files concurrency
  ceiling and do not exclude other jobs or unit turns.

This holds unless source drift, a failed migration/isolation case, or measured
concurrency failure invalidates the implementation mechanics. The unit boundary
itself remains the operator's closed decision.

## Rejected

- Per-room file filtering: scope, rather than a filter, is the read boundary.
- Flattening projects or merging cookie databases: collisions and login ambiguity.
- Rewriting audits, expiring or deleting colliding legacy sessions: loses history
  or changes state solely to satisfy a storage index.
- Per-unit VMs or another storage service: the existing scope owner is sufficient.
