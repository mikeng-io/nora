# Git hooks

Installed with `make hooks` (sets `git config core.hooksPath .githooks` — a
repo-level setting shared by every worktree, so one run per clone covers all of
them). Two things worth knowing before you trust it:

- **The hooks fire only where `.githooks/` exists on disk** — i.e. on a
  checkout of a branch that carries this commit. On the main checkout, or on a
  worktree of an older branch, git silently skips the missing path and the
  commit goes through unchecked; `pre-push` is the backstop for exactly that
  gap (it runs wherever the push happens from, on whatever tree is checked out
  there).
- **The checker resolves against the worktree you are committing in**
  (`git rev-parse --show-toplevel`), never `--git-common-dir` — in a linked
  worktree the common dir is the main checkout, which may hold a different
  (older) copy of the rules. Verified 2026-09-14: with the common-dir path,
  `git commit -m 'chore: x'` in a linked worktree failed with "can't open file
  …/.git/../scripts/check_commit_subject.py".

- **`commit-msg`** — runs `scripts/check_commit_subject.py` on every commit.
  The closed type list and the authored-merge rule are enforced here so the
  convention stops depending on memory. `--no-verify` bypasses it; if you use
  that, name the reason in the commit body.
- **`pre-push`** — same checker across EVERY commit in the pushed range, not
  just the head: the commits that need it most are the ones made before the
  hook was installed. Capped at 200 per ref, and it says so when the cap
  binds.

The rule these enforce lives in `CONTRIBUTING.md` §Commit messages / §Merge
strategy. The checker is the typing aid; the doc is the contract. To change
either, change both in one commit.
