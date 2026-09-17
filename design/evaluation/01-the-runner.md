# The runner — and why there are two builders

## 1. The question `00-overview` §5 asked first, answered

> Whether the production composition root can be driven in a test at all, and at
> what cost.

**It can, and something already does it ~90 times.** `tests/test_composition.py`
calls `await build_dependencies(cfg)` on nearly every test, against a throwaway
Postgres, with no mirror and no re-wiring.

`tests/integration/harness.py` goes further — *"real pipelines, real db, real
guards — fake model, fake senders"* — and its docstring says exactly what this
bundle wants:

> the wiring below is the harness-side mirror of
> `nora.app.composition.build_dependencies`, **using `nora/testing`/local fakes
> wherever `build_dependencies` would use a real collaborator.**

So the shape `00-overview` proposes is not hypothetical. It exists, it is used,
and it is not what `evals/` runs.

## 2. Why `evals/` does not use it — and it is not a technical reason

`tests/integration/harness.py` states the cause in its own docstring:

> The ingest+engagement+dispatcher wiring tail itself moved to
> `nora.app.offline.build_offline_pipeline` for the same reason — **`evals/` may
> import `nora.app` and `nora.testing` only**, so that function is what it calls
> instead of duplicating this module's pipeline assembly.

Enforced by `tests/test_boundaries.py::test_evals_only_imports_nora_app_and_nora_testing`
and a matching `import-linter` contract.

**`nora/app/offline.py` exists because of an import rule, not because the
composition root resists being driven.** The rule is defensible on its own terms
— `evals/` should not depend on `tests/` — but its consequence was a second
builder, and a second builder is a second Nora.

The cost was paid and is recorded. `tests/architecture/test_assembler_parity.py`
(2026-09-04):

> `nora/app/composition.py` passed 18 keyword arguments and `nora/app/offline.py`
> passed 7, so **every eval measured an agent missing
> `<standing>`/`<notes>`/`<facts>` with nothing to notice.**

That is the drift this bundle exists to remove, already having happened once,
already having been patched at two construction sites, and still unheld across
the rest of the wiring tail.

## 3. What this means for the runner

The runner does not need a new way to build Nora. **It needs the existing one to
be reachable from `evals/`.** Three candidate shapes, and the choice is the
decision this node is asking for:

**A. Move the real harness into `nora.testing`.** `evals/` may already import it.
The integration harness's wiring becomes one shared function; `offline.py`'s
mirror is deleted rather than kept in parity. Largest move, removes the cause.

**B. Widen the boundary** so `evals/` may import the integration harness. Cheapest
edit, and it inverts a rule that was chosen deliberately — `evals/` depending on
`tests/` makes the eval layer a consumer of the test layer, which is backwards
for a thing that is supposed to measure production.

**C. Keep two builders and hold them with a full parity gate.** Honest about the
constraint, and it is the status quo plus enforcement. It also accepts, forever,
that the thing measuring Nora is a copy of Nora maintained by a test.

**A is the one that matches `00-overview`'s sentence.** B and C both leave a
second assembly in the tree; the bundle's whole claim is that there should not be
one. But A is a real migration and its cost has not been sized, which is why this
is a decision and not a plan.

## 4. What the runner is, under A

- **Compose once** through the shared harness, `nora/testing` fakes wherever
  production would reach the world — fake sender, fake model when scripted, real
  model when live, throwaway store.
- **Take a case and an entry point.** `FirePath.speak`, `RelevanceGate.decide`,
  `DreamerRuntime` — the entry point is a field on the case, per `00-overview`
  §3. This is what absorbs the eight drivers' differences.
- **Return what happened**, in one shape. `Trajectory` already is that shape for
  the suites built on `run_case`.
- **Have no per-suite branch.** The test of the runner is that adding a suite
  requires no edit to it.

## 5. What is deliberately unanswered here

- **The cost of A.** How much of `tests/integration/harness.py` moves, what it
  breaks, and whether `nora/testing` is the right home or just the reachable one.
- **Whether `evals/` should import `nora.testing` at all** for the LIVE tier, or
  whether a live run should reach further toward production than a scripted one.
  Today both go through the same fakes.
- **Whether the entry-point field is really sufficient** — `00-overview` §4.1's
  `relevance_gate` case is the test, and it is untried.

## 6. The reading that would refute this node

If moving the integration harness into `nora.testing` turns out to drag `tests/`
machinery with it that has no business in a shipped package — container
fixtures, migration scaffolding, pytest-only helpers — then A is not available,
and the bundle's sentence has to survive with two builders or be withdrawn.

`tests/integration/harness.py`'s own note says `ScriptedTurn`, `FakeAgentRunner`
and `group_event` already made that trip in Task 10, which is weak evidence that
the rest can. Weak because those are data helpers and the pipeline assembly is
not.

**Size that move before anything else in this bundle is built.** It is the one
fact that decides whether `00-overview` describes an architecture or a wish.
