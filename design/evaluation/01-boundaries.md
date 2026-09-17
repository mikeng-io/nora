# The boundary — clean code, DDD, and where the current one went

## 1. The rule

> **Different evaluation suites. Different input. One Nora.**

Read as a dependency rule: **the eval layer depends on Nora. Nora depends on
nothing in the eval layer, and the eval layer assembles nothing.**

That single direction is what the current arrangement does not have, and §4 is
the evidence.

## 2. The four things, and nothing else

| | what it is | where it lives |
|---|---|---|
| **Case** | input + the expectation it must meet + which entry point it enters at | domain — data, no behaviour, no imports |
| **Subject** | Nora. Obtained, never built | one function, one caller |
| **Runner** | takes a case, obtains the subject, drives it, returns an outcome | application |
| **Outcome** | what happened, in one shape, for a grader to judge | domain |

**A suite is a set of cases.** It is not a module, a driver, or a function. If
adding a suite requires code, the boundary has already leaked — that is the test
of this design and it is mechanically checkable.

## 3. The DDD shape

**Domain** — `Case`, `Expectation`, `Outcome`, `Verdict`. Pure. No store, no
model, no `nora.app`. This is where "what a suite IS" lives, and it is small.

**Ports** — the seams the runner needs and does not own:

- **entry point** — where the input enters her. `FirePath.speak`,
  `RelevanceGate.decide`, a Dreamer pass. **This is the field that replaces the
  eight drivers**, and it is a selection on a case, not a branch in the runner.
- **stores** — what she knows. Per the operator: *配置嘅啫嘛*. A case says what
  she knows; that is input, not substitution.
- **model** — which one, at what effort.

**Application** — the runner. One function. No suite-specific branch.

**Infrastructure** — store implementations, the model client, reporters, the
provenance line. Swappable without the domain noticing.

### 3.1 Why the entry point is a port and not a taxonomy

`relevance_gate` drives a real component and no turn. A previous attempt at this
subject had a binary — either Nora is the subject or she is not — and had no room
for it, then graded it with a third answer its own rule did not contain (PR #307,
withdrawn).

As a port there is no taxonomy to fail: `RelevanceGate.decide` is an entry point
exactly as `FirePath.speak` is. **If that turns out to be false, this design is
refuted**, and `relevance_gate` is the case to try it on first — it is the
cleanest instrument in the repo and a shape that cannot hold it is wrong.

## 4. Where the current boundary went — measured

**`nora/app/eval_support.py` is 1,739 lines, and it lives inside the
application.** It holds three of the six composition sites (`:980`, `:1109`,
`:1303`, `:1359`). `evals/` imports it **five times**.

So today the eval layer's subject-assembly is inside `nora/app/`, and the eval
layer reaches in to use it. Both directions are wrong at once:

- Nora's package carries eval scaffolding — the application knows about being
  measured.
- The eval layer does not own its own subject — it cannot state what it is
  measuring without reading `nora.app`.

`nora/app/offline.py` (335 lines) is the same shape: a pipeline assembly that
exists only for evals and tests, sitting in the application package because
`evals/` may import `nora.app` and may not import `tests/`
(`tests/test_boundaries.py`). **The import rule is the reason the assembly is in
the wrong place, and the assembly being in the wrong place is why six of them
grew.**

## 5. What the boundary forbids, stated so it can be checked

1. **The eval layer never composes a subject.** One function returns Nora; no
   caller passes collaborators. Mechanically: no `DreamerRuntime(`,
   `RelevanceGate(`, `Agent(`, `build_offline_pipeline(` anywhere under the eval
   layer.
2. **Nora's packages contain nothing that exists only for evaluation.**
   `eval_support.py` and `offline.py` do not survive in `nora/app/`.
3. **Adding a suite adds no code.** A suite is cases.
4. **Nothing reaches the model that the deployment would not send.** The prompt
   is the observable; it is the same prompt or the subject is not Nora.

Each is a property a test can hold, and (1) and (3) are the two that decay first.

## 6. What is NOT decided here

- **Where the one subject function lives**, given that the import rule
  (`tests/test_boundaries.py`) is what pushed the assembly into `nora/app/`. The
  layer contract refuses `nora.testing` — measured 2026-09-17: `nora.testing →
  nora.app` breaks 2 contracts (a reverse import), `→ nora.infra` 1, `→
  nora.config` 2. So the home is an open question and it is the first one.
- **Whether the runner can drive `build_dependencies` directly.** It is drivable
  — `tests/test_composition.py` calls it ~90 times against a throwaway Postgres —
  but the live tier's cost is unmeasured.
- **What replaces the reconstruction suites.** A bare model on doctrine text is a
  cheap, useful probe of WORDING. Under this boundary it is not an evaluation of
  Nora and cannot use the runner; whether it survives as a separate named tool is
  open.

## 7. The reading that would refute this node

**If the entry point cannot be a port** — if driving `RelevanceGate.decide` and
`FirePath.speak` through one runner needs different lifecycles, different
setup order, or different outcome shapes — then §2's four things are really five
or six, and the boundary is drawn wrong.

Try `relevance_gate` and `participation` through one runner before building the
rest. Two entry points that are maximally unalike is the strongest cheap test
this design has, and it has not been run.
