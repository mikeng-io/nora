# Integrated build, proof and enablement

Status: plan, not a receipt. Depends on [execution](03-execution.md). All test
names below are **proposed tests to add**, not existing green tests. The
operator authorised design work only; this bundle does not start paid calls,
schema changes, enablement or deployment.

## Build one integrated change

1. Rebase the investigation on the actual latest source without rewriting the
   primary checkout. Re-read the identity, pipeline, Record, graph, Persona,
   background and delivery seams in [current flow](01-current-flow.md). Reuse
   existing code; amend this bundle if source drift changes a contract.
2. Add identity/default validation and the durable private binding/migration
   in [storage](02-storage.md), including schema metadata, migration parity,
   history/snapshot registration and scoped Record APIs. Reject the reserved
   private namespace in shared policy. Do not enable DMs at this step.
3. Wire WhatsApp text admission and singleton audience through the existing
   pipeline; implement the closed DM tool registry, separate shared/private
   context and guarded outbound binding. Every new Record point read must
   prove scope before hydrating text. Exclude all DM evidence from shared
   background paths, including legacy feed/backfill.
4. Reuse the person adaptor for eligible private accounts; add private
   enumeration, independent watermarks, same-account citations and scoped
   dispute/verdict processing. Add graph projection/retrieval/rebuild support
   and conservative shared-exposure dependencies for DM reply replay.
5. Complete the consumer census and proofs below, then run one final exact-tree
   gate and a fresh independent review under the repo's model/effort rules.
   Ship as one integrated behavioural PR, not disconnected partially safe DMs.

## Deterministic and input proof

Use the existing integration harness, real SQL stores and realistic transport
fakes. A sender fake must refuse wrong direct addresses; a scope fake returning
empty for every read proves nothing. Use synthetic fixtures, no private names,
quotes, IDs or provider calls in the normal gate.

| Proposed test / location | Must demonstrate |
|---|---|
| `tests/unit/test_dm_scope.py::test_default_resolution` | Explicit valid/default inference; multiple/zero units refused; wildcard excluded; reserved namespace and invalid explicit default rejected |
| `tests/integration/test_private_dm.py::test_admission_precedes_side_effects` | Unknown, sender-peer mismatch, DM-denied, ambiguous and unsupported-media events create no Record, blob, feed, stimulus, projection or model; valid text has positive controls |
| `tests/contract/test_dm_record_contract.py::test_scope_survives_default_change` | Same wire chat and verified second account preserve exact partition; default changes and old quotes/edits/touches cannot cross it; redelivery never rebinds |
| `tests/integration/test_private_dm.py::test_shared_read_private_write` | Owner's permitted unit fact is visible privately; other unit/person private facts absent; private facts and mixed turn outputs never appear in group prompts/results/writers |
| `tests/integration/test_private_dm.py::test_background_isolation` | Inspect actual shared and private writer batches, stimulus/Standing/Cognition/Rollup inputs, counts and cursor effects; private Persona alone learns from private evidence; DM model debrief and working-state creation/replay do not run |
| `tests/contract/test_dm_persona_contract.py::test_private_dispute_and_verdict` | Private told/induced/hypothesis chains persist; a real owner correction/forget journey invokes scoped dispute and immediately hides only its private line, with asynchronous private-only reweighing; prediction evidence cannot mutate/read another account; conflicts retain unconsumed evidence |
| `tests/integration/test_private_dm.py::test_remember_cannot_launder_shared_input` | A mixed-context turn asks to remember a shared-only fact under a private trigger citation; only the private Record stimulus is queued, the private-only writer cannot see that shared fact, and neither direct nor later Persona writes store it. Include a real private self-statement as positive control |
| `tests/integration/test_private_dm.py::test_retrieval_and_replay` | Exact/lexical/dense seeds and every hop/lookup/hydration stay scoped; projection rebuild preserves scope; excluded shared evidence cannot return through dependent DM replies |
| `tests/integration/test_private_dm.py::test_tool_and_destination_bounds` | Discovery and guessed execution refuse every disabled operation; sender receives only bound direct target; audit-first reply/fallback/queue retains private scope and exposure references |
| `tests/integration/test_private_dm.py::test_revocation_and_restart` | DM grant/unit/identity removal closes current and historical private access; default change cannot retarget queued work; restoring authorised anchor resumes only its own partition |
| `tests/integration/test_private_dm.py::test_portal_and_legacy_feed` | Shared portal lists, private-ID probes, lazy adoption and old Honcho surfaces cannot expose or ingest private contents |
| `tests/integration/test_private_dm.py::test_telemetry_never_exports_wire_identity` | Capture real exporter/provider attributes, retained trace serialisation and logs for admitted, refused, queued, retried and failing synthetic DMs; no phone/JID/sender ID appears, while opaque scope/turn correlation and actual transport targeting still work |

Run the existing schema parity and snapshot/restore checks for persistence
changes. Prove the central private-scope predicate and outbound binding by
temporarily reverting them: the named tests must fail from cross-scope data or
wrong destination, not a setup error. Restore, then run the final gate once.
No mutation sweep is needed beyond those real trust boundaries.

For model-input proof, capture actual assembled DM/group prompts plus private
and shared background batches from the same frozen synthetic journey. Read
every before/after input before creating providers. Confirm the intended
positive fact is actually present and private evidence is actually absent from
the shared batch; renderer success by itself is insufficient.

## Bounded model acceptance

After deterministic and offline input proof, use `nora-eval` and existing
subject/runtime composition for a bounded sequence agreed with the operator.
The implementation PR must define the exact cases, source snapshot, model,
budget and pass bars before spending. Do not invent a new eval runner or choose
a paid sample count from this document. At minimum the journeys cover group
recall in DM, persistent private self-knowledge, a private correction/hypothesis,
a group probe for that private fact and a default change.

A privacy pass requires inspected inputs and observed behaviour; fluent refusal
alone is insufficient. Provider failure/timeout is ERROR, disclosure or wrong
path is FAIL. Bind receipts to the exact tree and traces; keep deterministic
proof, model acceptance and live deployment readback separate.

## Enablement and rollback

Add `[dm] enabled = false` to policy as the explicit conversational enablement
gate, alongside the existing person allowlist. The current example already has
a nonempty list for an unreachable feature; with single-unit inference, that
old list could otherwise activate DMs merely by upgrading. Enabling requires
`enabled = true`, an admitted person and valid identity default resolution.
Composition refuses enablement unless `[memory_index] enabled` and both
`[persona] enabled` / `render` are on and private readers/writers are wired.
It never falls back to Honcho. Unsupported platforms remain refused.

Before any enablement, inspect live configuration through the normal procedure,
back up the database and affected files, and validate copies. The implementer
must ping the operator before deployment and follow `nora-deploy`'s exact-SHA
lock/build/recreate/readback procedure. A successful local test is not permission
to enable real identities or send them unsolicited test messages.

Read back one explicitly authorised DM journey: private reply delivered to its
direct target, scope-bearing Record and private Persona persisted, no shared
stimulus/feed/derived write, and no private hit from a group reader. Read back
the deployment/config identity separately; report VERIFIED/FAILED/UNVERIFIED
for each layer instead of upgrading absent evidence to a pass.

Immediate disablement is setting `[dm] enabled = false` in a validated policy
copy and recreating; clearing the person list also refuses everybody. Validate
identity/policy copies through their own loaders and composed default resolver,
not `nora config validate`, which validates deployment config. Existing data
remains isolated and stored. Keep the new binary/schema
for routine rollback. An old binary ignores private binding and cannot be
assumed safe: binary downgrade requires disabling DMs first, stopping writers
and proving the chosen version cannot read private Record/index/Persona rows.
Do not reverse migrations or delete data as a routine rollback.

## Decisions and status

Resolved engineering defaults: WhatsApp text first; one private partition per
person/anchor across verified accounts; existing stores and person writer;
owner-only private Persona; no DM Cognition or asynchronous tools initially;
closed seven-tool registry and explicit DM policy opt-in. These are proposed choices
within the operator's agreed direction, not additional operator rulings.

No product question blocks this design. Runtime implementation, migration
number, exact corpus/budget and live enablement receipts belong to the later
implementation task. Reopen a choice when a source trace, failed scope proof
or measured user journey shows it cannot meet the contract. Documentation
approval and documentation merge leave issue #124 open.
