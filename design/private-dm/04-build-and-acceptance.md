# Integrated build and acceptance

Status: proposed plan, not a receipt. Revision 2026-10-21 includes cross-platform
transcript, computer/export and subagents in the first integrated PR. All new
test names and migrations below are proposed. The operator authorised this design revision only; implementation,
paid evaluations, enablement and deployment are separate work.

## 1. Build the integrated feature

Completed prerequisite (former step 1): [unit-scoped computer workspaces](../computer-unit-scope/00-contract.md)
shipped in #164 and v6.5.0; the operator-verified 2026-10-21 migration is recorded
with #168's health correction in [the runbook](../computer-unit-scope/01-migration.md).
Do not rebuild it or rerun its migration as part of the DM feature. The remaining
integrated build steps are:

The [full-scope owner-allocation check](05-cross-platform.md#3-computer-browser-and-export-are-in-the-first-slice)
is a separate **unbuilt shared prerequisite for unit, legacy room and DM
workspaces**: the shipped 12-hex Unix name can collide despite distinct full
hashes. Require it before any scope is created or bound and before DM enablement;
the completed unit cutover is not evidence that this guard already exists.

1. Refresh the [source trace](01-current-flow.md),
   identity/default/opt-out validation and WhatsApp + Signal text flows. Add
   Signal inbound DMs; preserve group/OTP behaviour and the global DM switch
   default false. Every linked platform is on by default within an active grant;
   Discord/Instagram transport support remains deferred.
2. Add the raw dm_scope/Record binding and private graph/point-read selectors.
   Include scope-paged interleaved transcript, platform markers, one scope turn
   slot across transports and reply-platform pinning. Include actual
   metadata/migration parity and history/snapshot integration.
   Do not introduce private Persona accounts or synthetic private policy units.
3. Bind private input to the existing unit Persona/Cognition evidence paths.
   Add scoped internal curation capability and private agenda/session artifacts;
   leave ordinary shared prompts and actions on published views.
4. Wrap every DM-derived native write, correction/verdict and aggregate with
   the publication check/atomic receipt. Protect private source provenance
   across renderer, tool, graph, portal, citation and explanation reads.
5. Extend the shipped computer binding/session/context seams for DM computer,
   export and subagents: [dm_hash and scope lease](05-cross-platform.md#3-computer-browser-and-export-are-in-the-first-slice),
   unit-read/publish-write broker, per-person budget and bound deferred delivery.
   Use runuser for new scope-user launchers and meet backup safety below.
   Reuse context blocks, tools, guarded sends and telemetry sanitisation.
   Inspect every raw DM consumer and native mutation caller. One integrated
   PR ships the capability; do not enable an unsafe intermediate DM path.

Curation belongs in the existing writer/runtime, not a parallel service. Its
privacy and usefulness must be measured rather than inferred from new metadata.

## 2. Deterministic proof and actual inputs

Use existing SQL stores, harness and realistic transport fakes with synthetic
people/units/messages. The fake must reject wrong targets/scopes, and successful
reads/publications are required so an empty reader or universal hold cannot pass.
Proposed coverage:

- Proposed file tests/unit/test_dm_scope.py: identity/default/explicit-membership/enablement
  resolution; zero/multiple units, unknown peers and unsupported input refuse
  before Record, blobs, feeds, stimulus or provider creation.
- Proposed file tests/contract/test_dm_record_contract.py: same owner/anchor across verified
  accounts; default changes, redelivery, quotes, edits/touches and by-ID reads
  never cross private partitions. Raw citations remain protected.
- Proposed file tests/integration/test_dm_curation.py: DM and group evidence reach the same
  canonical unit Persona account; a useful safe DM-derived trait is published
  and reaches a group, while raw text/source pointers remain inaccessible.
- Proposed file tests/contract/test_curation_publication.py: exact candidate/revision/hash
  and unit/source authority; missing/forged/stale decision, explicit hold,
  wrong scope, ERROR or receipt-store failure commits neither target nor receipt.
- Proposed file tests/integration/test_dm_cognition.py: raw-capable curation cannot directly
  send, remind, self-wake, export, write unguarded shared state or leak a private
  draft into shared agenda/working state. Ordinary sessions can read and act on
  the committed curated result. Failed review does not consume source progress.
- Proposed file tests/integration/test_dm_disclosure.py: graph seeds/hops, cache expansion,
  citation/why-this-line queries, portal, backfill and projection rebuild cannot
  reveal source DMs through a visible Notebook item. Cover every target kind.
- Proposed file tests/integration/test_dm_disputes.py: a subject's private dispute hides the
  existing unit line without revealing the request/reason; replacement/verdict
  changes pass curation. Explicit privacy restrictions cannot be cleared by a
  generic remember request or untrusted third party.
- Proposed file tests/integration/test_dm_rollup.py: bounded private aggregates can inform
  curated unit understanding; raw DM counts, timestamps and activity episodes
  never appear in shared blocks merely because a Rollup exists.
- Proposed file tests/integration/test_private_dm.py: preserve scoped delivery, fallback,
  queues/default changes, legacy-feed exclusion and opaque telemetry coverage
  from the original bundle. Revocation closes raw access; prior published
  understanding remains in its original unit until corrected/retired.

Additional first-PR acceptance cases (all required, using synthetic identities):

| Case | Required observation |
|---|---|
| Cross-platform continuity | WhatsApp then Signal for the same person+unit read one interleaved transcript; every line marks its platform; pagination loses/duplicates no rows |
| Concurrent inbound turns | Simultaneous WhatsApp/Signal inputs cannot run two turns over one dm_scope; the second assembles after the first completes; another scope can progress |
| Reply-platform pinning | Reply, retry and fallback use only the admitted inbound platform/target; a linked account on another platform is never substituted |
| Platform-local pointers | Identical remote IDs/timestamps on the two platforms do not cross-deduplicate, quote, edit or touch; wrong-target pointers refuse |
| Admission and opt-out | Linked WhatsApp/Signal accounts are admitted by default under the grant; an explicit platform false blocks inbound and deferred sends without disabling the other; display-name matches admit nobody |
| Default change and workspace isolation | New anchor selects a distinct transcript/workspace; groups and other people/anchors cannot read DM files/cookies/logins via tools, sessions or arbitrary commands |
| Scope/lease/session reuse | Canonical dm_scope UUID yields the specified 32-hex dm_hash on both platforms; SCOPE_RE, inventory and archive_check accept its layout; non-job calls with different session IDs contend on scope:<dm_hash>, while a group retains scope:<unit_hash> and jobs retain job:<id>; session/context/browser reads use only the bound scope |
| Shared owner-name collision | Inject two distinct 32-hex scopes with the same first 12 hex characters, e.g. aabbccddeeff followed by twenty zeros versus twenty ones. Reserve/bind the first; the second must refuse and alert before user/directory creation, chown or session binding, leaving the first scope's bytes/owner unchanged. Repeat DM/unit in both orders and unit/legacy-room; same-full-scope reuse succeeds. An unaccounted pre-existing user or conflicting directory also refuses; concurrent reservations cannot both succeed |
| New-user execution | Create a DM user after sandbox boot; root-entry runuser executes with that user's UID/GID and reads its files; a different unit/DM user cannot enter the directory; missing user or failed execution is unavailable, never empty success |
| Unit read and publish-write | DM successfully reads anchor-unit files; unguarded write fails; explicit safe curated publication reaches the unit; held/private content leaves unit files unchanged |
| DM browser and subagent | Work begun via one admitted platform resumes privately via the other; job output and browser state stay in that dm_scope |
| Deferred recheck/no retarget | Revoke grant, change anchor, alter direct target or opt out while a job runs: delivery refuses, does not use another account/unit, retains private outcome; unchanged authority sends once to the launching target |
| Per-person budget | Simultaneous jobs/computer calls share one person budget across platforms/anchors; exhausted admission refuses before spend, no account-switch reset; another person retains their own budget |

### Backup safety — required before DM enablement

Choose **archive every regular file; omit only unsafe symlinks and
sockets/FIFOs**, keeping `deploy/computer/archive_check.py::problems` unchanged.
This replaces the earlier installer-manifest classification and refusal of user
files inside venvs. Rebuild hints do not authorise omitting regular files.
Extend the existing `deploy/computer/nora-computer` backup preparation
(`backup_in_vm`, shell function at line 360), not archive_check's trust boundary:

- Inventory every path in each scoped workspace, including hidden files, entry
  types, regular-file digests and exact symlink text, without following links.
  Archive **every regular file**, including all files inside venvs and private
  browser profiles. Preserve directories and links accepted by archive_check;
  do not exclude whole environments, caches or browser directories. The shipped
  backup's blanket browser exclusion is source baseline, not the repaired policy.
  Backups containing profiles remain private credential-bearing artifacts;
  stop browsers before a consistent profile backup.
- Omit only symlinks rejected by archive_check's target rule (absolute targets
  or targets resolving outside the same full scope), plus sockets and FIFOs.
  Examples are a venv's bin/python link to a system interpreter and Chrome's
  absolute Singleton links. Do not dereference them or copy their targets.
  Record **each omitted path and its exact link text or socket/FIFO type** in
  the private backup receipt. No installer manifest or package-file content
  classification is needed. Regular files are never omission candidates.
- Reconcile the complete source inventory against the **actual archive plus
  receipt**: every path must be archived with matching content/type or have an
  exact, allowed omission record. An unaccounted path, a mismatching record,
  an omitted entry that is not a symlink/socket/FIFO, or a symlink omitted
  despite passing the target rule fails the backup. Unsupported devices or
  invalid hard links are errors, not new omission classes. Run unchanged
  archive_check on the reconciled archive; its other path/link/type restrictions
  still apply. Preserve the previous verified backup and working files on any
  failure. Migration still requires drained processes and explicit relocation
  of stale sockets before its existing snapshot, not a planner bypass.
- Restore every archived regular file with its matching digest and scope owner.
  Omitted venv interpreter links are recreated by re-running the venv tool;
  record the original link text and that rebuild hint with the receipt. Keep
  all archived environment/user files; the tool must not clear them. Recreate
  browser singleton/runtime artifacts by normal process startup. Missing hints
  or failed link reconstruction are unavailable, not proof of a complete restore.

This is a **shared unit/DM follow-up**, not delivered by #164/#168 or this docs
revision. Require one synthetic end-to-end backup/restore case with an external
venv interpreter symlink, Chrome absolute singleton links, sockets and a FIFO.
Plant a user-authored regular file **inside the venv**: it must be archived and
restored in place with its source digest, alongside every other regular file.
Reconcile all source paths with actual archive entries or exact omission
records; deliberately drop a regular file or its accounting and require backup
failure. Omission of a regular file must fail even with a receipt entry. Pass
unchanged archive_check (exit 0) for the valid archive and restore in an isolated
VM; verify archived files as the new owner via root-entry runuser, recreate the
interpreter links with the recorded venv-tool hint and confirm dependencies run.
Verify browser profile regular files survive privately. A malicious cross-scope
**hard link**, which is not in the omitted symlink/socket/FIFO class, must still
fail archive_check. An unaccounted path or failed reconciliation/restore blocks
DM enablement; a tar creation receipt alone cannot satisfy this item.

Inspect actual model inputs for a frozen synthetic journey: private DM reply,
raw-capable curation, ordinary group Cognition and group response. Confirm the
intended private input reaches curation, the useful curated result reaches the
group, and raw DM/private rationale does not. Print/render success is not proof.
Run schema parity/snapshot checks and named revert proofs for central raw-scope,
publication-transaction and shared-evidence projection guards. A failing setup
or empty collection is not a killed mutation. Run the final exact-tree gate and
fresh independent disclosure review once the implementation is complete.

## 3. Semantic model acceptance

Use existing nora-eval/subject/runtime composition after inspecting every offline
input. Define the exact labelled cases, models, budget and pass bars before paid
calls, with operator-authorised bounded sampling and no redraw/judge override.

The corpus must include: a safe communication preference worth sharing; an
explicit keep-private instruction; confidential specifics disguised as a shorter
summary; an indirect hypothesis revealing a secret; source attribution leaking
DM existence; titles/predictions/conditions/aggregate side channels; a private
correction; another-person and another-unit information; multi-message privacy
instructions; and publication that is safe but changes the fact's meaning.
These are synthetic categories, not real private session quotes or a permission
list. Inspect complete committed shared views/actions, not only writer replies.

Require useful safe publication as well as privacy negatives. ERROR/timeouts
remain distinct from semantic FAIL. A semantic disclosure failure blocks enablement
and reopens curation. A receipt/hash or deterministic scope pass does not count
as semantic acceptance. Report deterministic proof, model acceptance, deployment
and external trace persistence separately.

## 4. Enablement, readback and rollback

Before enabling, inspect/back up live database and config files, validate
identity/policy copies through their loaders/default resolver, and require
[dm] enabled plus admitted identities, default platform admission/explicit opt-outs,
the satisfied unit workspace prerequisite, the shared owner-collision guard,
verified DM backup/restore safety, DM budgets and graph/Persona/curation composition.
Do not use deployment-config validation as a substitute for identity/policy
validation. Existing inert DM grants must not activate on an ordinary upgrade.

Ping the operator before deployment and use nora-deploy's locked exact-SHA
build/recreate/readback. Only an explicitly authorised live journey can send a
real test DM. Read back private Record binding, the unit's unchanged account
identity, a curated publication and private source access denial from a group;
verify no raw feed or direct curation action bypassed the boundary.

Immediate disablement: validated [dm] enabled=false and recreate. Stop raw-DM
curation as well as conversation admission; retain raw data and published
Notebook history. Existing published facts remain ordinary unit knowledge,
subject to dispute/revision/retirement. No automatic forgetting/cascade or reverse
migration. Binary downgrade needs proof that old readers cannot see protected
raw rows/session artifacts; staying on the new schema/binary is the routine undo.

Issue #124 stays open after this documentation revision. The proposal's defining
change is one unit Persona/shared Cognition with curated publication, replacing
the old isolated-private-Persona and no-DM-Cognition defaults. The 2026-10-21
revision also replaces WhatsApp-only transport and computer/export/private-job
deferral with [the integrated cross-platform contract](05-cross-platform.md).
