---
name: nora-review
description: Review an agreed Nora change, re-check a repair delta, or audit scope/acceptance at the assigned tier. Return coverage, grounded findings and limits under REVIEW.md; no implementation or unrequested repository audit.
---

# Nora review

Read [REVIEW.md](../../../REVIEW.md) for the review contract and
[ORCHESTRATION.md](../../../ORCHESTRATION.md#bounded-independent-review) for tier,
independence, dispositions and round limits. This skill is the procedure.

1. **Identify the review.** Read the work/operator brief and required design
   references. Record review type, obligations, non-goals, tier and base/head.
   Check: the requested result is understandable without deriving intent from
   the candidate. Missing material requirements return to scoping.
2. **Inspect the candidate.** Verify checkout/head/status; read
   `git diff <base>...<head>`. A repair re-check uses
   `git diff <previously-reviewed-head>..<head>` plus unresolved findings.
   Check: the actual candidate matches the assignment; preserve unrelated work.
3. **Test the claims.** Trace affected callers and failure paths to the tier's
   depth. Read actual checks/results and model inputs or runtime evidence only
   where the claim needs them. Run only necessary authorized focused checks;
   a refused test lease is unavailable, never bypassed.
   - For recurring database reads, check generated history-size scaling with
     fixed pending work, admission/commit boundaries, held reader elections,
     foreground progress and release on failure/cancellation.
   - For architecture/docs, inspect the assigned subject's owner and changed
     cross-references; assess contradictions or misplaced responsibilities,
     not unrelated style or a new architecture of the reviewer's own.
   Check: every required condition has met/unmet/unavailable evidence and limits;
   inspect relevant siblings before first reporting a defect class.
4. **Return and stop.** Use REVIEW.md's coverage/findings/limits format. Complete
   the assigned coverage rather than stopping at the first finding. For a
   re-check, distinguish unresolved classes from genuinely new repair defects.
   Check: only supported blockers or required review-evidence gaps cause
   REQUEST CHANGES; non-blocking harvest does not trigger another round.
   Return to the orchestrator without repairing, filing tasks or closing work.
