# Private workspaces and jobs

## Ownership and isolation

An admitted DM receives its own person-plus-anchor-unit computer workspace and
scoped browser runtime, not a global per-person directory and not the anchor
unit's VM. Every tool, session, browser tab, export and worker resolves that
same boundary. The browser design begins anonymously and does not import an
authenticated profile or promise restart persistence.

A domain-separated full scope derivation keeps DM, unit and legacy room
namespaces distinct. Any shortened operating-system owner name is only a local
label: before allocation or binding, a root-owned full-scope registry must
prove that the owner, directory and scope agree. A collision, conflicting
pre-existing owner or unknown provenance refuses before user creation, chown
or session binding, leaving both scopes untouched.

Workspace directories and browser state are private evidence with restrictive
ownership. A group, another person, another anchor or an arbitrary command
cannot read them. Newly created scope users execute through the runtime's
privileged user-switch path, never as root and never by assuming a user known
only after sandbox boot exists in its static account list.

## Unit reads and publication writes

The DM may read anchor-unit files through a code-bound read-only broker. The
broker resolves the source scope server-side and can return or copy permitted
bytes into the DM scope. It does not execute as the unit user, expose a
writable mount or accept an arbitrary destination.

A unit write is publication of the complete visible file, name and metadata.
It passes the same judgement and receipt boundary as any other DM-derived
shared mutation before privileged copying. Held or unavailable publication
leaves unit files unchanged. There is no shared scratch directory or automatic
synchronization.

Files copied from the unit can acquire private annotations and remain private
until published. Scoped export can return a file to the owner on the pinned
private target; a public link, shared document or group artifact is an
explicit publication, not an export loophole.

## Jobs, delivery and spend

A subagent retains its launching person, DM scope, anchor, platform and exact
direct target with its job identity. It works only inside the DM workspace and
has no group-send authority. Computer and job leases retain their distinct
purposes; the DM turn slot and per-scope computer lease are not interchangeable.

Before every deferred result send, the system re-resolves the active grant,
anchor, direct target and launching-platform opt-out. Revocation or a default
change holds the result privately without redirecting it to another account,
unit or group. Unchanged authority delivers once through the launching target
and scope turn slot.

Computer and subagent spend shares one per-person budget across linked
platforms, anchors and concurrent launches. Admission reserves atomically,
completion settles actual usage and cancellation releases unused reservation.
Changing account or unit cannot reset an exhausted allowance, and another
person's budget remains independent.

## Backup and failure

Private workspace backup accounts for every regular file, including user files
inside environments and browser profiles. Only unsafe links, sockets and FIFOs
may be omitted, each with an exact receipt. Restore reconciles every source
path with archived content or an allowed omission and preserves scope
ownership; an unaccounted regular file fails the backup.

A tar creation receipt is not proof of restorability. Browser profiles stop
for a consistent credential-bearing backup, unsafe interpreter links are
recreated by their owning tools, and failed reconciliation leaves the prior
backup and working files intact. Missing DM authority never falls back to an
anchor workspace or reports an empty success.
