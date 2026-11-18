# Reviewing Nora

Review decides whether a candidate satisfies the agreed change and which real regressions it could introduce. Return a scoped judgment and evidence.
[ORCHESTRATION.md](ORCHESTRATION.md) owns tiers, independence and convergence. [AGENTS.md](AGENTS.md) owns engineering constraints. [nora-review](.agents/skills/nora-review/SKILL.md) owns the procedure.
Automatic and interactive reviewers follow this contract for review behavior. Machine enforcement and paid-review admission have separate owners.

## Scope and inputs

The orchestrator puts the review brief in the launch message. An automatic reviewer without a brief states the scope implied by the diff and available requirements and identifies missing intent as a limit.
The brief states the review question and type (change, repair re-check, acceptance audit), original outcome and completion conditions, approved amendments, source revision and base/head.
It names affected callers, prior findings for a re-check, concrete risk questions, allowed checks and spending, evidence receipts and disproof, settled decisions and unrelated areas outside scope.
Carry the same outcome, completion conditions and boundaries through every repair. Include the relevant conditions in the brief itself and keep findings separate from acceptance.
An authorized amendment names what changed and which conclusions need reassessment. A re-check names its subset. Coverage outside it retains its earlier state.

Choose lenses by what the change can break and use the required risk floors. Review application behavior for real callers and failure paths on code changes.
Review auth, identity, disclosure and network reachability when those boundaries change. Review loss, duplication or races across retries, commits and cancellation when state or lifetime changes.
Review whether evidence measures its claim and whether configuration and releases reach their consumers when wiring or measurement changes. For architecture and docs, use affected responsibilities and invariants.
One reviewer may cover several lenses. Use specialists if scope or capability requires it. A lens title alone cannot specify the assignment.

Trace affected behavior through surrounding code and docs, including candidate-caused material regressions outside a named file or lens. Keep unrelated old defects outside the audit and repair.
A repair re-check covers the repair delta, affected paths and unresolved findings. Consult settled decisions and cite new evidence before reopening one.
Stay read-only. Fixes, agents, approvals, merge and deploy require a separate assignment.

## Findings

- Admit a defect with basis, reachability and consequence. Cite the violated requirement, contract or trust boundary, a real caller, supported state or credible attacker entry, and concrete harm.
- Identify the original completion condition still unmet or the existing behavior the candidate regresses. New capabilities, taste, speculative guards, duplicate tests and unrequested redesign stay outside repair.
- Check actual validation, configuration and lifetime before calling unusual input reachable. Rare races and adversarial cases count across real boundaries. For an unverified premise give the smallest deciding check.
- Severity measures impact and urgency. P1 covers material correctness, safety or availability failures. P2 covers bounded impact. Recurrence, confidence and repair effort leave severity unchanged.
- Disposition follows policy (BLOCKER / FIX-IF-CHEAP / ISSUE / DROP). Preserve an external finding's original rating. P2 harvest buys no new round.
- Report one class per structural remedy and name its sibling cases in the first round. Keep unresolved classes open and stop adding input shapes. Identify a repair-induced defect and keep it in the same class when its remedy matches.
- Measure behavioral claims through observable behavior. Use source inspection for source properties. Identify false passes at the weakened boundary and keep evasions of the same proxy in the same class.
- Require a test only to catch an admitted in-scope defect or prove an existing condition. Mark missing evidence unavailable. Harvest pre-existing defects outside the agreed completion separately. NONE is a valid finding set.

## Return and convergence

Lead with APPROVE or REQUEST CHANGES for the reviewed scope. Name base/head, review type and tier, and actual reviewer vendor and independence when required.
Return only the following:

1. **Coverage** marks each completion condition met / unmet / unavailable with an evidence pointer. Group conditions with the same result.
2. **Findings** give severity, disposition, file and line, trigger and harm, failed C, evidence, smallest remedy, and whether introduced, pre-existing or repair-induced.
3. **Limits** name unchecked work and required evidence gaps. An incomplete review lists specific unanswered conditions and why. Its continuation answers those conditions.

REQUEST CHANGES identifies a supported blocker or a required review-evidence gap. APPROVE covers the assigned review scope. Task completion, CI, merge and deployment have their own evidence.
Finish assigned coverage even after finding a defect. A round with no BLOCKER ends review once required evidence is in. Keep required gaps unresolved and collect non-blocking harvest in one PR comment.
The reviewer owns severity and the orchestrator owns disposition. Devin Review is supplemental. Fusion's internal review alone cannot establish independence.
Pure non-runtime docs and ordinary MCP client configuration require no automatic code review. Explicit review requests and mixed or runtime changes follow the tier.
