# AGENTS.md
The entry point for any agent working in this repo. `CLAUDE.md` points here so
there is one copy. Start or resume engineering work with `nora-work`: it routes
the current outcome through the lifecycle in [ORCHESTRATION.md](ORCHESTRATION.md).
That policy owns responsibilities and boundaries; skills own procedures. Load
only the skill and references needed for the next action, before that action.

State current instructions once; revise their owner in place. Delete obsolete
rules. Git is the history.

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
5. Only then, the minimum code that works. Ship it, measure it once, and harden
what breaks; a complex ask ships its small version and questions the rest.

Never simplified away: the egress and disclosure rules, fail-closed
measurement, error handling that would lose input, and anything the operator
asked for. Content filters are off by default ([ADR 0030](docs/adr/0030-content-filters-are-disabled-by-default-and-reversible.md)); credentials and doctrine protection always run.

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
GitHub Project owns roadmap; Linear owns execution; GitHub Issues hold reports. The ADRs are
the one decision record kept as history; `design/09-decision-index.md` lists
the decisions that hold now, each with its test or `NOT ENFORCED` (`nora-decision`).

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
identifiers in prose; errors get one clause; reasoning goes in the artifact,
not the message. **Proceed unless blocked**: stop only for a blocker or what the
operator alone holds: intent, authority or a redesign (`nora-work`). Merges deploy through the release pipeline; deploy by
hand only on request, for host config, or to recover.

**Decisions change on evidence, not argument**: a measurement, read-back or probe
changes one in a line; a better-argued restatement does not, so hold. Write the
reading that would overturn it when you decide. A decision the operator locked is closed.

## Code standards

Write and review every change against [docs/code-standards.md](docs/code-standards.md):
the cross-cutting rules, then the section for each stack the diff touches.
A reviewer's finding names the rule it breaks. A failure no rule caught is
recorded in [docs/maintainers/lessons.md](docs/maintainers/lessons.md); a
lesson seen on three separate days becomes a standard.

## Never

- `pip install`: dependencies via `uv add`.
- Point signal-cli at another account, re-register it or unlink its device
  without an explicit decision.
- Reference the archived legacy profile path, or reuse a Hermes gateway.
- Edit, commit or merge on `master` in this checkout; every change starts in
  its own worktree. Never run two agents against one checkout.
- Take a decision number from a chat agreement; allocate it at merge time.

## Operational constraints

1. Direct builds need `docker build --network=host` (compose sets it); Honcho
   health is `GET /health`, not `GET /v3/workspaces`.
2. signal-cli is Nora's transport, in her compose project, on Java 25;
   `--receive-mode=on-start` is load-bearing (without it every message drops).

## Settings and secrets

| file | answers | changes when |
|---|---|---|
| `config.toml` | ports, models, timeouts | the deployment changes |
| `identity.toml` | who people are | a human joins |
| `policy.toml` | who may talk to her, and where | trust changes |
| `secret.toml` | credential values | credentials change |

All are gitignored, their shapes in `*.example.toml`; no config change reaches a
process unvalidated (`nora-deploy`). Policy is deny-by-default (Nora refuses to start if it allows nothing); the host's file is authoritative.

## Deployment and releases

`nora-host` is a deploy target, not a dev checkout: Komodo's `nora-releases`
deploys from `deployments`; the host's `~/workspace/nora` holds live config,
data and state and is never deleted or reset. Key auth only, never a password.
Master pushes release automatically (`nora-release`); `nora-deploy` deploys.

## Skills: the procedures

Each is `.agents/skills/<name>/SKILL.md`, linked from `.claude/skills/<name>`: the
order of the commands and the check after each; a new skill adds both at once.

| when the task is | load |
|---|---|
| starting or resuming engineering work, choosing the next stage, or handing over | `nora-work` |
| starting a change, running the gate, committing, opening or merging a PR | `nora-land` |
| turning a design or issue into briefs, delegating a build, or reading a builder's result back | `nora-dispatch` |
| reviewing a change against its original scope and acceptance | `nora-review` |
| an architecture change, or any edit under `design/` | `nora-design` |
| a new DEC or R row, an ADR, an issue, a roadmap direction, or citing a decision | `nora-decision` |
| putting merged code or config live on `nora-host`, or checking it is live | `nora-deploy` |
| cutting a release: the release-please PR, the `vX.Y.Z` tag, the GitHub Release | `nora-release` |
| reading a trace, a metric, a row or a log as evidence | `nora-readback` |
| confirming a built prompt, doctrine, tool-text or routing fix with the eval suite (find the cause offline first) | `nora-eval` |
| writing or changing a test under `tests/` | `nora-test` |
| proving a fix is guarded (revert, watch a named test go red, restore) | `nora-prove-fix` |
| adding a module, or moving or splitting code | `nora-structure` |
| adding or changing one of Nora's tools | `nora-tool` |
| adding or changing one of Nora's runtime skills in `nora/skills/` | `nora-runtime-skill` |
| creating or editing digital 3D assets with Meshy | `meshy-3d-generation` |

## Reading order

`README.md` → `ARCHITECTURE.md` → the area's design bundle → `docs/code-standards.md` → `CONTRIBUTING.md`, `docs/maintainers/conventions.md`; `DESIGN.md` before touching how she looks; `docs/observability.md`, `docs/eval-sop.md`, `docs/runbook.md` as needed.
