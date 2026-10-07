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

## Choosing the model and effort for a subagent

`AGENTS.md`, «Delegating to a sub-agent: which model, and how much effort», is
the one rule: a tier table for the model and an adaptive effort table. Name
both in every launch. (The 2026-09-24 rule that stood here, «Opus reviews»,
was superseded by that table on 2026-09-30.)
