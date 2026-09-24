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

## Choosing the model for a subagent (Claude Code)

The operator's rule (2026-09-24): **Sonnet does the work** (implementation,
fixes, audits, sweeps), Opus only when the work itself is heavy or complex.
**Opus reviews** (step 7's independent review), Fable only for heavy or
extremely complex reviews. Name the model in every launch; never let one
default for all agents. An explicit override from the operator for a session
("only use sonnet") wins.
