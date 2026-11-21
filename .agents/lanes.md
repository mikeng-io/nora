# Lanes and standing authorities

Mike edits this file; agents read it before every launch and never ask again
for what it already grants. [ORCHESTRATION.md](../ORCHESTRATION.md) holds the
invariants: the reviewer is a fresh session whose vendor differs from every
author's (an orchestrator that commits is an author), and a fallback meets the
same bar as the lane it replaces.

## Lanes

| role | lane | fallback, in order |
|---|---|---|
| orchestrator | Claude Opus 5.5 (Claude Code), effort medium | Claude Sonnet 5.5 high |
| builder | Claude Sonnet 5.5 high; small edits stay with the orchestrator | Devin SWE-2 (`acpRegistry_devin`, `swe-2-high`); GPT-6.2 Sol xhigh (`codex`) |
| reviewer | Devin SWE-2 (`acpRegistry_devin`, `swe-2-high`) | GPT-6.2 Sol high (`codex`); Kimi K3.1 Pro (`kimi-code/k3.1-pro`) |
| adversarial review of a prompt or doctrine | DeepSeek V4.1 Flash (Nora's production model) | — |

- A fallback replaces a lane that is out of quota, errors twice, or returns no
  verdict, without asking. Skip any fallback that shares a vendor with an
  author of the work; Blocked only when the chain is exhausted.
- GPT Sol is 6.2, never 6.1. Devin runs SWE-2 only. Never MiniMax M3;
  M3.1-Flash only when Mike names it.
- A Done-when item, a brief or a running delegate keeps the lane it launched
  with; a changed lane is a new, linked launch.

## Standing authorities

Granted without asking:

| action | condition |
|---|---|
| merge a PR | its route's required review passed with no BLOCKER and required checks are green (enable auto-merge, or end the turn and resume on the checks, never wait); report the merge, which releases and deploys |
| paid eval, replay, judge-only calibration or spike | inside the scenario budget the brief or issue states ("unbounded" is a budget); a goal-driven rerun needs both it and the convergence budget left; each receipt records its effective cutoff (`docs/eval-sop.md`) |
| re-run after the agent's own fix | same cases, inside the same budget, criteria unchanged; rescoring is not a re-run |
| switch to the next fallback lane | as above |
| retire merged or archived artifacts | `make gc APPLY=1` |

Mike is asked for: intent (a disputed or new Done-when item), spend beyond the
scenario budget, a hand deploy or host config change, live accounts and
signal-cli, destructive operations on host data, changing grading criteria,
and an architecture verdict («Escalation» in
[the lifecycle](skills/nora-work/references/lifecycle.md#escalation)).
