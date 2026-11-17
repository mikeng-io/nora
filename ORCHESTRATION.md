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

Policy for agents building Nora, not her runtime. Frontmatter tiers are cumulative
work/checking depth, never providers, models, effort or toolsets.

## The orchestrator

The operator-facing agent owns the outcome: scope, do or delegate, evidence,
triage, delivery and reporting, regardless of its provider.

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

The orchestrator owns the whole outcome across workers/sessions. `nora-work`
routes the next procedure. This soft lifecycle defines responsibilities, not
mandatory documents or phase gates. Combine or skip satisfied/inapplicable steps,
retain operator exceptions and required checks/holds, and note material omissions
in the work/PR rather than a stage ledger.

An implementation request continues from design through execution, verification
and agreed delivery; finishing a bundle is not completion. A design-only request
stops at its agreed design result. Design agreement within an implementation task
allows the authorized next work; it does not grant new spending or deployment.

| stage | responsibility | procedure | sufficient result |
|---|---|---|---|
| **triage** | understand the request and current position | `nora-work` | outcome, authority and missing context understood |
| **shape** | define intent, completion and boundaries | `nora-dispatch`; `nora-design` for architecture; `nora-decision` for records | one current work brief, with design links as needed |
| **execute** | do or delegate the next bounded work | `nora-dispatch`, `nora-land`; specialist skills as needed | inspectable work and remaining obligations |
| **verify** | compare the result with the brief | `nora-test`, `nora-prove-fix`, `nora-eval`, `nora-review`, `nora-readback` as applicable | each required result met, unmet or unavailable with evidence |
| **deliver** | land and deliver to the agreed boundary | `nora-land`, `nora-release`, `nora-deploy` as applicable | merge/publication/live evidence only where required |
| **resolve or resume** | close the outcome or preserve its continuation | `nora-dispatch` | evidenced completion, or a short handoff with remaining work |

Skills guide existing tools, not a lifecycle engine, phase lock or automatic
wakeup. Scripts repeat useful operations; they do not decide scope/completion.

## Coordination and decisions

The operator owns intended outcomes, scope amendments and explicit exceptions.
The outcome orchestrator chooses units, workers, review lenses/tier and next
steps within that authority, and reconciles evidence before closing work.
Builders implement their assigned slice; reviewers judge it without adding
requirements or authorizing repairs. Required hosted checks remain applicable.

Before assigning shared design/interface/migration writes, read related active
work/threads and coordinate through `nora-dispatch`. Links are context access,
not synchronization: one outcome owner integrates; one writer owns each seam.
## Execution tier
Pick the lowest tier that covers the ambiguity and the cost of a miss; disclosure,
security, data loss, concurrency, hot-table migrations and model spending need
critical capability even in a small diff.
## Review and validation are adaptive
Choose review/check depth by what changed, what could break and what evidence
answers it, not type or size alone; spend only what resolves those questions.

| Change | Usual review |
|---|---|
| Docs, agent-skill wording, comments, images; one- or two-line fixes whose effect is obvious; small off-by-default additions | none: the orchestrator reads the diff and checks the changed property |
| Tests, CI, hooks, test selection; larger additive work; a fix in shared code | light, when the orchestrator cannot settle it alone with confidence |
| Live behaviour on merge: defaults, prompts, doctrine, tool text, routing, model settings | full |
| Disclosure, security, locks/concurrency, hot-table migrations, new model spending | full, with critical execution capability |

Only the last row fixes a minimum; otherwise adjust by judgement, explaining
why in the PR Gate. Missed fixes, scope surprises or unplanned gate repairs
warrant raising review. The operator may require or waive checks/reviews.

Every tier applies the five Cs at its inspection depth, without changing scope
or severity. `nora-review` owns the procedure; reports include acceptance
coverage, findings and unverified limits, never just a tier name or APPROVE.

Validate the changed property with focused checks; docs-only work needs a diff
read/property check, not a local gate. Keep hosted/branch requirements. An old
exact-tree receipt is not a new run; check only affected upstream changes.

## Intent, evidence and completion

- GitHub Project owns roadmap; Linear owns execution. A **work brief** in its issue description
  combines intent, observable completion conditions and boundaries, with design
  links as needed. The model chooses implementation details; no separate plan,
  spec or PRD is required. Small direct work may use the PR-recorded brief.
- Read the current brief, parent intent and actual blockers before launch,
  intake and handoff; record the work link and the source revision or update time read.
  Keep the description concise and current. Comments hold discussion and
  precise evidence, not the only current acceptance or another live queue.
- Agreement to a concrete proposed fix means implement it. File only work
  explicitly deferred from this slice, after checking for an existing home.
- Judge obligations met/unmet/unavailable against actual source/runtime.
  Missing evidence is unavailable, not missing code. Tests, review, merge and
  deployment prove different things; require the specified acceptance.
- An invalid plan returns to scoping; a defect returns to bounded repair.
  Expanding scope needs an explicit amendment; a reviewer suggestion is not a
  requirement. Never soften an obligation to get a pass.
- Close a slice only with every obligation evidenced and no BLOCKER. Record
  limitations and remaining scope without claiming delivery; close work only
  for its whole outcome. Reopen settled results only on new evidence/requirements.
- A worker's stop point is not the orchestrator's completion condition. After
  intake, reconcile the whole outcome and continue authorized work; a new
  operator-facing session inherits that responsibility and the existing holds.
## Choosing a worker
- Before launch, read current catalogue/options, credential availability (not
  values), permissions, quota and operator limits. Choose the least-cost worker
  meeting the required capability/evidence.
- Catalogue presence proves neither competence nor authorisation; rely on prior
  evidence or a bounded authorised probe. If either is missing, report Blocked
  with the reason and the unblock step. Never guess.
- Pass advertised provider/model/options, including effort; read back the launch.
  Never silently lower effort, translate unsupported options or weaken quota-bound
  requirements. Fallbacks meet the same capability/evidence/independence bar.
  Model/option changes are new launches linked to the preceding report.
- Escalate on observed failure or unresolved ambiguity; reread the input and
  spec before spending another run.
- Model exclusions: never MiniMax M3; M3.1-Flash only when the operator names it.

**Reviewer independence.** A reviewer is a fresh session, neither the builder
nor the brief-author, on a different model vendor from the builder. A different
endpoint for the same vendor is not independence. An orchestrator that
implements directly is the builder. If no independent reviewer is available,
block or get the operator's waiver.

**Execution briefs/handoffs** follow `nora-dispatch`. Workers own their slice;
the orchestrator owns integration/final acceptance. Reviews keep assigned
base/head and spawn no agents. Prepared worktrees retain their base: no second
checkout or dependent restart from master.
## Bounded independent review
`REVIEW.md` owns review scope, finding quality and the return format;
`nora-review` owns the steps. The orchestrator preserves all grounded findings
in the review receipt, and groups at most six classes in the PR summary with
a link to the rest. Non-blocking findings are useful input, not new scope.

| Verdict | Consequence |
|---|---|
| BLOCKER: the candidate materially fails agreed completion, or introduces reachable data loss, disclosure, crash or a weakened required gate | Fix and verify; non-blocking findings do not buy another round |
| FIX-IF-CHEAP: a real in-diff defect with bounded harm | Fix if it takes minutes within the slice; otherwise record a limitation; no new round |
| ISSUE: real but pre-existing outside agreed completion, or out of scope | Use the existing Linear work item or GitHub defect report; file only actionable work with no home (`nora-decision`); never blocks |
| DROP: hypothetical, contrived, style or unrelated | One-line disposition |

- Severity measures consequence; disposition determines action. Preserve the
  source finding when triaging it. A low-severity observation cannot become a
  blocker merely by recurring; a material failure needs its scenario and impact.
- Verify each finding at the cited line with the right check; production fixes
  follow `nora-prove-fix`. A clean review does not replace the gate or a
  required live read-back.
- With no BLOCKER or required evidence gap, end review. **At most two rounds**;
  round two checks `git diff <reviewed-sha>..HEAD`, unresolved findings and
  previously unavailable required evidence, even when the diff is empty. Waiting
  for evidence is not a round; a requested re-check consumes the next round.
  A BLOCKER or required evidence gap left after round two is addressed within scope
  and measured, then the orchestrator rereads the spec and reports; only the
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
| Linear issue | One work brief: intent, completion conditions and boundaries; native ownership/dependencies |
| Linear project | Optional bounded delivery grouping; not a second roadmap |
| GitHub Project roadmap item | Capability outcome, horizon and outcome-level progress; links Linear execution |
| GitHub issue | Defect/problem or community request reports and their disposition; not execution tasks |
| Run / review receipt | Actual provider, model, toolset, options, fit reason, run reference, input revision and evidence |

- Use native ownership/dependencies and link evidence; do not copy plans,
  receipts or tracker state into parallel files. Preserve existing work IDs,
  owners and holds until verified successors are linked during migration.
- Roadmap outcomes, report disposition and execution status are separate. Link
  them; do not mirror their descriptions or statuses. Roadmap changes follow
  [roadmap conventions](docs/maintainers/conventions.md#roadmap).
- AgentSession completion, PR merge and child closure are not whole-outcome
  acceptance. A skill or MCP connection does not install unattended execution
  or grant spending authority.

Task states describe work and run readiness, not agent session states:
**Inbox** may be incomplete (blank tiers mean unclassified); **Ready** has a
bounded outcome, resolvable acceptance, available dependencies and
permissions, a priority and tiers; **Running** links a real launched run;
**Review** links the run and inspectable evidence; **Blocked** links its work item
and the reason and unblock step, never fabricated Ready metadata; **Done** has
acceptance evidence for the whole slice, including merge or live read-back when
specified. Cancelled or superseded work closes as not planned with its reason;
partial work references its parent without closing it. Linear's configured
workflow names are read back rather than assumed to match these labels.
## Interchange shape
Optional `.agents/orchestration.schema.json` checks `kind`-selected policy/task/run
shapes, not live states, links, capability, independence or acceptance. It requires
no stage records or tracker integration. Task metadata is a projection, not an API
payload; `issue_ref` accepts Linear URLs. Unknown properties except run `options`
are rejected. Use absolute URIs and immutable revisions for changing content.
The architecture contract checks this policy and synthetic, non-evidentiary examples.

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
