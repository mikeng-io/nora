# Integrated build and acceptance

Status: plan, not a receipt. All new test names and migrations below are
proposed. The operator authorised this design revision only; implementation,
paid evaluations, enablement and deployment are separate work.

## 1. Build the integrated feature

1. Refresh the [source trace](01-current-flow.md), identity/default validation
   and WhatsApp text flow against the actual current tree. Preserve group/OTP
   behaviour and keep conversational DM enablement false by default.
2. Add the raw dm_scope/Record binding and private graph/point-read selectors.
   Include actual metadata/migration parity and history/snapshot integration.
   Do not introduce private Persona accounts or synthetic private policy units.
3. Bind private input to the existing unit Persona/Cognition evidence paths.
   Add scoped internal curation capability and private agenda/session artifacts;
   leave ordinary shared prompts and actions on published views.
4. Wrap every DM-derived native write, correction/verdict and aggregate with
   the publication check/atomic receipt. Protect private source provenance
   across renderer, tool, graph, portal, citation and explanation reads.
5. Reuse context blocks, tools, guarded sends and telemetry sanitisation.
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
[dm] enabled plus admitted identities and graph/Persona/curation composition.
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

Issue #124 stays open after this documentation PR. The proposal's defining
change is one unit Persona/shared Cognition with curated publication, replacing
the old isolated-private-Persona and no-DM-Cognition defaults.
