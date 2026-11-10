# Subject and sandbox

## Obtained through production

`nora/subject` obtains the graph through the same composition root as the
deployment. The subject carries the deployment's configuration, room policy,
identity mappings, model and enabled features. Only isolation keys differ:
database, workspace, process resources and the frozen clock.

A case enters through a real entry point: admission, addressed reply,
background cognition, deferred fire or another declared occasion. There is no
taxonomy of fake Noras and no suite-specific construction branch.

## Anchored input

A case names a real room and instant. The sandbox reconstructs what that room
held there from a read-only deployment clone: source Record and history,
Notebook revisions, Rollups, media references and the stores the selected
entry point reads. Every case in one corpus uses the same restoration path.

The source room's real ids and policy remain intact. Rows keep their original
times, and the subject's clock is the anchor instant, so ages and relative dates
mean what they meant then. The case's arriving message and any structural
mention are input, not authored context.

Some corpora need complete row-history restoration rather than the smaller
snapshot projection. That choice is corpus-level, never a per-case optimisation.
A store that cannot be reconstructed and is required by the case makes the run
unavailable rather than silently reduced.

## Isolation

Each case gets its own migrated database and external memory workspace. Writes,
derived memory and later draws cannot leak into another case. External
workspaces are created empty and deleted after the draw; cleanup and background
cancellation happen on every exit path.

Shared source and blank-template databases are read-only invariants. A run may
create and migrate a disposable private template when revisions diverge, but it
never upgrades a shared one. Source, target and shared-version checks happen
before and after the run, including when a case fails or the process is
interrupted.

## Counterfactuals

A counterfactual starts from a real anchor and varies one declared tier that no
real moment could contain, with the reason stated. It is a manipulation, not a
reconstruction, and is counted separately from anchored cases.

The set of variable tiers is closed and scoped to the corpus that needs it.
A missing anchor, an invented override or a counterfactual parked case is
refused. A seam the rebuild itself owes is unfinished work, not a reason to
park a case out of sight.

Active, unanchored, parked and counterfactual counts are visible in every
report. An unanchored active case errors; a parked case has a reason and an
observable return condition and is never silently dropped from the census.

## Declared limits

Any remaining divergence is explicit: unavailable adapters, unrecoverable
history, media not reconstructed, held transport health, or an external store
with no point-in-time restore. These are not passed off as production parity.
Where a platform cannot execute the entry point, the case errors rather than
becoming an easier scenario.
