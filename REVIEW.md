# Reviewing Nora

[ORCHESTRATION.md](ORCHESTRATION.md) owns review tiers, independence, finding
triage and the two-round limit. [AGENTS.md](AGENTS.md) owns engineering rules.
This file is reviewer context, not an enrollment filter; it cannot cancel an
already-triggered paid review. Interactive reviewers load
[nora-review](.agents/skills/nora-review/SKILL.md) for the procedure; automatic
reviewers apply this same contract within their available surface.

First read the original outcome, scope/non-goals and revisioned acceptance from
the issue/design or operator brief. PR claims describe delivered work, not a
replacement specification. Read the actual diff against the named base and exact
head, then inspect callers and evidence to the assigned policy tier's depth.
Stay read-only and in scope; do not spawn agents, call another reviewer, auto-fix,
submit a GitHub approval, merge or deploy. A report may recommend APPROVE.
State the reviewed head/base, scope and limits. Independence needs a fresh
reviewer who is neither builder nor brief-author, with evidenced different model
vendor. Devin Review is supplemental evidence; Fusion's internal review does
not establish that independence.

Report at most six findings, most severe first. NONE is valid. Each finding
needs a file/line, reachable trigger, concrete harm, failed five C (Coherence,
Consistency, Conformance, Correctness or Completeness), evidence, remedy and
whether this diff caused it. Tie it to a stated requirement or repository rule;
prefer observed behavior to source-shape proxies for behavioral claims.
A new reachable wrong result, data loss,
disclosure, crash or weakened gate is a BLOCKER. Mark pre-existing/out-of-scope
defects ISSUE. Use the policy's FIX-IF-CHEAP and DROP dispositions; style-only
requests, speculative guards and a second design are not findings.
Apply the policy's defect-class grouping and convergence rules. Consult accepted
decisions before recommending a design change; reopening one needs new evidence.
Verification judges the agreed work; it does not invent requirements or perform
its own repairs.

Pure authored design/docs outside runtime trees and ordinary MCP client config
changes get no automatic code review. Runtime Markdown under `nora/`, mixed
changes, tests, CI, gate, security and boundary changes follow the canonical
policy and assigned tier. The controller's narrow path exclusion is not a
semantic tier classifier; an unresolved classification is held for orchestration.

Distinguish source inspection, focused tests, final gate, independent review,
merge and deployed behavior. A completed review is not acceptance, a green gate
is not deployment, and neither author nor reviewer marks the task Done alone.
Report APPROVE or REQUEST CHANGES for the reviewed scope, each acceptance
obligation's met/unmet/unavailable evidence, and inspection limits. Unavailable
required evidence prevents acceptance but does not prove missing implementation.
An incomplete/invalid brief returns to scoping instead of provoking more code.
The orchestrator applies the completion rule and records the result; this file
and the skills guide agents, while schema/CI checks do not certify semantic
coverage or enforce review cadence.
