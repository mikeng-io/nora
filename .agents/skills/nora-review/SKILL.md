---
name: nora-review
description: Review a Nora change against its original scope and acceptance at an assigned none/light/full tier. Load for code review, fix-diff review or scope-drift audit. Not for implementing fixes, running an unrequested repository-wide audit, merging or deploying.
---

# nora-review

For recurring database reads, inspect work as history grows with a fixed pending
batch, and admission across cursor/input/commit boundaries. Account for held
reader elections; verify foreground progress and release on failure/cancellation.

`ORCHESTRATION.md` owns tier selection, independence, finding dispositions and
stopping rules; `REVIEW.md` is the shared reviewer contract. Read both first.
This skill supplies the order, not another policy or automatic enforcement.

1. **Bind the input.** Read the original operator brief or issue and the referenced
   design acceptance at its named revision, then the PR's delivery claims.
   Record the outcome, non-goals, obligations, assigned tier and review base/head.
   Check: missing or conflicting requirements return to scoping; do not invent a
   specification from the implementation or expand the brief's unknowns.
2. **Read the actual candidate.** Confirm checkout/head/status and inspect
   `git diff <base>...<head>`. For round two use exactly
   `git diff <previously-reviewed-head>..<head>` and prior findings.
   Check: the inspected objects match the brief. Preserve unrelated work and
   report any unreviewed delta; do not repair or change the candidate.
3. **Measure the agreed obligations.** At the assigned tier's policy depth, trace
   the changed behavior through affected callers and inspect actual tests/results,
   model inputs or runtime read-back as the obligation requires. Reuse applicable
   evidence with its revision and limits; run only necessary authorized focused
   checks. A refused test lease is unavailable; never bypass it.
   Check: classify each obligation met, unmet or unavailable with a locator.
   A structural check proves structure, not runtime behavior. Missing evidence
   does not establish missing code, and passing tests do not establish deployment.
   **Documents and comments** (`nora-design`, AGENTS.md «Design, documents
   and comments»): new text placed beside the section that already owns its
   subject, or repeating or contradicting it, is a finding; so is a bundle
   holding spec, plan, log or status claims, an architecture change with no
   bundle edit, or a comment that retells history.
4. **Report and stop.** Apply `REVIEW.md`'s finding contract and the policy's
   dispositions/class grouping. Lead with APPROVE or REQUEST CHANGES, name the
   base/head/tier, and give acceptance coverage, findings and unverified limits.
   Check: each finding is grounded in a requirement/rule and reachable harm;
   NONE is valid. Return the report to the orchestrator without auto-fixing,
   launching another reviewer, filing follow-up issues or declaring the task Done.
   The orchestrator triages and closes or routes bounded repair/replanning under
   the policy; the reviewer does not extend the round sequence.
