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
      - Orchestrator records the applicable review row, green final gate and read-back.
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

This is the canonical policy for agents **building Nora**, not Nora's runtime.
The operator's ruling of 2026-10-27 replaces fixed positive vendor/model routing.
The schema and examples below describe an interoperable contract. The
[engineering controller](docs/maintainers/github.md#engineering-admission-pilot)
adds default-off owner-admitted CI sessions and a review candidate selector;
general worker dispatch, automatic paid intake and live board enforcement remain
unbuilt. There is no checked-in task database.
The broader evaluation remains open in [issue #277](https://github.com/mikeng-io/nora/issues/277).
The [Project](https://github.com/users/mikeng-io/projects/8) is a scheduling view;
adopting this file does not update its fields or launch work.

## Capability and review requirements

The frontmatter owns tier capabilities and required evidence. Both catalogues
are cumulative in their listed order: a tier adds to every preceding tier.
Execution tiers describe the work; review tiers describe independent verification.
Neither prescribes a provider, model, effort label or toolset. Select the lowest
execution tier that covers the task's ambiguity and the cost of a miss; critical
boundaries require critical capability even in a small diff.

Select review depth when scoping; confirm the highest applicable row against the
final diff after the gate is green:

| Change | Minimum review |
|---|---|
| No runtime effect: agent tooling or docs outside `nora/`; or a few dozen additive/off-by-default lines in one area | none |
| Tests; checks, CI, hooks or test selection; larger additive/off-by-default work; bounded findings naming a file and line | light |
| Live behavior on merge: defaults, prompts, doctrine, tool text, routing or model settings | full |
| Disclosure, security, locks/concurrency, hot-table migrations or new model spending | full, with critical execution capability |

None still requires the gate, orchestrator's diff read and read-back. The operator
can require or waive review explicitly. A missed first fix, scope surprise or
unplanned gate repair raises review one tier (full is the ceiling) and prompts
reassessment of execution capability. A light-review BLOCKER requires full review.

| Tier | Inspection and stopping boundary |
|---|---|
| none | Orchestrator checks the agreed slice, actual diff, applicable gate and required read-back; no independent reviewer is launched. |
| light | Independent reviewer checks the changed behavior, direct affected callers and named acceptance/regression paths. |
| full | Independent reviewer traces affected flows through inputs, outputs, failure paths and relevant trust/cost boundaries; this is not a repository-wide audit. |

All tiers apply the five Cs to the same agreed slice. Tier is inspection depth,
not finding severity or authority to add requirements. Review procedure:
`.agents/skills/nora-review/SKILL.md`. A report must show acceptance coverage and
unverified limits as well as findings; a tier name or APPROVE alone is insufficient.

## Intent, evidence and completion

Before editing, identify the requested outcome, scope/non-goals, acceptance and
evidence obligations in the existing issue/design or direct operator brief. Pin
the relevant revision; for direct work the PR records the brief. Do not create
an issue, task database or separate contract file merely to repeat it.
Carry that same intent through implementation, review and closure: comprehend
once, measure each obligation, serialize the result in existing artifacts.

Inspect the current implementation before adding work. Judge each obligation as
met, unmet or unavailable, with evidence bound to the relevant source/runtime.
Missing evidence is unavailable, not proof that implementation is missing.
Tests, review, merge and deployment prove different properties; require only
those specified by acceptance and applicable repository rules.

An invalid plan returns to scoping; an implementation defect returns to bounded
repair and verification. Record an explicit scope/acceptance amendment and its
authority before expanding work; neither a reviewer suggestion nor an unknown
in a brief is a new requirement. Do not soften an obligation to obtain a pass.

The orchestrator closes the slice when every required obligation is evidenced
and no BLOCKER remains, then stops adding implementation, checks or reviews.
Record remaining non-blocking limitations without claiming they are delivered.
Update the design's delivery coverage and the issue's remaining scope/state when
present. Only a whole-issue outcome closes that issue; a completed slice of a
larger design does not keep the slice perpetually unfinished. Reopen a settled
result only on new evidence or an explicit change of requirement.

## Resolve a worker at launch

The operator-facing session orchestrates: it scopes, briefs, admits, launches,
reads evidence, triages, lands and reports. The builder implements the brief;
it does not become its own reviewer. The orchestrator may do a light read-back
itself. Keep one committing agent per isolated worktree and reviews read-only
in their own checkout of the exact head. Request `pwd`, head and clean status;
read those states back instead of trusting the worker's claim.

Before **each launch**, read the current capability/model catalogue, advertised
options, credential/environment availability (without disclosing secrets),
permissions, quota and operator constraints. Choose the least-cost available
worker that meets the capability and evidence requirements for this task's
ambiguity and risk. Catalogue presence alone proves neither competence nor
authorization: use relevant prior evidence or a bounded authorized probe.
If evidence or permissions are insufficient, report unavailable/Blocked and
link the reason and unblock next step. Never guess availability or capability.

Pass the actual advertised provider/model and supported options explicitly,
including effort where supported; read back what launched. Do not silently
lower effort, translate unsupported options or weaken requirements because of
quota. A fallback must satisfy the same minimum capability, evidence and
independence requirements. Record why it fits, the actual selection, options,
toolset, input revision, run reference and evidence links in the run receipt.
Changing model or options requires a new launch with the prior report linked.
Escalate on observed failure or unresolved ambiguity; reread the input and spec
before spending another run. Explicit operator model exclusions still apply:
never MiniMax M3; M3.1-Flash only when the operator asks for it by name. Removing
fixed positive routing does not remove separate prohibitions or permissions.

A reviewer is fresh, neither the builder nor the brief-author, and uses a
different model vendor from the builder. A different provider endpoint for the
same vendor does not establish independence. Verify actual selection; if no
independent fit is available, block or obtain an explicit operator waiver.

A brief links the issue/design acceptance and input revisions, names allowed
paths/non-goals, worktree/base/branch, skills, named checks and reporting/stop
boundary. It carries task-specific deltas, not another full spec. Builders run
the named focused checks once and stop at the agreed handoff; the orchestrator
owns the final gate once on the final head. No extra handling, tests, reviewers
or scope expansion: report an adjacent concern instead. Review briefs include
claims, diff command and **do not spawn sub-agents; do the work yourself**.
For a T3 delegated worktree, start a build with
`git fetch origin && git switch -c <type>/<slug> origin/master` inside it;
do not create a second worktree. Reviews select the exact review head.

## Bounded independent review

The orchestrator triages findings, each naming a reachable path, harm, failed
five C and whether this diff caused it. Report at most six, most severe first;
NONE is valid. Mark pre-existing findings explicitly.

| Verdict | Consequence |
|---|---|
| BLOCKER: this diff causes reachable wrong output, data loss, disclosure, crash or weakened gate | Fix and verify; only this verdict buys another round |
| FIX-IF-CHEAP: real in-diff defect with bounded harm | Fix if minutes within the agreed slice; otherwise record a limitation or use its existing home; no new round |
| ISSUE: real but pre-existing/outside scope | Use the existing design/issue; file only actionable deferred work with no home (`nora-decision`); does not block this change |
| DROP: hypothetical, contrived, style or unrelated | One-line disposition |

Severity follows reachable consequences, not recurrence, confidence or repair
effort. One remedy fixing several instances makes them one defect class: search
the affected diff for siblings on the first report and group them. Do not buy
rounds by enumerating new inputs for an already reported class. A repair-induced
finding names its predecessor and explains the distinct failure; an unresolved
same-class BLOCKER remains blocking, not silently dropped as a duplicate.

The higher applicable verdict wins. Verify findings at the cited line and with
the appropriate check; production fixes follow `nora-prove-fix`. A clean review
does not replace the gate or live read-back when acceptance requires it.
A round with no BLOCKER ends review. **At most two rounds**: round two checks only
`git diff <reviewed-sha>..HEAD`, with stronger capability/evidence justified by
the failure. A remaining BLOCKER is fixed and proved, then the orchestrator
rereads the spec and reports; only the operator can authorize round three.
Record review tier and triage in the PR's Gate section.
Keep the source finding and evidence when adjudicating it; do not silently
downgrade a blocker. A disagreement needs a grounded disposition, and a waiver
of an applicable requirement remains the operator's decision.

## Artifact ownership and admission

| Artifact | Owns | Joins by reference |
|---|---|---|
| This root policy | Generic assignment, capabilities, review and ownership rules | Schema, skills and source guidance |
| Design / ADR | Architecture, rationale, requirements and acceptance; design lifecycle | Requirement-to-issue/PR/evidence delivery coverage, not a second live task queue |
| Issue | One bounded outcome, task-specific scope/non-goals/delta and acceptance, dependencies/prerequisites | Revisioned design acceptance sections instead of copied specs; run/review evidence |
| Project card | Issue reference, status, priority, required execution/review tiers and links | Acceptance, run, evidence or unblock record; no issue body or resolved worker assignment |
| Run / review receipt | Actual provider/model/toolset/options, fit justification, run reference, input revision and evidence | Issue/contract snapshot, catalogue/probe, exact source head/base and checks; review links the build it checks |

An issue-backed Project card is a view of that same issue, not another task body.
Keep assignees, milestones, relationships and Development links native in GitHub;
do not duplicate them as custom fields. The Project's execution tier replaces
vendor-valued Worker lane. The issue can state acceptance directly for a small
slice or point to revisioned design acceptance; “What's specified” is a pointer
plus delta, never a full specification copy. Design coverage links show which
requirements are delivered and which remain; task status stays on the Project,
task scope on the issue, measured results in receipts/PR checks. Keep receipts
in the run/evidence system, not committed run files or a new task database.

The orchestrator checks references and admission before changing status:

- **Inbox** may be incomplete; blank tiers mean unclassified, not review none.
- **Ready** has an issue with a bounded outcome/scope, resolvable acceptance,
  dependencies/environment/permissions available, priority and appropriate tiers.
- **Running** also links a real launched run; choosing a worker is insufficient.
- **Review** links the run and inspectable result/check evidence for acceptance.
- **Blocked** must link its owning issue and an issue/run entry stating the reason
  and unblock next step. Before admission, priority, tiers and acceptance may be
  omitted while unclassified because scope, permissions or dependencies are missing;
  do not fabricate Ready metadata.
- **Done** has acceptance evidence for the whole slice, including merge or live
  read-back when specified. Cancellation/supersession is recorded with its reason
  and archived/closed as not planned; it is never successful Done. Partial work
  references the parent issue without closing it.

## Shape and synthetic examples

[`.agents/orchestration.schema.json`](.agents/orchestration.schema.json) is one
JSON Schema Draft 2020-12. Its `$defs` own the canonical status, priority and
tier identifier vocabularies. The schema validates policy shape and allowed IDs;
`tests/architecture/test_orchestration_contract.py::test_schema_and_canonical_policy`
checks catalogue coverage/order, including omissions and duplicates.
Capabilities/evidence live only above.
The discriminator `kind` selects policy, task metadata or run record. Task
metadata is a small interchange projection, **not the GitHub API payload**.
Additional properties are rejected at each fixed object boundary; run `options`
alone permits provider-specific advertised keys. References are whitespace-free
absolute URIs, including section anchors; use immutable revision/snapshot links
where the content can change. `input_revision` identifies the immutable input
source; evidence links bind the issue/design/policy snapshot and review base.

Validation checks shapes and conditional required fields, not link resolution,
live execution, worker competence, independent identity, acceptance truth or
successful deployment. The orchestrator must read back those facts. The schema
does not itself enforce dispatch; the bounded controller checks its admitted
route. This allocation holds
unless measured delivery gaps require a revised contract; it does not authorize
automation or spending by itself.

The following are synthetic documentation examples, not tasks or run receipts.
YAML anchors abbreviate repeated scheduling metadata; URLs under example.invalid
and the example repository resolve to no claimed evidence.

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

Sources: [JSON Schema validation vocabulary](https://json-schema.org/draft/2020-12/json-schema-validation)
defines structural validation; [GitHub Projects best practices](https://docs.github.com/en/issues/planning-and-tracking-with-projects/learning-about-projects/best-practices-for-projects#have-a-single-source-of-truth)
supports keeping task information in its owning artifact. Landing procedures
remain in [nora-land](.agents/skills/nora-land/SKILL.md), issue conventions in
[maintainer conventions](docs/maintainers/conventions.md), and superseded routing
incidents in [the historical reference](.agents/skills/nora-land/reference.md).
