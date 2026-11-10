# AGENTS.md

The entry point for any agent working in this repo. `CLAUDE.md` points here so
there is one copy. **Read [ORCHESTRATION.md](ORCHESTRATION.md) before assigning
work**: it owns worker selection, review tiers and artifact boundaries. When a
task matches a skill in [Skills](#skills-the-procedures), load it before the first edit.

This file states what holds now. It is revised in place, never appended to: a
new rule rewrites the section that owns its subject, and a rule that no longer
holds is deleted. Git is the history.

## The spine: comprehend → measure → serialize

Every path, including a new tool, stage or eval, keeps this loop.

- **Comprehend once.** Raw input becomes a typed model (a Signal message becomes
  a `NormalizedMessage`, then a `TagContext`), and everything downstream
  computes on that model, never on the raw text again.
- **Measure fail-closed.** A tool result is `ok`, `nothing_found` or
  `unavailable`; a wiring gap or a stale fix is `unavailable`, never a guess
  and never "nothing". Bind to the real property (a live GPS read, HKO's own
  reading), never a proxy. Silence beats a wrong answer.
- **When the measurer is a model, its input must explain itself.** If the
  judgement turns on *when*, the block carries a time; on *who*, an index; on
  *whether it is still open*, the thing that would close it. When a model
  judges badly, read its input before rewriting its instruction.
- **Serialize with provenance.** Her reply is the one authored surface: only a
  `send_reply` call delivers. Audit rows and the Honcho feed carry her words
  with their doctrine hash and tag id; the trace is what every claim about a
  turn is re-derived from.

It holds only if a measure is fail-closed, "done" is measured by a gate, a
metric or a read-back and never declared by the doer, and every step can be
re-derived without trusting its producer. Evals are the same loop: a timeout is
ERROR, never PASS.

A verdict names which of the **five Cs** fails: Coherence (one name, one
owner), Consistency (reuses what exists), Conformance (this file, the design,
the operator's rulings), Correctness (measured), Completeness (the whole slice).

## Build the least that works

Nora is an experimental, self-hosted project: maintainable, not ceremonial.
Read the real flow end to end, then stop at the first rung that holds:

1. Does it need to exist? 2. Is it already in this codebase? 3. Does the
stdlib, a Postgres constraint or an installed dependency do it? 4. One line?
5. Only then, the minimum code that works.

- **A guard needs a consequence**: a live count, an incident, or a real trust
  boundary (an identifier or coordinate egressing, a room reading another, a
  credential). Otherwise leave it out and name the ceiling in one line.
- **A boundary is drawn by scope, never by a filter inside it.** The unit is
  the read boundary: rooms that must not share belong to different units.
- **A special case is a missing requirement.** Before an `if` for one value, a
  retry, a swallowed exception or a skipped test, find the fix behind it. Two
  patches to one seam mean the seam is wrong.
- **What changes together lives together.** No unrequested abstraction, no
  config for a constant. A deliberate corner gets a `# ponytail:` comment
  naming its ceiling. One check, not a suite.

Never simplified away: the egress and disclosure rules, fail-closed
measurement, error handling that would lose input, and anything the operator
asked for explicitly.

Content filters are off by default (`[content_checks] enabled = true` and a
restart restore them; [ADR 0030](docs/adr/0030-content-filters-are-disabled-by-default-and-reversible.md)); credential and doctrine protection always run.

## Design, documents and comments

**The design bundle is the anchor.** `design/<topic>/` holds the high-level
architecture: the parts, their responsibilities and connections, the
vocabulary, the invariants, and why. It is never a spec, a plan, a log, a
decision table or a status report. An **architecture change** (a component, a
connection, a boundary, an invariant or a term changes) is compiled from the
design: the bundle changes first or in the same PR. A **code change** (hotfix,
refactor, threshold, prompt or doctrine wording, eval case) goes straight to
code. `nora-design` owns the procedure.

**Git and GitHub are the log.** Commits and PRs record what changed and why;
plans, roadmaps and open items are GitHub issues and the project. The ADRs are
the one decision record kept as history; `design/09-decision-index.md` lists
the decisions that hold now, each with its enforcing test (`nora-decision`).

- **A document says what holds now**, high level, present tense, precise and
  concise. Revise or consolidate the section that owns the subject; never add
  a section beside it, never mark text deprecated or superseded. No dates,
  issue or PR numbers, reversal stories or "was X, now Y".
- **A comment says what the code cannot**: a unit, a bound, a non-obvious
  constraint, one business fact, in at most three lines. Never history, a
  measurement, a review story or a decision citation.
- **A docstring says what the thing is**; an internal (`_name`) function gets
  one line or none. A model-facing tool docstring is behaviour (`nora-tool`).
- **Size**: a file at most 800 lines, a function at most 120, `AGENTS.md` at
  most 250, a design subject file at most 300. Split by responsibility.

Gates under `tests/architecture/` hold existing debt to baselines that only
shrink; a cleanup rewrites its baseline (`python -m scripts.code_size --write`,
likewise `scripts.log_markers`, `scripts.design_shape`).

## Reporting to the operator

Two registers. **Thinking** (architecture, trade-offs, why something is wrong)
takes full depth. **Reporting** (work finished, a check ran) is terse:

```
<the result, first line>

STATUS      what is true now
DECIDED     what changed, one line each
NEEDS YOU   only what requires the operator, with the real trade-off
```

Lead with the conclusion; headers are subjects, not verdicts; no internal
identifiers in prose; errors get one clause. **Proceed unless blocked**: stop
only for a blocker or a fact only the operator holds. Changes land by merge and the release
pipeline deploys them; deploy by hand only on request, for host config, or to recover.

**Decisions change on evidence, not argument.** New evidence (a measurement, a
read-back, a probe) changes a decision in one line. A better-argued
restatement does not: hold, and say what reading would overturn it. Write that
reversal condition when you decide. A decision the operator locked is closed.

## Never

- `pip install`: dependencies via `uv add`.
- A test whose fake answers every request the same way, or that stays green
  when the code under test is reverted.
- Point signal-cli at another account, re-register it or unlink its device
  without an explicit decision.
- Reference the archived legacy profile path, or reuse a Hermes gateway.
- Assert a per-tag object was freed by checking an attribute; count live
  `gc`-tracked objects.
- Build a model prompt outside context assembly: every model call is a
  declared occasion (`test_every_model_call_is_a_declared_occasion.py`).
- Paste production rows into a fixture. Fixtures are generated, or placeholders
  from `identity.example.toml`; the hooks refuse real values, and you never
  acknowledge a finding yourself.
- Trust an instrument's null before saying what its positive would look like.
- Claim a merge, deploy or fix without reading back its state; a fix needs its
  line reverted and a named test red (`nora-prove-fix`). Read a gate's rc, never
  through a pipe. Cite only files and functions opened in this session.
- Edit, commit or merge on `master` in this checkout; every change starts in
  its own worktree. Never run two agents against one checkout.
- Trust `alembic revision --autogenerate` for `idx_messages_conv_ts`.
  `uv run alembic heads` prints exactly one line after every merge.
- Take a decision number from a chat agreement; allocate it at merge time.

## Instruments

Read `docs/observability.md` before adding one.

- One root span per entry point; stages are its children. Spawned work does
  not inherit the caller's span: attach the intended context explicitly.
- Langfuse gets traces with a generation in them (a root declares
  `nora.trace_kind`); the rest is a counter label. Model calls use Strands'
  spans, or `start_raw_model_span` for raw completions; never hand-write `gen_ai.*`.
- A log line is not a metric. A declared metric has a call site and an
  increment has a declaration (`test_every_recorded_metric_is_declared.py`).
  Label vocabularies are derived from the code (`get_args` off a `Literal`).
- Three states on every instrument; measure the cost (the plan, the bound),
  not only the result; pair every zero-assertion with a positive control.

## Code conventions

- One stage, one module, one public entry point (responsibilities, not lines).
- Collaborators come through `__init__`: no `*_fn` hooks, no `_for_test`
  parameters, no mutable module singletons.
- A port gains a second implementation, a test fake included, only with a
  shared contract suite in `tests/contract/`, its database scoped to the module.
  Port fakes live in `nora/testing/`.
- A test directory names what makes the test true (`tests/README.md`); the
  `tests/` root takes no new files. Tests read config from the template
  fixtures, never the repo's `config.toml`.
- A new behavioural rule gets a decision-index row and a named test in
  `tests/test_acceptance_rules.py::RULE_TESTS`.

## Operational constraints

1. Direct builds need `docker build --network=host`; compose sets it.
2. Honcho health is `GET /health`, not `GET /v3/workspaces`.
3. signal-cli is Nora's transport, in her compose project, on Java 25;
   `--receive-mode=on-start` is load-bearing (without it every message drops).
4. Dependencies via `uv add`.

## Settings and secrets

| file | answers | changes when |
|---|---|---|
| `config.toml` | ports, models, timeouts | the deployment changes |
| `identity.toml` | who people are | a human joins |
| `policy.toml` | who may talk to her, and where | trust changes |
| `secret.toml` | credential values | credentials change |

All are gitignored; `*.example.toml` record their shapes. No config change
reaches a process unvalidated (`nora-deploy`). Policy is deny-by-default and
Nora refuses to start if it allows nothing; the host's file is authoritative.

## Deployment and releases

`nora-host` is a deploy target, not a dev checkout. The Komodo stack
`nora-releases` deploys from the `deployments` branch; `~/workspace/nora` on
the host holds live config, data and state and is never deleted or reset. Key
auth only: never `sshpass` or a password in `.env`, chat or a readable file. Every ordinary master push releases a patch or minor automatically;
`nora-release` owns the rest, `nora-deploy` owns deployment.

## Skills: the procedures

Each is `.agents/skills/<name>/SKILL.md`, linked from `.claude/skills/<name>`.
A skill is the order of the commands and the check after each. A new skill
adds its row here and its link in the same commit.

| when the task is | load |
|---|---|
| starting a change, running the gate, committing, opening or merging a PR | `nora-land` |
| reviewing a change against its original scope and acceptance | `nora-review` |
| an architecture change, or any edit under `design/` | `nora-design` |
| a new DEC or R row, an ADR, an issue, a roadmap direction, or citing a decision | `nora-decision` |
| putting merged code or config live on `nora-host`, or checking it is live | `nora-deploy` |
| cutting a release: the release-please PR, the `vX.Y.Z` tag, the GitHub Release | `nora-release` |
| reading a trace, a metric, a row or a log as evidence | `nora-readback` |
| confirming a built prompt, doctrine, tool-text or routing fix with the eval suite (find the cause offline first) | `nora-eval` |
| proving a fix is guarded (revert, watch a named test go red, restore) | `nora-prove-fix` |
| adding or changing one of Nora's tools | `nora-tool` |
| adding or changing one of Nora's runtime skills in `nora/skills/` | `nora-runtime-skill` |
| creating or editing digital 3D assets with Meshy | `meshy-3d-generation` |


## Reading order

`README.md` → `ARCHITECTURE.md` → the area's design bundle → `CONTRIBUTING.md`
and `docs/maintainers/conventions.md`; `DESIGN.md` before touching anything she
looks like; `docs/observability.md`, `docs/eval-sop.md`, `docs/runbook.md` as needed.

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
