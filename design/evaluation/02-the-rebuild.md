# The rebuild — what goes, what survives, and the order

## 1. Size

| | lines |
|---|---|
| `evals/**/*.py` | **5,629** |
| `evals/**/*.yaml` (13 corpora) | data — **survives** |
| `nora/app/eval_support.py` | **1,739** — five of the seven composition sites |
| `nora/app/offline.py` | **335** |
| **deleted or re-homed** | **~7,700** |

Not small. The argument for doing it anyway is `00-overview` §1: there is no
target shape the current code is an imperfect version of, because there are six
shapes and the target is in production.

## 2. Order, and the first step is not code

**Step 0 — DONE, 2026-09-17. See `03-step0-results.md`.**

Two components checked against production's construction of the same component.
**Both diverge in bytes that reach the model**, so `00-overview` §8's refutation
does not fire and this bundle stands:

- **`RelevanceGate`** — 539 vs 559 bytes for the same window. The eval gate loses
  the operator-canonical name and the media description; an image-only message
  reads as an empty body and its row is dropped.
- **`FirePath`** — three prompt blocks empty or absent, from three integers that
  default to zero. `<people_in_window>` is not rendered at all
  (`fire.py:1122`). Nothing guards this: `test_assembler_parity.py` compares
  `composition.py` against `offline.py`, both `FirePath(36)`, and never looks at
  `eval_support.py`.

**The cost objection is also settled and was false** — `build_dependencies` costs
**64ms** per case against one session-scoped container and a template clone, not
a container per case. So "one Nora, obtained per case" is affordable and a case
can be plain data.

**All seven now checked.** Four reach the model (`RelevanceGate`, `FirePath`,
`RegistryToolSource`, `build_offline_pipeline`); three come back empty (both
`DreamerRuntime`s — the note-candidates adaptor declares no tools and
`store_timeout_seconds`' default equals the configured value — and
`LiveAgentRunner`, whose missing argument is a metric sink).

**The three empty results are the sharpest evidence here.** Nobody could tell in
advance which four mattered: the `DreamerRuntime` difference looks alarming and
is inert, `store_timeout_seconds` looks inert and had to be checked against a
constant, and `media_join` turns up in three of the four. Every answer was
derived by hand today, and nothing stops them changing tomorrow.

**Step 1 — the one subject. Home DECIDED 2026-09-17: option 1, name the slot.**

A new layer above `nora.app`, inside the `nora` root package so the contracts can
see it (`01-boundaries` §6). Probed: the layer holds, and `nora.app` → the new
package is correctly refused.

**Three edits, two found only by probing** — the layers list; the "only app and
`__main__` read config" contract, whose `source_modules` is explicit so a new
package is unconstrained by default; and `EVALS_ALLOWED_NORA_PACKAGES` in
`tests/test_boundaries.py`, which is the gate that actually binds (the
import-linter evals contract passed while `evals/` imported the new package, by
design — `allow_indirect_imports`).

Then: one function returning Nora, no caller passing collaborators.

**Steps 1-4 and 6 — DONE 2026-09-17. Step 5 is the only one left.**

> **SUPERSEDED IN PART 2026-09-18 by `07-sdk-native`.** Steps 2 and 5 below
> built a runner, an outcome type and a report renderer that
> `strands-agents-evals` already ships — 1,037 of 2,268 lines. Step 1 (the one
> subject) is untouched by that and is the part worth keeping. Read
> `07-sdk-native` before treating this sequence as the plan.

| step | state |
|---|---|
| 1 — the one subject | `nora/subject/` — `obtain(spec)`, 40-field graph, 3+4 tests. **Stands** |
| 2 — two entry points, one runner | §7 run; did NOT refute. 4 pins, all falsified. **The runner is the SDK's `Experiment`; the entry-point-as-port finding stands** |
| 3 — re-home the corpora | 13 files, 149 cases, pinned field-by-field. Content stands; the bespoke YAML schema is `Case` data |
| 4 — the remaining entry points | `tag` and `dreamer` added. 23 cases refused for missing seams, loudly |
| 5 — the delete | **DONE 2026-09-18.** 8,218 lines deleted, three files re-homed into `tests/`. See `04-the-convention` §6 |
| 6 — the convention | `04-the-convention.md` |
| 7 — the SDK was there all along | `07-sdk-native.md`. The harness half of steps 2 and 5 is to be replaced, not re-derived |

**Why Step 5 was held, stated as a rule rather than a hesitation.** The new
layer had never reached a model. Every measurement in the session that wrote
this was taken with the provider deliberately unreachable, which proves the
fail-closed behaviour and proves nothing about agreement. Deleting the only
instrument that had produced a live number, on the strength of a replacement
that had not, would have been this bundle's own finding repeated one level up.

It was released by the live run in `06-first-full-run` §8 — and then that run's
own numbers were withdrawn by adversarial review, so the delete landed on a
layer whose numbers do not stand. `07-sdk-native` §7 records what survives.

---

**Step 2 — two entry points through one runner.** `relevance_gate` and
`participation`, deliberately the least alike. `01-boundaries` §7: if they cannot
share a runner, the boundary is drawn wrong and we learn it here rather than at
the sixth.

**Step 3 — re-home the corpora.** Data, not rewrites. Their headers carry
measured readings and caveats; those travel.

**Step 4 — the remaining entry points**, one at a time.

**Step 5 — delete `evals/*.py` and `eval_support.py`. NOT `offline.py`.**
Last, not first.

**`nora/app/offline.py` IS NOT AN EVAL MODULE, and this bundle said it was.**
Measured 2026-09-18 by an adversarial reviewer who ran the delete:
`build_offline_pipeline` is the offline composition root for
`tests/integration/harness.py`, so deleting it stops **106 integration tests**
and takes **ten acceptance rules** with it — R-1, R-3, R-5, R-8, R-9, R-10,
R-10b, R-10c, R-12, R-23. §1's line item "`nora/app/offline.py` (335 lines)"
was wrong about what that file is for, and `01-boundaries` §4's reading of it
as "a pipeline assembly that exists only for evals and tests" collapsed two
very different things: evals, and the integration suite.

It stays. If it should eventually go, that is its own piece of work with its
own argument, and it is about the integration suite rather than about
evaluation.
Deleting before the replacement drives one real case means a period with no
instrument at all, and the instrument being wrong is better than absent while
something is being built against it.

**Step 6 — the convention**, describing what exists. Not before. Attempted first
on 2026-09-17 (PR #307) and withdrawn: a convention written first describes the
six variations and legitimises them.

## 3. What survives, explicitly

- **13 corpora.** Cases and expectations are data and were never the problem.
- **The graders** — judge, rubric, structural. What a verdict means does not
  depend on how the subject was built.
- **`subject=` in the provenance line** (#310). The qualifier travels with the
  number into logs and pastes; prose in a document does not.
- **The recorded findings inside corpus headers** — measured readings with their
  caveats, which are provenance.
- **Every local design decision that was right.** `participation_gate=None` is
  deliberate and correct (*"a suite that had to get past a live gate call first
  would be measuring the gate's verdict and the turn's behaviour in one
  number"*). The rebuild keeps such decisions and drops only the six assemblies.

## 4. What this costs while it runs

**The live suites keep working until step 5.** They measure six different
subjects, which is the defect — but they are the only behavioural instruments
there are, and `00-overview` §3 counts 13 rules needing one.

So during the rebuild: **no number from the old layer may be quoted as behaviour
without naming its subject.** That is #310's provenance token doing the work it
was added for, and it is the only discipline this bundle asks for before step 5.

## 5. What would make this stop

- **Step 0 shows the subjects agree.** Withdrawn.
- **Step 2 shows the entry point cannot be a port.** The boundary is redrawn
  before anything else is built.
- **The one subject has no legal home.** `01-boundaries` §6 — if the layer
  contract admits no location, the contract is the thing to change, and that is a
  larger decision than this bundle and would need its own.

Each of the three is checkable before the expensive part, which is the property
this bundle has that PR #307 did not.
