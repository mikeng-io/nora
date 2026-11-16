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

Read the original Linear work brief or operator brief, referenced design
invariants, exact base/head and PR claims. The implementation is not its own
specification. Missing or conflicting intent returns to scoping.

| review request | inspect |
|---|---|
| change review | the agreed diff, reachable callers, failure paths and relevant tests at the assigned tier |
| repair re-check | the previous review's repair delta and affected paths; retain unresolved findings |
| scope/acceptance audit | each agreed completion condition against current artifacts and evidence |

Surrounding code/docs are context, not permission to audit the repository.
Runtime prompts/config, tests and CI are behavior when the change touches them.
Design/docs are targets when assigned or needed to assess a changed invariant;
do not harvest unrelated prose, formatting or historical-record preferences.
Consult settled decisions before proposing a mechanism change; cite new evidence
before reopening one. Stay read-only; no fixes, agents, GitHub approval, merge or
deploy unless separately assigned. Report the actual review scope and limits.

## Findings we want

- Report grounded defects, including useful P2 observations; NONE is valid.
  Trace a reachable trigger to concrete harm and an agreed requirement or rule.
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
  claims. Mark pre-existing/out-of-scope defects explicitly.
- Exclude taste, speculative guards, duplicate tests and unrequested redesign.
  Do not invent requirements or ask for extra checks without naming what they
  could establish. Missing evidence is unavailable, not proof of missing code.

## Return and convergence

Lead with APPROVE or REQUEST CHANGES for the reviewed scope, then base/head,
review type/tier and actual reviewer vendor/independence where required.

Return only:

1. **Coverage:** each completion condition met / unmet / unavailable, with a
   compact evidence pointer; group conditions only when their results match.
2. **Findings:** severity and disposition, file:line, trigger → harm, failed C,
   evidence, smallest remedy, and introduced / pre-existing / repair-induced.
3. **Limits:** what was not inspected or executed and any required evidence gap.

REQUEST CHANGES means a supported blocker or a required review-evidence gap;
identify which. APPROVE is not task completion, green CI, merge or deployment.
Finish the assigned coverage even after finding a defect; do not manufacture
findings to fill the report. With no BLOCKER, summarize non-blocking harvest
once and stop. The orchestrator owns triage and any next round under the policy.
Devin Review is supplemental; Fusion's internal review alone is not independent
review. Pure non-runtime docs and ordinary MCP client config need no automatic
code review; explicit review requests and mixed/runtime changes follow the tier.
