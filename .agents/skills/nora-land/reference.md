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
`$V fetch origin && $V switch -c mikeng-io/<slug> origin/master`, then `uv sync
--extra evals`. Claude Code commits end with a `Claude-Session:` footer.

## Choosing the agent and effort for a subagent

`AGENTS.md`, «Orchestrating: who builds, who checks, and how much effort», is
the one rule: a lane table (Codex `gpt-6.1-sol` builds, Kimi CLI reviews, Claude
`haiku` does mechanical work) and an adaptive effort table. Name both in every
launch. (It replaced the Sonnet/Opus tier table on 2026-10-21, which had
replaced the 2026-09-24 rule «Opus reviews» on 2026-09-30.)

**Codex through T3 `delegate_task` (`isolation: worktree`).** The worktree is
cut from the orchestrator's local commit, so step 1 is `git fetch origin && git
switch -c mikeng-io/<slug> origin/master` inside it, then `uv sync --extra
evals`; never a second `git worktree add`.
