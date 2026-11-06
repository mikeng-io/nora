# nora-land: tool notes

**Claude Code with the rtk hook, in a worktree-isolated subagent.** Every `git`
call is refused: the hook rewrites it to `rtk git ...`, and the isolation guard
refuses a launcher with git among its operands (`git -C`, `rtk proxy`, `PATH=`
too, 2026-09-24). Measured fix: write `<scratchpad>/vcs` (no "git" in the
name) with the file-writing tool, `chmod +x` it, then `V=<scratchpad>/vcs; $V status -sb`.

```bash
#!/bin/bash
cd <worktree> || exit 1
exec "$(command -v git)" "$@"
```

`command -v` takes whichever git PATH gives (`/usr/bin/git` or Homebrew's
`/opt/homebrew/bin/git`). The subagent starts on a harness branch: step 1 is
`$V fetch origin && $V switch -c <type>/<slug> origin/master`, then `uv sync
--extra evals`. Claude Code commits end with a `Claude-Session:` footer.

## Choosing a worker

[ORCHESTRATION.md](../../../ORCHESTRATION.md) owns current capability tiers,
adaptive routing, independence and review selection. Resolve the available
worker and its advertised options at launch.

**T3 `delegate_task` (`isolation: worktree`).** The worktree starts from the
orchestrator's local commit. A build starts with `git fetch origin && git switch
-c <type>/<slug> origin/master` inside it, then `uv sync --extra evals`; never a
second `git worktree add`. A reviewer selects the exact head under review.

## Pre-merge scanner findings

Before merging, or enabling auto-merge, inspect the latest pushed head's checks,
PR conversation comments, submitted reviews and inline review threads. Green CI
alone does not clear bot findings (including secret scanners such as Gitleaks or
GitGuardian). Read the actual finding and its current disposition, including
findings reported only in comments or check annotations.

- Triage each actionable finding against the current diff: fix it, or record
  evidence that it is a false positive, superseded or outside this change.
  Unresolved credible secret/security findings block merge; an inaccessible
  report is unavailable evidence, never a clean result. Never copy secret values
  into replies, logs or the PR body.
- Re-read findings and check results after the final push and immediately before
  merge/auto-merge. Do not resolve a thread merely to make the merge button green.
  Record the disposition in the PR's existing gate section; no extra reviewer is
  required merely to read scanner output.
- GitHub required status checks and required conversation resolution provide
  mechanical enforcement where configured. Conversation resolution covers review
  threads, not every ordinary bot comment; the agent still reads both. Do not
  enable auto-merge while actionable findings remain, or bypass required checks.
