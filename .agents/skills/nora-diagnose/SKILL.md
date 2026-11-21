---
name: nora-diagnose
description: Investigates a Nora failure to its root cause and the long-term fix, for an incident, a Sentry or SigNoz error, a failing CI or eval run, a wrong reply, or an architecture verdict. Load when a task says investigate, diagnose, root cause, why did it fail, or what is the real problem. Not for reading back that a fix landed (nora-readback) or proving a fix is guarded (nora-prove-fix).
---

# nora-diagnose

An investigation ends with a cause proven by evidence and the seam fix that
stops it recurring. It never ends with the symptom restated, a list of
unknowns, or a patch for the one value that failed (`AGENTS.md` «A special
case is a missing requirement»).

1. **State the symptom once.** What happened, where, when, and how often:
   the trace id, the Sentry issue, the CI run, the eval case. Do not repeat back
   what Mike already said; start from it.
   Check: the symptom names one observable a reader can find.
2. **Reproduce or locate.** Reproduce it offline, or find the exact failing
   record: the trace, the log line, the row, the job step. If it cannot be
   reproduced, say what would reproduce it and get that.
   Check: you hold the failing input, not a description of it.
3. **Collect evidence across layers.** Read what each layer actually saw:
   Sentry and SigNoz for errors and traces, container logs, Postgres rows, the
   model's rendered input and output (`nora-readback`), CI logs, the config
   that was live. Read the input before blaming the instruction.
   Check: each layer on the path is read, or named as unavailable with the
   access that would open it.
4. **Hypotheses, then the discriminating check.** List the candidate causes.
   For each, name the check whose result tells it apart from the others, run
   the cheapest first, and drop what the evidence refutes.
   Check: the surviving cause explains every observation, including the ones
   that did not fail.
5. **Root cause and the seam.** Name the cause with its evidence (file:line,
   row, trace span) and the seam that let it happen: a parallel implementation,
   a missing contract, a wrong boundary, an untested path. Two patches already
   on that seam mean the seam is wrong.
   Check: reverting the proposed fix in your head brings the failure back.
6. **Fix and guard.** Propose or make the fix at the seam, and the test or
   guard that keeps it fixed (`nora-test`, `nora-prove-fix`). Use the lifecycle
   (`nora-work`) for anything beyond a direct change.
   Check: the fix covers every sibling instance of the seam, not only this one.
7. **Report.** One line of conclusion, then the cause, the evidence pointers,
   the fix and what it does not cover. Unknown is allowed only with the evidence
   or access that would resolve it.

## Architecture verdict

When the lifecycle escalates (a target failed a third round, a finding
survived its second repair, or a re-check found a new blocker class), run the
steps above at full scale:
the evaluated code, the eval or test harness, the runtime, the context the
model sees and the prompt are all suspects, and step 5 names which design
assumption is wrong. Bring Mike the cause and the redesign it implies.
