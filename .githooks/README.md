# Git hooks

Installed with `make hooks` (sets `git config core.hooksPath .githooks` for
this clone — run it once per worktree; worktrees share the repo config, so one
install covers all of them on this machine).

- **`commit-msg`** — runs `scripts/check_commit_subject.py` on every commit.
  The closed type list and the authored-merge rule are enforced here so the
  convention stops depending on memory. `--no-verify` bypasses it; if you use
  that, name the reason in the commit body.
- **`pre-push`** — same checker against the pushed head. The last local gate
  before the branch is visible.

The rule these enforce lives in `CONTRIBUTING.md` §Commit messages / §Merge
strategy. The checker is the typing aid; the doc is the contract. To change
either, change both in one commit.
