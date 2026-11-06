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
The frontmatter defines the execution and review tiers; each tier adds to the
ones before it. Tiers describe the work and its checking, never a provider,
model, effort label or toolset.

## The orchestrator

The orchestrator is whichever agent holds the operator-facing session: Claude
Code, Codex, Devin or any other coding agent. It owns the outcome: scope, do or
delegate, reconcile evidence, triage, land and report.

- **Do small changes yourself.** Documentation, agent-skill wording, comments,
  one- or two-line fixes and bounded edits across a few files are done in the
  session, never delegated.
- **Delegate only for a reason**: a separate worker brings capacity or
  capability that outweighs the briefing cost and lost context, or the operator
  asks. File count alone is not a reason.
- **Claude models are never a delegated builder or an independent reviewer.**
  A Claude orchestrator still makes small changes directly; it hands
  substantial implementation to a non-Claude builder.
- Delegate through the session's own mechanism: a native subagent,
  `delegate_task` or the host's equivalent. The brief and read-back rules are
  the same for each.
- One committing agent per isolated worktree; reviews run read-only in their own
  checkout of the exact head. Read back `pwd`, head and clean status rather than
  trusting a worker's report.

## Execution tier

Pick the lowest tier that covers the task's ambiguity and the cost of a miss.
Disclosure, security, data loss, concurrency, hot-table migrations and model
spending need critical capability even in a small diff.

## Review and validation are adaptive

The orchestrator chooses review depth and checks from what the change can
break, not from its type or size alone. Answer three questions: what changed,
what could break, and what evidence shows it did not. Then spend the least that
answers them.

| Change | Usual review |
|---|---|
| Docs, agent-skill wording, comments, images; one- or two-line fixes whose effect is obvious; small off-by-default additions | none: the orchestrator reads the diff and checks the changed property |
| Tests, CI, hooks, test selection; larger additive work; a fix in shared code | light, when the orchestrator cannot settle it alone with confidence |
| Live behaviour on merge: defaults, prompts, doctrine, tool text, routing, model settings | full |
| Disclosure, security, locks/concurrency, hot-table migrations, new model spending | full, with critical execution capability |

The last row is the only fixed minimum. Everywhere else, move a step up or down
on judgement and say why in the PR's Gate section. A missed first fix, a scope
surprise or an unplanned gate repair is evidence to raise review a step. The
operator can require or waive any check or review.

- **none**: the orchestrator checks the agreed slice, the diff, the chosen
  evidence and any required read-back. No reviewer is launched.
- **light**: an independent reviewer checks the changed behaviour, its direct
  callers and the named acceptance and regression paths.
- **full**: an independent reviewer traces the affected flows through inputs,
  outputs, failure paths and trust/cost boundaries. Not a repository-wide audit.

Every tier applies the five Cs to the same agreed slice. Tier is inspection
depth, not severity and not authority to add requirements. Procedure:
`.agents/skills/nora-review/SKILL.md`. A review report shows acceptance coverage
and unverified limits as well as findings; a tier name or APPROVE alone is not a
report.

Validation follows the same judgement. Run the focused checks that prove what
changed; docs-only changes need a diff read and a check of the changed property
(content, links, references), not a local gate. Required hosted checks and
branch rules always apply. Never relabel an old exact-tree receipt as a run on a
new tree. A moving master alone is no reason to repeat a gate or review: check
only what the upstream change touches.

## Intent, evidence and completion

- Before editing, find the requested outcome, scope and non-goals, acceptance
  and evidence obligations in the existing issue, design or operator brief, and
  pin its revision. For direct work the PR records the brief. Do not create an
  issue, task database or contract file only to repeat it.
- Agreement to a concrete proposed fix means implement it; filing an issue is
  not completion. File only actionable work explicitly deferred from this slice,
  after checking for an existing home.
- Read the current implementation before adding work. Judge each obligation
  met, unmet or unavailable, with evidence bound to the source or runtime.
  Missing evidence is unavailable, not proof the implementation is missing.
- Tests, review, merge and deployment prove different things; require only what
  acceptance and the repository rules specify.
- An invalid plan goes back to scoping; an implementation defect goes back to
  bounded repair. Expanding scope needs an explicit amendment and its authority;
  a reviewer suggestion or an unknown in a brief is not a requirement. Never
  soften an obligation to get a pass.
- Close the slice when every required obligation is evidenced and no BLOCKER
  remains; then stop adding work, checks or reviews. Record remaining
  limitations without claiming them delivered. Update the design's delivery
  coverage and the issue's remaining scope when present. Only a whole-issue
  outcome closes the issue. Reopen a settled result only on new evidence or a
  changed requirement.

## Choosing a worker

- Before each launch, read the current model catalogue, advertised options,
  credential availability (never the values), permissions, quota and operator
  constraints. Choose the least-cost worker that meets the capability and
  evidence the task needs.
- Catalogue presence proves neither competence nor authorisation; rely on prior
  evidence or a bounded authorised probe. If either is missing, report Blocked
  with the reason and the unblock step. Never guess.
- Pass the advertised provider, model and options explicitly, including effort,
  and read back what launched. Do not silently lower effort, translate
  unsupported options or weaken requirements because of quota. A fallback meets
  the same capability, evidence and independence bar. Changing model or options
  is a new launch, linked to the previous report.
- Escalate on observed failure or unresolved ambiguity; reread the input and
  spec before spending another run.
- Model exclusions: never MiniMax M3; M3.1-Flash only when the operator names it.

**Reviewer independence.** A reviewer is a fresh session, neither the builder
nor the brief-author, on a different model vendor from the builder. A different
endpoint for the same vendor is not independence. An orchestrator that
implements directly is the builder. If no independent reviewer is available,
block or get the operator's waiver.

**A brief** links the acceptance and input revisions and names allowed paths,
non-goals, worktree/base/branch, skills, named checks and the stop point. It
carries task-specific deltas, not another full spec. Builders run the named
checks once and stop at the handoff; the orchestrator owns final acceptance. No
extra handling, tests, reviewers or scope: report an adjacent concern instead.
Review briefs include the claims, the diff command and **do not spawn
sub-agents; do the work yourself**. In a T3 delegated worktree start with
`git fetch origin && git switch -c <type>/<slug> origin/master`; never create a
second worktree. Reviews check out the exact review head.

## Bounded independent review

Reviewers report every finding they can ground; NONE is valid. Each names a
reachable path, the harm, the failed C and whether this diff caused it, and
pre-existing findings are marked. The orchestrator triages them and carries at
most six into the PR, most severe first.

| Verdict | Consequence |
|---|---|
| BLOCKER: this diff causes reachable wrong output, data loss, disclosure, crash or a weakened gate | Fix and verify; only this verdict buys another round |
| FIX-IF-CHEAP: a real in-diff defect with bounded harm | Fix if it takes minutes within the slice; otherwise record a limitation; no new round |
| ISSUE: real but pre-existing or out of scope | Use the existing design or issue; file only actionable work with no home (`nora-decision`); never blocks |
| DROP: hypothetical, contrived, style or unrelated | One-line disposition |

- Severity follows reachable consequence, not recurrence, confidence or repair
  effort. One remedy for several instances is one defect class: group siblings
  on the first report, and do not buy rounds by finding new inputs for a known
  class. A repair-induced finding names its predecessor and its distinct
  failure.
- Verify each finding at the cited line with the right check; production fixes
  follow `nora-prove-fix`. A clean review does not replace the gate or a
  required live read-back.
- A round with no BLOCKER ends review. **At most two rounds**; round two checks
  only `git diff <reviewed-sha>..HEAD`. A BLOCKER left after round two is fixed
  and proved, then the orchestrator rereads the spec and reports; only the
  operator authorises a third round.
- Record review tier and triage in the PR's Gate section. Keep the source
  finding when adjudicating it; never silently downgrade a blocker. Waiving an
  applicable requirement is the operator's decision.

## Artifact ownership

| Artifact | Owns |
|---|---|
| This policy | Assignment, capability, review and ownership rules |
| Design / ADR | Architecture, rationale, requirements, acceptance and delivery coverage |
| Issue | One bounded outcome: scope, non-goals, acceptance delta, dependencies |
| Roadmap card | A capability outcome and its horizon; never an execution queue |
| Run / review receipt | Actual provider, model, toolset, options, fit reason, run reference, input revision and evidence |

- An issue points to revisioned design acceptance and states only its delta;
  never copy the spec. A Project card is a view of its issue, not another body.
- Keep assignees, milestones, relationships and Development links native in
  GitHub. Tiers are recorded with the issue's readiness and run brief.
- Receipts live in the run/evidence system, never as committed run files or a
  task database.

Task states describe issue and run readiness, not Project fields:

- **Inbox** may be incomplete; blank tiers mean unclassified, not review none.
- **Ready**: a bounded outcome and scope, resolvable acceptance, available
  dependencies, environment and permissions, a priority and tiers.
- **Running** links a real launched run; choosing a worker is not enough.
- **Review** links the run and inspectable evidence for acceptance.
- **Blocked** links its issue and an entry stating the reason and the unblock
  step. Never fabricate Ready metadata for it.
- **Done** has acceptance evidence for the whole slice, including merge or live
  read-back when specified. Cancelled or superseded work is closed as not
  planned with its reason, never Done. Partial work references the parent issue
  without closing it.

## Interchange shape

`.agents/orchestration.schema.json` (JSON Schema 2020-12) owns the status,
priority and tier vocabularies and validates policy, task and run shapes; the
`kind` field selects which. It checks shape only, not link resolution, worker
competence, independence or acceptance: read those back. Task metadata is a small
projection, not the GitHub API payload. Unknown properties are rejected except in
run `options`. References are absolute URIs; use immutable revisions where content
can change. `tests/architecture/test_orchestration_contract.py` validates this
policy and the synthetic examples below, which resolve to no real evidence.

```yaml
inbox:
  kind: task
  status: Inbox
ready: &ready
  kind: task
  issue_ref: https://github.com/example/nora-synthetic/issues/1
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
  issue_ref: https://github.com/example/nora-synthetic/issues/1
  status: Blocked
  blocked_ref: https://example.invalid/tasks/1#reason-and-unblock
resolved_run:
  kind: run
  issue_ref: https://github.com/example/nora-synthetic/issues/1
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
