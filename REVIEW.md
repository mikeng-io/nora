# Reviewing Nora

[ORCHESTRATION.md](ORCHESTRATION.md) owns review tiers, independence, finding
triage and the two-round limit. [AGENTS.md](AGENTS.md) owns engineering rules.
This file is reviewer context, not an enrollment filter; it cannot cancel an
already-triggered paid review.

Read the actual diff against the named base and exact head, then trace affected
callers and reachable failures within the brief. Stay read-only and in scope;
do not spawn agents, call another reviewer, auto-fix, approve, merge or deploy.
State the reviewed head/base, scope and limits. Independence needs a fresh
reviewer who is neither builder nor brief-author, with evidenced different model
vendor. Devin Review is supplemental evidence; Fusion's internal review does
not establish that independence.

Report at most six findings, most severe first. NONE is valid. Each finding
needs a file/line, reachable trigger, concrete harm, failed five C (Coherence,
Consistency, Conformance, Correctness or Completeness), evidence, remedy and
whether this diff caused it. A new reachable wrong result, data loss,
disclosure, crash or weakened gate is a BLOCKER. Mark pre-existing/out-of-scope
defects ISSUE. Use the policy's FIX-IF-CHEAP and DROP dispositions; style-only
requests, speculative guards and a second design are not findings.

Pure authored design/docs outside runtime trees and ordinary MCP client config
changes get no automatic code review. Runtime Markdown under `nora/`, mixed
changes, tests, CI, gate, security and boundary changes follow the canonical
policy and assigned tier. The controller's narrow path exclusion is not a
semantic tier classifier; an unresolved classification is held for orchestration.

Distinguish source inspection, focused tests, final gate, independent review,
merge and deployed behavior. A completed review is not acceptance, a green gate
is not deployment, and neither author nor reviewer marks the task Done alone.
