# Persona coverage census

`persona_coverage` is an entry in `python -m evals`, not a second runner.
It uses the existing Experiment, Judge client, bounded timeout, spend accumulator,
local OTel trace capture, Outcome/MeetsGrader and positions report. A frozen
census has no writable subject: it does not create a sandbox, Honcho workspace,
Nora reply, or config file. The writer and reply paths are unchanged. This is
the coverage gate in `design/unified-memory/45-persona-adaptor.md` §6; the
80-pair reply replay remains subsequent work. No formal coverage is claimed by
building this instrument or by the independent synthetic writer acceptance.

## Export and inspect without model calls

Run in the branch checkout on norty, with its eval extra installed. Keep every
input, rendered prompt, log, review file and receipt private on that host. Use
a read-only database URL in `DATABASE_URL` (the exporter also creates the
existing readonly SQL engine). Credentials stay in the environment, never the
command arguments or artifacts. The runtime container's URL is on the compose
network; for a host checkout use the published database address and the same
credentials through the normal private environment setup.

```bash
uv sync --extra evals
uv run python -m evals --coverage-export /tmp/persona-coverage-input.json \
  --coverage-manifest /tmp/persona-coverage-census.manifest.private.json \
  --coverage-inventory /tmp/persona-honcho-coverage-r1 \
  --coverage-semantic /tmp/persona-honcho-semantic-r1 \
  --coverage-recovery /tmp/persona-attributes-deploy-recovery \
  --coverage-identity /home/norty/workspace/nora/identity.toml
# Root retains the exporter-reported manifest_hash independently of editable input.
export NORA_EVAL_PERSONA_COVERAGE_MANIFEST=/tmp/persona-coverage-census.manifest.private.json
export NORA_EVAL_PERSONA_COVERAGE_MANIFEST_HASH=<exporter-reported-manifest_hash>
export NORA_EVAL_PERSONA_COVERAGE=/tmp/persona-coverage-input.json
uv run python -m evals persona_coverage --print-input frozen_coverage \
  > /tmp/persona-coverage-render.private.json
```

Export refuses overwriting. It reads `SqlPersonaStore.account_heads` fresh and
filters current state, then uses `SqlCharacterEvidence.historical_page` through
the frozen account's rooms/person/boundary. It does not re-sample or re-label.
All original 561 rows and their prelabels expand to exactly 879 scoped ledger
rows, including 140 stable card and 39 stable explicit comparisons. Zero
exclusions are permitted. Every row retains its original row ID, scope,
prelabel and original source metadata. All nonstable rows stay visible in the
input, receipt and plan's prelabel counts, outside the stable-fact thresholds.

Before adjudication, root freezes a separate private manifest. Export re-reads
and hashes the original source files, reconstructs every scoped row, original
claim, prelabel, kind, scope and reference ID from their original roster,
comparisons and prelabels, and compares them with the export. The manifest holds
this projection, counts and source hashes. It is created exclusively at the
explicit destination with mode 0400; changing an input filename never replaces
it. Keep its reported canonical JSON hash in root's independent run instructions.
Every load and measurement (including pending-gold print preflight) requires the
manifest path AND hash from the environment and compares the exact projection.
Rehashing an edited input cannot change this independently retained expectation.
The receipt records the manifest path/hash. This prevents accidental/post-hoc
census edits under an independently pinned source; it is not filesystem security
against an operator who can replace both the manifest and its external pin.

To pin an ALREADY exported original without touching live stores or resampling,
run from the directory where its source provenance paths resolve (including the
original relative identity path):

```bash
uv run python -m evals \
  --coverage-freeze-input /tmp/persona-coverage-eval-build/input.json \
  --coverage-manifest /tmp/persona-coverage-census.manifest.private.json
```

This also verifies every original source-file hash and refuses any existing
manifest destination. Copy the original input to a new private adjudication file;
attach only the root reference labels/provenance, model routes and card absence
proofs there. Preserve reference IDs/order and all immutable population fields.
Use the SAME manifest path and independently retained hash for that new input.
Do not regenerate the pin from an edited adjudication file.

The input contains hashes of the original denominator, roster, prelabels,
comparisons, source search, original context, closure ledger and optional
identity file; a canonical hash binds the expanded frozen denominator. Full
current canonical heads remain in the private input. Judge context projects
current typed Identity and actual authored name/alias rendering only. It does
not promote legacy prose or character traits. Claim-specific supplied Record
candidates, cited originals and nearby original question/answer context carry
actual author, timestamp, age, room and unit. Evidence is oldest first, newest
last. This is bounded source evidence, not a claim that a no-hit proves absence.
Sources outside eligible rooms/bounds are refused. Closure annotations are
retained for inspection; fresh heads and eligible originals decide coverage.
Common evidence is rendered once within each account-scoped batch.

`--print-input` calls no models and can inspect pending review/404 uncertainty.
It prints the full private input and exact calibration/measurement prompts,
plus hashes, fixed counts, call counts, prompt characters and a conservative
planning proxy of two characters per input token. This proxy is not measured
usage or latency. No prompt contains expected or proposed reference labels.
The response token cap remains 4096 per call (reasoning plus content), with
`reasoning_effort="low"` on coverage calls only. The plan prints both this cap
and its total: 24 calls permit at most 98,304 response tokens at the client
request level. Gateway retry/fallback can consume more physical tokens; SDK
retries remain zero. No partial-ID salvage is allowed.

The observed run failed before measurement: five complete fenced JSON arrays
were rejected by raw JSON parsing; three gateway errors exhausted 4096 tokens
in reasoning without content. The original ERROR receipt is not reclassified.
Coverage now accepts one complete JSON payload, optionally inside one complete
JSON fence, while rejecting surrounding prose, malformed/truncated JSON and
multiple payloads. All schema, ID, source and physical-identity checks still run.

[GLM's official contract](https://docs.z.ai/guides/capabilities/thinking) supports
`low` for GLM-5.3-Flash and forbids disabling thinking; its unspecified default
is `max`. Inspected gateway OpenAI dispatch preserves explicit effort through
OpenCode and Ollama routes. [Ollama's compatibility contract](https://docs.ollama.com/api/openai-compatibility)
also exposes `reasoning_effort`. Low reduces requested reasoning rather than
blindly enlarging the cap. It does not guarantee completion or calibration;
any starvation, timeout or provider error remains an instrument ERROR. No other
Judge grading method, model alias or gateway/runtime configuration changes.

A future run requires fresh independent fix review and separate authorization.
The existing plan remains 8 calibration + 16 measurement sequential client
calls, 90 seconds each (36 minutes of bounded client waits), with a hard
55-minute outer timeout. Neither client timeout nor outer timeout proves
upstream cancellation. No redraw, relabel, resample or exclusions.

## Root review and route verification

Export also writes a sibling `.review20.json` with the EXACT 20 existing
reference claims, each selecting its first explicitly scoped comparison.
These are `PENDING_ROOT_REVIEW`, with preliminary proposals clearly marked;
`reference20-for-independent-review.json` is never promoted to gold.

Root independently reviews each reference against the full scoped row in the
input and its actual originals/qualifiers. Set each `expected` to `covered`,
`derivable_missed` or `source_unresolved`. In `references`, set `status` to
`ADJUDICATED`, `adjudicated_by` to the independent reviewer and `provenance` to
the private adjudication receipt/hash. Copy the reviewed references object into
the input, preserving all 20 IDs and the frozen denominator/prelabels.

Set `models.judge_id` (default `nora-judge`), `writer_id` (`nora-dreamer`),
`judge_actual`, `writer_actual`, and `route_provenance` from independently
verified gateway routes. Distinct aliases alone prove nothing. Unknown actual
identity or the same actual producer/judge refuses before spending. Every
response's model identity must match the verified actual judge model; an alias
returned instead of an actual identity is UNVERIFIED and an error. Root owns
route verification, including any fallback route. Do not guess physical models.

An unavailable card refuses measurement unless its `absence_proof` binds an
independent database snapshot hash and proves `peer_exists: false`,
`card_exists: false`, `explicit_population: 0`. The existing two HTTP 404s and
zero explicit-conclusion counts alone do not supply that proof. No card is
silently treated as empty. Root must provide real snapshot evidence; these
fields are an evidence receipt, not a bypass flag.

## Calibrate, measure, read back

Only after review and explicit authorization for model spending:

```bash
export NORA_EVAL_PERSONA_COVERAGE_RECEIPT=/tmp/persona-coverage-run.private.json
# Take the existing host eval lock; do not overlap another eval run.
flock -w 900 /tmp/nora-eval.lock \
  uv run python -m evals persona_coverage --judge nora-judge \
  --sample 1 --coverage-timeout 90 --reasons --positions \
  > /tmp/persona-coverage-run.private.log 2>&1
printf 'RC=%s\n' "$?"
```

Use a new receipt filename per run. The CLI prints its no-call plan first.
The run refuses a planned worst-case wall-clock total over 55 minutes; timeout
must be positive and at most 300 seconds. Calls are sequential, at most 16
lines each, and never cross account/unit. Plan counts both calibration and
measurement. One census draw is required; generic tool selection is not coverage.

Calibration runs first on exactly 20 root-adjudicated references. At least
18 agreements and zero instrument errors are required; otherwise ERROR stops
before any measurement call. This implementation always recalibrates; it
never reuses a receipt from another judge/settings/reference hash.

The private receipt contains every original scoped denominator row, hashes,
judge/writer identities, judge settings, calibration results, per-line labels,
why/evidence IDs, exact batch membership, response IDs, local trace IDs, spend
and errors. The normal private Outcome position reports every stable line as PASS/FAIL/ERROR,
with evidence IDs/why and batch trace/spend, and binds its receipt path and canonical
JSON hash (`digest(json.load(receipt))`), input hash, calibration and aggregate.
Local OTel trace IDs are not proof of external Langfuse persistence. Failed
calls count in calls; unavailable token usage is not a known spend of zero.
No dollar pricing is inferred.

PASS requires at least 80% of the 140 stable card comparisons, at least 60%
of the 39 stable explicit comparisons, less than 5% instrument errors, and
zero fresh `derivable_missed` results. `source_unresolved` remains noncovered
in the denominator. At exactly 5% errors the run is ERROR. Provider failure,
timeout, malformed labels, wrong model identity, missing/extra/duplicate IDs,
invalid evidence IDs or unsupported typed-head grounding make the whole batch
error. No lexical match or judge-self-reported aggregate decides PASS.

A derivable miss cannot be closed merely by editing a closure annotation.
Fix and read back its canonical head through the normal authorized workflow,
then export fresh heads against the SAME frozen denominator and judge again.
The paired reply gate still has to follow; coverage alone never enables render.
