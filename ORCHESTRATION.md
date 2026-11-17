---
kind: policy
version: 1
execution_tiers:
  - id: routine
    capabilities:
      - Follow a known mechanical transformation or perform a bounded lookup/read-back.
    required_evidence:
      - Read the actual source or result and report revision, scope and limitations.
  - id: scoped
    capabilities:
      - Implement a finished specification within explicit file and behavior boundaries.
    required_evidence:
      - Named focused checks with actual exit codes and a diff bound to the resulting head.
  - id: complex
    capabilities:
      - Resolve ambiguity within a specification, trace causes, and compare design trade-offs.
    required_evidence:
      - Ground the chosen approach in inputs, alternatives and reachable failure paths.
  - id: critical
    capabilities:
      - Reason about disclosure, security, data loss, concurrency, hot-table migrations or model spending.
    required_evidence:
      - Verify the real trust/cost boundary with failure evidence and independent scrutiny.
review_tiers:
  - id: none
    capabilities:
      - Read the diff for scope and reconcile the gate and read-back with acceptance.
    required_evidence:
      - Orchestrator records the applicable review row, appropriate validation and read-back.
  - id: light
    capabilities:
      - Independently check a bounded change and its named acceptance paths.
    required_evidence:
      - Fresh independent reviewer reports reachable findings against the exact head.
  - id: full
    capabilities:
      - Independently trace affected callers, failure paths and cross-cutting boundaries.
    required_evidence:
      - Review binds the spec, exact head/base, findings and their triage to evidence.
---

# Engineering orchestration

How the agents that build Nora (not Nora's runtime) split, do and check work.
The frontmatter defines the execution and review tiers, each adding to the ones
before it; a tier describes the work and its checking, never a model or toolset.

## The orchestrator

The orchestrator is whichever agent holds the operator-facing session: Claude
Code, Codex, Devin or any other coding agent. It owns the outcome: scope, do or
delegate, reconcile evidence, triage, land and report.

- **Do small changes yourself**: documentation, agent-skill wording, comments,
  one- or two-line fixes and bounded edits across a few files.
- **Delegate only for a reason**: a worker's capability outweighs the briefing
  cost and lost context, or the operator asks; file count alone is not one. Any
  delegation mechanism follows the same brief and read-back rules.
- **Claude models are never a delegated builder or an independent reviewer.**
  A Claude orchestrator makes small changes directly and hands substantial
  implementation to a non-Claude builder.
- One committing agent per isolated worktree; reviews run read-only in their
  own checkout of the exact head. Read back `pwd`, head and clean status rather
  than trusting a worker's report.

## The lifecycle

Engineering follows UACP's phases in order: **triage → propose → plan →
execute → verify → resolve**. `nora-work` routes it; its
[lifecycle reference](.agents/skills/nora-work/references/lifecycle.md) owns
the routes, the transition table and the re-triage triggers.

- **No self-attestation.** At each transition its route requires, a checker
  other than the doer (the operator, the reviewer by rule, CI or the next
  agent) records pass or block. A skill halts when that output is missing.
- **Ask only for intent, authority or redesign.** The agent obtains reviewer and
  CI checks itself; only an operator-held gate waits, never a "continue".
- **Ceiling.** The gates are policy-level stops held by skills, not an
  unskippable control; the reviewer and the operator's merge refuse when the
  route's required artifact is missing.
- An implementation request continues through delivery; a design-only request
  stops at its design. Agreement grants no new spending or deployment.

## Coordination and decisions

The operator owns outcomes, scope amendments and exceptions. The orchestrator
chooses units, workers, review tier and next steps within that authority;
builders implement their slice; reviewers judge it without adding requirements.
For a shared design, interface or migration, read the related active work and
T3 threads before assigning writes (`nora-dispatch`); one writer owns a shared
seam at a time, and one outcome owner integrates.

## Execution tier

Pick the lowest tier that covers the ambiguity and the cost of a miss; disclosure,
security, data loss, concurrency, hot-table migrations and model spending need
critical capability even in a small diff.

## Review and validation are adaptive

Review depth and checks follow what the change can break, not its type or size:
what changed, what could break, and what evidence shows it did not; spend the
least that answers them.

| Change | Usual review |
|---|---|
| Docs, agent-skill wording, comments, images; one- or two-line fixes whose effect is obvious; small off-by-default additions | none: the orchestrator reads the diff and checks the changed property |
| Tests, CI, hooks, test selection; larger additive work; a fix in shared code | light, when the orchestrator cannot settle it alone with confidence |
| Live behaviour on merge: defaults, prompts, doctrine, tool text, routing, model settings | full |
| Disclosure, security, locks/concurrency, hot-table migrations, new model spending | full, with critical execution capability |

The last row is the only fixed minimum; elsewhere move a step on judgement and
say why in the PR's Gate section. A missed first fix, a scope surprise or an
unplanned gate repair raises review a step. The operator can require any check.

Review tiers set inspection depth on the same agreed slice, never severity or
new requirements; routes (`nora-work`) set which gates apply. A report shows
acceptance coverage and limits as well as findings; APPROVE alone is not one.
Validation follows the same judgement: run the focused checks that prove what
changed. Required hosted checks always apply, and an old exact-tree receipt is
never relabelled as a run on a new tree.

## Intent, evidence and completion

- GitHub Project owns roadmap; Linear owns execution. The **work brief**
  (`nora-dispatch/references/brief.md`) is the issue description, or the PR
  body on the light route. No separate plan, spec or PRD file is required.
- Read the current brief, project Intent and actual blockers before launch,
  intake and handoff, and record the revision read. Comments hold discussion
  and precise evidence, never the only current acceptance.
- After the operator approves a brief, its Done-when list is locked; expanding or
  softening it needs his amendment, and a reviewer suggestion is not a
  requirement. An invalid plan returns to propose; a defect returns to repair.
- Each item is met, unmet or unavailable against the real source or runtime,
  marked by the reviewer; missing evidence is unavailable, not missing
  implementation. Tests, review, merge and deployment prove different things.
- Close the outcome once every item is evidenced and no BLOCKER remains. A
  worker's stop point is not completion; a new session inherits the outcome
  and its holds.

## Choosing a worker

- Before each launch, read the current model catalogue, advertised options,
  credential availability (never the values), permissions, quota and operator
  constraints. Choose the least-cost worker that meets the capability and
  evidence the task needs.
- Catalogue presence proves neither competence nor authorisation; without
  prior evidence or an authorised probe, report Blocked with the unblock step.
- Pass provider, model and options explicitly, including effort, and read back
  what launched. Never silently lower effort or weaken requirements for quota;
  a fallback meets the same bar, and a changed model is a new, linked launch.
- On observed failure or ambiguity, reread the input and spec before another run.
- Model exclusions: never MiniMax M3; M3.1-Flash only when the operator names it.

**Reviewer independence.** A reviewer is a fresh session, neither the builder
nor the brief-author, whose vendor differs from every author's — an
orchestrator that commits is one; a same-vendor endpoint is not independence.
Default: GPT-6.1 Terra (Codex); an OpenAI author means Kimi K3.1 Pro
(kimi-code/k3.1-pro). No qualifying reviewer available means Blocked, with why.

**Execution briefs and handoffs** follow `nora-dispatch`: the worker owns its
slice, the orchestrator integration and acceptance. Reviews spawn no agents; a
prepared worktree keeps its base, never a second worktree or a restart on master.

## Bounded independent review

`REVIEW.md` owns review scope, finding quality and the return format;
`nora-review` the steps. All grounded findings stay in the receipt; non-blocking
ones go into one PR summary comment, at most six classes, and open no round.

| Verdict | Consequence |
|---|---|
| BLOCKER: the candidate materially fails agreed completion, or introduces reachable data loss, disclosure, crash or a weakened required gate | Fix and verify; non-blocking findings do not buy another round |
| FIX-IF-CHEAP: a real in-diff defect with bounded harm | Fix if it takes minutes within the slice; otherwise record a limitation; no new round |
| ISSUE: real but pre-existing outside agreed completion, or out of scope | Use the existing Linear work item or GitHub defect report; file only actionable work with no home (`nora-decision`); never blocks |
| DROP: hypothetical, contrived, style or unrelated | One-line disposition |

- Severity measures consequence and is the reviewer's; the orchestrator
  chooses the disposition and never re-grades it. Recurrence alone never makes
  a blocker; a material failure needs its scenario and impact.
- Verify each finding at the cited line with the right check (`nora-prove-fix`
  for production fixes); a clean review never replaces the gate or read-back.
- A round with no BLOCKER ends review once required evidence is in; waiting
  for evidence is not a round. One re-check covers
  `git diff <reviewed-sha>..HEAD`, unresolved findings and previously
  unavailable required evidence. Rounds continue while each closes a blocker
  class; one that closes none, or a repair-made class, goes to the operator.
- A class is one remedy fixing all its instances: first report names the
  structural remedy and every sibling instance in that round, then no new
  input shapes. A repair-induced finding names its origin; same class unless
  its remedy differs.
- Record review tier and triage in the PR's Gate section; never silently
  downgrade a blocker. Waiving a requirement is the operator's call.

## Artifact ownership

| Artifact | Owns |
|---|---|
| This policy | Assignment, capability, review and ownership rules |
| Design bundle | The high-level architecture and its rationale (`nora-design`); never a plan or a status report |
| ADR | One decision, its alternatives and what it cost |
| Linear issue | One work brief: intent, completion conditions and boundaries; native ownership/dependencies |
| Linear project | Optional bounded delivery grouping; not a second roadmap |
| GitHub Project roadmap item | Capability outcome, horizon and outcome-level progress; links Linear execution |
| GitHub issue | Defect/problem or community request reports and their disposition; not execution tasks |
| Run / review receipt | Actual provider, model, toolset, options, fit reason, run reference, input revision and evidence |

- Use native ownership and dependencies and link evidence; never copy plans,
  receipts or tracker state into parallel files. Roadmap outcomes, report
  disposition and execution status are linked, never mirrored
  ([roadmap conventions](docs/maintainers/conventions.md#roadmap)).
- Session completion, PR merge and child closure are not whole-outcome
  acceptance; a skill or MCP connection grants no unattended spending authority.

Task states follow the Linear convention (`docs/maintainers/conventions.md`
«Linear execution»); read the configured workflow names back rather than assuming them.

Artifact lifecycle — one owner and one end event each; retirement is mechanical, not a judgement:

| artifact | created when | ends when | ended by |
|---|---|---|---|
| Linear issue | an outcome is agreed | whole outcome evidenced | its owner |
| integration branch | the whole outcome starts | PR to master merges | GitHub auto-delete |
| handoff branch | a builder starts | its PR merges into its base | GitHub auto-delete |
| worktree | the first write needs one | branch merged or deleted | orchestrator, via `make gc` |
| review checkout | a read-only review needs one | its result is read | orchestrator, via `make gc` |
| evidence | a check runs | never | PR comment or CI artifact, never only `/tmp` |
| working log | a check runs | its run ends | `/tmp` |

- Resolve settles each artifact (`nora-land/reference.md` «Resolve»): work
  that will not merge is archived once with `make archive`, an annotated
  `archive/<branch>` tag verified on origin, and `make gc APPLY=1` runs at
  intake and after merge. Reviews use a detached checkout and never push.
- A handoff PR's base is the integration branch or the previous handoff's
  branch; on merge GitHub deletes the head and retargets the PRs stacked on it.
  Repairs land on the open handoff's branch, or a new handoff off the current base.

## Interchange shape

The optional `.agents/orchestration.schema.json` interchange describes policy,
task and run records for the consumers that use it; it governs no live Linear
state and requires no record per stage. `kind` selects the shape, and it checks
shape only: links, competence, independence and acceptance are read back.
References are absolute URIs, with immutable revisions where content can change.
`tests/architecture/test_orchestration_contract.py` validates this policy and
the synthetic examples below, which resolve to no real evidence.

```yaml
inbox:
  kind: task
  status: Inbox
ready: &ready
  kind: task
  issue_ref: https://linear.app/example/issue/SYN-1
  status: Ready
  priority: P2
  execution_tier: scoped
  review_tier: light
  acceptance_ref: https://example.invalid/spec/revision-a#acceptance
running: &running
  <<: *ready
  status: Running
  run_ref: https://example.invalid/runs/build
review:
  <<: *running
  status: Review
  evidence_refs: [https://example.invalid/runs/build#checks]
done:
  <<: *ready
  status: Done
  evidence_refs: [https://example.invalid/acceptance/accepted-head]
blocked:
  kind: task
  issue_ref: https://linear.app/example/issue/SYN-1
  status: Blocked
  blocked_ref: https://example.invalid/tasks/1#reason-and-unblock
resolved_run:
  kind: run
  issue_ref: https://linear.app/example/issue/SYN-1
  run_ref: https://example.invalid/runs/build
  input_revision: synthetic-commit-a
  selection:
    provider: example-provider
    model: example-model
    toolset: [example-repository-tools]
    options: {example_effort: deliberate}
  selection_reason: Finished spec; bounded edit and focused-check capability observed.
  evidence_refs:
    - https://example.invalid/catalog/snapshot#capability-probe
    - https://example.invalid/contracts/revision-a
    - https://example.invalid/runs/build#head-base-checks-and-limits
```
