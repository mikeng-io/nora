# Persona person writer acceptance

This grades the actual Persona writer separately from `tool_selection`.
The original vectors use deterministic grading; only the optional three-case
representation bundle adds semantic judging through the existing Judge. A draw obtains the existing Subject, restores the normal
snapshot background, marks that restored history complete in the private sandbox,
appends declared invented sources using the roster's account,
seeds the held birthday via SqlPersonaStore.persona_write, and calls DreamerRuntime
with PersonaPersonAdaptor's entity key and evidence reader. It reads
SqlPersonaStore.account_heads, then selects current typed Identity. Predecessors
whose stored state still says current are never counted as current heads.

The eight invented vectors are in `design/unified-memory/75-typed-identity.md`.
They are private-file synthetic evidence experiments, counted as COUNTERFACTUAL
when supplied. The committed contracts remain unanchored without the private file.
No production names, account IDs, messages or audit conclusions belong in Git.
The generator reuses the first existing `tool_selection` anchor and its roster
pseudonym. It never invents a sender account or a snapshot. If that person is not
listed in the room's actual unit, setup ERRORs; adapt the private file to a real
listed person/anchor already in the corpus, never weaken the roster check.

## Setup and offline inspection

In a separate eval worktree on norty, with the standard deployment environment,
source clone and pseudonym map (the nora-eval skill gives the lock and setup):

```bash
uv sync --extra evals
uv run python -m evals.persona_cases /tmp/persona-identity.yaml
export NORA_EVAL_PERSONA_CASES=/tmp/persona-identity.yaml
for case in birthday_retained birthday_corrected residence_unknown marriage_unknown partner_unspecified explicit_home_spouse height_age_stated activity_not_ownership; do
  uv run python -m evals persona_identity --print-input "$case"
done
```

The private writer setup seeds `persona-person:history-boundary` and
`persona-person:history` at the restored Record head through
`SqlLanguageStore.advance_watermark` before appending any invented arrival.
This declares an already-known background, rather than paying to backfill it.
The production reader then selects the invented arrivals by later Record IDs;
their original assertion timestamps remain unchanged, including dates before the
snapshot anchor. No recent watermark or account settlement timestamp filters them.
The held birthday retains its original citation and assertion date; the session
clock is the private file's declared counterfactual clock.

Before building an agent, the writer checks every declared synthetic row ID is
present in the actual production-reader batch passed to the adaptor. Missing or
unavailable evidence is ERROR with zero model calls, including negative cases.
It runs the exact checked batch; it neither fabricates a batch nor bypasses
production history behavior. Only this private writer entry point seeds cursors;
normal backfill and other eval callers retain their existing behavior.

`--print-input` composes the real instruction store (shared Dreamer doctrine plus
persona-person objective) and the real adaptor input, including source dates,
held typed heads and the frozen clock. It builds no agent, calls no model and
commits no writer settlement. Setup still uses the existing sandbox/Honcho
lifecycle. Never treat the inspection's ERROR verdict as a behavior sample.

The local integration test scripts only the provider boundary. It proves a real
no-change settlement passes, a syntactically valid but unsupported residence write
lands through the real runtime/store and FAILs, and provider failure/unsettled
output ERROR. It also checks the birthday seed and correction through canonical
SQL heads and verifies no-call rendering includes the composed objective and date.
It is instrument validation, not evidence that a model improved.

## Two arms, same harness

Create two **fresh eval worktrees at the harness PR head**. Keep all harness code,
composition clock plumbing, corpus, private file and database source identical.
In the before worktree only, restore these three files from `853e8d5a`:

```bash
git restore --source=853e8d5a -- \
  nora/domain/persona.py nora/language/persona.py \
  nora/dreamer/objectives/persona-person.md
```

The after worktree retains those files from `126bb10f9` (the harness does not edit
them). This composes the whole source-grounding change: retention semantics,
objective, and clock/source-date rendering. No fake-before commit is made.
Record each worktree HEAD, `git diff`, and tree/file hashes. Run all six offline
inspections above on both arms, capture their output, and diff it before
paying: every input must contain every declared source; the after input must contain the new about-person clock, source timestamps
and the amended objective. The input differences, not merely Git refs, prove the
instrument sees the treatment. Both arms keep the clock-plumbing fix so the
comparison varies the stated treatment, rather than today's machine clock.

Run sequentially under the host's eval lock, with the same frozen source:

```bash
uv run python -m evals persona_identity --sample 1 --reasons --positions
```

Six cases x one draw x two arms; no `--judge`, standalone baseline or automatic
redraw. At most two draws only when the root justifies them. Capture each command's
rc and complete log. `--positions` contains private canonical-head/settlement
receipts, so keep it private. The regular eval environment state carries the
model-call/tool spans, spend and trace provenance; each receipt also carries its
trace ID, actual source row IDs/times, revision movement, settlement time, samples,
configured runtime budget and canonical heads with writer-session provenance.
A provider error, zero readable samples, missing settlement, unknown entity key,
refused seed or unreadable heads is ERROR even for an expected empty result.

## Caps and limits

Use the deployment config, never a model override. `DreamerRuntime.budget_for`
resolves persona-person's own block, then the culture block, then the bounded
fallback. The checked-in example inherits culture's 10 turns / 120000 total tokens /
6000 output tokens, with a 120s timeout default and 12 memory reads; the default
without that block is 6 / 40000 / 4000 / 120s / 2 memory reads. One schedule sample
per fire is the current composition default. These are config ceilings, not
measured latency or spend. Read the live config privately before the run and use
the receipt's resolved budget. The CLI's generic unjudged estimate is 46s/draw
(~9.2 min for both arms), unmeasured for this writer; the operator's ~42min bound
is the conservative planning bound. No `--allow-long` without root authorization.
Canonical as_of remains the max cited source timestamp ceiling. A structurally
valid citation is not proof of semantic support; that is precisely what these
invented stimuli measure in the model's writer output.


## Additional-attribute treatment

The original six cases and their grading contracts remain unchanged; two new
invented vectors combine explicit height plus source-reported age, and reject
incidental activity as durable ownership. Use the same registered corpus, private
case generator, production writer and deterministic grader. No model judge or
second harness is added. Offline tests validate this instrument only; model
acceptance for the fallback remains UNMEASURED until the root runs both arms.

For this treatment, create two fresh eval worktrees at the additional-attributes
PR head (record the actual SHA). Both retain that head's eight-case harness and
private file. In the **before** worktree only, restore from `3c39b39c08967be8eb709422b260f0fe98aa493b`:

```bash
git restore --source=3c39b39c08967be8eb709422b260f0fe98aa493b -- \
  nora/domain/persona.py nora/agent/tools.py \
  nora/dreamer/objectives/persona-person.md
```

The after arm keeps those files at the PR head. Record HEAD/tree, treatment-file
hashes and the before diff. Inspect all eight inputs with the loop above before
spending: verify the declared source rows/dates, source height/age and composed
objective in both inputs. Run sequentially under the existing host eval lock:

```bash
uv run python -m evals persona_identity --sample 1 --reasons --positions
```

Eight cases x one draw x two arms. No paid calls are part of the build. Root may
select six including controls to fit its operator budget; record every selected
case, call/spend/runtime receipt and any ERROR separately from FAIL. The generic
CLI estimate is not a measured writer latency. Root must read the actual live
budget privately and keep private positions/receipts outside Git.

The remember name, opening paragraph and level are unchanged; argument guidance
and the schema's fact_key enum changed. Root should measure before/after routing
with the existing command (same two treatment arms, no model override):
`uv run python -m evals tool_selection --sample 5 --reasons`.
No result interpretation moved, so no new tool_reporting arm is required.

The production change consists of the validator/formatter, remember signature and
argument guidance, Persona objective, and the portal payload/schema addition.
The domain/tool/portal code needs the normal image rebuild; the objective is
bind-mounted and becomes live at the next pull. This document authorizes no
pull, config change, historical write, merge or deploy. Root owns independent
review, bounded model acceptance and subsequent append-only recovery.

## Historical v1 cached-source input acceptance

The v1 strict deduction expectation is semantically ambiguous for the explicit
self-endorsements below. Its recorded cached-late run remains FAIL; it is not a
recommended provenance gate. Frozen v1 names, private files, strict grader and
receipts remain historical evidence, never retroactively regraded. The prior
independent review cleared the exposure instrument but missed this ambiguity.
The original eight-case coverage results are unrelated and unchanged.

The original eight-vector private file remains valid without changes. Generate a
**new** private file with the optional three media vectors (never overwrite the
original file or its receipts):

```bash
uv run python -m evals.persona_cases /tmp/persona-source-inputs.yaml --source-lanes
export NORA_EVAL_PERSONA_CASES=/tmp/persona-source-inputs.yaml
export NORA_EVAL_PERSONA_MEDIA_EXPOSURE=cached
for case in profile_cached profile_missing profile_late birthday_retained; do
  uv run python -m evals persona_identity --print-input "$case"
done
```

This is the existing corpus, Subject, writer runtime and storage grader. The file
must contain exactly the original eight vectors, optionally plus all three source
vectors. Sources accept only `body`, integer `ts`, and optional `attachment` with
exactly `reading` (nonempty synthetic cached text or null) and `late` (boolean).
Unknown fields, arbitrary author/source IDs and malformed readings are refused.
The anchor and roster account are still private; adapt them in the new file only.
The invented self-profile and caption carry no real personal facts.

Each attachment becomes a synthetic MediaBlob on the author's actual scoped
Record row, through the audit store. Its placeholder uses the same blob hash.
SqlMediaObjectStore.comprehend seeds only cached interpretation; there is no
vision, download, byte retrieval or optional-index authority. The seed is read
back from its owning store. `profile_missing` supplies no cached interpretation;
its caption is still real evidence, with an explicit unavailable media block.
Missing declared rows or an unavailable source domain are fixture ERRORs.

`profile_late` first reads and settles the uncached message's actual
SqlPersonaInputs revision, advances the consumed message and completed-history
watermarks past that message, then writes the cached interpretation. The actual
revision reader must re-deliver that old message after the cache changes. A second
caption arrives afterward. The writer runs once, without a warmup model session.

The positive canonical value is `food_preferences=["vegetarian ramen"]`, mode
`deduced`, no `told_by`, both invented Record citations, and assertion time of the
later caption. The first caption explicitly attributes the self-profile to its
sender; the second confirms that its preference is current. The image reading
contains the qualified preference. Neither a pictured person nor an asset implies
identity or ownership. Missing reading must produce no current typed Identity.
The independent storage grader reads settled chain heads, never writer prose.

No runtime-ref selection seam exists here. The supported comparison is a fixed
**exposure ablation**, not a historical release baseline: two fresh sandboxes at
the same reviewed harness/runtime SHA, identical seeds and source, varying only
`NORA_EVAL_PERSONA_MEDIA_EXPOSURE=caption_only` versus `cached`. The former makes a
shallow copy of the composed adaptor with its existing optional MediaJoin absent;
revision delivery, SQL seeds, objective and runtime remain unchanged. It never
copies new writer logic into an old release. Root must review this limitation
before any paid run. Capture `--print-input` for all selected cases in both arms:
only the intended cached-reading block should expose the preference, with author,
room, source IDs and timestamps retained. The retained-birthday control is unchanged.

After root review, select at most these four cases, n=1 in each arm (at most eight
writer invocations), sequentially under the existing eval lock, with a 30-minute
outer bound. Use repeated `--case` only if supported by the CLI; otherwise invoke
one case at a time with `--sample 1 --reasons --positions`. Read the actual resolved
case budgets first; reduce selection if their time ceilings exceed 30 minutes.
Invocations are not provider-call counts. Keep the current model/route and actual
call/token receipts; no proxy price estimate, judge, retries or redraws. A positive
case may FAIL in the caption-only arm; that is the exposure measurement.

Offline real-Postgres tests script only the provider boundary and separately run
no-agent render inspection. They validate the instrument, not model quality.
Social, Register, Rollup, source-edit model quality, real cached interpretation
accuracy and historical-release acceptance remain unmeasured by this selection.

Both arms freeze the same declared Subject clock. Owning-store row-history
`recorded` timestamps are database observation times and naturally differ across
fresh sandbox creation; preserve them in raw receipts and exclude only that
field in a secondary exposure comparison. Never normalize assertion time,
revision hashes, IDs, captions or attribution to make an arm comparison pass.


## V2 provenance contracts (model acceptance UNMEASURED)

Generate a separate file with `python -m evals.persona_cases /tmp/persona-source-v2.yaml
--source-lanes-v2` (on one command line). The closed loader accepts the original
eight alone, eight plus the complete v1 trio, or eight plus the complete v2 trio;
unknown, partial and mixed bundles are rejected. The existing `--source-lanes`
output is unchanged. Do not modify a frozen private file or an old verdict.

- `profile_endorsed_late`: the exact two synthetic v1 endorsements and reading,
  with a new name. Either `told` by the actual source author or `deduced` with
  null teller is defensible. Wrong author, other modes, unqualified value,
  wrong time or missing roots fail. This is a new contract, not a rerun to green.
- `profile_media_only_late`: two attachment-only human Record messages on
  separate HKT days. Reading one is “Profile K7: usual food preference is
  vegetarian ramen.” Reading two is “Profile registry: K7 belongs to Miles.”
  The subject name is resolved from the existing synthetic roster fixture.
  This is printed textual attribution, never face or biometric inference.
  Both uncached source revisions and cursors are consumed through their owning
  stores before either reading is cached. One writer invocation follows the
  transition, with no warmup. Both roots must return in its real reader batch.
  The sole current Identity is `food_preferences=["vegetarian ramen"]`,
  `deduced`, null `told_by`, exact two roots and later source assertion time.
  `told` fails even with the correct value and author.
- `profile_media_only_missing`: the same two attachment-only sources with one
  reading absent. Both revisions stay pending so both roots remain visible;
  absence is explicitly unread media, not an unavailable source domain.
  Either omitted premise must leave typed Identity unknown. Offline tests omit
  first and second independently. Missing fixtures or reader failures ERROR.

Both raw bodies become actual attachment placeholders, with no personal claim
or endorsement. The real renderer marks both sources interpretation-only; the
normal validator refuses direct `told` and permits two-premise deduction.
No runtime code, prompt, semantic classifier or general mixed-media policy changes.
The birthday-retained control and original assertion timestamps are unchanged.

Real PostgreSQL no-agent render and scripted-provider writer/store tests prove
instrument behavior, not model acceptance. Both exposure arms seed identical SQL
cache and differ only in the optional media join. Preserve physical `recorded`
times separately from assertion time in the raw input receipts.

After fresh root review, the proposed paid selection is only strict
`profile_media_only_late` plus `birthday_retained`, n=1 in each exposure arm:
four writer invocations at the previously observed 360s cap, at most 1440s under
a 1800s outer bound. Recheck actual caps before running. Stop on failed cached
positive; no redraw or judge. Endorsed and missing cases may remain paid
UNMEASURED. The previous r3 used three invocations/six provider calls and stopped
at cached-late FAIL; it is not a v2 acceptance result. This correction makes no
runtime-fix, historical-release or general-quality claim.

## Qualified representation treatment (model acceptance UNMEASURED)

Generate a new private file with `python -m evals.persona_cases
/tmp/persona-representation.yaml --representation` on one line. This optional
bundle adds three invented cases to the unchanged eight-case corpus: a dated
inclusive age-band report, three shared reels, and authored participation on two
transports. Use `birthday_retained` as the unchanged control. No private source
text, names or real IDs are included. Existing source-lane bundles remain separate.

The participation fixture selects only existing listed rooms for the actual
subject in the anchor's unit, using the requested platform. It creates no room or
account. The private anchor/person must already support Signal and Discord;
missing membership/account mappings are setup ERROR, never an expected absence.
The source platform now appears beside the room label in real writer input.
A reel URL establishes sharing that content, not participation on its destination.

Categorical reports use the existing human told-only `attribute` shape with
`name="statement"`, a complete qualified value, the original teller, root and
assertion date. Observed behavior uses existing Character traits and mode rules;
it gains no Identity attribute, ownership claim, cap exemption or decay exemption.
Only these three vectors require `--judge`: full qualified meaning is judged
collectively across canonical heads, permitting equivalent wording and faithful
decomposition. Frozen properties retain the original subject, dates and full
conjunction; ownership and usual frequency remain unknown. The judge receives
declared source bodies/times/transports, mapped roots, author and canonical heads.
Deterministic shape, teller, date, citation and settlement checks still bind.
Missing, truncated, malformed or indecisive judge output is ERROR. One bounded
structured draw uses the existing `nora-judge` client, low effort and 4096 response
cap, with trace/model/usage accounting. The original eight vectors make no judge
calls. Scripted tests establish wiring, not semantic model quality. Character
acceptance does not close typed Identity coverage.

For before/after, keep this extended harness/private file on both arms and restore
only `nora/language/persona.py` and `nora/dreamer/objectives/persona-person.md`
from the recorded pre-treatment base in the before arm. Record exact HEAD, diff
and treatment hashes. Inspect actual `--print-input` for the three cases and
birthday control on both arms. Sources/dates must match; only treatment input
may differ. Physical database observation timestamps remain in raw receipts.

Root owns the fresh review, final gate, any bounded paid acceptance and historical
recovery. No model calls, merge, deploy or recovery are authorized by this section.

Synthetic writer `--positions` receipts retain the normal tool inputs and results
from the existing span exporter, appended **after** grading so those diagnostics
do not cue the judge. They exclude provider generation output and reasoning.
Keep these receipts private. Empty captured content is not proof of an empty call;
a recorded trace ID does not establish external trace durability.
