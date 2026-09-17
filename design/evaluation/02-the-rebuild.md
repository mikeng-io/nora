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

**Five components remain unchecked** — the two `DreamerRuntime`s,
`RegistryToolSource`, `LiveAgentRunner`, `build_offline_pipeline(14)` — and each
could still come back empty.

**Step 1 — the one subject.** One function returning Nora, its home settled
(`01-boundaries` §6 — the layer contract refuses `nora.testing`, measured). No
caller passes collaborators.

**Step 2 — two entry points through one runner.** `relevance_gate` and
`participation`, deliberately the least alike. `01-boundaries` §7: if they cannot
share a runner, the boundary is drawn wrong and we learn it here rather than at
the sixth.

**Step 3 — re-home the corpora.** Data, not rewrites. Their headers carry
measured readings and caveats; those travel.

**Step 4 — the remaining entry points**, one at a time.

**Step 5 — delete `evals/`, `eval_support.py`, `offline.py`.** Last, not first.
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
