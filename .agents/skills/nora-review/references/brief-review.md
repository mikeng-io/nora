# Brief review and item marking

Two review jobs that fall to the reviewer chosen by rule, beside code review
(`REVIEW.md`). The reviewer's independence rule is in `ORCHESTRATION.md`
«Reviewer independence».

## Brief review (propose → plan)

**Input:** the brief, its attached plan, the author's source census, the
source issue or request, and the design it cites, all readable at named
revisions (`nora-dispatch/references/brief.md` «Before review»). Without the
plan or the census, a standard- or full-route brief is returned unreviewed. An
input that cannot be opened is also returned, as a packet gap, not reviewed
around.

Check, and cite file:line or the source for each finding:

1. **Finite.** Each Done-when item passes the checklist in
   `nora-dispatch/references/brief.md`. Quote any item that is still open-ended.
2. **Faithful.** No requirement of the source or the design that belongs to
   this outcome is dropped or weakened. Name the requirement and where it comes from.
3. **Mapped.** Every item maps to a plan unit with a named check. Each unit
   states its write set, base, side effects and rollback, and overlapping write
   sets are sequenced.
4. **Operator authority.** The authority, side effects and blast radius are
   stated, and none exceeds what Mike has granted.
5. **Route.** The route matches the triggers in `nora-work/references/lifecycle.md`.

**Return:** for each item, APPROVE or BLOCKER with a one-line reason. Label a
BLOCKER that the author's pre-flight should have caught as `pre-flight miss`.
A round with no BLOCKER ends brief review, passes propose → plan and locks
the Done-when list. Escalate under `nora-work/references/lifecycle.md`
«When Mike is asked».

## Marking items (verify)

In code review, the reviewer marks each Done-when item the candidate addresses
as met, unmet or unavailable. The evidence comes from the execution receipt and
the reviewer's own reading of it. The author never marks its own items, and a
mark the reviewer could not verify is unavailable, not met.

## Severity and follow-through

A finding's severity is the reviewer's. The orchestrator chooses the
disposition (`ORCHESTRATION.md` «Bounded independent review») but never
lowers or raises the severity.

After a repair, a re-check covers the repair delta, the open findings and
any evidence that was unavailable. Rounds continue without asking while each
one closes blocker classes. Mike decides how to proceed when a re-check leaves
blockers open and closes no class, or a repair introduces a new blocker class.

When fixing a reported defect class, the author searches for every sibling
instance, as the reviewer does when reporting one. A re-check that finds an
instance the author missed is the same class, not a new one.
