# nora-eval reference

Detail moved out of `SKILL.md` to keep its body under the ceiling.

## The shared source

`nora_eval`/`nora_eval_blank` are SHARED with every other session on the host:
a run refuses only if the source is AHEAD of this checkout's own head
(refresh it with `make eval-source` or switch branch — a source BEHIND head
runs, and a read that actually hits a column it lacks refuses just as
clearly), and a divergent template migrates a private per-run copy rather
than the shared one, swept at startup if a killed run (or SIGTERM) left one
behind — fixed 2026-09-25 after an ahead branch upgraded `nora_eval_blank`
and broke every other session's seeding
(`design/evaluation/11-the-sandbox-rebuild.md` §1.10).

## Reading the log

The long form of step 8.
   - The first line names the config the subject was rebuilt from; the census line
     per corpus counts parked cases (never run) and unanchored ones (they ERROR).
   - Per case: PASS or FAIL only when every draw agreed; UNSTABLE prints the split
     `[p pass / f fail / e error]`. ERROR is never PASS. A case read stable at n=3
     later failed 1 of 4 draws (2026-09-22): report n with every number.
   - `spend:` per corpus: calls, input (cached, fresh), output. `TRUNCATED` means a
     call hit its ceiling and that draw measured a cut-off output.
   - While #462 is open, draws on a memory-reading entry point (tag,
     participation, proactive_act, dreamer with memory tools) are correlated:
     call the result a direction, not a rate, and treat a "you already asked"
     FAIL as #462 before calling it a model defect.
   - Judged corpora: the judge's source is bound to the window her turn
     rendered since #459 (fixed 2026-09-25): a judged number from an older
     build is not a baseline; compare only against a before arm run now.
   - A case that speaks (tag, participation, proactive_act) anchored in a Discord
     room ERRORs; Discord's adapter is off in the sandbox (2026-09-21).
   - A FAIL is the end of the run, not a reason for more samples: go back to
     "An eval confirms a fix" and read the case's input, anchor and expectation.
     A wrong expectation fails every draw and measures the anchor, not the model.
   Check: every number you will quote has its n, draw split and arm beside it.
