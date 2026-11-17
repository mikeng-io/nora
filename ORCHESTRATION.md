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

The operator-facing orchestrator owns the whole outcome across workers and
sessions. `nora-work` is the entry router; it loads the procedure for the next
action. This is a soft lifecycle: responsibilities, not mandatory documents or
phase gates. Combine or skip inapplicable/already-satisfied steps; honor explicit
operator exceptions and existing required checks/holds. Note material omissions
briefly in the work/PR, not in a stage ledger.

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

This is skill-guided reinforcement. Existing tools perform the operations;
there is no lifecycle engine, phase lock or automatic session wakeup. Scripts
are for useful repeated reads/writes, not for deciding scope or completion.

## Coordination and decisions

The operator owns intended outcomes, scope amendments and explicit exceptions.
The outcome orchestrator chooses units, workers, review lenses/tier and next
steps within that authority, and reconciles evidence before closing work.
Builders implement their assigned slice; reviewers judge it without adding
requirements or authorizing repairs. Required hosted checks remain applicable.

For shared designs, interfaces or migrations, read related active work and T3
threads before assigning writes; `nora-dispatch` owns that coordination procedure.
A thread link is context access, not shared memory or automatic synchronization.
One outcome owner integrates; one writer owns a shared seam at a time.

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
operator can require any check or review.

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
- Judge each obligation met, unmet or unavailable against the real source or
  runtime; missing evidence is unavailable, not missing implementation. Tests,
  review, merge and deployment prove different things: require what acceptance
  specifies.
- An invalid plan returns to scoping; a defect returns to bounded repair.
  Expanding scope needs an explicit amendment; a reviewer suggestion is not a
  requirement. Never soften an obligation to get a pass.
- Close the slice for its whole outcome once every obligation is evidenced
  and no BLOCKER remains, updating remaining scope and recording limitations
  without claiming them; reopen only on new evidence or a changed requirement.
- A worker's stop point is not the orchestrator's completion: after intake,
  reconcile the whole outcome; a new session inherits it and the holds.

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
nor the brief-author, whose vendor differs from every author's — an
orchestrator that commits is one; a same-vendor endpoint is not independence.
Default: GPT-6.1 Terra (Codex); an OpenAI author means Kimi K3.1 Pro
(kimi-code/k3.1-pro). No qualifying reviewer available means Blocked, with why.

**Execution briefs and handoffs** follow `nora-dispatch`. The worker owns its
slice; the orchestrator owns integration and acceptance. Reviews run on the
assigned base/head and spawn no agents; a prepared worktree keeps its base —
never a second worktree or a restart on master.

## Bounded independent review

`REVIEW.md` owns review scope, finding quality and the return format;
`nora-review` owns the steps. The orchestrator preserves all grounded findings
in the receipt; non-blocking findings go into one summary comment on the PR —
at most six classes grouped, linked — and never open a round.

| Verdict | Consequence |
|---|---|
| BLOCKER: the candidate materially fails agreed completion, or introduces reachable data loss, disclosure, crash or a weakened required gate | Fix and verify; non-blocking findings do not buy another round |
| FIX-IF-CHEAP: a real in-diff defect with bounded harm | Fix if it takes minutes within the slice; otherwise record a limitation; no new round |
| ISSUE: real but pre-existing outside agreed completion, or out of scope | Use the existing Linear work item or GitHub defect report; file only actionable work with no home (`nora-decision`); never blocks |
| DROP: hypothetical, contrived, style or unrelated | One-line disposition |

- Severity measures consequence; disposition determines action — preserve the
  source finding when triaging. Recurrence alone cannot raise an observation
  to a blocker; a material failure needs its scenario and impact.
- Verify each finding at the cited line with the right check (`nora-prove-fix`
  for production fixes); a clean review never replaces the gate or read-back.
- A round with no BLOCKER ends review once required evidence is in; waiting
  for evidence is not a round. A later round checks
  `git diff <reviewed-sha>..HEAD`, unresolved findings and previously
  unavailable required evidence, even when empty. Self-contained work
  converges in at most two rounds; past three, the orchestrator rereads the
  spec and asks the operator whether to redesign rather than run another.
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

Artifact lifecycle — one owner and one end event each; retirement is mechanical, not a judgement:

| artifact | created when | ends when | ended by |
|---|---|---|---|
| Linear issue | an outcome is agreed | whole outcome evidenced | its owner |
| integration branch | parent outcome starts | PR to master merges | GitHub auto-delete |
| handoff branch | a builder starts | its PR merges into its base | GitHub auto-delete |
| worktree | the first write needs one | branch merged or deleted | orchestrator, via `make gc` |
| review checkout | a read-only review needs one | its result is read | orchestrator, via settle and `make gc` |
| evidence | a check runs | never | PR comment or CI artifact, never only `/tmp` |
| working log | a check runs | its run ends | `/tmp` |

- Every finished handoff or outcome gets one settle record from the
  orchestrator — `make settle BRANCH=<b> OUTCOME=<o>`: `merged` (its PR
  merged), `archived` (`archive/<branch>` tag at the tip pushed to origin;
  dirty worktree state saved first) or `abandoned` (one-line reason; refused
  with commits not in `origin/master`). Never deletes; logs to the gc log.
- The orchestrator runs `make gc APPLY=1` at intake and after merge; reviews
  use a detached checkout and never push a branch.
- A handoff PR's base is the integration branch or the previous handoff's
  branch; on merge GitHub deletes the head and retargets the PRs stacked on it.
- Builder briefs never say to preserve branches or worktrees; repairs land on
  the open handoff's branch, or a new handoff PR off the current base.

## Interchange shape

The optional `.agents/orchestration.schema.json` interchange describes policy,
task and run records for consumers that use it; it does not govern live Linear
states or require agents to produce a record at each stage. The `kind` field
selects the shape. It checks shape only, not link resolution, worker
competence, independence or acceptance: read those back. Task metadata is a small
projection, not a tracker API payload. `issue_ref` accepts the owning Linear
work URL; the schema does not install a tracker integration. Unknown properties are rejected except in
run `options`. References are absolute URIs; use immutable revisions where content
can change. `tests/architecture/test_orchestration_contract.py` validates this
policy and the synthetic examples below, which resolve to no real evidence.

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
