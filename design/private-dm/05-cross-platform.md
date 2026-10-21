# One private conversation across linked platforms

Status: proposed, 2026-10-21. Operator decisions, not runtime receipts.
This extends [the contract](00-contract.md) on the shipped
[unit-scoped workspace implementation](../computer-unit-scope/00-contract.md).
That prerequisite is satisfied by #164, v6.5.0 and the operator-verified
2026-10-21 migration; [the runbook](../computer-unit-scope/01-migration.md)
records #168 and the cutover lessons. DM-specific changes remain proposed.
Decision recorded at merge; no behavioural number is reserved.

## 1. Accounts are entry points to one person+unit scope

WhatsApp and Signal text ship together in the first integrated PR. Add inbound
Signal DM support at `nora/platform/signal/parser.py::parse_event`; today its
no-groupId branch drops the message. WhatsApp already normalises DMs. Discord
DMs and Instagram DMs are deferred: Discord drops them, and Instagram treats
unknown is_group as group. A missing classification is never a new DM grant.

Keep the bundle's global `[dm] enabled` switch (default false), DM person
allowlist, default-unit resolution and explicit unit-membership requirements.
Within that authority, every operator-linked platform is enabled by default.
Proposed identity field: per-person `dm_platforms.<platform>.enabled`, a boolean
with omitted=true and false opting that platform out. It governs all accounts
linked for that person on that platform, admission and every later send.
Unsupported transports remain unavailable until their implementation is built;
default admission policy does not claim Discord/Instagram support in this slice.

Only operator-authored account IDs establish the person link. Display names,
profile names, text claims and contact guesses never match. Resolve the sender
and direct chat recipient to the same authorised person before any side effect.
No linked account grants another person's or another unit's private history.

The trade-off is explicit: compromise of any one admitted linked account exposes
the entire person+anchor-unit transcript and DM workspace, including logins,
across those platforms. Opt-out removes an entry point; it cannot undo material
already disclosed. Per-line platform labels provide provenance, not separate
privacy partitions. This trade-off is the operator's chosen continuity model.

## 2. Transcript, turn slot and reply address

Reuse proposed `dm_scope`'s unique `(person_id, anchor_unit)` binding. Its
`dm_scope_id` identifies one transcript across verified accounts/platforms.
Read pages through the scope selector in [storage](02-storage.md), never
`nora/infra/sql/audit.py::SqlRecordStore.recent` by wire conversation ID.
Interleave by the stable Record paging cursor and render a platform marker on
every line alongside speaker/time. Keep the original transport timestamp too;
arrival order is not a claim that two transport clocks agree.

Admission binds scope and inbound `(platform, direct target)` once. Proposed
DM turn-slot key: `dm:<dm_scope_id>`. Acquire it before loading transcript and
retain it through model execution, guarded delivery/audit and private state
updates. Existing debounce/queue machinery can provide the slot, but its key
must span platforms. A second platform may record a new bound inbound while
waiting; it cannot start a simultaneous turn over that transcript. Deferred
result composition takes the same slot. Another DM scope remains independent.

Send a reply only through the inbound platform to its original direct target.
Retries/fallbacks cannot pick a different linked account when that adapter is
down. Scope is a read/serialization key, never a send destination. Quotes,
edits, touches and deduplication retain platform-local wire keys plus original
scope/author checks; a remote ID or timestamp collision cannot cross platforms.

Changing default unit selects a different dm_scope and workspace, starting new
history or resuming that unit's existing partition. It never copies old private
history or retargets queued work. Existing unit publications keep their anchor.

## 3. Computer, browser and export are in the first slice

Ordinary authorised DM turns may use computer tools and launch subagents in the
first integrated PR. This reverses the earlier deferral of computer/export and
unsolicited private jobs: a completed requested job may now initiate its deferred
private result send. It does not enable arbitrary proactive private outreach.
The internal raw-capable Cognition curation phase keeps its restricted effects;
computer tools are not a bypass for [publication](00-contract.md#2-the-publication-boundary).

The shipped `nora/domain/computer.py::computer_unit_scope` (line 78) takes
`sha256(UTF-8("unit:" + exact_policy_unit_name)).hexdigest()[:32]`;
`computer_scope` (line 70) retains the legacy `platform:conversation_id` room
hash for migration/provenance only. Proposed DM derivation follows that style:
`dm_hash = sha256(UTF-8("dm:" + str(dm_scope_id))).hexdigest()[:32]`, with the
UUID rendered in canonical lowercase hyphenated form. The `dm:` domain tag is
inside the hash, never a literal directory prefix. It separates the DM input
namespace from `unit:` and supported room-platform prefixes; no room or unit
identifier is reused as a DM key. As with existing truncated hashes, this is
cryptographic separation, not a mathematical guarantee of no hash collisions.

Use `/workspace/<dm_hash>/`, the existing `c-<dm_hash first 12 hex>` user from
`deploy/computer/computerd.py::scope_user` (line 147), and 0o700 ownership from
`Computer._ensure_scope` (line 471), subject to the shared owner-allocation
prerequisite below. This is person+unit scope, not a global per-person directory.
Both admitted platforms share its files/browser profile.

**Shared prerequisite — fail closed on Unix owner-name collisions.** Distinct
full scope hashes can share their first 12 hex characters, so `scope_user`
assigns them the same Unix name. 0o700 does not isolate directories owned by the
same UID. The shipped `deploy/computer/computerd.py::Owner.ids` (line 166)
reuses an existing user by name without verifying its full scope. This latent
risk already affects unit and legacy room scopes; it is not a DM-only defect,
and #164/#168 did not fix it. DM enablement requires this shared fix first:

- Keep `c-<12 hex>`. Record each owner name's **full scope** in one persistent,
  root-owned map outside writable workspaces (root-only directory 0o700, map
  0o600). Atomically check/reserve that mapping under one allocation lock shared
  by every creator/binder, before mkdir, user creation/reuse, chown, cached owner
  reuse or scope/session binding. Computerd, migration, restore and privileged
  broker helpers must honour the same check; a Nora-only admission check is
  insufficient.
- A reservation, existing directory or Unix user attributable to a different
  full scope refuses the operation and emits an operator-visible alert. An
  existing user/directory whose full-scope ownership cannot be established also
  refuses; never infer identity from the 12-hex prefix, overwrite the map, reuse
  the UID for another scope or fall back to the anchor's owner. Keep both scopes'
  data and ownership untouched. Same full-scope reuse requires the matching map
  entry and directory/user ownership, not merely a matching user name.
- Seed the map for existing unit/room workspaces only from a checked complete
  full-scope/user/directory inventory; duplicate claims or unaccounted existing
  users block binding pending reconciliation. Restore must reconstruct/check
  that root-only allocation state before attaching any workspace. This is the
  minimum shared check, not a change to hashing or Unix user naming. The
  [collision acceptance case](04-build-and-acceptance.md#2-deterministic-proof-and-actual-inputs)
  must pass before claiming group-to-DM filesystem isolation.

The unchanged `SCOPE_RE` in computerd and archive_check accepts 16–64 lowercase
hex; `deploy/computer/migrate_unit_scope.py::inventory` (line 77) accepts exactly
32 through `SCOPE` (line 27). The DM hash fits all three. The existing
`deploy/computer/nora-computer` backup scope count and archive layout need no
DM naming exception. The shipped backup currently excludes browser directories;
the required [backup repair](04-build-and-acceptance.md#2-deterministic-proof-and-actual-inputs)
replaces that blanket exclusion and archives every regular file privately.
The legacy room-to-unit migration planner must never remap a DM hash into a
unit: inventory recognition is not migration authority. No new migration of
already migrated unit workspaces is needed for DMs.

Extend the existing `nora/agent/tools.py::build_tool_specs` binding (lines
14602–14603): groups retain `computer_unit_scope`; an admitted DM binds its
server-resolved dm_hash. All commands, file/export and browser calls use that
bound scope, never the anchor's scope. Reuse
`nora/infra/sql/computer_sessions.py::SqlComputerSessionStore.continue_or_open`
and `for_scope` (lines 56 and 112); new DM sessions store dm_hash while keeping
their creation platform/target as protected provenance. Migrated group rows
still use the existing effective `_workspace_scope` (line 35). Extend
`nora/engagement/computer_context.py::ComputerContextReader._read` (line 115)
to select the admitted DM scope for sessions/events/state. Group context and
`computer_session_list` keep selecting only the unit scope; by-ID event/detail
reads must check that same bound workspace before returning private data.
Unknown/missing DM authority returns unavailable, never an anchor-unit fallback.

Host-side commands as a DM user must use
`sbx exec -u root nora-computer runuser -u <dm-user> -- …`, including broker,
backup/restore readback and operational helpers; direct sbx user lookup fails
for users created after sandbox boot (#168). Ordinary computerd execution
retains its existing `Computer._user` UID/GID drop (line 465), rather than
introducing sbx into Nora or giving DM commands root. Any new process launcher
that switches by scope-user name uses runuser inside the VM. Batch host readback
per scope to limit sbx calls.

| Access | Permission |
|---|---|
| DM → own workspace/browser | Read/write within its dm_scope |
| DM → anchor unit workspace | Read only |
| DM → unit workspace write | Explicit curated publication only |
| Group → any DM workspace | None, including the requester's own DM |
| Another person or anchor → DM workspace | None |

The DM Unix user does not receive the unit user's identity or writable directory
access. Implement unit reads as a code-bound read-only broker through existing
computer runtime/file operations: it resolves the anchor scope server-side and
can return/copy file bytes into the DM scope. It cannot execute an arbitrary
command as the unit user or accept an arbitrary destination scope. Raw shell
paths remain blocked by Unix ownership. This supplies actual unit reads without
a writable mount defeating the publication boundary.

A proposed shared file write is a publication of its full visible bytes/name/
metadata to the unit audience. Use the existing publication judgement and bound
receipt contract, extended to that file target, before privileged copying into
the unit scope. Hold/refusal/ERROR leaves shared files untouched. No automatic
sync, shared scratch folder or command run as the unit user is permitted.
A useful safe file publication must work; denying every write is not acceptance.

DM workspace contents, terminal/browser state, cookies and logins are private
evidence. Files copied from the unit can acquire private annotations and remain
private until published. Scoped export may return files to the admitted owner
on the pinned private target; a public link, shared document or group file is
an explicit publication, not an automatic way around curation. Group readers
receive only the published artifact, never DM source paths or browser profiles.

Non-job computer mutation/browser leases use `scope:<dm_hash>`, by passing
dm_hash to the unchanged `nora/domain/computer_leases.py::lease_key` (line 48;
`scope_key` at line 44), independently of the anchor unit's lease. A different
session cannot split that lock. Keep the existing bounded live wait/background
refusal and read behaviour. The separate `dm:<dm_scope_id>` turn slot serializes
whole DM turns; it is not a computer lease. Subagents retain `job:<id>` from
`nora/agent/tools.py::build_tool_specs` (line 14833), their project convention
and job/audit records rather than a live-turn computer session. Job leases do
not promise filesystem exclusion from the DM turn or another job.

The hash fits the unchanged backup/restore format; ordinary venv/browser
artifacts are a separate existing backup-safety defect. The required shared
backup preparation and restore acceptance are in
[build and acceptance](04-build-and-acceptance.md#2-deterministic-proof-and-actual-inputs).
DM enablement cannot treat a tar creation receipt as a restorable backup.

## 4. Subagent completion is a deferred private send

Reuse the existing deferred-job outbox/poller and guarded delivery. At launch,
retain person, dm_scope_id, anchor unit, platform and exact direct target with
the job. The worker uses that DM workspace, bounded tools and private outputs;
it gains no group send capability. Computer audit rows keep the originating
private transport/target under existing protected provenance, not a synthetic
unit destination. Operational projections use opaque IDs as before.

Before sending a result (including retries), re-resolve the active person,
recheck DM grant, anchor/default-unit binding, direct target ownership and the
launching platform's opt-out. Failure holds/refuses private delivery and keeps
the result private; it never redirects to a new default unit, another linked
account, a group or a newly selected platform. Successful delivery is once to
the launching platform/target, through the DM turn slot and guarded sender.
A private outcome is not a licence for a public export or group notification.

## 5. One person's DM spend is bounded

Add a per-person DM budget for subagent and computer use, shared across that
person's linked platforms and anchor units. Reuse existing runner limits and
budget accounting; aggregate model tokens/cost and computer runtime at person
scope instead of granting a fresh allowance per account/room/job. Limits and
the accounting window must be explicit in the proposed DM budget configuration;
this proposal chooses no arbitrary numeric allowance.

Admission reserves budget atomically before starting paid work or computer
execution, including concurrent job launches; completion settles actual usage
and releases unused reservation on cancellation/failure. Exhaustion refuses new
work before spend, without erasing completed results. Existing per-job/per-call
bounds still apply. Another person's allowance is independent; a default-unit
change cannot reset the exhausted person's allowance. Implement and prove this
with synthetic accounting in [acceptance](04-build-and-acceptance.md); no paid
evaluation is authorised by this design task.
