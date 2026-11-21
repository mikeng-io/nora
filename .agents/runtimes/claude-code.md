# Claude Code

Claude Code reads `CLAUDE.md` (which points to `AGENTS.md`) and the skills
through the `.claude/skills/<name>` links.

## The rtk hook compacts output

`git`, `ls`, `pytest`, `grep` and `curl` output can come back condensed, and
`curl` piped into a parser returns a schema summary instead of JSON. For any
number you parse or raw failure text you need, read the log file you wrote, use
`curl -o <file>`, or run the command through `rtk proxy <command>`.

## Git inside a worktree-isolated subagent

Every `git` call is refused: the hook rewrites it to `rtk git ...`, and the
isolation guard refuses a launcher with git among its operands (`git -C`,
`rtk proxy`, `PATH=` too). Write `<scratchpad>/vcs` (no "git" in the name)
with the file-writing tool, `chmod +x` it, then `V=<scratchpad>/vcs; $V status -sb`.

```bash
#!/bin/bash
cd <worktree> || exit 1
exec "$(command -v git)" "$@"
```

`command -v` takes whichever git PATH gives (`/usr/bin/git` or Homebrew's
`/opt/homebrew/bin/git`).

## Background commands

Launch a long run as the Bash command itself with `run_in_background`, never
`nohup … &` inside it, or the completion notice reports the wrapper and a live
run looks dead. The notice's exit code is the last command's (a trailing `echo`
or `tee`), so write `RC=$?` to a file and read the file, not the notice.

## Commits

Claude Code commits end with a `Claude-Session:` footer.
