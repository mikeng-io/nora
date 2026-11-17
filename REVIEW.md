# Reviewing Nora

Review asks whether an exact candidate satisfies the agreed change and avoids
real regressions. It returns a scoped judgment, not new scope or merge authority.
[ORCHESTRATION.md](ORCHESTRATION.md) owns tiers, independence and round limits;
[AGENTS.md](AGENTS.md) owns constraints. Automatic and interactive reviews follow
this contract and the [review procedure](.agents/skills/nora-review/SKILL.md).

## Scope and inputs

The orchestrator supplies a short launch brief, not a new document:

| Field | Required basis |
|---|---|
| Question | Change, repair re-check or acceptance audit; what must be decided |
| Basis | Original outcome, acceptance, approved amendments, source revision and base/head |
| Surface | Changed behavior/callers; prior findings and unanswered coverage |
| Lenses | Concrete risks/questions; tier defines depth, not a fixed panel |
| Evidence | Actual receipts, allowed checks and what could disprove the claim |
| Exclude | Unrelated work, settled decisions and action/spending limits |

Without a brief, derive and state scope from available requirements; missing
intent remains a limitation. Acceptance survives repairs: findings or an archive
link never replace it. Amendments identify changed conditions and invalidated
conclusions. A subset review does not implicitly pass excluded conditions.
Trace affected callers/failures, including material candidate-caused regressions
outside named files. Re-check the repair delta and unresolved findings/evidence,
not unrelated base debt. Reopen decisions only on new evidence. Stay read-only;
no fixes, agents, approvals, merge or deploy unless separately assigned.

## Findings we want

A defect needs basis, reachable trigger and harm, its failed C, and the smallest
remedy; distinguish introduced, remaining original, pre-existing and repair-induced.
Severity measures impact; BLOCKER / FIX-IF-CHEAP / ISSUE / DROP determines action.
Group siblings needing one remedy; inspect that class fully on first report.
Unverified premises are hypotheses; NONE is valid. Observable behavior supports
behavior claims; source shape supports only shape. Missing evidence is unavailable,
not missing code. Exclude taste, speculative guards, duplicate tests and redesign.
Detailed risk lenses, admission criteria and report examples live in the procedure.

## Return and convergence

Lead APPROVE or REQUEST CHANGES, exact base/head, review type/tier and actual
vendor/independence. Return acceptance coverage (met/unmet/unavailable), supported
findings (file:line, trigger/harm, failed C, evidence, remedy, severity/disposition)
and inspection/execution limits. Finish coverage even after a defect; incomplete
reviews name unanswered conditions and why. Only a supported blocker or required
evidence gap warrants REQUEST CHANGES. APPROVE is not completion, CI or delivery.
With neither, summarize non-blocking harvest once and stop. The orchestrator owns
triage and any next round; review does not authorize new requirements or spending.
