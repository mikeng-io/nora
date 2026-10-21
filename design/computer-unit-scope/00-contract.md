# Group computer work belongs to the unit

Status: proposed, 2026-10-21. Operator decisions are settled; implementation,
migration and enablement are not delivered by this documentation change.
Decision recorded at merge: take the number from origin/master when behaviour
lands, with its enforcing tests, following nora-decision. No number is reserved.

## 1. Contract and cost

A job started in a unit's Signal group must be resumable from that unit's
Discord or WhatsApp group. Group computer work therefore belongs to the unit
named by `policy.toml`, rather than to a transport room. Different units never
share a workspace, even if their members or Honcho workspace are identical.

This aligns the computer with existing unit-shared Persona/Notebook and the
unit trust boundary. DEC-105 permits `send_to` between rooms of one unit,
subject to its independent asker/destination checks; it does not equate their
membership. `nora/policy.py::_ConversationEntry.persons` is still per room.
A file made in room A becomes readable in room B of that unit even when B has
different members. This is the same exposure class that unit relaying already
permits, with persistent files and browser logins now included. It does not
inherit the relay's per-request membership checks. A room needing privacy
from its unit belongs in its own unit; no per-room file filter is proposed.

Only group rooms use this scope. Private DMs require the separate person-plus-
anchor scope in the [private DM bundle](../private-dm/00-contract.md).
This design is its prerequisite, not permission to give a group access to DMs.

## 2. Source baseline and existing seams

Inspected origin/master: `5856cd9f08cb16f273e7d992e7e9bc42d9606a36`.
These are source observations, not a live deployment readback.

| Existing seam | Today | Proposed change |
|---|---|---|
| `nora/domain/computer.py::computer_scope` | SHA-256 of platform plus conversation ID, first 32 hex characters | Group scope hashes a unit-domain key |
| `nora/agent/tools.py::build_tool_specs` | Binds computer scope from the turn's room | Resolve the room's policy unit once and carry its scope |
| `deploy/computer/computerd.py::scope_user` | `c-<scope first 12 hex>` owns the scope | Keep this user scheme, changing the scope input |
| `deploy/computer/computerd.py::Computer` | Scope directory 0o700; commands run as its owner | Keep isolation and path checks at the unit boundary |
| `nora/domain/computer_leases.py::lease_key` | Session key when present, scope key otherwise | Non-job group callers always use the unit scope key |
| `nora/infra/sql/computer_sessions.py::SqlComputerSessionStore.for_room` | Filters platform and conversation ID | Query the bound unit scope |
| `nora/engagement/computer_context.py::ComputerContextReader._read` | Reads room sessions and room workspace state | Read unit sessions and unit workspace state together |
| `nora/agent/tools.py::build_tool_specs` browser closure | Passes bound room scope to browser | Pass the same bound unit scope |

[ADR 0019](../../docs/adr/0019-noras-computer-runs-in-an-sbx-microvm.md)
keeps one persistent shared sbx microVM behind computerd. Its room-hash clause
is the part this proposal changes; no VM per unit, new sandbox runtime or
second storage service is introduced. Per-scope Unix users are the current
implementation, later than the ADR's original common-user wording.

## 3. Resolve once; keep origin separately

Proposed helper name: `computer_unit_scope(unit)`, producing the first 32 hex
characters of SHA-256 over UTF-8 `unit:<exact policy unit name>`. The prefix
separates it from legacy room hashes and the future DM scope domain. Do not
normalise two operator-authored unit names into one. The model never supplies
or chooses a scope. Unknown/unlisted room or unresolved unit means unavailable,
never a default workspace inferred from the first configured unit.

The resulting path is `/workspace/<unit-scope>/`; raw platform IDs and unit
names do not reach the VM. Keep `c-<hash12>`, directory 0o700, scope-owner
command execution and existing no-follow path handling. Scope resolution is
Nora-side; computerd accepts the opaque scope, not policy files or membership.

Carry both workspace scope and originating `(platform, conversation_id)`.
Audit/events retain the originating room, actor and tag; do not rewrite their
historical provenance as if the operation originated in a synthetic unit room.
Computer sessions gain unit-scope selection while retaining creation-room
provenance. Session IDs and audit references remain stable across migration.

## 4. Leases, browser and session reads

Use `scope:<unit-scope>` for non-job group computer busy checks/acquisition,
even when a computer session exists. Signal and Discord calls in one unit
serialize through one key; different session IDs cannot split the lock.
Preserve current live bounded wait/background refusal in
`nora/domain/computer_leases.py::ComputerLeases.acquire`. Release-in-finally is
the existing caller-side behaviour, not part of acquire. Reads keep their
current lease behaviour. This is computer-operation exclusion, not serialization
of all group conversation turns or long-running OS processes.

Subagent jobs keep their own `job:<id>` lease and project-folder convention.
They use the launching unit's workspace but results still report to the room
that launched the job. A job key does not exclude another job or a unit turn:
the existing shared-files concurrency ceiling remains; no new job-wide lock
is implied. Do not replace the outbox's destination with the workspace key.

When `[computer.browse]` is enabled, one unit uses one browser scope/session,
including cookies, logins and resumed tabs. All its group transports see that
state. Different units must not reuse a profile. Browser task admission uses
the same unit computer lease; scope identity alone is not mutual exclusion.

`computer_session_list`, the session context block, session opening/reuse and
expiry/state reads become unit-level. Proposed store selector name: `for_scope`
replaces the room predicate of `computer_sessions.for_room` for these callers.
A room can list sessions started by another room of its unit, not another unit.
By-ID session detail applies the same scope check before reading events.
Session origin is descriptive provenance, never an alternative access grant.

## 5. Move existing work without collisions or loss

Migration belongs in the integrated implementation. It runs with computer
admission paused and all holders/jobs drained; stop remaining old-user browser
and tmux processes cleanly before changing ownership. Record their state, since
moving files does not transfer running processes or resume their sessions.

1. Back up the workspace disk and session metadata. Inventory each legacy
   room hash against the operator's current policy unit mapping on the host;
   carry only opaque old/new hashes into the VM. Unmapped/ambiguous rooms are
   held untouched for operator mapping, never guessed into a unit.
2. Create the unit directory/user using the existing ownership mechanism.
   Place the entire old workspace at
   `/workspace/<unit-scope>/rooms/<old-scope>/`. Two old rooms with `report.txt`
   keep two distinct files. Preserve nested paths, hidden files and browser
   data; do not flatten, overwrite or combine independently named projects.
3. Change migrated ownership to the new unit user/group, preserving executable
   bits and restrictive modes; the unit root stays 0o700. Use privileged,
   no-follow traversal so symlinks cannot chown or copy another scope's data.
   Verify content digests, inventory and ownership before retiring the source.
4. Keep historical audit rows unchanged. Bind session listing/reuse to the new
   scope, retaining each session's original room and its old-path mapping.
   Old objectives/paths remain historical text; show the `rooms/<old-scope>/`
   prefix for resumption instead of promising that an absolute path still works.
5. Keep legacy browser profiles in their room subdirectories as preserved data.
   Start the unit browser with a fresh profile; do not merge cookie databases
   or arbitrarily choose one room's active login. Restoring a chosen profile
   requires an explicit operator choice; no legacy login data is deleted.
6. Read back both files with the new owner, deny reads from another unit user,
   and reconcile every migrated session. A pre-existing destination is verified
   as that same completed move or migration stops without overwriting it.
   Keep backups until successful readback; an interrupted move must be
   reconcilable before admission resumes. On failure leave admission paused,
   restore the backed-up mapping/data, and report unavailable rather than empty.

Unit reassignment of a room does not silently move/copy the old unit's entire
shared workspace to its new unit. That is an explicit operator data move.
The workspace remains working disk, not canonical export storage, as in ADR 0019.

## 6. Integrated acceptance

Implementation ships scope binding, migration, leases, browser/session changes
and their proof together. Proposed acceptance cases, using existing runtime
contracts and synthetic rooms/units:

- Write from Signal, resume/read/edit from Discord and WhatsApp in the same
  unit; another unit cannot list or read the files, even through raw commands.
- Different per-room membership does not imply file privacy inside the unit;
  assigning the private room its own unit restores that workspace boundary.
- Simultaneous mutating calls from two rooms/sessions contend on the same unit
  key. Different units progress independently; background refusal/live timeout
  remain unavailable. A subagent still takes `job:<id>` and reports only to
  its launching room; its audit records that room.
- Browser login/tab continuity spans the unit's rooms; another unit has no
  profile access. Concurrent browser callers use the unit admission key.
- Session list/context/by-ID reads cover the unit's sessions and no other
  unit, preserving creation room, actor, tag, events and expired-session state.
- Migrate two legacy scopes with matching filenames, hidden files and browser
  data: all bytes survive under distinct room prefixes, unit ownership works,
  old users lose access, and session/audit provenance remains re-derivable.
- An unmapped scope, an interrupted move or a conflicting destination preserves
  sources and prevents partial enablement; a verified repeat does not duplicate
  or overwrite data. No claim of live migration is made by this proposal.

The operator's unit choice is closed. Reopen implementation mechanics only on
source drift, a failed migration/isolation case or measured concurrency failure.
No runtime/config/tests/migrations, paid evals or deployment are authorised here.
