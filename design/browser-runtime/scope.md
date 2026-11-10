# Scope and lifecycle

## Authority

Admission resolves a group unit or canonical private-DM binding once. Every
management call, state read, process handle, browser connection and native or
worker operation carries that VM and instance. Unknown, conflicting or revoked
scope is unavailable with no shared fallback.

Nora's app has no Docker socket, host shell or general management credential.
Only the host lifecycle controller invokes the existing sandbox wrapper. Its
Unix-socket API validates scope and operation identity, authenticates the app,
and exposes acquire, release, idle-stop and passive observation. It never
accepts arbitrary create parameters, exec or destroy.

The host chooses images, resources, names and wrapper arguments. The app
continues to read the registry read-only and independently verifies boot
identity. A stopped record has no live binding to probe or accidentally wake.

## Acquisition and capacity

Work first claims the existing execution segment. Nested work inherits it;
maintenance and context reads do not acquire or wake a VM. Same-scope
transitions and global capacity admissions serialize so concurrent requests
cannot create duplicates or spend one headroom reservation twice.

A genuinely absent owned scope uses the wrapper's provision path. A stopped
owned VM restarts its disk rather than being recreated. Readiness waits for
authenticated live scope and nonempty boot identity to agree with the registry;
changed boots expire old handles and never replay uncertain work.

Starting and running reservations include guests discovered after controller
restart and unmanaged or eval VMs when measuring host capacity. Unknown
inventory or inspection refuses admission. At capacity, active guests remain
and the new request gets capacity-unavailable; only the durable task queue may
report queued, with its original deadline and current admission rechecked.

## Recovery and quiet stop

Controller restart reconciles partial provisioning against registry and actual
sandbox identity. Foreign, conflicting, corrupt or uninspectable state is
unavailable, never adopted, deleted or reset. Old leases and guest work are
verified before new execution or stop.

A VM stops only when positively quiet: no execution lease, admitted work,
resumable tab, pending browser command or detached process. Failed state reads
are not quiet. Page traffic, context rendering, enumeration, sweeps and queue
wait do not refresh user activity or wake a stopped VM.

Acquire and stop serialize under the same elected ownership. New dispatch is
withheld until stop completes or reconciles. Stop preserves workspace and
installed packages; browser tabs, cookies, processes and in-memory sessions
have no restoration promise and expire explicitly.

Durable operation leases do not expire merely because a long call is still
running. Release follows drain and cancellation, and guest effects are never
repeated to resolve a lost response.

## Execution ownership

The queue is per scope, with one active bounded execution segment at a time.
Intent, origin, payload reference, task/tab binding and trace are durable before
dispatch. Existing result delivery and ACK semantics remain separate from
pre-execution admission.

Native and worker calls join one owner. Nested calls inherit ownership rather
than queueing behind their parent. Revocation prevents start; cancellation stops
and drains managed work before release. An abandoned operation is reconciled
only after the existing SQL owner permits it and actual guest state is quiet.

A lost response is not proof of success or safe replay. Terminal state is
committed before ACK, or durable downstream ownership before a handoff. The
queue measures wait separately from execution and reports no hidden retry loop.
