# The runner — where the one composition lives, and what actually diverges

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
— `evals/` should not depend on `tests/`.

*(An earlier draft concluded from this that "a second builder is a second Nora".
§3.2 measures that and it is not what happened: both harnesses call the same
wiring. What diverges is the setup around it.)*

The cost was paid and is recorded. `tests/architecture/test_assembler_parity.py`
(2026-09-04):

> `nora/app/composition.py` passed 18 keyword arguments and `nora/app/offline.py`
> passed 7, so **every eval measured an agent missing
> `<standing>`/`<notes>`/`<facts>` with nothing to notice.**

That is the drift this bundle exists to remove, already having happened once,
already having been patched at two construction sites, and still unheld across
the rest of the wiring tail.

## 3. What this means for the runner

### 3.1 A is REFUTED, measured 2026-09-17

Moving `tests/integration/harness.py` into `nora.testing` **breaks the layer
contract three ways.** Not argued — three probes, each an injected import run
through `lint-imports`:

| probe | result |
|---|---|
| `nora.testing` → `nora.app.offline` | **2 contracts broken** |
| `nora.testing` → `nora.infra.sql.engine` | **1 contract broken** |
| `nora.testing` → `nora.config` | **2 contracts broken** |

The first is decisive. `nora.app` is the top layer, `nora.testing` sits at the
bottom beside `nora.infra`. **`nora.testing` → `nora.app` is a reverse import**,
which is the thing layers exist to prevent. The harness needs four `nora.app`
modules, four `nora.infra.sql` modules and `nora.config`.

The test-only dependency, by contrast, is trivial: `tests.builders` supplies two
names, `tests.fakes` supplies one, and **the harness imports no `pytest` at
all**. §6's worry was the wrong worry. The blocker is architectural, and taking A
means redrawing the layer contract — a different and much larger change than an
eval refactor.

### 3.2 And "two builders" was not accurate

**Both harnesses already call the same wiring:**

```
tests/integration/harness.py:484   build_offline_pipeline(...)
evals/harness.py:612               build_offline_pipeline(...)
```

What diverges is the **setup around it**:

| | `tests/integration` | `evals/harness` |
|---|---|---|
| store | real Postgres + `run_migrations` | `FakeRecordStore` / `FakeMemoryStore` |
| doctrine | `shutil.copytree` of the real files | the same `cfg` |
| sender | `SinkSender` | fake |

So the duplication this node was named for is not in the composition. It is that
`build_offline_pipeline` takes **13 parameters defaulting to `None`**, which is
what lets two callers each forget a different thing —
`test_assembler_parity.py`'s recorded incident (18 kwargs versus 7) is exactly
that, and it is patched at two construction sites and unheld elsewhere.

### 3.3 The remaining shapes

**A′ — remove the `None` defaults.** One `nora.app`-level seam that takes what
kind of store, which doctrine, which sender, and returns a composed subject.
No optional collaborators, so nothing can be forgotten. Both harnesses become
its callers. This is A's intent at a location the layer contract already allows,
because `nora/app/offline.py` is already there.

**B — widen the `evals/` import boundary.** Still available, still makes the
eval layer a consumer of the test layer.

**C — two setups behind a full parity gate.** The status quo plus enforcement.

**A′ is the one that matches `00-overview`'s §5.3.** It is also the one that makes
the operator's framing literal: *the stores are config behind it* becomes a
parameter with no default rather than a collaborator each caller wires.

## 4. What the runner is, under A′

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

- **The cost of A′.** What removing 13 `None` defaults breaks at each call site,
  and whether the setup differences really reduce to parameters.
- **Whether `evals/` should import `nora.testing` at all** for the LIVE tier, or
  whether a live run should reach further toward production than a scripted one.
  Today both go through the same fakes.
- **Whether the entry-point field is really sufficient** — `00-overview` §4.1's
  `relevance_gate` case is the test, and it is untried.

## 6. The reading that would refute this node

**A was already refuted this way** — §3.1 predicted a `tests/`-machinery drag and
found an architectural blocker instead, which is a better refutation than the one
being looked for. Recorded because the node asked for evidence and got some.

A′ falls the same way if **the setup differences do not reduce to parameters.**
If `tests/integration`'s real-Postgres path needs something structurally
different from `evals/harness`'s fake-store path — a different call ORDER, a
collaborator that only exists in one — then "store kind is a parameter" is false
and A′ collapses into C with extra steps.

**Check that before writing the seam.** Diff the two call sites' argument lists
against `build_offline_pipeline`'s 22 parameters and see whether the difference
is values or shape. Values → A′ holds. Shape → it does not.

That check is cheap, it is the first thing to do in this node, and it has not
been done.
