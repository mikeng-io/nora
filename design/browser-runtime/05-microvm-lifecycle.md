# Automatic scoped MicroVM lifecycle

Status: source implemented in the stacked child; **NOT INSTALLED OR LIVE-VERIFIED**. Operator direction, 2026-11-09:
automate provisioning using the existing Docker Sandboxes runtime rather than
adopting OpenSandbox. This document extends the
[scoped browser contract](00-design.md#current-target--scoped-anonymous-browser-2026-11-02)
and belongs to [outcome #520](https://github.com/mikeng-io/nora/issues/520).
The implementation is a child PR based on the completed design head. No host installation,
configuration change, deployment or paid evaluation is part of this revision.

## Baseline and outcome

PR #584 merged as `f3bcfe7b6fcf3ecc1d44cd57d724a303e8cbc00c`.
At inspected master `6e3de0a75556c2ffc95ee2cd8628a019c679b35b`,
`FileScopeEndpoints` reads a host-written registry; an absent scope is unavailable.
`nora-computer scope provision`, `serve` and `observe` create the sandbox,
supervise computerd and publish its verified boot identity. The wrapper supplies
3 GiB and two vCPUs by default; these are guest sizing, not a guaranteed physical
RAM reservation. Scope routing, SQL execution admission, browser tab expiry and
capture already exist; automated host VM lifetime does not.

The outcome is an admitted computer operation that obtains its own scope's VM
without an operator manually provisioning it. Stopped VMs restart with their
workspace intact. Quiet VMs stop consuming running resources while their disks
remain available. A unit or canonical `(person, anchor unit)` owns persistent
state; ownership does not require an always-running VM. Existing DM admission
requirements remain in force; provisioning never enables a DM by itself.

Non-goals: another sandbox platform, multi-host scheduling, memory ballooning,
snapshot/hibernate restoration, automatic disk deletion, login/vault, a browser
engine change, new models or a new task/queue system. Reopen the sbx choice only
if measured lifecycle or isolation requirements cannot be met through it.

## Authority and control boundary

Nora continues to have no Docker/sbx socket, host shell or container-management
credential. Only the host lifecycle controller invokes the existing
`nora-computer` wrapper. It owns the registry and its serialization; the app
continues to read that directory read-only.

Use a dedicated Unix socket in a mounted control directory for a narrow lifecycle
API. Authenticate with the existing computer bearer through the existing secret
delivery path, restrict directory/socket permissions to the installed service
and intended app reader, and expose no network listener. This is a scope lifecycle
socket, never a proxy for Docker or arbitrary commands. The host chooses the
installed image, sandbox name, resource size, port and wrapper arguments.

The app supplies only the canonical scope already resolved by admission, an
operation identity and an opaque lease handle where required. The controller
validates the scope format and request shape; the model cannot choose them.
Application admission remains the authority for unit/person grants and is
rechecked when queued work reaches execution. Token possession is trusted-app
authority, not an additional model-facing tool. Unauthenticated or malformed
requests must have no host effect. Revocation prevents new acquisition; running
work follows existing cancellation/drain semantics. It never deletes the disk.

The API exposes acquire/release/idle-stop and passive observation. Leases are durable operation ownership,
not expiring timers: a long call cannot lose protection because renewal was delayed.
Release and recovery require positively quiet guest state; an elected successor
may reconcile an abandoned operation only after the existing SQL owner permits it.
Passive observation remains the read-only registry and authenticated guest state. Idle
stop uses the existing elected maintenance owner, with the controller checking
its own lease state too. No arbitrary create parameters, exec or automatic
destroy operation is exposed. Lifecycle status is typed: absent, starting,
running, stopping, stopped or unavailable, with scope and observed instance.
A start accepted by the controller is not yet a ready computer.

## Acquire, recovery and capacity

1. Resolve the admitted scope once and claim the existing computer execution
   segment. Acquire the controller lease before native, file, worker or browser
   work; nested calls inherit the segment rather than acquiring behind themselves.
   Context, health and maintenance observations do not acquire or wake a VM.
2. Serialize same-scope lifecycle transitions and global capacity admissions.
   Reserve a starting slot before launching. Concurrent requests for the same
   scope join one measured transition, never create duplicate VMs.
3. For a genuinely absent owned scope, use the wrapper's provision/serve/observe
   path. Read back per-sandbox egress before publishing readiness. For an owned
   stopped VM, restart that disk and supervise computerd; do not recreate it.
4. Publish readiness only after authenticated live scope and nonempty boot
   identity agree with the host registry. The app retains its independent identity
   check. Changed boots expire old handles; no automatic replay of uncertain work.
5. Release after execution has drained. Long operations retain their durable lease; timeout
   and cancellation stop/drain through existing ownership before release. A lost
   response is reconciled by operation identity and measured host state, never by
   blindly issuing another create or repeating a guest action.

Configure an explicit maximum of running/starting managed VMs and host memory
headroom before enabling automation; no unlimited default. Guest RAM/vCPU sizing
comes from the existing host wrapper. Count starting reservations and existing
owned running VMs, including those discovered after controller restart. Other
host workloads and unmanaged/eval VMs consume headroom even though they do not
belong to this controller. Unknown inspection or resource readings cannot count
as spare capacity. Serialize resource checks with reservations so two starts
cannot both spend the same measured headroom.

At capacity, leave existing active VMs alone and return a typed capacity-unavailable
outcome. Only the existing durable task queue can report queued; the controller
adds no second queue and performs no hidden retry loop. A future queue attempt
must still meet its original deadline and current admission. Headroom is an
admission measurement, not a guarantee against later allocations by other host
services. Do not promise that configured virtual RAM fits resident RAM merely
because a lightly used VM has small RSS.

Reconcile controller restart and partially completed provisioning against owned
registry/state and actual sbx identity. Persist the operation/ownership information
needed to distinguish a controller-created partial VM from a foreign sandbox.
An unowned sandbox, conflicting name/port, missing disk, corrupt record or
uninspectable state is unavailable, never adopted, deleted or reset to escape
the conflict. Do not trust pre-restart leases blindly: reconcile their owners
and guest work before accepting stop or new execution. Record the lifecycle
transition using the existing host audit spool; retain uncertainty on failures.

## Quiet stop and retained state

Reuse browser expiry: task-owned tabs age out, then Chrome/Xvfb stop. A VM can
stop only after the VM idle interval, with no execution lease, admitted work,
resumable tab, pending browser command or native/detached process. Guest quiet
must be positively observed; a failed state read is not idle. Background page
traffic, registry enumeration, context rendering, browser sweeps and queue wait
must neither wake a stopped VM nor refresh its user-activity timestamp.

The app's stop transition holds the same per-scope elected maintenance ownership
that excludes real execution; the host serializes it with acquire and verifies
no live lease before stopping. No read-quiet-then-stop window may admit new work.
Lost maintenance ownership cancels the transition. If stop is uncertain, withhold
new dispatch until reconciliation; never send work to a stopping VM. Verify the
VM actually stopped before reporting resources released.

Stopping preserves workspace files and installed packages on the same disk.
Browser tabs, cookies, processes and in-memory sessions have no restoration
promise; old handles expire explicitly. A restart creates fresh anonymous browser
state only when requested. Existing scoped backup/restore and explicit destructive
operator operations retain their contracts. Revocation retains data; no retention
expiry, automatic rm or cleanup ledger is introduced.

## Existing seams and implementation sequence

One integrated implementation against this revision, followed by its focused
checks and required independent review:

| Existing owner | Required delta |
|---|---|
| `deploy/computer/nora-computer` | Reuse provision/serve/observe and backup; add measured disk-preserving restart/stop without weakening ownership or egress checks. |
| Host installation and release packaging | Install/supervise the controller and scope services, mount only its control directory and read-only registry, and validate host limits/credentials. A repository merge cannot install host files. |
| `nora/domain/computer_vms.py`, `nora/infra/computer/scoped.py` | Keep passive endpoint/state reads distinct from authorized acquire; all sandbox verbs retain exact scope/instance validation. |
| `nora/app/composition.py`, `nora/app/computer_queue.py` | Wire lifecycle acquisition to admitted segment ownership, capacity failure, cancellation and restart reconciliation; reuse existing queue and journal. |
| `nora/infra/computer/manager.py` | Hold lifecycle ownership for browser work; expiry/maintenance cannot wake sleeping VMs or race new acquisition. |
| Existing context, audit and configuration contracts | Render observed unavailable/stopped state without side effects; reuse declared typed state and existing telemetry rather than adding model prompts or a separate audit store. |

First trace every foreground/background caller, then implement control + app
ownership together. Validate with generated scopes and realistic sbx wrong-call
responses. No full local suite is a PR prerequisite. Deploy only after the host
installation, validated configuration and operator notification procedure; read
back the installed behavior separately from source checks.

## Acceptance

The requirements below remain the acceptance contract. Local delivery evidence
and unavailable live boundaries are recorded after the table. Source tests do
not constitute an installed-host journey.

| Property | Required evidence |
|---|---|
| First use and reuse | Real admitted native/file/browser paths obtain the exact group or canonical DM scope; repeated and cross-transport use reuse its disk. Unadmitted requests cause no provisioning. |
| Concurrent acquisition | Concurrent same-scope requests create once; different scopes cannot exceed the starting/running bound or spend the same headroom reservation. Positive control proves a permitted start ran. |
| Readiness and isolation | Foreign/empty/stale boot identity, corrupt registry, bad authentication, unknown host state and startup timeout all return unavailable; no other-scope/shared fallback. |
| Restart and partial failure | Stop/start retains generated files and expires old boot handles. Crash at each provision/serve/observe boundary recovers owned partial state without duplicate creation, foreign adoption or data loss. |
| Idle and active work | Idle expiry stops a genuinely quiet VM; active/long-running/native-detached/browser work blocks stop. Acquire-versus-stop and ownership-loss probes exercise the real serialization, not a fake that always reports quiet. |
| Passive reads | Registry/context/health/browser maintenance against a stopped VM starts nothing, renews no user activity and reports stopped/unavailable honestly. |
| Failure and revocation | Controller loss, cancellation, capacity refusal and revoked admission preserve original task/deadline/result semantics; an uncertain effect is never blindly replayed. |
| Packaging and live journey | Installed controller, limits, socket permissions, read-only registry, credentials, exact guest identity/egress and service restart are read back; generated first-use → file/browser work → idle stop → disk-preserving wake succeeds. Capture/export acceptance remains separate. |
| Resource effect | Compare cold/warm and idle/active whole-VM cost with browser descendants, PSS/RSS, swap, pressure and concurrent demand. Attribute the browser's share by guest measurements; configured RAM or host Chrome processes alone do not establish it. |

The existing scoped browser/worker outcome is not closed by lifecycle delivery.
No Lightpanda performance arm, paid model comparison or production data fixture
is required to prove this slice.

## Reconciled execution contract

`ComputerAdmission.run_segment` is the acquisition boundary after SQL election;
maintenance segments never acquire. Nested work inherits the segment. The existing
runner's rebuilt calls pass through admission again. Acquisition failure closes
the unstarted coroutine and returns `unavailable` with `no_guest_effect`, so queue
settlement need not infer quiet from a VM that never started. Guest effects are
never retried by lifecycle code. `binding`, `state`, `instance`, registry reads and
context readers remain passive. A stopped record has an empty published instance
before stop, preventing a cached binding from probing and accidentally waking it.

A single installed controller serializes host transitions globally (including the
bounded readiness wait). This deliberately simple lock reserves capacity across
concurrent starts and excludes acquire during stop; per-scope parallel startup is
not required. Explicit maximum slots, configured guest memory and minimum host
headroom are mandatory host settings. Linux MemAvailable must cover guest memory
plus headroom, without relying on guest RSS. Unknown inventory refuses admission.
The wrapper alone runs sbx; the controller invokes only its fixed scope verbs and
installed scope service template. Its private durable operation record precedes
creation. Ambiguous or conflicting ownership remains unavailable; no deletion,
foreign adoption or reset is recovery.

Manual mode remains available with an empty app controller socket setting. Enabling
automation requires installing the controller and scope unit template, delivering
the existing bearer privately, setting host limits, and mounting the control
directory and read-only registry into the application. Merge alone installs none
of these. The host runtime and whole-VM resource comparison remain live acceptance
boundaries even when generated local tests pass.

## Source delivery evidence

The implementation PR is based on design head
`a3a6953464c193cc29ae5991129a9432695ed9b0`. Review and merge belong to the parent.
No host installation, configuration change, deploy or paid evaluation occurred.

| Acceptance | Local status and evidence | Remaining boundary |
|---|---|---|
| First use / canonical reuse | MET at the elected executor boundary: `test_elected_execution_acquires_but_maintenance_is_passive`, `test_durable_queue_executor_acquires_before_dispatch`; existing scoped tests retain canonical group/DM routing and grants. | UNAVAILABLE: admitted platform native/file/browser journey on an installed controller. |
| Same/different scope / capacity | MET: `test_first_use_serialized_reuse_capacity_and_retained_wake`, unknown-inventory refusal; the real Unix server holds one lock through start/readiness and stop. Pending durable starts consume reservations after restart. | UNAVAILABLE: installed sbx capacity and physical headroom readback. |
| Identity / auth / isolation | MET: bad auth and scope have no wrapper effects; real guest HTTP test rejects wrong/empty boot identity; corrupt ownership and missing controller fail unavailable. | UNAVAILABLE: production socket permissions, egress and guest identity. |
| Restart / partial state | MET: controller restart, disk-missing refusal, partial ready publication, and actual wrapper recovery after successful create then failed policy publication. A crash before the successful-create receipt is uncertain and remains unavailable, never adopted. | UNAVAILABLE: installed sbx/service crash injection and generated file retention journey. |
| Idle / active / race | MET: active runs block release/stop, unknown guest reads refuse quiet, acquire waits behind an actual held stop transition; wrapper stops supervision before disk-preserving stop. Maintenance uses existing SQL election. | UNAVAILABLE: installed long-call cancellation/drain, native detached processes and complete browser-tab journey. |
| Passive | MET: elected maintenance does not acquire; empty boot registry binding/state/instance/enumeration have no host effects. Passive host observation can positively confirm a stopped owned VM for SQL settlement. | UNAVAILABLE: installed background sweep/context/health readback. |
| Failure / original effects | MET: acquisition failure never awaits guest work and returns `no_guest_effect`; both executors use the same boundary. Durable leases have no expiry; failed release retains exclusion. Existing result journal/queue semantics remain. | UNAVAILABLE: host/process failure journey; no guest-effect replay was performed. |
| Install / packaging | MET source: units, fixed wrapper controller verb, explicit limits template, read-only mount overlay and installation/package instructions in `deploy/computer/README.md`. Existing host-owned registries can be reused only after exact validation; unowned names are refused. | UNAVAILABLE: actual install and runtime readback; merging does not install files. |
| Resource effect | UNAVAILABLE locally. | Whole-VM cost, guest-attributed browser descendants, swap/pressure and concurrent demand require installed measurements. |

No acceptance row is silently dropped. Broader worker/DM/export outcome #520
remains open; independent stack review and required hosted checks remain pending.
