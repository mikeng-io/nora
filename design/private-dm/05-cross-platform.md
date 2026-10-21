# One private conversation across linked platforms

Status: proposed, 2026-10-21. Operator decisions, not runtime receipts.
This extends [the contract](00-contract.md) and depends on the integrated
[unit-scoped workspace change](../computer-unit-scope/00-contract.md), including
its migration. Decision recorded at merge; no behavioural number is reserved.

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

Proposed workspace derivation: first 32 hex of SHA-256 over UTF-8
`dm:<dm_scope_id>`. Use `/workspace/<dm-hash>/`, its own `c-<hash12>` Unix user
and 0o700 directory in the existing microVM. It is a user scope of person+unit,
not one global per-person directory. All admitted platforms for that scope use
the same files, computer session and browser profile. Bound scope chooses all
computer/session/browser reads; group session listings never include DM rows.

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

Non-job computer mutation/browser leases use the DM workspace key independently
of the anchor unit's key. The dm_scope turn slot still serializes conversation
turns. Subagents retain `job:<id>` leases and their existing project convention;
these job leases do not promise filesystem exclusion from the DM turn.

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
