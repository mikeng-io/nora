# Identity and policy hot reload

Operators can change rooms, people, account mappings, metadata, tools and trust
flags without recreating Nora, presence or analytics. Every resident process
keeps its last validated snapshot: a broken edit neither mutes a working
process nor partially changes its trust decisions.

**Status:** built

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Fpolicy-reload%2F%22)

## Parts

- **Live policy and identity holders** preserve the existing domain interfaces
  while routing field reads to one current immutable value. Consumers may
  retain methods and projections without retaining a stale parsed file.
- **The file reloader** detects changed content, invokes the same loaders and
  cross-file validation used at startup, and publishes an accepted candidate.
- **Domain projections** expose current maps, sets and sequences to consumers
  whose natural interface is a collection rather than the holder itself.
- **Process lifecycles** own polling in the main, presence and analytics
  daemons. Health reporting compares visible file content with the last
  applied snapshot.
- **Workspace routing validation** is shared by startup and reload so a new
  candidate cannot introduce ambiguous rooms, duplicate routes, a reserved
  Nora identity or an unadmitted external workspace.

## Publication contract

Validation and publication are synchronous within a process. A valid candidate
replaces exactly one holder reference, so event-loop readers cannot observe a
partially applied file. Independent files and processes are not a distributed
transaction: a cross-file edit must be valid against each currently applied
counterpart, and different processes may apply it on different ticks.

Content, not timestamp alone, defines a change. Identical content is not a
reload, and a file that changes during validation is refused. A refused file
is reconsidered only when its own content changes again. File fingerprints and
private values never become logs, metric labels or artifacts.

A turn or collection cycle already in flight retains the context it captured;
subsequent admissions and newly built consumers read the new value. Derived
collections remain live projections rather than extra mutable snapshots.

## What may change live

Room admission and membership, people and account resolution, names, labels,
descriptions, tools, flags and room-to-unit routing are live. A declared unit
may temporarily have no rooms: its workspace was admitted at boot, so removing
its last room does not make the binding stray. Rooms may move among existing
declared units, and an implicit-workspace deployment may add a unit without
introducing a new external tenant.

Declared workspace structure and external workspace bindings are restart-only.
Their ownership admission and initialized clients belong to boot. A candidate
that changes either is refused in full, including unrelated edits in that file;
applying it partially could route one person's speech or memory to the wrong
tenant. Runtime endpoints, credentials and similar resources continue to come
from startup configuration rather than these live files.

## Failure and observation

Missing, unreadable, malformed, ambiguous or allow-nothing policy refuses
startup. The same failure at runtime retains the last good snapshot. Invalid
identity and failed companion-specific membership validation do likewise.
Restart-only change, invalid candidate and successful application are distinct
outcomes; unchanged polls are not events.

Health remains stale while visible content differs from the applied snapshot,
including a missing or refused file. A successful application clears that
staleness. Logs identify the failure category or restart-only boundary without
serializing private configuration.

Polling cannot observe a path the process cannot see, and a stopped process
cannot reload. Validated in-place copying and any container mount constraints
are operational concerns owned by the deployment runbook. Reload never triggers
a restart, a network migration or historical replay.
