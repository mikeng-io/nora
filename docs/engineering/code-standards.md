---
class: engineering
subject: "the code standards every change follows"
owns: []
---

# Code standards

The rules every Nora change is written and reviewed against. A reviewer checks
each rule that applies, and a finding names the rule it breaks.

The rules sit on two axes. **Cross-cutting** rules hold for every change,
whatever it touches. **Per-stack** rules add what one stack needs: apply each
section the diff reaches. When two rules disagree, the stricter one holds.

Each row has three parts:

- **Rule**: what the change does.
- **A reviewer checks**: the observable thing that shows the rule holds.
- **Held by**: the test, gate or skill that enforces it, or *review* when only
  a reader can. Rules held by review alone are where lessons come from.

The stories behind the rules are in
[conventions rationale](conventions-rationale.md). Rules that are
not standards yet are in [lessons](lessons.md): a lesson seen
three times becomes a row here.

## Cross-cutting

### What a change contains

| Rule | A reviewer checks | Held by |
|---|---|---|
| A change does only what its goal needs. | Every behaviour change traces to the brief. Unrelated refactors, extra features and found-on-the-way fixes are separate PRs. | review; [scope](#reviewing-a-change) |
| Stop at the first rung that holds: no need, already here, stdlib/Postgres/installed dependency, one line, then the least code. | No new abstraction, config key for a constant, or helper beside an existing one. | review |
| A guard needs a consequence: a live count, an incident or a real trust boundary. | A guard without one is removed, and its ceiling is named in one line. | review |
| A special case is a missing requirement. | An `if` for one value, a retry, a swallowed exception or a skipped test comes with the fix behind it. Two patches to one seam mean the seam is wrong. | review |
| A boundary is drawn by scope, never by a filter inside it. | Rooms that must not share are in different units; no read filters a shared scope. | review; design bundle invariants |
| A change holds on the whole path. | Follow it from the input to the reply, the stored row or the trace, through the callers and every side effect. One correct function does not prove the operation. | review |
| A fix reaches every sibling. | Grep for the same shape: every platform, occasion, store and caller of a changed predicate gets the fix in the same change. | review |
| A change re-derives what is built on it. | A changed formula, list, threshold or timing has every caller, saved copy, baseline and test that assumed the old meaning updated and run. | review; the baselines under `tests/architecture/` |
| What changes together lives together. | A deliberate corner has a `# ponytail:` comment naming its ceiling. | review |
| Never simplified away: egress and disclosure rules, fail-closed measurement, error handling that would lose input, and anything the operator asked for. | None of these is weakened to make a diff shorter. Credentials and doctrine protection always run. | review; ADR 0030 |

### Measurement and failure

| Rule | A reviewer checks | Held by |
|---|---|---|
| A result has three states: `ok`, `nothing_found`, `unavailable`. | A wiring gap, a stale fix, a failed read or a timeout is `unavailable`, never "nothing" and never a guess. | ADR 0015; review |
| A failed read, parse or hash means unknown. | It is never read as empty, none or equal. A list that guards access fails closed when it can't be read. | review |
| Bind to the real property, never a proxy. | The value comes from the source that owns it: a live GPS read, HKO's own reading, the vendor's own reply. | review |
| When the measurer is a model, its input explains itself. | A judgement about *when* carries a time, about *who* an index, about *still open* the thing that would close it. A bad judgement is fixed in the input before the instruction. | `test_every_block_declares_its_type`; [why](conventions-rationale.md#the-instruction-is-the-first-thing-blamed-and-usually-the-wrong-one) |
| An instrument's null is evidence only after its positive is shown. | The PR says what a positive result would look like and that the system can produce one. | review; [why](conventions-rationale.md#the-never-rules--with-the-war-stories) |
| Source and tests are authoritative. | A brief's unknowns are checked against the code; a generated summary or a peer's claim is not evidence. | review |

### Tests

| Rule | A reviewer checks | Held by |
|---|---|---|
| A fix comes with a test that fails without it. | Reverting the production line turns a named test red with a real failure, not a `NameError` or an empty collection. The PR names the test. | `nora-prove-fix`; `make mutate` |
| A fake behaves like the real thing. | A fake answers the wrong call the way the real server does. A fake that answers every request the same way is decorative. | `tests/contract/`; `test_port_fakes_live_in_nora_testing` |
| A port gains a second implementation, a fake included, only with a shared contract suite. | The suite is in `tests/contract/`, its database scoped to the module; the fake lives in `nora/testing/`. | `test_port_fakes_live_in_nora_testing` |
| A test needs to exist, at the right level. | `nora-test` decided it; a test that stays green with the code reverted is removed or fixed. | `nora-test` |
| A test lives where what makes it true lives. | The directory matches `tests/README.md`; the `tests/` root takes no new files. | `test_tests_root_takes_no_new_files` |
| Tests read config from the template. | Only a deployment test reads `config.toml`. | `test_config_comes_from_the_template` |
| Fixtures are generated, never production rows. | Values come from a generator or `identity.example.toml`. A hook finding is cleared by a replacement, never acknowledged by an agent. | `scripts/pii_scan.py` hooks |
| A freed per-tag object is counted, not inferred. | The test counts live `gc` objects across two batch sizes; an absent attribute proves nothing. | `test_no_retained_per_tag_state` |
| Every zero is paired with a positive control. | A test that asserts "nothing happened" also shows the same path producing something. | review |
| A timing test leaves room on both sides. | No wall-clock bound a loaded runner can exceed; the timing test is listed in `TIMING_TESTS` (`scripts/pytest_gate.py`). | review |
| A test that fails on `master` is a bug now. | "Fails the same on master" is not a baseline: it is fixed, or filed with its cause, before shipping on top. | review |
| A new behavioural rule has a decision row and a named test. | The row is in `docs/design/decisions.md` and the test in `RULE_TESTS`. | `test_acceptance_rules`; `test_decision_index_tests_exist` |

### Evidence and acceptance

| Rule | A reviewer checks | Held by |
|---|---|---|
| Done is measured, never declared. | The PR names a gate result, a metric or a read-back for each claim; a check that wasn't run is listed as not run. | `nora-review`; the PR template |
| A gate's result is its exit code. | The rc is captured and read, never through a pipe or a backgrounded `echo`. | review; `nora-land` |
| Validation follows impact. | Focused local checks prove the changed behaviour; never a full local pre-PR suite. The hosted fast baseline and the on-demand full suite are reported separately, and a skipped run claims nothing. Automatic checks take three minutes, five at most; a timeout fails. | `test_ci_plan`; `nora-land` |
| A merge, deploy or fix is claimed only after it is read back. | The merge commit is on `origin/master`, the container runs the image, or the row exists. | `nora-land`; `nora-readback` |
| Cite only what was opened. | A file, test or function named in a PR was read in that session; a definition is not a caller. | review |
| The merge names the reviewed head. | `gh pr merge --merge --match-head-commit <reviewed sha>`; a push after review is reviewed again. | `nora-land` |

### Reviewing a change

| Rule | A reviewer checks | Held by |
|---|---|---|
| Review the change against its stated purpose. | Findings outside the brief are follow-ups, never blockers. | `docs/engineering/review.md`; `nora-review` |
| Run it, then break it. | The head is checked out and the cited checks run; the fix is reverted and the named test goes red. | `nora-review`; `nora-prove-fix` |
| Check the vendor, not the PR's account of it. | Upstream behaviour is read from the upstream source, binary or real reply. | review |
| Classify narrowly. | A status, mark or error class covers only the case it describes. | review |
| A preview writes nothing. | A dry run, plan or shadow mode has no side effects. | review |
| Don't reverse a decision the operator locked. | A change to a ruled decision is left to the operator. | `docs/design/decisions.md` |

### Commits and PRs

| Rule | A reviewer checks | Held by |
|---|---|---|
| A subject is a Conventional Commit and the release note. | `type(scope): clause`, with a type from the closed list in `CONTRIBUTING.md`. | `scripts/check_commit_subject.py` hooks; `test_commit_body_rule` |
| The body records what was run. | What was broken, what was measured, what shipped, what it does not cover, and which test fails without it. | `test_commit_body_rule`; review |
| One commit, one goal. | Two reports fixed together are two commits; `git show --stat HEAD` lists only this change's files. | review |
| A change starts in its own worktree from `origin/master`. | One committing agent per worktree; never edit, commit or merge on `master` in the primary checkout. | `nora-land` |
| A PR describes each design bundle it changes. | The «Architecture» section names the bundle, the trigger, the behaviour before and after, and the test or read-back that shows it, or `none` and why. | the PR template; `nora-design` |
| Merge with a real merge commit and an authored subject. | `merge: <the arc> (#N)`; never squash or rebase. | `test_contribution_conventions` |

### Documents and comments

| Rule | A reviewer checks | Held by |
|---|---|---|
| One fact, one owner. | The section that owns the subject is revised in place; no section is added beside it, and no other page copies it. | review |
| A guide says what holds now. | Present tense, no dates, issue or PR numbers, or "was X, now Y". History is in git, the ADRs and the rationale. | `test_root_docs_are_instructions` |
| A design bundle is architecture, not a spec. | The parts, connections, invariants, terms and why. A part names its owning module or symbol, never a line number. | `test_design_is_design`; `nora-design` |
| A changed contract updates its bundle in the same PR. | A component, connection, boundary, invariant or term that moves is in the diff under `docs/design/`. | `nora-design`; review |
| A comment says what the code cannot. | A unit, bound, constraint or business fact in at most three lines; never history, a measurement or a review story. | `test_comments_carry_no_new_log` |
| A docstring says what the thing is. | An internal function has one line or none; a model-facing tool docstring is behaviour. | review; `nora-tool` |
| Size has a budget that is met, not raised. | File at most 800 lines, function 120, `AGENTS.md` 250, a design subject file 300. A cleanup rewrites its baseline, which only shrinks (`python -m scripts.code_size --write`; likewise `scripts.log_markers` and `scripts.design_shape`). | `test_code_stays_within_size_limits`; `test_root_docs_are_instructions`; `scripts.design_shape` |
| Links resolve. | Every path a document names exists. | `test_doc_references_resolve` |

## Per stack

### Python and layering

| Rule | A reviewer checks | Held by |
|---|---|---|
| Code is grouped by area in three layers that import inward. | `nora/<area>/{domain,app,adapters}` with its `wire.py`; nothing imports outward. | `lint-imports`; `test_boundaries`; `nora-structure` |
| Collaborators come through `__init__`. | No `*_fn` hooks, `_for_test` parameters or mutable module singletons. | `test_no_module_level_mutable_singletons`; review |
| A runtime import is a declared runtime dependency. | It is in `pyproject.toml`'s runtime dependencies, added with `uv add`. | `test_runtime_imports_are_runtime_dependencies` |
| Types are clean where the gate says. | `mypy` covers the packages named in the `typecheck` target; a package's own errors are measured by scoping the override, not command-line flags. | `make gate` |
| A store method has a production caller. | No method exists only for a test. | `test_store_methods_have_production_callers` |

### Postgres and Alembic

| Rule | A reviewer checks | Held by |
|---|---|---|
| One migration head after every merge. | `uv run alembic heads` prints one line; a parent is re-checked at merge time. | `test_alembic_single_head` |
| The metadata matches the migrated database. | `schema.py` and the migrations agree, including CHECKs and index order. | `test_schema_parity` |
| `idx_messages_conv_ts` is hand-written. | Autogenerate output is never trusted for it. | review; [why](conventions-rationale.md#gate-commands--the-detail) |
| A revision id fits its column. | The id is short enough for `alembic_version.version_num`. | `test_migration_revision_fits_version_column` |
| A recurring SQL repair is measured at scale. | Generated history-size scaling and background/foreground probes under the normal lease. | `nora-land` |

### Model calls, prompts, doctrine and tool text

| Rule | A reviewer checks | Held by |
|---|---|---|
| Every model call is a declared occasion built by context assembly. | No prompt is assembled by hand outside it. | `test_every_model_call_is_a_declared_occasion` |
| One composer per block. | A prompt-input bug is fixed by reusing the shared renderer, not by a parallel one. | `test_one_block_injector`; `test_one_transcript_renderer` |
| A prompt block names its author by id. | `of=`, `written_by=` and `from=` carry ids; names stay on messages. | review |
| A tool's name, docstring and result follow ADR 0015. | A new tool is `<domain>_<verb>`; a non-ok result carries a `note`; routing is measured before and after. | `test_tool_names`; `test_every_status_carries_a_note`; `nora-tool` |
| A wording change is confirmed by eval, after its cause is found offline. | The before/after arms are reported with their spend. | `nora-eval` |
| Doctrine is classified and versioned. | Each rule has its bucket (law, practice, character, seed), and the version moves with the text. | `test_doctrine_is_classified`; `test_doctrine_version_parity` |

### Observability

| Rule | A reviewer checks | Held by |
|---|---|---|
| Read `docs/operations/observability.md` before adding an instrument. One root span per entry point; stages are its children. | Spawned work does not inherit the caller's span; it attaches its intended context explicitly. | `docs/operations/observability.md`; review |
| Langfuse gets traces with a generation in them. | A root declares `nora.trace_kind`; model calls use Strands' spans or `start_raw_model_span`, never hand-written `gen_ai.*`. | `test_raw_model_calls_use_strands_telemetry` |
| A declared metric has a call site and every increment a declaration. | Labels derive from the code (`get_args` off a `Literal`). | `test_every_recorded_metric_is_declared` |
| An instrument measures the cost as well as the result. | Every instrument has three states. | review; [why](conventions-rationale.md#instruments-and-spans--the-war-stories) |

### Evals

| Rule | A reviewer checks | Held by |
|---|---|---|
| An eval timeout is ERROR, never PASS. | No missing or timed-out result is counted as a pass. | `nora-eval` |
| Evals replay recorded input through the real session. | No faked input; the subject is the deployed Nora. | `test_the_eval_subject_is_the_deployed_nora` |
| A result records its trajectory. | Reasoning, per-call input and output, and judge reasons are kept so a score can be explained. | `nora-eval` |
| Every case is anchored or parked. | A case is a real recorded moment, parked, or visibly unanchored. | `test_every_case_is_anchored_or_parked` |
| Paid runs need the operator's authorization. | The PR or brief names the authorization and reports the spend. | `nora-eval` |

### Site and portal

| Rule | A reviewer checks | Held by |
|---|---|---|
| Both locales make the same claims. | A correction in one locale is made in the other; the same keys are not enough. | `test_portal_locales_make_the_same_claims` |
| How she looks follows `docs/design/appearance/README.md`. | The change is checked against its section before it is built. | review |
| A change under `app/` runs its own tests and build. | `npm test` and `npm run build` in `app/`. | CI portal jobs |

### Deploy, config and secrets

| Rule | A reviewer checks | Held by |
|---|---|---|
| No config change reaches a process unvalidated. | Each file's shape is in its `*.example.toml`; policy stays deny-by-default. | `nora-deploy` |
| Base images are pinned by digest. | Every `FROM` and compose image carries `@sha256:`. | `test_dockerfile_bases_are_pinned`; `test_postgres_image_is_pinned` |
| The host checkout is live state. | It is never deleted or reset; deploys go through the release pipeline. | `nora-deploy` |
| signal-cli's account and device are not changed. | It is never re-registered, re-pointed or unlinked without an explicit decision. | review |
| Identity and policy files never reach GitHub. | `identity.toml`, `policy.toml`, `secret.toml` and `.env` stay gitignored; only their `*.example.toml` shapes are committed. | `scripts/pii_scan.py` hooks |
