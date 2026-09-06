# Conventions & operational detail

The long-form behind `CLAUDE.md`'s rules. `CLAUDE.md` carries the terse rules an
agent must not violate; this file carries the *reasoning*, the war-stories, and
the specific values. Read it when a rule needs its "why" — every mistake recorded
here cost an afternoon, and the terse rule is only safe because the story is
preserved. Nothing here is optional context; it is the load-bearing detail moved
out of `CLAUDE.md` so that file can stay scannable.

## Gate commands — the detail

The schema gate is `tests/infra/test_schema_parity.py`, not the `alembic check`
CLI. `alembic check` guards column presence, column type and server defaults;
it is provably blind to the CHECK constraint and to the `ts DESC` index
ordering, and `idx_messages_conv_ts` is excluded from autogenerate because the
expression index otherwise reports drift on every run. Both run in
`uv run pytest -q`. See DEC-11 in the decision index for the full story,
including a 2026-08-05 correction to its original server-default claim.

**Dropping the CLI once meant dropping the half of the job it was good at**
(2026-08-16). The two tools were complementary and only one survived: the
catalogue reads above cover the CHECK and the DESC ordering, but for a while
nothing compared `schema.py`'s `MetaData` against the migrated database at
column level. Every other assertion in that file compares the database against
something else derived from the migrations — the existence check never looks at
a column, and the dump/baseline test compares a `pg_dump` against a file
regenerated from that same dump — so `schema.py` was in no loop at all, and a
metadata type that disagreed with its migration was invisible. That matters
because `METADATA` is what the ORM issues queries through: a type it is wrong
about is a query built on a false premise. `test_the_metadata_matches_the_
migrated_database` restores that half in-process, via the same autogenerate
comparison the CLI uses, filtered through the same DESC-index exclusions so the
phantom drift that made the CLI unusable here does not come back with it.

`uv run lint-imports` uses a local cache (`.import_linter_cache/`, gitignored).
If it reports a phantom contract failure that doesn't reproduce after a clean
checkout, delete that directory before spending time on it.

There is no longer a module-size gate. Spec §10's 300-line hard cap was
deleted 2026-08-06 — see the Conventions section below for why, and prefer
counting a module's responsibilities to counting its lines.

## The "Never" rules — with the war-stories

- **Never `pip install`.** Dependencies are added with `uv add`, never raw pip
  — this is one of the five operational constraints below.
- **Never write a test whose fake answers every request the same way.** Three
  separate no-ops shipped green this way and each was invisible for months:
  signal-cli ran without `--receive-mode` so no websocket ever opened; the SSE
  parser required a `method` key the HTTP transport does not send, so every
  real message was dropped; and `HonchoClient.ensure_session` POSTed to a
  **list** endpoint, so `feed()` returned `False` before making a single HTTP
  call and Nora's semantic memory received nothing for the entire life of the
  deployment. That last one had a passing test — it asserted the wrong URL,
  against a fake server that returned `{"id": ...}` for *any* path, so it
  asserted the implementation back to itself. **Make the fake behave like the
  real server**, including the shapes it returns for the wrong call. If
  reverting the code under test does not turn the test red, the test is
  decorative.
- **Never point signal-cli at a different account, re-register it, or unlink
  its device** without an explicit decision — that store holds the device
  registration, and losing it means re-linking. Nora *does* now own the daemon
  (see constraint 3); routine reads and RPC calls are ordinary, destructive
  account operations are not.
- **Never reference the archived legacy path** (`~/.hermes/profiles/nora/`) —
  it no longer exists; see constraint 4.
- **Never assert a per-tag object was cleaned up by checking that an
  attribute was deleted.** `Handler._prefills`/`_guards` (the leak the
  pipelines phase exists to remove) existed precisely because tests asserted
  on retained *constructed* objects, not counted live ones — an
  attribute-absence check would have passed anyway.
  `tests/architecture/test_no_retained_per_tag_state.py` asserts on
  `gc`-tracked live `ReplyGuard` counts across two different batch sizes
  instead, so the same leak shape can't reappear under a new attribute name
  and still pass.
- **Never read an instrument's output as evidence without first checking it
  can measure the thing.** Absence is not evidence when the instrument was
  never wired to produce presence. On 2026-08-06 a grep of `data/nora.log` for
  `lookup_person` returned nothing and was read as "the tool is never called" —
  but no tool logged its own name on any success path, so that grep returned
  nothing whether it ran every turn or never. Two confident architectural
  conclusions followed, one of which nearly retracted a design document, and
  the correct answer turned out to be the opposite (6/6). Before concluding
  from a null result, state what a POSITIVE result would have looked like and
  confirm the system can produce it. This is the same discipline as the
  fake-that-answers-everything rule above, applied to observability instead of
  tests.
- **Never claim a merge, deploy or fix without reading back the state you
  claim.** Also 2026-08-06: `git merge` reported "Already up to date" because
  the session was *on* the branch it was merging; that was reported as merged,
  and master stayed fourteen commits behind while the container ran branch
  code. `git log --oneline -1`, `docker exec … python -c "import config"`, the
  metric endpoint — one command, every time. The check is cheaper than the
  correction.
- **Never edit, commit, or merge on `master` in this checkout.** 2026-08-20: a
  session ran five worktree agents correctly, then merged all five, committed two
  specs, and pushed — all directly on `master`, in the checkout the running
  container bind-mounts. `nora/doctrine` is mounted read-only from this working
  tree and `[doctrine] reload = "mtime"`, so the doctrine changes went live on
  the *next message* with no build, no deploy step, and no gate between the merge
  and the group reading it. Nothing broke, and nobody decided to deploy — the
  merge deployed itself. The same session also swept two untracked spec files and
  an unreviewed `AGENTS.md` into a merge commit via a reflexive `git add -A`
  while resolving a conflict.

  Both failures share one cause: on `master` there is no step between "I changed
  a file" and "it is live and public". A worktree restores that step. Every
  change starts on a branch — a one-line doc fix included — and reaches `master`
  by merge, so the merge is the moment somebody decides, rather than a side
  effect of editing.
- **Never run two agents against this checkout at once.** Three times on
  2026-08-06 a subagent's `git add -A` swept another session's staged work into
  its commit, and once an agent amended a commit it did not author. Nothing was
  lost, but commit history stopped describing who changed what. Sequence them,
  or give each a worktree.
- **Never run `alembic revision --autogenerate` and trust the result for
  `idx_messages_conv_ts`.** It is excluded from autogenerate on purpose; a
  revision that touches it must be hand-written.

## Instruments and spans — the war-stories

`design/dreamer-agent-loop/02-instrument-blindness.md` is the catalogue: eight
layers where an instrument could not observe the thing it existed to observe,
plus the silent-match family one level down — the instruments we use to FIND
those layers returning a positive-looking answer without ever engaging.
`design/dreamer-agent-loop/09-measurement.md` is where the bundle gives that
discipline a home for its own change. Neither is repeated here. This section
carries the eight rules and the measurement behind each.

### One root span per entry point

`nora/ingest/pipeline.py` is the pattern. `run()` opens `span("ingest")` around
`await self._run(...)` (:146), and the six stage spans — `ingest.scope` (:163),
`ingest.identity` (:177), `ingest.media` (:186), `ingest.audit` (:193),
`ingest.memory_feed` (:200), `ingest.gate` (:206) — nest inside it. Splitting
the body out of `run` is what makes the root wrap EVERY exit including the
early returns, which is why `_outcome()` (:60) can stamp `nora.outcome` on a
refusal instead of leaving a trace that simply stops.

`nora/engagement/pipeline.py` does the opposite. `engagement.admission` (:315),
`engagement.debrief` (:409) and `engagement.invoke` (:617) are three siblings
with nothing above them, so one tagged message produced three separate traces
plus the one from ingest. The operator's first reading of the new skeleton was
«10+ separate trace but its empty», and both halves of that sentence are a
different rule below.

A rootless span is not a span with a missing parent. It IS a trace.

### Langfuse gets the traces with a generation in them (R-76)

One root per entry point says wrap everything. It does not say every entry point
belongs in the collector, and for two weeks the codebase read it as if it did.

`nora/ingest/pipeline.py` opens six spans and there is no model call under any
of them — six deterministic stages, a row, a feed. It is also the
highest-volume entry point in the system, so the operator's Langfuse was **97 of
its last 100 traces from ingest** (measured 2026-08-28, across 12 rooms Nora
does not serve). The obvious reading is a volume problem with a sampling answer,
and DEC-142's reversal condition said exactly that. It was the wrong axis: even
the three traces that were not refusals carried no generation, so no *fraction*
of them belongs in an LLM-observability tool. Langfuse holds traces of model
calls; «how many messages ended which way» is a Prometheus question and was
always going to be answered badly by a trace.

**The rule is about ROOTS, and that is the part worth getting right.**
`engagement.admission` has no model call in it either — and it must keep being
exported, because as a CHILD of a trace that has one it is how a turn that ended
before the model ends visibly at a named stage. A per-span rule would leave
every real trace with holes exactly where an early exit needs to be legible. So
the sampler (`nora.obs.tracing.build_sampler`) is composed under `ParentBased`:
the delegate decides roots, children inherit and never re-decide. That is also
what keeps a QUEUED tag's turn — re-attached to a captured context whose root
span has already ended — in the trace that admitted it.

**It is «CAN contain a generation», declared on the entry point.** A sampler is
called at root start and cannot know whether a model call will happen; and a tag
refused at admission never reaches one, yet its trace is precisely what DEC-141
exists to preserve («it SAYS it was rejected» rather than an unexplained stub).

**Fail-open.** An undeclared root, and an unrecognised kind, are exported and
logged. Getting it wrong towards export costs noise, which is visible and
complains about itself; getting it wrong towards silence costs a missing trace
for a model call — an invisible gap in exactly what the rule protects.

**Nothing is lost, only moved.** `nora_ingest_outcomes_total{platform,
conversation_type, outcome}` carries what the root carried, with `IngestOutcome`
as its derived label vocabulary. Everything else was already counted and no
duplicate was added: `person_known` is `nora_unmapped_senders_total` against
`nora_messages_total`, attachments are `nora_media_custody_total`, the two
refusals have their own counters, and `engage_reason`'s one load-bearing value —
`policy_error`, the gate degrading closed under R-5 — is
`nora_errors_total{stage="engage_gate"}`.

**The trace is unexported, not deleted**, and that is deliberate twice over. It
is what makes «the collector gets nothing» a measurement of the RULE rather than
of an absence — a pipeline that opened no spans would satisfy that assertion for
the wrong reason, and the test that reads the dropped trace with the sampler
removed is the positive control. And it makes the reversal one attribute rather
than a re-instrumentation.

**The test provider runs production's sampler.** `tests/conftest.py`'s
`exported_spans` is built with `build_sampler()`. A test provider that sampled
everything would export spans the deployment drops, so every «what reaches the
collector» assertion would be reading a provider no deployment has — the
silent-match failure, inside the instrument that exists to find silent matches.

**This holds unless** a plumbing entry point turns up whose trace an operator
genuinely needs, which is one declaration on that root. The answer is never to
widen it back to «export everything and sample».

### Spawned work does not inherit the caller's span

`asyncio.create_task` **does** copy the current context — the repo already says
so about `tag_id` at `nora/agent/tools.py:929`, and a three-case probe
reproduces it. What it copies is the context of the task that CALLS
`create_task`, at the moment it calls it, and that is not always the logical
parent:

| what was spawned | the context it ran under |
|---|---|
| a task created inline, inside the span | the caller's — nesting works |
| a factory built here, INVOKED by another task | **that other task's** |
| a task created before the context was set | none |

The queued-tag path is row two, exactly. `EngagementPipeline.run` stores a
zero-argument factory (`nora/engagement/pipeline.py:334`);
`ConversationSlots.release_and_dispatch` calls `self._spawn(factory())`
(`nora/engagement/slots.py:74-84`) from the PREVIOUS turn's task, in its
`finally`; and `BackgroundTasks.spawn` is `asyncio.create_task`
(`nora/app/runtime.py:64`). So a QUEUED tag's `invoke` and `debrief` run under
the releasing turn's context, never under the dispatch call where its own
`admission` ran — and the two halves of one tag land in two traces even once
the root above exists.

If work is spawned, capture the context you mean and attach it. Inheriting one
by accident is not the same as propagating one on purpose.

### A span with only a name is worse than no span

Ten spans on master — seven in ingest, three in engagement — of which exactly
ONE, the ingest root, passes attributes of its own (`nora.platform`,
`nora.conversation_id`); `_outcome` adds a second on the early returns. Every
other span carries a name and nothing else. That is what shipped on 2026-08-27
(`d673a7a`), and the operator's report was that the traces were empty.

Prefill sizes and which blocks were built, store-read counts on the critical
path, the model's stop reason, the delivery outcome — none of it is on a span,
and all of it is what the disclosure and budget arguments in this repo reason
about. The span exists; the re-derivation it is credited with does not.

An absence prompts the question. A page of named-but-empty traces answers it
wrongly, is harder to work with than nothing, and reads as progress.

### Instruments measure the result; something must measure the cost

The sharpest instance this repo has recorded.
`SqlTurnVerdictStore.latest_ungrounded` shipped with `.is_(False)`, which
renders `grounded IS false`; `idx_turn_verdict_ungrounded` is declared `WHERE
(grounded = false)`, and Postgres's predicate prover cannot show a
`BooleanTest` implies an `OpExpr`. The partial index was unusable, so the one
read the debrief stage makes on every fold, inside the held lock, sequentially
scanned the whole verdict table.

**Measured at 60k rows: 3.063 ms over 525 buffers against 0.109 ms over 12 on
the index — with an IDENTICAL answer.** Right row, right order, both
implementations agreeing, every test green, two reviews clean. The divergence
lived only in the plan, and nothing in this repo had ever looked at one. The
fix is `== sa.false()` (`nora/infra/sql/turn_verdicts.py:100`) and the check is
`tests/contract/test_turn_verdict_store_contract.py::test_the_trigger_read_
actually_uses_its_partial_index`.

**And the first version of that test compiled its OWN statement** with the
correct spelling and EXPLAINed that — so reverting the store's predicate to the
exact defect left it green, measured. The SQL is now captured off the engine as
the shipped method issues it. A test that spells the predicate pins today's
fix; a test that captures it pins the property.

A claim about cost — an index is used, a call is bounded, a read is scoped — is
never checked by reading the answer, because the answer is what stays correct.

### R-27's three states, pointed at the instrument

R-27 governs what Nora says. It governs a counter identically, and three
instances landed in one night, each inside code written to honour it:

- **A wiring gap counted as a model fault.** The revisitor's `_asked` map was a
  bare attribute; `dream_loop`'s running lock is per-`(adaptor, key)` and the
  live `policy.toml` has two workspaces, so a sibling tick wiped the map
  mid-flight and a grounded verdict was dropped as `reason="unknown_note"` — a
  model answering about a note it was never shown. It was a lock scope.
  (`b661f83` F3, branch `worktree-note-revisitor`.)
- **`unavailable` counted as `absent` as well, on the same candidate.** A
  failed record read left the evidence tuple empty, and the branch below read
  an empty tuple as «the room has said nothing since the note» — so one
  candidate incremented two states and the tick reported 2 for 1. Fixed by
  carrying `evidence_state` as ONE value; the module's own comment records that
  the defect was «committed inside its own instrument»
  (`nora/language/note_revisitor.py`, `_Candidate.evidence_state`, `92eccc7`).
- **A tick that wrote nothing reporting `ok`.** `dream_screen` matches any
  hyphenated UUID (R-18/R-28) and note ids are `uuid4`, so every curator item
  was rejected: the tick fired, spent a model call, `accept` received nothing,
  `commit` wrote nothing, and the run reported `ok` because the ENVELOPE was
  non-empty. Silent, and every test in the file missed it because the double's
  ids are `note-1`. (`e5c5bea`.) The mirror image landed the same night — an
  over-cap merged body raised out of `commit` and reported
  `unavailable`/`written=0` over rows that exist in Postgres (`6d23a41` P2).

A wiring gap is `unavailable`. Never `nothing_found`, never `ok`, and never two
states for one event — the third is the one that is hard to see, because both
series look plausible on their own.

### A declared metric with no call site is a lie

An UNdeclared metric is a `KeyError` at the first increment. A declared one
with no increment renders its `# HELP`/`# TYPE` header and nothing else, which
on a dashboard is a series that is genuinely always zero: the reading «this
never happens», not «this is not wired».

Two on master today — `nora_fact_candidates_total`
(`nora/obs/metric_specs.py:963`) and `nora_fact_entity_unresolved_total`
(:987). *(Both went with the knowledge tier when it was deprecated and deleted on
2026-09-07, issue #115 — the instance stands as the war-story, and the rule it
earned is unchanged.)* The second is named twenty lines away in the note tier's own block as
«the exact defect this block was written to close» (:1236) — the argument for
shipping three note counters instead of the ten that tier's spec names — while
it sits unwired itself. `nora_store_busy_total` (:121) scans the same way and
is NOT an instance: its call site was deliberately deleted with the SQLite WAL
apparatus (DEC-51), and the name is retained because names are append-only.

Issue #56 is the gate, and it is the ONE family from 2026-08-27 with a cheap,
total, automatable check available: an AST walk over the declared names against
`metrics.inc(`. Until it exists the scan is one loop over
`grep -oE '"nora_[a-z0-9_]+"' nora/obs/metric_specs.py`.

### Label vocabularies are derived, never hand-copied

`EVIDENCE_STATES` was a hand-written tuple nothing referenced, while
`_candidate` assigned bare string literals. **Emitting a fifth state left every
test green** — so the parity test, written to catch «a value in the code the
spec does not name», could only ever catch DELETION from the help text. The
exact drift it existed to prevent went through it unseen: `self_only` was added
to the code and never to the spec, so the endpoint emitted four values while
the documentation described three, for two review rounds. The fix for a
silent-failure finding had the silent failure's shape.

It is now `EvidenceState = Literal[...]` with `EVIDENCE_STATES =
get_args(EvidenceState)`, enforced TWICE — statically, and in
`_Candidate.__post_init__` — because `nora/language/` is outside `make gate`'s
mypy scope (issue #61) and **a static-only guard in an unchecked package is a
claim rather than a check** (`443bbca` F3).

Where mypy does reach, the derivation alone is enough: `nora/domain/facts.py:
52-56` builds five frozensets with `get_args` off five `Literal`s *(that file was
deleted with the knowledge tier on 2026-09-07, issue #115; the pattern is still
the rule, and `nora/language/note_curator.py` still carries it)*. Where it
does not, the vocabulary is a frozenset the code increments AGAINST and a value
outside it raises — a label typo forks a NEW series rather than erroring, and
the old one silently goes flat (`nora/language/note_curator.py`,
`_VOCABULARIES`, `6d23a41`).

A derivation check needs its own positive control, and the walk that reads the
emitted labels out of the source asserts `emitted, "the walk found no reasons
at all — it is not measuring"` before comparing sets. Its first version matched
`reason="..."` with a regex and flagged a string inside a COMMENT — a matcher
that matches too much, reproduced inside the test written to prevent drift.

### Pair every zero-assertion with a positive control

`assert metrics.get(x) == 0` is true of a stage that raised. `Metrics.get` also
sums matching series and answers `0.0` when there are none, so on its own it
cannot separate «published zero» from «never published» either.

`tests/unit/test_debrief.py::test_the_first_fold_of_a_conversation_is_not_a_
rederivation` asserted `nora_standing_rederivations_total == 0` and
`nora_debrief_prior_total{prior="absent"} == 1`. Remove `if prior is None:
return None` from `_should_rebuild` and the stage raises `AttributeError` on
`NoneType.fold_count`, writes nothing, lands `outcome="error"` — and BOTH
assertions still hold. R-72 names that test among the ones enforcing it, and it
measured nothing. It was found by a reviewer mutating a clause the author had
not.

At least five tests in that one file had the shape. The fix in each is two
lines — `outcome="written" == 1` and a read-back of the row that was actually
written (:722-724, :767-772). Presence and magnitude are two different
questions, and a test whose assertions are all true of a crash is not a test.

The general form is the bundle's own: **a test that has never been seen red is
a claim, not a measurement.** After a test goes green, break the production
line it names and watch it go red — per CLAUSE, not per change. One author
truthfully reported falsifying three properties of one feature, and the fourth
clause was the one that shipped the defect.

## The five operational constraints (spec §2, verbatim)

These came from `design/08-stack-refactor-brief.md` and are preserved here
because the spec that retired that document said they were the most
operationally valuable text in it. Losing any of these wastes an afternoon.

1. Container builds require `docker build --network=host` — BuildKit's default
   bridge cannot resolve `deb.debian.org` on this host (systemd-resolved stub
   `127.0.0.53`).
2. Honcho health uses **`GET /health`**, not `GET /v3/workspaces` (POST-only →
   405 → forever `health: starting`).
3. **signal-cli is Nora's transport and runs in her compose project.**
   *Corrected 2026-08-05.* This constraint used to read "neutral and shared —
   never touched", which was true when the legacy Hermes gateway used it. It is
   no longer: signal-cli *binds* `:18080` and Nora was its only *client* (`ss -tlnp` shows
   java listening, `python3 -m nora` merely connecting), and
   hermes-agent runs profiles `attest`/`bento`/`default` with no `nora` profile.
   The daemon now lives in `docker-compose.yml` alongside Nora, built from
   `deploy/signal-cli/` (upstream ships **no** official image, so `fetch.sh`
   downloads and GPG-verifies the release tarball against AsamK's key
   `FA10826A74907F9EC6BBB7FC2BA2CD21B5B09570`).

   **`--receive-mode=on-start` is load-bearing — never drop it.** Without that
   flag signal-cli starts its HTTP server and answers every RPC perfectly while
   **never opening its websocket to Signal**, so not a single message is
   delivered. The host daemon ran that way for 61 days: `listGroups`,
   `listContacts` and `sendSyncRequest` all worked, `/health` was green, and
   `nora_messages_total` sat at `0.0` the entire time. Under R-5 Nora is silent
   when broken, so it was indistinguishable from a quiet group.

   It needs **Java 25** (class file version 69). Temurin 21 fails at class load.

   If group state goes stale on a linked device, `sendSyncRequest` over
   `/api/v1/rpc` asks the primary to re-send it — that is what made the
   `Ridgeline` group appear after `listGroups` had returned `[]`.
4. The legacy Hermes gateway is archived at
   `/home/norty/backups/nora-legacy-profile-20260804/`. Do not reference the
   old `~/.hermes/profiles/nora/` path.
5. Dependencies are added with **`uv add`**, never raw pip.

## Conventions (spec §10 — the short version; see the spec for the rest)

- **One stage, one module, one public entry point.** This is about
  RESPONSIBILITIES, not lines. A 400-line module doing one thing is fine; a
  150-line module doing five is not.

  There used to be a mechanical 300-line cap enforced in CI (spec §10),
  calibrated on `handler.py` "going wrong at 229 lines with 8
  responsibilities". It was deleted 2026-08-06. The calibration confused the
  symptom for the cause: `handler.py` was bad because of the eight
  responsibilities, and a line count cannot see those. In practice the gate
  made things worse — it was satisfied twice by DELETING explanatory comments
  from `nora/agent/guards.py` rather than by changing any structure, which is
  the opposite of what it existed to encourage, and it blocked
  `nora/domain/ports.py` (pure Protocol declarations, nothing to split) from
  gaining a method. Its own docstring conceded that file was "legitimately
  dense with no behaviour to split out" and enforced the cap on it regardless.

  If a module feels too big, count what it is responsible for.
- Test-seam policy: production classes take collaborators through `__init__`
  only — no `*_fn` hooks, no `_for_test` params, no module-level mutable
  singletons.
- A port never gains a second implementation — including a test fake —
  without a shared contract suite both pass (`tests/contract/`).
- A new behavioural rule gets an entry in `design/09-decision-index.md` and a
  named test registered in `tests/test_acceptance_rules.py::RULE_TESTS`.
- Where a test LIVES, what config it LOADS and where its FAKES live are the
  three rules of the next section (issue #113), each with its own gate under
  `tests/architecture/`.

## Test structure — where a test lives, what it loads, where its fakes live

Filed as issue #113 (2026-09-07) from the operator's own words: *"our test
cases and testing methodologies have no convention, no practice, no policy,
and no standard."* Measured, not estimated: 324 test files across 8
locations; **zero** documents defining what any test directory meant; the
largest bucket the unnamed `tests/` root (144 files, 45%); **seven** distinct
ways a test acquired a `Config`; 11 shared fakes in `nora/testing/` against
**86** test files defining their own fakes inline.

The cause was precise. The three test rules this file already carried — a fake
must behave like the real server, collaborators come through `__init__`, a
port's second implementation needs a contract suite — all govern whether a test
is HONEST. None governed where a test lives or where it gets its inputs. The
quality bar was high and the skeleton was absent. That gap was already costing
correctness: the port-fake rule was being broken silently in dozens of files,
because a fake defined inside a test module is invisible to every contract
suite by construction, and nothing compared it to production.

Three rules, each shipped with its gate — a rule with no test is a claim.

### R-A. A test directory names what makes the test TRUE, not what it is about

| directory | what makes a test here true |
|---|---|
| `tests/unit/` | pure logic — no I/O, no container, no network |
| `tests/contract/` | ONE suite, N implementations of a port (fake AND production) |
| `tests/integration/` | the wired pipeline, real Postgres, real HTTP servers |
| `tests/infra/` | a real adapter against its real backend (Postgres, rustfs, docker) |
| `tests/architecture/` | a property of the SOURCE, read off the AST, not the runtime |
| `tests/web/` | the aiohttp control-plane surface |
| `tests/domain/` | legacy subject-named bucket — `unit/` for new tests |
| `tests/` root | **legacy** — holds no NEW test files; the 140-odd there move under R-A |

`domain/`, `infra/` and `web/` are *subject*-named, a second taxonomy cutting
across the first, and that is why 45% of files landed in the root: with two
axes and no rule, the path of least resistance was neither. The taxonomy is
written here so the next test has a home; **the move itself is deferred** (the
largest diff and the least urgent) and tracked on #113. The gate that comes
with the move — *a test collecting a container fixture outside `integration/`
or `infra/` fails* — lands with it, not before, because today `contract/`
legitimately collects `pg_url` for its SQL implementations.

### R-B. A test gets its config from the TEMPLATE; only a test whose subject IS the deployment reads the real file

Two fixtures in `tests/conftest.py`, both built on
`tests.builders.copy_example_config` — the one place in the suite that knows the
template's name:

- `example_config_path` — a copy of `config.example.toml` in `tmp_path`, for a
  test that edits the TOML before loading it.
- `example_config` — that copy, loaded through the production loader with the
  LLM-key placeholder set (the load is fail-closed on the key, as startup is).

Seven acquisition patterns collapsed to two. The war-story that named the rule:
four files loaded the eval harness's config through `load_eval_config((real,
example))`, which prefers the DEPLOYMENT's file, so unit tests of `_build_sink`
asserted the operator's `config.toml` — and went red the day the operator
enabled the Langfuse export. A false dependency, recorded in
`tests/test_evals_gate.py`'s own docstring, which is now the fixture's.

The three files that DO read the real `config.toml` cannot take the fixture,
and are pinned with the reason: `tests/builders.py` (the copier),
`tests/test_env_template.py` (the F11 drift gate — pointed at the template it
would compare the template to itself, green forever, measuring nothing) and
`tests/test_turn_budget.py` (R-24 asserts the deployment's budgets against
BOTH files because a fix applied to one is half a fix).

**Gate:** `tests/architecture/test_config_comes_from_the_template.py`. Reads
the AST, not a grep: `<repo root> / "config.toml"`, `Path("config.toml")`, an
import of `load_eval_config`, or a module-level constant naming either file,
outside the pinned three, fails. A message string mentioning the file does
not. Every pinned file must still show a read — the positive control and the
stale-pin check in one.

**Side benefit:** the "should `config.toml` be tracked in git?" question that
started the discussion dissolves — the coupling to the deployment file is now
in one place with a stated reason, instead of four.

### R-C. A fake standing in for a port lives in `nora/testing/` and passes that port's contract suite

Inline classes are for local scaffolding only. «Standing in for a port» is
read off the source: a class in `tests/` that defines EVERY public method of a
`Protocol` in `nora/domain/ports.py`, each with EXACTLY the port's parameter
names (or a `*args`/`**kwargs` wildcard). Exact, not a superset — a
`describe(data, media_type, prompt, model, provider)` double is a
`VisionClient`, not a `MediaInterpreter`, and a one-method port must not claim
every class with a same-named method. A noisy gate is a disabled gate.

Three structural exemptions, none by name: a class inside `tests/contract/`
(under the suite by construction); a double whose port methods only FAIL (a
bare `raise`, or a `sleep` stall then a raise — it implements nothing, so it
cannot drift); and a subclass of a `nora.testing` fake (the contract-bound
fake with one local twist).

**Gate:** `tests/architecture/test_port_fakes_live_in_nora_testing.py`.
Everything else is pinned in `_ALLOWED` with one of three reasons —
`NO_SHARED_FAKE`, `DRIVES_THE_LOOP`, `FAULT_SWITCH_OVER_SHARED` — and the pin
is checked BOTH ways: an unpinned stand-in fails, and a pin whose class no
longer stands in fails too. The list is an inventory to burn down, never a
place to add to. The positive control runs the matcher on `nora/testing/fakes.py`
and must see the shared fakes it points at.

**What the first landing found — the issue predicted "red tests that were
green", and it was right:**

- `tests/fakes.py::FakeHoncho` — 24 users, never under the memory-store
  contract suite — had become a byte-for-byte behavioural copy of
  `nora.testing.FakeMemoryStore`. Deleted; every user now imports the
  contract-bound one.
- `MediaObjectStore` had a SQL store and NO shared fake, so two files wrote
  their own with DIFFERENT search semantics (one substring-matched
  description/prompt/title, one returned every row). Writing the shared
  `FakeMediaObjectStore` and its contract suite against
  `SqlMediaObjectStore` turned one test red **against production**: the SQL
  `search` combined words with `or_` while its own docstring said it ANDed
  them — "birthday card miles" returned every birthday card. Fixed in the
  store. No offline test could have found it, because every offline test ran
  against a fake that invented its own semantics.
- Three files each carried a one-method `ReverseGeocoder` fake; now
  `FakeReverseGeocoder`, held to the port's shape in the geocoder suite beside
  the Google and HK-gov backends.
- The blob, roster and description-store copies in the tool tests were
  replaced by the shared fakes — one of which had to learn to record the
  `model` it was handed, because a test needed it and the inline copy had
  been written instead of teaching the shared one.

**Not done, and pinned rather than hidden:** 24 inline stand-ins remain in
`_ALLOWED`, most for ports with no shared fake yet (the room-language readers,
`SearchProvider`/`GifSource`/`SocialProvider`, `TrackSource`/`MusicToolBridge`,
`DreamAdaptor`/`DreamSink`) and seven that steer `dream_loop`'s supervision
rather than model a port. Each is a candidate; promoting one means a
`nora/testing/` fake plus a contract suite against the production
implementation, which is the work the rule exists to force.

**Environment finding, recorded because it hid the suite from the operator's
own laptop:** SQLAlchemy's async engine needs `greenlet`, and SQLAlchemy's own
dependency marker skips macOS arm64, so on an Apple Silicon machine every
SQL-touching test errored with "the greenlet library is required". The
container is Linux and never saw it. `greenlet` is now declared directly.
