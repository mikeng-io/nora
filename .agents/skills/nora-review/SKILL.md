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

1. **Identify the review.** Use REVIEW.md's brief: confirm the question, basis,
   surface, relevant lenses, evidence and exclusions. Read its source requirements;
   record the review type, tier and base/head. Recover the original outcome and
   approved amendments before reading prior findings; retain this basis on
   re-checks and name the selected subset. State any inferred scope.
   Check: the requested result is understandable without deriving intent from
   the candidate. On the standard and full routes, an approved brief exists,
   and a candidate arrives with its execution receipt; a missing one is returned
   unreviewed. On the light route, the PR body carries a brief. Missing material
   requirements return to scoping.
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
