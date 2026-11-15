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

- **Do small changes yourself**: documentation, agent-skill wording, comments,
  one- or two-line fixes and bounded edits across a few files.
- **Delegate only for a reason**: a worker's capacity or capability outweighs
  the briefing cost and lost context, or the operator asks. File count alone is
  not a reason. Delegate through the session's own mechanism (a native
  subagent, `delegate_task` or the host's equivalent); the brief and read-back
  rules are the same.
- **Claude models are never a delegated builder or an independent reviewer.**
  A Claude orchestrator makes small changes directly and hands substantial
  implementation to a non-Claude builder.
- One committing agent per isolated worktree; reviews run read-only in their
  own checkout of the exact head. Read back `pwd`, head and clean status rather
  than trusting a worker's report.

## The lifecycle

Stages apply as a change needs them; a small one passes several in one sitting.

| stage | answers | owner | produces | exits when |
|---|---|---|---|---|
| **intake** | is this architecture or code, and where does it belong? | `nora-design` §1; `nora-decision` for issues | an issue, or a direct brief recorded in the PR | outcome, scope and non-goals are pinned to a revision |
| **design** (architecture changes only) | what is the system? | `nora-design` | a bundle edit, before or with the code | the bundle change is reviewed per its tier |
| **decide** | does this need a ruling on record? | `nora-decision` | a DEC row or ADR | numbered at merge |
| **dispatch** | how is it cut, briefed and assigned? | `nora-dispatch` | units, briefs, acceptance in the issue or the PR-recorded brief | every acceptance statement is covered by a unit |
| **build** | — | the builder, with `nora-land`, `nora-structure`, `nora-test`, `nora-tool`, `nora-runtime-skill` | commits on a branch | the brief's named checks ran once |
| **verify** | does it do what it claims? | `nora-prove-fix`, `nora-eval`, the gate in `nora-land` | evidence | each obligation is met, unmet or unavailable, with evidence |
| **review** | does it hold up to someone else? | `nora-review`, `REVIEW.md` | findings and a verdict | no BLOCKER, within two rounds |
| **land** | — | `nora-land` | a merge commit | on master and read back |
| **release and deploy** | — | `nora-release`, `nora-deploy`, `nora-readback` | a release; the live service | released; live state read back when acceptance requires it |
| **close and learn** | is the outcome delivered, and did the process itself fail anywhere? | the orchestrator | the issue updated; the owning skill or root document edited | obligations reconciled; a process failure (an unreadable brief, a skipped rehearsal, a rule nobody loaded) fixed in its owner in the same arc |

## Execution tier

Pick the lowest tier that covers the ambiguity and the cost of a miss; disclosure,
security, data loss, concurrency, hot-table migrations and model spending need
critical capability even in a small diff.

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

Tiers (frontmatter) are inspection depth on the same agreed slice, never
severity or authority to add requirements; every tier applies the five Cs.
Procedure: `.agents/skills/nora-review/SKILL.md`. A report shows acceptance
coverage and unverified limits as well as findings; a tier name or APPROVE
alone is not a report.

Validation follows the same judgement: run the focused checks that prove what
changed; docs-only changes need a diff read and a check of the changed property,
not a local gate. Required hosted checks and branch rules always apply. Never
relabel an old exact-tree receipt as a run on a new tree; a moving master only
needs checks on what the upstream change touches.

## Intent, evidence and completion

- Pin the outcome, scope, non-goals and acceptance to the issue or the
  operator's brief, recorded in the PR for direct work, before editing; the
  design holds the invariants they link to. Never
  create an issue or contract file only to repeat it.
- Agreement to a concrete proposed fix means implement it. File only work
  explicitly deferred from this slice, after checking for an existing home.
- Judge each obligation met, unmet or unavailable against the real source or
  runtime; missing evidence is unavailable, not missing implementation. Tests,
  review, merge and deployment prove different things: require what acceptance
  specifies.
- An invalid plan returns to scoping; a defect returns to bounded repair.
  Expanding scope needs an explicit amendment; a reviewer suggestion is not a
  requirement. Never soften an obligation to get a pass.
- Close the slice when every obligation is evidenced and no BLOCKER remains,
  then stop. Record limitations without claiming them delivered; update the
  issue's remaining scope; close an issue only for its whole outcome. Reopen a
  settled result only on new evidence or a changed requirement.

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

**A brief** follows `nora-dispatch`: one bounded outcome a reviewer can read in
one sitting, in complete sentences a fresh agent can follow, linking what the
repo holds instead of pasting or abbreviating it. It names acceptance (in the
issue or the PR-recorded brief, never only a temporary file), input revisions, allowed
paths, non-goals, worktree/base/branch, skills, named checks and the stop
point. Builders run the named checks once and stop at the handoff; the
orchestrator owns acceptance. No extra handling, tests, reviewers or scope:
report an adjacent concern instead.
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
| ISSUE: real but pre-existing or out of scope | Use the existing issue; file only actionable work with no home (`nora-decision`); never blocks |
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
| Design bundle | The high-level architecture and its rationale (`nora-design`); never a plan or a status report |
| ADR | One decision, its alternatives and what it cost |
| Issue | One bounded outcome: scope, non-goals, acceptance, dependencies |
| Roadmap card | A capability outcome and its horizon; never an execution queue |
| Run / review receipt | Actual provider, model, toolset, options, fit reason, run reference, input revision and evidence |

- An issue links the design it builds and owns its scope and acceptance, never
  a copy of the design; a Project card is a view of its issue. Assignees,
  milestones, relationships and Development links stay native in GitHub; tiers
  live with the issue's readiness and run brief; receipts live in the run
  system, never as committed run files or a task database.

Task states describe issue and run readiness, not Project fields:
**Inbox** may be incomplete (blank tiers mean unclassified); **Ready** has a
bounded outcome, resolvable acceptance, available dependencies and
permissions, a priority and tiers; **Running** links a real launched run;
**Review** links the run and inspectable evidence; **Blocked** links its issue
and the reason and unblock step, never fabricated Ready metadata; **Done** has
acceptance evidence for the whole slice, including merge or live read-back when
specified. Cancelled or superseded work closes as not planned with its reason;
partial work references its parent issue without closing it.

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
