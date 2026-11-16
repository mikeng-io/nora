# Reviewing Nora

Review answers: **does this candidate satisfy the agreed change, and what real
regression could it introduce?** It produces a scoped judgment and evidence,
not another design, implementation task list or permission to merge.

[ORCHESTRATION.md](ORCHESTRATION.md) owns tiers, independence and round limits.
[AGENTS.md](AGENTS.md) owns engineering constraints.
[nora-review](.agents/skills/nora-review/SKILL.md) is the operating procedure.
Automatic and interactive reviewers use this same contract; these instructions
shape review behavior, not machine enforcement or paid-review admission.

## Scope and inputs

The orchestrator supplies a short review brief in the launch message, not a
new document. An automatic reviewer without one derives and states the scope
from the diff and available requirements; missing intent remains a limitation.

```
Question   what this review must decide; change / repair re-check / acceptance audit
Basis      original outcome and completion conditions, approved amendments; source revision and base/head
Surface    changed behavior and affected callers; prior findings for a re-check
Lenses     relevant risks and the concrete question for each; tier is depth
Evidence   relevant receipts and allowed checks; what would disprove the claim
Exclude    unrelated areas, settled decisions and action/spending limits
```

Carry the same outcome, completion conditions and boundaries through every
repair review; include the relevant conditions in the brief, not just an archive
link or the latest findings. Findings do not replace acceptance. An authorized
amendment states what changed and which earlier conclusions need reassessment;
otherwise the anchor stays unchanged. A re-check names its subset; conditions
outside that subset are not newly reviewed or implicitly passed.

Select lenses from what changed and what it can break, not a fixed panel:

| lens | question and trigger |
|---|---|
| application | does the agreed behavior work for actual callers and failure paths? baseline for a code change |
| security/privacy | can a real input cross an auth, identity, disclosure or network boundary? when that boundary is touched |
| data/concurrency | can state be lost, duplicated or raced across retries, commits or cancellation? when state/lifetime changes |
| measurement/operations | does the check measure its claim, and does config/release reach the real consumer? when evidence or wiring changes |

For architecture/docs, use changed responsibilities and invariants as the basis.
One reviewer can cover several lenses; select separate specialists only when
capability or scope warrants it. A title such as "security reviewer" alone is
not a brief. Keep required risk floors from ORCHESTRATION.md.

Read surrounding code/docs as needed to trace the affected behavior. The focus
does not suppress a material regression caused by the candidate outside a named
file/lens, but does not authorize repairing or auditing unrelated old defects.
A repair re-check covers the previous repair delta, affected paths and unresolved
findings, not a fresh full audit. Consult settled decisions before reopening one;
cite new evidence. Stay read-only; no fixes, agents, approvals, merge or deploy
unless separately assigned.

## Findings we want

- Admit a defect only with **basis, reachability and consequence**: the violated
  requirement/contract/trust boundary; an actual caller, supported input/state
  or credible attacker entry; and concrete harm. Show whether this candidate
  introduced it or left an original completion condition unmet. Name that
  condition or the existing contract/trust boundary the candidate regresses;
  a proposed new capability is not a repair requirement. NONE is valid.
- An extreme input is not a defect merely because a direct function call can
  construct it. Check real entry validation, supported config and lifetime.
  Rare concurrency or adversarial cases still count when reachable across a
  real boundary. An unverified premise is a hypothesis: name the smallest
  discriminating check, not a speculative guard or test suite.
- Separate **severity** (impact/urgency) from **disposition** (what happens next).
  P1 denotes material correctness, safety or availability failure; P2 denotes
  bounded impact. Use BLOCKER / FIX-IF-CHEAP / ISSUE / DROP per the policy.
  Recurrence, confidence or repair effort does not raise severity. Preserve an
  external finding's original rating when triaging; P2 harvest buys no new round.
- One remedy means one defect class. On its first report, inspect siblings in
  the affected flow and group the instances. Keep an unresolved class open;
  do not emit new findings for new inputs with the same remedy. Distinct fixes
  remain separate. Repair-induced defects name the earlier finding and why
  their class differs.
- For a behavioral claim, check observable behavior. A regex over source,
  AST check or green test count is not proof the real path works. Report a
  false-PASS proxy at the boundary it weakens; do not spend rounds enumerating
  evasions of that same proxy. Source-shape checks are valid for source-shape
  claims. Pre-existing defects outside agreed completion are separate harvest.
- Exclude taste, speculative guards, duplicate tests and unrequested redesign.
  New tests must catch an admitted in-scope defect or prove an existing condition;
  missing evidence is unavailable, not proof of missing code.

## Return and convergence

Lead with APPROVE or REQUEST CHANGES for the reviewed scope, then base/head,
review type/tier and actual reviewer vendor/independence where required.

Return only:

1. **Coverage:** each completion condition met / unmet / unavailable, with a
   compact evidence pointer; group conditions only when their results match.
2. **Findings:** severity and disposition, file:line, trigger → harm, failed C,
   evidence, smallest remedy, and introduced / pre-existing / repair-induced.
3. **Limits:** what was not inspected or executed and any required evidence gap.
   An incomplete review lists the specific unanswered conditions and why; its
   completion answers those questions, not a restarted broad audit.

REQUEST CHANGES means a supported blocker or a required review-evidence gap;
identify which. APPROVE is not task completion, green CI, merge or deployment.
Finish the assigned coverage even after finding a defect; do not manufacture
findings to fill the report. With no BLOCKER, summarize non-blocking harvest
once and stop; required evidence gaps remain unresolved under the policy.
The orchestrator owns triage and any next round.
Devin Review is supplemental; Fusion's internal review alone is not independent
review. Pure non-runtime docs and ordinary MCP client config need no automatic
code review; explicit review requests and mixed/runtime changes follow the tier.
