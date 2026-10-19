# Persona person writer acceptance

This is deterministic grading of the actual Persona writer, not `tool_selection`
and not a semantic judge. A draw obtains the existing Subject, restores the normal
snapshot background, marks that restored history complete in the private sandbox,
appends declared invented sources using the roster's account,
seeds the held birthday via SqlPersonaStore.persona_write, and calls DreamerRuntime
with PersonaPersonAdaptor's entity key and evidence reader. It reads
SqlPersonaStore.account_heads, then selects current typed Identity. Predecessors
whose stored state still says current are never counted as current heads.

The six invented vectors are in `design/unified-memory/75-typed-identity.md`.
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
for case in birthday_retained birthday_corrected residence_unknown marriage_unknown partner_unspecified explicit_home_spouse; do
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
