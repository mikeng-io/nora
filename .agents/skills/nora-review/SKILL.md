---
name: nora-review
description: Review a Nora work brief and plan before dispatch, review an agreed change and mark its Done-when items, re-check a repair delta, or audit scope at the assigned tier. Return coverage, grounded findings and limits; no implementation or unrequested repository audit.
---

# Nora review

Read [REVIEW.md](../../../REVIEW.md) for the review contract and
[ORCHESTRATION.md](../../../ORCHESTRATION.md#bounded-independent-review) for tier,
independence, dispositions and convergence. This skill is the procedure.
A brief review follows [references/brief-review.md](references/brief-review.md)
instead of steps 2–3; item marking, severity and follow-through are there too.
A panel of reviewers (tiers, re-check checks, brainstorm packets, synthesis)
follows [references/council.md](references/council.md).

1. **Identify the review.** Use REVIEW.md's brief: confirm the question, basis,
   surface, relevant lenses, evidence and exclusions. Read its source requirements;
   record the review type, tier and base/head. Recover the original outcome and
   approved amendments before reading prior findings; retain this basis on
   re-checks and name the selected subset. State any inferred scope.
   Check: the requested result is understandable without deriving intent from
   the candidate. On the standard and full routes, the brief and plan passed
   brief review with no BLOCKER and the candidate arrives with its execution
   receipt. Return a candidate with missing gate evidence unreviewed. On light,
   the PR body carries a brief. On every route, confirm the recorded route against the ordered triage rule in
   `nora-work/references/lifecycle.md`; a lower route is a finding.
   Missing material requirements return to scoping.
2. **Inspect the candidate.** Verify checkout/head/status; read
   `git diff <base>...<head>`. A repair re-check uses
   `git diff <previously-reviewed-head>..<head>` plus unresolved findings and
   previously unavailable required evidence, including an unchanged candidate.
   Check: the actual candidate matches the assignment; preserve unrelated work.
3. **Test the claims.** Trace affected callers and failure paths to the tier's
   depth. Read actual checks/results and model inputs or runtime evidence only
   where the claim needs them. Run only necessary authorized focused checks;
   a refused test lease is unavailable, never bypassed.
   - For recurring database reads, check generated history-size scaling with
     fixed pending work, admission/commit boundaries, held reader elections,
     foreground progress and release on failure/cancellation.
   - For design-to-implementation audits, map each assigned promise to the
     actual entry, wiring, observed behavior and evidence head. Distinguish
     source presence from exercised behavior; mark unknowns without inventing
     new implementation work. Include connected bundle contracts when affected.
   - For architecture/docs, inspect the assigned subject's owner and changed
     cross-references; assess contradictions or misplaced responsibilities,
     not unrelated style or a new architecture of the reviewer's own.
   Check: every required condition has met/unmet/unavailable evidence and limits;
   each defect has a grounded basis, reachable trigger and consequence. Inspect
   relevant siblings before first reporting a class; a reported class's remedy
   is named once, and new input shapes of it are not new findings. Label
   uncertain premises.
4. **Return and stop.** Use REVIEW.md's coverage/findings/limits format. Complete
   the assigned coverage rather than stopping at the first finding. For a
   re-check, distinguish unresolved classes from genuinely new repair defects.
   Mark each Done-when item the candidate addresses as met, unmet or unavailable.
   Check: only supported blockers or required review-evidence gaps cause
   REQUEST CHANGES; a round with none ends review, and non-blocking harvest
   goes to the summary comment, never another round.
   Return to the orchestrator without repairing, filing tasks or closing work.

## Lenses and affected scope

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
  Recurrence, confidence or repair effort does not raise severity. Preserve an
  external finding's original rating when triaging.
- One remedy means one defect class. Keep an unresolved class open; distinct
  fixes remain separate. Repair-induced defects name the earlier finding and
  why their class differs.
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
