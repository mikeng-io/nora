# AGENTS.md

**The entry point for any agent working in this repo** — Claude Code, Codex,
Cursor, or anything else that reads an [`AGENTS.md`](https://agents.md).
`CLAUDE.md` points here so the vendor-neutral rules have one copy.

**Read [ORCHESTRATION.md](ORCHESTRATION.md) before assigning engineering work.**
It owns capability tiers, adaptive worker selection and artifact boundaries.

This is the terse rule set. **`docs/maintainers/conventions-rationale.md`** preserves the reasoning,
war-stories and specific values; read it when a rule needs its "why".
**`CONTRIBUTING.md`** carries branch, commit, PR and issue conventions.
**`.agents/skills/`** carries the procedures for recurring tasks.
**When a task matches a skill in the table under [Skills](#skills-the-procedures),
load it before the first edit**: the rules say what must hold; the skill gives
the command order and the check after each step.

## Content checks: operator reversal, 2026-10-25

`[content_checks] enabled = false` disables the phone/UUID/coordinate content
filters, third-party place/person-name screens and location audience
subset/unknown-person checks. Keep their implementations and enabled-mode tests;
`true` plus a restart restores enforcement. Credentials and doctrine LeakGuard
always remain enabled. Admission, unit read scope, location consent/opt-in,
availability, freshness, budgets and network security are unchanged. Older
always-on egress/disclosure wording below describes enabled mode; this ruling
controls the default. See [the decision](docs/adr/0030-content-filters-are-disabled-by-default-and-reversible.md).

## The spine: comprehend → measure → serialize

Every path, including a new tool, stage or **eval**, preserves this loop:
UACP's CMS applied to Nora. It is a **constraint, not a procedure**.

- **Comprehend** — turn raw input into a *computable model*, and interpret it
  **once**. A Signal message becomes a `NormalizedMessage` → `TagContext` (tagger
  resolved, transcript, peer card, the prefill blocks); everything downstream
  computes on that fixed model and never re-parses the raw text. Interpreting
  twice is how two copies drift.
- **Measure** — reduce the model to a **decidable signal**: grounded, and
  **fail-closed**. This repo's signature is the R-27 three-state tool result —
  `ok` / `nothing_found` / `unavailable` — where an ERROR is never a PASS: a
  wiring gap is `unavailable` («I couldn't check»), never `nothing_found`
  («there's nothing»), and a stale/absent fix is `unavailable`, never a guessed
  point. The guards are the *negative* measurement — R-5 (silence beats a wrong
  answer), R-18/R-28 (no identifier or coordinate egresses), the disclosure
  subset (R-31 fails closed on any unknown in the room). **Bind to the real
  property** — a `where_is` fix is a live GPS read, a `weather` reading is HKO's
  own — never a proxy, and never assert what you cannot ground.

  **When the measurer is a MODEL, its input has to explain itself.** A block
  handed to a model is not evidence because it is present; it is evidence when
  the model can tell what it is, how old it is, and which way it reads. Every
  question you ask the model, ask of the block first: if the judgement turns on
  *when*, the block carries a time; on *who*, it carries an index; on *whether
  this is still open*, it carries the thing that would close it. A judgement
  whose input cannot ground it does not fail loudly — the model answers anyway,
  and it answers wrong at a rate nothing reports.

  **When a model judges badly, read its input before rewriting its instruction.**
  Measurements and incidents: `docs/maintainers/conventions-rationale.md`.
- **Serialize** — fix the result into **canonical, typed state with provenance**.
  Her reply is the one authored surface (strict-silence: only a `send_reply` tool
  call delivers; the never-silent fallback is delivered but explicitly kept out
  of her authored `sent_texts`). The audit rows and the Honcho feed carry her
  words under the Nora aiPeer with their `doctrine_sha256`/`tag_id`; the Langfuse
  trace is the provenance every claim about a turn re-derives from.

**The three rules that make it hold** (UACP's, and they are load-bearing here):

1. **The discipline IS the engineering.** A `measure` that isn't fail-closed, or
   a `serialize` without provenance, is *decoration* — the narrative reads
   systematic while the result rots back into re-judgment.
2. **No self-attestation.** "Done" is never decided by the doer — it is
   **measured against evidence** (a green gate, a moved metric, a read-back
   trace) judged by something else. This is exactly the "Never claim a merge,
   deploy or fix without reading back the state" rule below.
3. **Re-derivable, so no actor is trusted.** Every step binds to reality and
   carries provenance, so a turn — or an eval verdict — can be reconstructed and
   checked *without trusting the producer*.

**Every decision and every judgement passes the five Cs** — a design, a
review finding, a fix, a report, a "done". Name the C that fails; a verdict
that names none is not a verdict.

1. **Coherence** — it holds together as one model: no part contradicts
   another, one name per thing, one owner per job.
2. **Consistency** — it agrees with what already exists: the same component,
   pattern, store and word the repo already uses (context assembly for every
   block, the existing verbs and stores). A parallel custom part fails here.
3. **Conformance** — it follows what was ruled: this file, the decision
   index, the design, the operator's rulings.
4. **Correctness** — it is true and it works, measured against evidence (a
   named test, a read-back), never asserted by whoever built it.
5. **Completeness** — it covers the whole scope: every caller, every doc,
   every deleted name, nothing left "still in shadow" or "not yet built".
   Coverage of the agreed slice, not the whole surrounding system; each C is
   checked on its own. Completion and scope changes follow `ORCHESTRATION.md`.

**Apply it at every grain** — the whole turn AND each sub-step, down to one tool
call. `serialize(N)` is the input you `comprehend(N+1)`. The **eval layer is this
same loop one level up**: comprehend a real user-journey into a typed case,
measure the LIVE model's trajectory fail-closed (empty reply / wrong path /
budget blown = FAIL; timeout = ERROR ≠ PASS), serialize a verdict bound to its
Langfuse trace. Same three verbs, same mindset — that is how a new capability's
evaluation reuses this framework instead of growing a bespoke one.

## Build the least that works

Nora is an experimental, self-hosted project. It must stay maintainable
(clear code, a green gate, true docs, one deploy path); it does not need production ceremony. Distilled from
[ponytail](https://github.com/DietrichGebert/ponytail) (MIT), and active on
every task: writing, fixing, reviewing, designing.

**Read first, then climb the ladder and stop at the first rung that holds.**
Trace the real flow end to end before choosing; the ladder shortens the
solution, never the reading.

1. **Does it need to exist at all?** A speculative need is skipped, in one line.
2. **Already in this codebase?** Reuse the helper, store, verb or pattern a few
   files over. Re-implementing it is the most common slop.
3. **Stdlib, a Postgres constraint, or an installed dependency does it?** Use
   it. Never add a dependency for what a few lines can do.
4. **One line?** One line.
5. **Only then** the minimum code that works.

**A guard needs a consequence.** Before writing a validator, cascade, cap,
alert, ledger, switch or review round, name the harm it prevents and show it
can happen: a live count, an incident, or a real trust boundary (an identifier
or coordinate egressing, a room reading another room, a credential). Then check
that the store that matters honours it. If the harm has never happened and
nothing breaks without the guard, leave it out and note the ceiling in one
line. The delete-withdrawal incident is in `docs/maintainers/conventions-rationale.md`.

- **A boundary is drawn by scope, never by a filter inside it.** The unit is
  the read boundary (DEC-231): if two rooms must not share what is said, they
  belong in different units. Memory has no lineage; `[lineage]` cannot enforce it.
- **Ship the feature, then harden what breaks.** Undo is a config flip and a
  restart, or a database backup, not revert, pause or ledger machinery.
- **One integrated change from one spec**, measured once by snapshot replay.
  No piecewise PRs with nothing measured between them, and no shadow week at
  this volume.
- **No unrequested abstraction**: no port with one implementation and no
  reason, no config for a value that never changes, no scaffolding "for later".
- **Deletion over addition; fewest files; shortest working diff**, but only in
  the right place. A bug fix goes where every caller routes through, not in
  the one path the report names.
- **A deliberate corner gets a `# ponytail:` comment** naming its ceiling and
  the upgrade path (`# ponytail: one global lock; per-unit locks if sessions
  contend`).
- **One check, not a suite.** Non-trivial logic leaves the smallest test that
  fails if it breaks. A mutation table, a second review round or a strongest-
  tier reviewer is spent only where a real failure was found.
- **Complex ask: ship the small version and question the rest in the same
  report.** "Did X; Y covers it. Need full X? Say so." Never stall on a choice
  you can default.

**Never simplified away:** the egress and disclosure rules (R-18, R-28, R-31),
fail-closed measurement (R-27), error handling that would lose input, and
anything the operator asked for explicitly.

## Reporting to the operator

**Two registers, and using the wrong one is the common failure.**

**THINKING** — architecture, brainstorming, design trade-offs, *why is this
wrong*. **Full depth. Take the room you need.** Compressing an architectural
argument damages it, and terse reporting must never bleed into design
discussion.

**Proceed unless blocked.** Before stopping to ask, answer two questions: is
there a blocker, and is there a fact only the operator holds? If both are no,
carry the work end to end (merge and deploy included, through the skills
above). A decision you can make from the evidence is not input.

**REPORTING** — work finished, a check ran, a peer replied. Terse:

```
<the result, first line, no preamble>

STATUS      what is true now
DECIDED     what changed — one line each
NEEDS YOU   only what genuinely requires the operator, with the real trade-off
```

- **Headers are subjects, not verdicts** — *"notes never called"*, not *"three
  things I got wrong"*.
- **Lead with the conclusion.** If the most important fact is in paragraph five,
  the report failed.
- **No internal identifiers in prose unless asked** — no section numbers, metric
  names, instance counts. The operator should not have to look something up to
  parse a sentence.
- **Reasoning goes in the artifact, not the message.** Design nodes carry
  provenance because they must be re-derivable; a status report must not.
  Applying the spine's provenance rule to *conversation* is a category error —
  it is the single most common way these reports go wrong.
- **Errors get one clause, then move on.** Never a paragraph on how you found it,
  never a tally of past mistakes.

## Decisions change on EVIDENCE, not on argument

When a peer, a reviewer or the operator pushes against something already decided:

1. **Is there new evidence** — a measurement, a read-back, an executed probe, a
   file that says otherwise?
2. **Yes** → change it, state the evidence in one line, do not narrate the
   reversal.
3. **No** → **hold the decision.** Say what it rests on and what reading would
   overturn it. A better-argued restatement of a known position is not new
   information.

**Deference-to-recency is the failure**: treating the most recent confident input
as authoritative. It reads as responsiveness and is the absence of a position.

**Write the reversal condition when you decide.** *"This holds unless X"* — the
bundle already does this for design nodes (*"the reading that would reopen it"*);
it applies to any position you state. It makes the next challenge cheap to answer
and makes a flip visible as a flip.

**A decision the operator has locked is closed.** Surface new reasoning; leave
the decision.

## Never

- **Never `pip install`** — dependencies via `uv add` (operational constraint 5).
- **Never write a test whose fake answers every request the same way** — make the
  fake behave like the real server, including its wrong-call shapes. If reverting
  the code under test doesn't turn the test red, the test is decorative.
- **Never point signal-cli at a different account, re-register it, or unlink its
  device** without an explicit decision — that store holds the device
  registration. Routine reads/RPC are ordinary; destructive account ops are not.
- **Never reference the archived legacy path** (`~/.hermes/profiles/nora/`).
- **Never assert a per-tag object was cleaned up by checking an attribute was
  deleted** — count live `gc`-tracked objects instead.
- **Never rewrite a model's instruction before reading its input** — if the
  judgement turns on *when*, check the block carries a time; on *who*, an
  index; on *still open*, the thing that would close it.
- **Never build a model prompt outside context assembly** — a `_BLOCKS` table walked by
  `assemble`; no debt ledger, no exception for an occasion (DEC-273, ADR 0021).
  `tests/architecture/test_every_model_call_is_a_declared_occasion.py` fails on one;
  a new occasion follows `design/context-assembly/04-every-occasion.md` «How a new occasion joins».
- **Never paste production rows into a fixture** — not a slice, not "just six
  people", not with a docstring saying it is real. Fixtures are GENERATED, or
  placeholders from `identity.example.toml`; if a synthetic corpus cannot
  reproduce the property under test, that is a finding about the property.
  Hooks (`.githooks/README.md`) refuse added real values, phone/id/email shapes
  and coordinate tables, fail-closed and deterministic, against a private
  denylist. Install with `make hooks` and the denylist; disguised corpora remain
  your responsibility. A denylist hit has no bypass; a shape finding needs a
  pushed declaration, the private pseudonym registry, or a human's
  `NORA_PII_ACK=<id>` after LOOKING. Never acknowledge a value yourself:
  replace it with a generated one, or ask your human.
- **Never read an instrument's output as evidence without first checking it can
  measure the thing** — state what a positive result would look like before
  concluding from a null one.
- **Never claim a merge, deploy or fix without reading back the state you claim**
  — `git log -1`, `docker exec … import config`, the metric endpoint. Every time.
  A fix needs the exact production line reverted and a NAMED test red:
  `nora-prove-fix` and `docs/maintainers/conventions.md` «Proving a fix» carry the harness rules.
- **Never read a gate's success through a pipe.** Capture its rc before reading
  its log; a background notice reports the last command's rc (`nora-land`).
- **Never cite a file, test or function you have not opened** in this session.
  A peer saying it will write one is not an opening, and finding a definition
  is not finding a caller: grep for the call before repeating that it works.
- **Never edit, commit, or merge on `master` in this checkout** — every change,
  including a one-line doc fix, starts in its own worktree and reaches `master`
  by merge. Landing a merge does not move you back in: the next change starts
  in a new worktree too. The host owns the deploy checkout; follow `nora-deploy`
  for bind-mount versus image changes, including runtime skills.
- **Never run two agents against this checkout at once** — sequence them, or give
  each a worktree.
- **Never trust `alembic revision --autogenerate` for `idx_messages_conv_ts`** —
  it is excluded on purpose; a revision touching it is hand-written.
- **Never assume your migration's parent is unclaimed.**
  **`uv run alembic heads` must print exactly ONE line after every merge.**
- **Never take a DEC or R number from a chat agreement** — re-read the index at
  MERGE time, not claim time; use `nora-decision` for allocation and duplicate checks.

## Instruments and spans

Read **`docs/observability.md`** before adding an instrument; incidents are there
and in `docs/maintainers/conventions-rationale.md`, catalogued in `design/dreamer-agent-loop/02-instrument-blindness.md`.

- **One root span per entry point, and stages are its CHILDREN.** Sibling spans
  with no root are not one trace with gaps — they are N traces.
- **Langfuse gets the traces with a GENERATION in them; everything else is
  Prometheus'.** A root declares `nora.trace_kind`; plumbing is dropped at the
  sampler; its outcome is a counter label. This is about ROOTS: a stage without
  a model call makes early exits legible under a root that has one.
- **Spawned work does not inherit the caller's span.** `create_task` copies the
  CALLING task's context; capture and attach the intended context explicitly.
- **One spelling for a model call: Strands'.** Agent-backed
  `ask_text`/`ask_structured` needs no wrapper; raw `chat.completions.create`
  uses `nora.agent.telemetry.start_raw_model_span`/`end_raw_model_span`.
  Never hand-write `gen_ai.*` or `langfuse.observation.*` literals or wrap an
  Agent call in a second span.
- **Detaching a span and dropping it are two decisions.** Spawned work should
  detach so it does not hold a trace open; that is not a reason to declare it
  plumbing.
- **A log line is not a metric.** If the answer is "how many, how often, with
  what outcome" it is a counter; a line that restates one every interval is a
  third copy.
- **Severity is a NUMBER as well as a word** — `level` sorts alphabetically, so
  "at least a warning" is not expressible over it. `severity_number` carries
  OTel's SeverityNumber bands; an unmapped level gets no field, never a guess.
- **A span with only a name is worse than no span** — carry what makes the step
  re-derivable. Empty traces read as progress; an absence at least asks.
- **Instruments measure the RESULT; something must measure the COST.** An index
  is used, a call is bounded, a read is scoped: check the PLAN or the bound.
  Never the answer — a correct answer is compatible with any cost.
- **R-27's three states on every instrument: `ok` / `nothing_found` /
  `unavailable`.** A wiring gap is `unavailable` — never `nothing_found`, never
  `ok`, and never two of them on one event.
- **A declared metric with no call site is a lie** — it renders as a series that
  is genuinely always zero. Ship the name with its increment, or not yet.
- **And an increment with no declaration is worse** — `Metrics.inc` raises
  `KeyError`, misreported by the caller's `except Exception` as the wrapped failure.
  `tests/architecture/test_every_recorded_metric_is_declared.py` is the gate;
  a call site whose name it cannot read statically must be PINNED there, never
  skipped.
- **Label vocabularies are DERIVED from the code, never hand-copied.** One named
  constant the code increments against and the help text reads from: `get_args`
  off a `Literal`, plus a RUNTIME check outside mypy's scope (issue #61).
- **Pair every zero-assertion with a positive control.** `== 0` is also true of
  a stage that crashed; assert something only a run that RAN produces.

## Comments and maintained documentation

Comments are minimal local descriptions or short constraints with pointers.
Architecture, rationale and decisions belong in canonical maintained docs, never
primarily in comments. Apply this across the repository; do not mechanically
strip existing comments. See [configuration contract](docs/configuration.md) and
its accepted amendment (DEC-325).

## Conventions (short — full text in `docs/maintainers/conventions-rationale.md`)

- **One stage, one module, one public entry point** — about RESPONSIBILITIES, not
  lines (a 400-line module doing one thing is fine; count what it is responsible
  for).
- Test-seam policy: production classes take collaborators through `__init__` only
  — no `*_fn` hooks, no `_for_test` params, no module-level mutable singletons.
- A port never gains a second implementation — including a test fake — without a
  shared contract suite both pass (`tests/contract/`).
- **A contract suite scopes its database to the MODULE, not the test.** Build
  the schema once per file and `TRUNCATE` between tests; isolate rows, not schema.
- A new behavioural rule gets an entry in `design/09-decision-index.md` and a
  named test in `tests/test_acceptance_rules.py::RULE_TESTS`.
- **A test directory names what makes the test TRUE, not what it is about**
  (`tests/README.md`): `unit/` no I/O, `contract/` one suite N implementations,
  `integration/` the wired pipeline, `architecture/` properties of the source.
  The `tests/` root is legacy and takes no NEW files.
- **A test gets its config from the template** — the `example_config_path` /
  `example_config` fixtures — never a path to the repo's `config.toml`. Only a
  test whose SUBJECT is the deployment reads the real file, pinned in
  `tests/architecture/test_config_comes_from_the_template.py`.
- **A fake standing in for a port lives in `nora/testing/`** and is
  parametrized into that port's contract suite. Inline classes are local
  scaffolding only; `tests/architecture/test_port_fakes_live_in_nora_testing.py`
  is the gate and its `_ALLOWED` list only shrinks. Issue #113 for all three.

## The five operational constraints (full text in `docs/maintainers/conventions-rationale.md`)

1. Direct builds require `docker build --network=host` (the bridge cannot resolve
   `deb.debian.org` here); compose `build:` sets `network: host`, so
   `docker compose build` needs no flag.
2. Honcho health uses **`GET /health`**, not `GET /v3/workspaces` (POST-only → 405).
3. **signal-cli is Nora's transport, in her compose project.**
   **`--receive-mode=on-start` is load-bearing — never drop it** (without it the
   websocket to Signal never opens and every message is silently dropped). Needs
   **Java 25**.
4. The legacy Hermes gateway is retired. If migrating an old installation,
   archive its profile outside the checkout; do not reuse the old profile path.
5. Dependencies via **`uv add`**, never raw pip.

## Skills: the procedures

Each skill is a `SKILL.md` under `.agents/skills/` (the cross-tool location);
`.claude/skills/<name>` is a symlink to it, so Claude Code reads the same
files and there is one copy. A skill is the ORDER of the commands and the check after
each one; the reasons stay in the documents it links. Load the skill when the
task starts, not when it goes wrong: the audit behind them (ADR 0017) found the
rules were written down and not read at the moment they applied.

| when the task is | load |
|---|---|
| starting a change, running the gate, committing, opening or merging a PR | `nora-land` |
| reviewing a change against its original scope and acceptance | `nora-review` |
| putting merged code or config live on `nora-host`, or checking it is live | `nora-deploy` |
| cutting a release: the release-please PR, the `vX.Y.Z` tag, the GitHub Release | `nora-release` |
| reading a trace, a metric, a row or a log as evidence | `nora-readback` |
| confirming a built prompt, doctrine, tool-text or routing fix with the eval suite (find the cause offline first) | `nora-eval` |
| proving a fix is guarded (revert, watch a named test go red, restore) | `nora-prove-fix` |
| a new DEC or R row, an ADR, an issue, or citing a decision | `nora-decision` |
| adding or changing one of Nora's tools (ADR 0015) | `nora-tool` |
| adding or changing one of Nora's runtime skills in `nora/skills/` (ADR 0016) | `nora-runtime-skill` |
| creating or editing digital 3D assets with Meshy | `meshy-3d-generation` |

`nora/skills/` holds NORA's skills, which she loads at runtime; `.agents/skills/`
holds the skills of the agents that build her. `tests/architecture/test_agent_skills.py`
keeps this table and that directory in step.

## Orchestrating: who builds, who checks, and how much effort

**[ORCHESTRATION.md](ORCHESTRATION.md) is the canonical engineering-agent policy**
(operator ruling, 2026-10-27): capability/evidence tiers, adaptive worker admission,
artifact ownership, independent review and its two-round limit. The operator-facing
session orchestrates; the builder implements its bounded brief. Use isolated
worktrees, read back the actual head/results, and keep the reviewer independent
of both builder and brief-author, with a different model vendor from the builder.
No fixed positive provider/model routing applies. Explicit operator constraints
and model exclusions remain in the root policy. Superseded provider incidents
and routing history live in `.agents/skills/nora-land/reference.md`.

## Reading order

1. `README.md` — what Nora is, how to run her.
2. `docs/maintainers/conventions-rationale.md` — gate reasoning, war-stories, constraints and conventions.
3. `ARCHITECTURE.md` — the map: topology, package table, ports, a walk of one
   ambient message and one tag.
4. `design/09-decision-index.md` — one line per decision, pointing at the
   document that made it and the test that enforces it.
5. `design/*.md` — the topology documents the decision index points into.
6. `CONTRIBUTING.md` — public setup, branches, commits and PRs;
   `docs/maintainers/conventions.md` — merge, numbering and decision flow.
7. `docs/adr/` — Nygard-form ADRs: why a decision was made and what it cost.
8. `DESIGN.md` — the visual system for every Nora surface (portal, social,
   imagery, 3D): palette, type, shape, motion. Read it before touching `app/`
   styling or anything she looks like.
9. `docs/observability.md`, `docs/eval-sop.md`, `docs/runbook.md` — how to
   instrument, measure behaviour, and operate her. Read the one the task touches.

Historical paths and deprecated tiers: `docs/maintainers/conventions-rationale.md` «Historical reading references».

## Gate commands

Load **`nora-land`** for setup, the fast loop, final gate and rc read-back.
Run the final gate once on the final head; use focused tests while iterating.
Docs-only changes use its affected-test procedure, never hand-picked readers.
`make test` is incremental; `make test-fast` and `make test-affected` are aliases.
They compare staged, unstaged and untracked changes against HEAD, or the last
commit (HEAD~1) when clean. Only exact generated untracked LFS post-hooks are
ignored. `BASE=<ref>` overrides auto selection. Unmapped/shared-helper changes
and selector errors run the full suite; the command reports its base and scope.
`make test-all` explicitly runs the full serial suite. For a named file/function,
use `uv run python -m scripts.pytest_gate <file-or-node>`.

`make gate` runs static checks and affected tests, or document readers for docs-only changes;
local identical candidate/context PASS receipts may be reused. `make gate-fresh`
forces execution; CI and `make full-gate` always run fresh, with full-gate serial.
The schema gate, import cache and typecheck scope are in `docs/maintainers/conventions-rationale.md`.

## Reaching the deployment

Development is local; `nora-host` is the operator-configured SSH alias for a
**deploy target, not a dev checkout**. The installed Komodo release Stack
`nora-releases` owns application deploys from the `deployments` branch; its enabled
poller checks every ten minutes. `~/workspace/nora` is the legacy live runtime
root for config, `.env`, `data/`, `state/`, infrastructure Compose services and
the computer unit's `ExecStart`. **Do not delete or reset it.** Doctrine and
Dreamer are image-baked in Komodo mode; a host pull does not change them.
Load **`nora-deploy`** to select deployment mode, lock, validate and deploy;
load **`nora-readback`** for database access, container logs and evidence.
Read host status before any pull; preserve unpushed commits on a branch, never
reset them. Deploys must not overlap. Key auth only: never `sshpass` or passwords
in `.env`, chat or session-readable files. Setup: `docs/maintainers/conventions-rationale.md`.
The host's `config.toml` is authoritative: read it before asserting a live flag.

## Settings, identity, policy and secrets

The three non-secret files retain their lifetimes (DEC-8); the credential-source
amendment adds private `secret.toml` with controlled recreation:

| file | answers | changes when |
|---|---|---|
| `config.toml` | ports, models, timeouts | the deployment changes |
| `identity.toml` | **who people are** | a human joins |
| `policy.toml` | **who may talk to her, and where** | trust changes |
| `secret.toml` | private credential values with explicit environment overrides | credentials change; controlled recreation |

All four are gitignored; `config.example.toml` and `secret.example.toml` record their shapes.
**No config change reaches a process unvalidated.** Use **`nora-deploy`**'s
validated-copy procedure; secrets live in private `secret.toml`, referenced by name with environment overrides.
`config.toml` also mounts read-only into `stt`/`voice-wake`, which read
`[speech.stt]`/`[speech.wake]` at boot. Identity and policy are read-only and
**hot-reloaded after validation every three seconds** in each reader process.
Invalid edits keep the last good file; workspace-binding edits require recreation
and refuse the whole reload. Read `nora_config_reloads_total{outcome}` and
`/health` config staleness. See `design/policy-reload/00-design.md`.
Identity takes one `[[persons.<id>.<platform>]]` block **per account**, all
resolving to one `person_id` (Honcho peer, A2/DEC-14). Policy is deny-by-default;
Nora **refuses to start** if it is missing or allows nothing (R-5).
Memory hierarchy (DEC-214): policy `[units]` maps units to workspaces; identity
`[workspaces]` binds each to Honcho; `honcho.workspace_id` is only the fallback
when there is no `[units]`. History and drift-check limits: `docs/maintainers/conventions-rationale.md`.

## Releases

Load **`nora-release`**. The operator's latest 2026-10-28 ruling reverses the
manual/daily cadence: every ordinary master push automatically handles patch/minor
releases, including docs, CI, config and maintenance. Generated metadata is
excluded to prevent loops. `scripts/release_plan.py` uses complete git reachability;
release-please alone tags/publishes. One generated metadata PR may auto-merge
with an authored subject and exact-head guard after required checks; strict branch
rules prevent stale-source merges. This authority never merges application or
implementation PRs. No bypass, alternate token, hand tag or release fragment.

PATCH is the default for compatible work within an existing capability. A complete
new product capability or a new/deprecated documented supported integration API/CLI
uses the explicit commit footer `Release-Bump: minor`. `feat` alone does not force
minor. Breaking markers anywhere in the unreleased range stop automatic version
writes; owner-only dispatch can explicitly prepare/release major at an exact full
current candidate SHA, with drift refusal. Existing versions/tags are preserved.

A stable published Release automatically feeds the existing `release-images.yml`
→ `deployments` → Komodo pipeline under its existing control. This standing
release/deployment authority covers automatic patch/minor and explicitly
owner-prepared major releases. Publication and verified deployment remain separate
states. Devin release automation is stopped; no AI evaluator or daily/seven-day
criteria remain. Do not invoke or re-enable it (issue #316 closed not planned).

The owner may separately dispatch **Deploy master** in `release-images.yml`.
Keep `MASTER_DEPLOY_ENABLED` default-off until the reviewed installed wrapper
outside the fetched clone is updated and verified (`nora-deploy`). This ruling
enables no flags and introduces no selective builds or RC/nightly channel.

## Adding or changing a tool

Load **`nora-tool`** before editing: ADR 0015, naming, result shape and before/after routing numbers in the PR.

## Adding or changing a skill

Nora's runtime skills (`nora/skills/`): load **`nora-runtime-skill`** for ADR 0016 placement, reach/cost and PR evidence.
Agent skills (`.agents/skills/`): ADR 0017; add the Skills-table row and `.claude/skills/<name>` link in the same commit.

## Design backlog

Update an existing design sequence or issue before opening another. File deferred
work with no existing home as a GitHub issue on `mikeng-io/nora`; tiny residues
belong in the PR limitations. See `nora-decision`. Only landed decisions enter
the decision index.

<!-- OPENWIKI:START -->

## OpenWiki

This repository has a generated `openwiki/` evidence index. It is optional just-in-time context, not required startup reading.

- Do not enumerate, preload, or search wikis at task start. Use retrieval when the user asks for it, when unfamiliar architecture or dependency behavior materially affects the task, or when source inspection leaves an important uncertainty. Stop once the question is grounded.
- When those conditions apply and OpenWiki retrieval tools are available, use `openwiki_search` for just-in-time context and `openwiki_read` for the relevant complete sections. If search returns `workspace_required`, ask which listed workspace to use and retry with its ID.
- Use `openwiki_list_workspaces` or `openwiki_list_wikis` when workspace membership itself needs to be discovered.
- If the retrieval tools are unavailable, read `openwiki/quickstart.md` and follow its links to the relevant pages.
- Treat source code and tests as authoritative. A brief's unknowns and review items are verification gaps, not automatic requirements.
- Prefer the narrowest quiet validation that proves the changed behavior. Preserve complete failure output.

The scheduled OpenWiki GitHub Actions workflow refreshes the repository wiki. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->
