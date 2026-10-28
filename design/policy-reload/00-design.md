# Identity and policy hot reload

Operator ruling, 2026-10-28. One integrated change; no host or private-file edits.
The decision number is allocated from current `origin/master` immediately before
merge, as required by `nora-decision`; this draft does not reserve a number.

## Intent

An operator can add or remove rooms, people, account mappings, labels,
descriptions, tools and trust flags without recreating Nora, presence or
analytics. Each process keeps its own last validated snapshot. A broken edit
must neither mute a working process nor partially change its trust decisions.

This holds unless a measured consumer needs a new boot-only resource; add that
consumer to the audit and either make it read live or refuse the entire file.

## Mechanism

`nora.config_reload` owns one holder per loaded file. `LivePolicy` and
`LivePersons` inherit the existing value's methods, and route all dataclass field
reads to one current immutable value. A captured bound method therefore reads
the current holder too. No consumer mutates individual fields of that value.

Every three seconds a poll reads mtime and SHA-256 of the small local files.
`POLL_SECONDS` is a module constant with a ponytail ceiling, not a configuration
option. A content hash is checked even when the mtime is unchanged; touching
identical content does not count a reload. Fingerprints stay in process memory,
never logs, metrics labels, Git or artifacts.

The candidate uses the same `load_policy` / `load_persons` loaders as startup.
The shared `validate_pair` also checks the reserved aiPeer and the existing
workspace-router invariants: stray/duplicate bindings and ambiguous room IDs.
Every declared workspace is admitted at boot, including units with no current
room, so removing the last room does not turn its binding into a stray. This
supersedes the old unused-unit rejection in both startup and reload: a unit
declaration with no current rooms still carries a boot-admitted resource.
Missing unit declarations and invalid/duplicate bindings are still refused.
Workspace validation lives in `nora.memory_routing`, with the old app module
retaining its public imports. Presence and analytics additionally reuse their
startup Life360 membership/circle checks. Analytics now refuses a missing or
empty policy at startup too, rather than silently omitting its unit rollups.

After validation, restart-only fields are compared with the boot snapshot.
A valid candidate swaps exactly one holder reference. Validation and publication
contain no await, so other tasks on that process's event loop cannot read a
partially applied candidate. Independent files/processes are not a distributed
transaction: a cross-file edit must be valid against each currently applied
counterpart. A refused file is retried when its own content changes again.

Derived maps/sequences/sets are read-only projections through `domain.live`,
not additional published snapshots. Iteration holds one computed collection;
long-lived consumers retain the projection rather than copying it to a dict or
tuple. A turn already running may retain its per-turn context; subsequent
admissions and newly built tools use the new values.

The one counter is `nora_config_reloads_total{file,outcome}`, with outcomes
`applied`, `refused`, `restart_required`. `ReloadOutcome` is a Literal;
`get_args` supplies the vocabulary to both metric help and runtime recording.
Unchanged ticks are not counted. Refusals log WARNING, naming the error class
or the restart-only field, without printing private TOML values. `/health`
compares disk content with the last applied snapshot, so a successful reload
clears staleness and a refused or missing file remains stale.

The main process starts its poller in its existing `BackgroundTasks`; presence
and analytics start and cancel theirs alongside their daemon lifecycle.

## Capture audit

Audit: all `allowlist`, `Policy`, `persons`, loader and identity-field reads in
`nora/`, including all three composition roots. Grouped rows name components
that share the same capture mechanism; no row relies on a mutable parsed object.

| Component | What it reads | Hot or restart-only |
|---|---|---|
| IngestPipeline, scope and sender stages | room admission, account resolution, sender grants | Hot through the policy/person holders, including the very next message |
| RelevanceGatePolicy, RelevanceGate, EngagementPipeline | relevance flag, sender identity, names | Hot; method calls read the holders |
| TranscriptInjection and per-turn Room | policy flags, unit, roster resolution, profile names | Hot when context/room is built; an existing turn retains its context |
| FirePath, HeartbeatLoop, ProactiveScreen, ComputeJobPoller | current grants, target rooms, people, proactive flags | Hot; each decision reads the holder |
| Relay and PresenceReader | room addressing, labels, descriptions, tools, flags, people | Hot through policy methods |
| DecisionLogRecorder, record extraction, note unit/room helpers | room-to-unit, aliases, primary room, label, platform | Hot closures and live note maps |
| DocumentService, DocumentsContextReader, document/media/artifact portal authorization | room IDs and named viewers | Hot; membership helper is a closure rather than a captured dict method |
| OTP, web API, memory/profile portal and room-list callbacks | current person/account, viewer rooms/workspaces | Hot through holders and existing callbacks |
| Honcho WorkspaceRouter | conversation-to-unit and existing unit-to-workspace routes | Room map is hot; declared unit/workspace structure and external bindings are restart-only |
| Honcho ownership admission and ensure_workspace | external tenant ownership and initial scopes | Boot; no new external binding is admitted by reload |
| ComputerdRuntime, ComputerContextReader, subagent backend/tools | current room/unit and mapped requester | Hot room/unit lookup; runtime endpoint, credentials and other resources come from startup config.toml, not these files |
| AdapterRegistry, Signal/WhatsApp senders | adapter connections and direct-recipient account set | Accounts hot via LiveSet; adapter subscriptions/credentials are startup config.toml resources |
| Discord voice resolution and music notifications | voice flag, guild's voice room and announcement channel | Hot through existing resolver methods; audio-controller guilds and music resources are selected by startup config.toml |
| LineagePolicy and all its render/read callers | every room's membership, unit, label/location and identity circles | Hot projections; RenderRoom takes a coherent per-render tuple |
| where_is registry and presence preflight | identity circles and their names/membership | Hot sequence; the read store exists whenever presence is configured, even if initially circle-less |
| RegistryToolSource and proactive runner | unit home rooms, scope rooms, location flags | Hot maps, including initially empty maps; runner is available before the first home room is added |
| Dreamer scope/unit callbacks and Culture account reads | current room and unit populations, label, description, members | Hot callbacks |
| Character person/unit/Nora adaptors and evidence | current key populations, identity/name, rooms | Hot key maps and populations recomputed at use |
| Profile person/collective/verdict and Lessons adaptors/evidence | current accounts, labels, descriptions, docket members and rooms | Hot key/member maps and populations |
| Cognition grounder, upcoming reads, session runner and WakeEvaluator | unit rooms, labels, members, home rooms and runnable units | Hot maps/sets, including room removal and home-room flag changes |
| Social subjects, poller, perception and SqlSocialStreamStore readers | account owners, per-person social flags, units/rooms and opted-out IDs | Hot holders/projections; no copied opt-out set |
| PresenceCollector composition, summaries, member refresh and circle loops | identity membership and circle subscriptions | Hot sequence; supervisor adds/cancels circle loops, and each collection cycle rereads the circle's membership |
| Analytics dependencies, RollupRunner and UnitRollups | subjects, former IDs, circles, rooms, listed members/accounts | Hot sequences/maps; former-ID index is computed from the current people |
| CLI reports and subject/evaluation snapshot loaders | files for a single requested command/snapshot | Read at invocation; no resident reader or daemon subscription |
| Historical adoption/backfill | mapping during the existing startup/explicit historical sweep | New messages resolve live; historical replay remains an explicit/startup operation, not a reload side effect |
| RevisionRecorder | process-start provenance of startup configuration | Records startup; reload outcome/last-applied health are the runtime evidence |

## Restart-only fields

| Field | Why the whole reload is refused |
|---|---|
| policy `[units]` presence and each unit's `workspace` declaration | Honcho tenant ownership and initial scopes are admitted at boot. Moving the boundary without that admission could send people's speech to another tenant. |
| identity `[workspaces]` / `honcho_workspace` bindings | Honcho ownership checks and initialized clients are bound at boot; applying the new binding partially would misroute reads/writes. |

Adding/removing rooms inside the boot-declared structure is live, including
removing a unit's last room. An implicit-workspace policy can add a new unit
live; no new Honcho tenant is introduced. Adding a new declared unit/workspace
requires recreation. Moving a room among existing declared units is live.
People, room metadata, tools and flags are not restart-only fields.

The audit found no unit-computer workspace map or adapter/voice/music resource
derived at boot from these two files: those resources come from config.toml.
Their live policy inputs are listed above rather than unnecessarily refusing
otherwise applicable room/flag changes.

## Failure modes and operational limits

- Missing, unreadable, malformed, ambiguous or allow-nothing policy at startup
  refuses startup. At runtime it counts `refused` and retains the last good file.
- Invalid identity, the reserved aiPeer, or a companion's invalid/empty Life360
  membership likewise retains the last good identity. Restart-only changes count
  `restart_required` and do not apply even unrelated changes from that candidate.
- A file changed again while loading is refused; the next changed tick retries.
- A stopped process cannot reload. Daemon shutdown cancels and awaits its poller.
- Use validated copies and copy in place. Replacing a host-side single-file bind
  mount's inode can hide the new file from the container; recreation is still
  needed in that case. A poller cannot observe a file it cannot see.
- Processes can apply at different ticks. No restart or network request is
  automatically performed on refusal. Existing in-flight model calls and current
  collection/rollup cycles complete with the context they already read.
- `config.toml`, environment variables and new image code retain their startup
  lifecycle. No deploy target or live private files are touched by this change.

## Acceptance

`tests/integration/test_policy_reload.py` exercises real composition and the
background pollers: next-message admission after adding a room with an unchanged
mtime (hash positive control), malformed/missing/empty-policy last-good retention,
whole-file restart refusal, a newly resolved identity, reserved-aiPeer rejection,
identity binding refusal, and live presence/analytics subjects/circles/rooms with
their startup cross-checks retained, and removal of the last room in a bound
workspace without losing that declaration or its admitted route. Named room-admission and invalid-edit tests
are falsified by suppressing the production swap, then restored and rerun green.
