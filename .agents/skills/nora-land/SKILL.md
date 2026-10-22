---
name: nora-land
description: Lands a Nora change from first edit to merged master, covering a new worktree from origin/master, the test loop, the gate read by its exit code, a Conventional Commit that is the release note, a PR from the template, an adaptive review, an authored merge commit and the post-merge read-back. Load when starting work, committing, opening a PR or merging. Not for deploying to nora-host (nora-deploy).
---

# nora-land

Worktree, sync, (gate, commit)*, push, PR, review, merge, read-back.
Rules and reasons: `CONTRIBUTING.md`, `AGENTS.md`. Every step ends in a check:
the log or the read-back, never an exit code alone. Go end to end; stop only
for a blocker or a fact only the operator holds (a decision is not input).

```bash
TYPE=<commit-type>; SLUG=<kebab-slug>; WT=<new-worktree-path>        # WT outside every checkout
LOG=${TMPDIR:-/tmp}/land-$SLUG; mkdir -p "$LOG"   # fixed, OUTSIDE the worktree
```

Shell state may not survive between calls (Claude Code's Bash): re-set `TYPE`, `SLUG`
and `LOG` in every call; carry results in files (`$LOG/gate.rc`), not variables.

## 1. Start: a new worktree on a new branch

```bash
git fetch origin
git worktree add -b "$TYPE/$SLUG" "$WT" origin/master
cd "$WT" || exit 1
uv sync --extra evals        # = make setup; without it pytest dies at COLLECTION
git status -sb; git config core.hooksPath
```

Check: `## <type>/<slug>...origin/master`, no ahead/behind; hooksPath ends
in `.githooks` (empty: `make hooks`, once per clone covers every worktree).

- Cut from `origin/master` after a fetch, never local `master`: it lags, and a
  `reset --soft` onto the real tip commits a revert of the gap (2026-09-17,
  near-revert of #315). After any squash, `git show --stat HEAD` lists only
  files you touched.
- Never edit in the primary checkout. One committing agent per worktree: an
  `--amend` landed on another agent's commit on a shared HEAD (2026-09-22).
- Never `cd "$WT" 2>/dev/null && ...`: a stale path runs in the old directory
  (session audit 2026-09-24: 42% error co-occurrence).

## 2. Iterate

```bash
uv run pytest -q -n 8 <test paths>   # uncommitted edits: name the tests
make test-affected                   # judges the LAST COMMIT only (HEAD~1...HEAD)
make test-fast                       # whole suite, parallel
```

It never sees uncommitted edits (`scripts/affected_tests.py`). The gate is the
final check, not the loop (2026-08-21: eight gate runs in one session).

## 3. Gate

```bash
make gate > "$LOG/gate.log" 2>&1; GATE_RC=$?; echo "GATE_RC=$GATE_RC" > "$LOG/gate.rc"
grep -cE '^uv run (ruff|mypy|lint-imports)' "$LOG/gate.log"   # 3 = every static step ran
grep -E '^affected_tests:|passed|failed|error' "$LOG/gate.log" | tail -n 5
```

`make gate`: static steps in full, then the tests the diff against `origin/master`
reaches (all when unmapped or the selector fails; only `NONE` runs nothing). PASS: `GATE_RC=0`, 3 steps, no `failed`. ONCE per PR,
on the final head; fix rounds run their own tests. `make full-gate` = whole suite.

- Never `make gate | tail`: the rc is tail's (2026-09-22, merged a red master).
- In the background read `$LOG/gate.rc`, not the notice (last command's rc).
  Kill only a PID you recorded, never `pkill -f`: it kills other sessions' gates.

**Docs-only** (every path a `.md` outside `nora/`, `evals/`, `scripts/`, or data under
`docs/`, `design/`): the gate's selector, without the static steps:

```bash
git add <paths>    # the doc gate scans `git ls-files`: an unstaged new doc is never read (#391)
make test-affected BASE=origin/master INCLUDE_WORKTREE=--include-worktree \
  > "$LOG/docs.log" 2>&1; echo "GATE_RC=$?" > "$LOG/gate.rc"
```

The selector scans `tests/` for each doc's readers and runs everything for an
unmapped path. Never hand-pick test files: a hand-picked list omitted real readers
until 2026-10-02. PASS is `GATE_RC=0` with a `passed` count, or `NONE`. Other
paths: `make gate`.

## 4. Commit

```bash
python3 scripts/check_commit_subject.py "$LOG/msg.txt"; echo "SUBJECT_RC=$?"
. "$LOG/gate.rc"; if [ "${GATE_RC:-1}" = 0 ]; then git add <paths> && git commit -F "$LOG/msg.txt"; fi
rm -f "$LOG/gate.rc"      # a gate result measures one tree; the next commit needs a new one
git log -1 --format='%h %s'
```

- Subject `type(scope): description`, a full clause. Closed types
  (`CONTRIBUTING.md` Commit messages): feat, fix, docs, test, refactor, chore,
  change, merge, design, config, build, style, ci, ops, doctrine, perf, security.
- Body: what was broken, what was measured, what shipped, what it does not cover.
- Hooks: `commit-msg` runs `scripts/check_commit_subject.py`, `pre-push`
  re-checks every unpushed commit. Refused: no type, an undeclared type, `wip:`,
  GitHub-default merge subjects. `--no-verify` only with the reason in the body.
- Commit on the recorded rc, never `gate; commit`. Name paths, not `git add -A`.

## 5. The subject is the release note

There is no release fragment (DEC-314). release-please turns the `feat`, `fix`,
`security`, `perf`, `change` and `doctrine` subjects merged since the last tag
into the CHANGELOG and the GitHub Release (`nora-release`). Other types never
show up there.

- Write a user-visible subject as a changelog line: `fix(music): resume after a
  voice reconnect`. A breaking change gets `!` or a `BREAKING CHANGE:` footer.
- Internal work takes a hidden type (`refactor`, `test`, `docs`, `chore`, …),
  not a `fix` that no user will see.

## 6. Merged tree, push, PR

```bash
git fetch origin; git rev-list --count HEAD..origin/master    # 0: branch is the merged tree
git merge origin/master -m "merge: origin/master into $SLUG, <what came in>"   # only if >0, then step 3
git push -u origin HEAD
cp .github/pull_request_template.md "$LOG/pr.md"   # fill in, delete the comments
gh pr create --base master --title "<type(scope): description> (#N, DEC-M)" --body-file "$LOG/pr.md"
gh pr view <N> --json baseRefName,headRefOid       # master, and == git rev-parse HEAD
```

- A bare `git merge origin/master` writes a default subject the hook refuses.
- Title: append `(#N, DEC-M)` only when it closes an issue or lands a decision.
- Body: the template headings exactly, in order: `What`, `Also`, `Gate on the
  merged tree`, `Authored by`, `Closes`; delete what does not apply (`What`,
  `Gate on the merged tree` and `Authored by` are required). `Authored by` names
  human or agent, the tool and model for an agent, and who reviewed it. Labels and
  the assignee are set by the PR triage workflow; do not set them by hand. Never
  `Summary` or `Test plan`, both retired (still in 11 of the last 140 PRs).
- Gate section: the real outcome (GATE_RC, pass count, origin/master sha merged
  in); a pre-existing unrelated failure is named with its issue number; the
  review level and, after step 7, the triage line.
- A deployment that bind-mounts `nora/doctrine` or `nora/dreamer` can hot-reload
  changes on mtime; mention this operator option when relevant.
- `Closes #N` only when this PR fully resolves it; otherwise `issue #N`.
- A fix reports its mutation result (`docs/maintainers/conventions.md` Proving a fix).

## 7. Review, when the change earns one

- Whether and at which level: `AGENTS.md` «Review is adaptive». Level
  none: go straight to step 8. The PR's Gate section names the row.
- Otherwise, a fresh agent from a vendor that did not build the change:
  DeepSeek for light, and Kimi or `gpt-6.1-sol` (whichever did not build it)
  for full, launched through T3 (lane and effort: `AGENTS.md`).
  Give it the diff (`gh pr diff <N>`) and the PR's claims.
- Triage every finding (`AGENTS.md` «Triage decides what blocks»). Only a
  BLOCKER buys another round, and that round reviews the fix diff only.
- Verify each finding before acting on or dismissing it: open the line, run the
  test, revert the fix and watch the named test fail.
- A clean review is not a pass: one plus a green gate missed an empty-body read
  returned as `ok`; the live read-back caught it (2026-09-24).
- Review fixes: steps 3 and 4 again, push, re-check `headRefOid`.

## 8. Merge: a real merge commit, authored message

```bash
git status -sb     # nothing ahead: pushed head == reviewed head
git fetch origin && test "$(git rev-list --count HEAD..origin/master)" = 0 && echo NOT_BEHIND &&
gh pr merge <N> --merge --match-head-commit "$(git rev-parse HEAD)" \
  --subject "merge: <the arc> (#N)" --body-file "$LOG/merge-body.md"
```

- `NOT_BEHIND` is read right before the merge, not at the gate: master
  moves during a 6-minute gate, and the merge commit then combines a tree
  nobody gated (2026-09-25, #495 merged 3 behind; the landed tree was gated
  afterwards and was green). Chained, so a failed fetch or a behind branch
  never reaches the merge. Behind: bring master in, gate, push, re-check.
- Without `--subject`, `gh pr merge --merge` writes GitHub's default `Merge pull
  request #N from ...` (99 of the 137 PRs merged #321 to #478). No hook sees a server-side
  merge: the subject is `merge: ` plus the arc, the body says what the branch
  set out to fix, what was measured, what shipped.
- `--merge` only, never `--squash` or `--rebase`.
- Stacked PR: `gh pr edit <child> --base master` BEFORE merging its base with
  `--delete-branch`, or the child is closed (2026-09-23, #411 closed #413).

## 9. Read back

```bash
gh pr view <N> --json state,mergeCommit          # MERGED; note mergeCommit.oid
git fetch origin; git merge-base --is-ancestor <oid> origin/master; echo "ON_MASTER_RC=$?"   # 0 = landed
git log -1 --format='%p | %s' <oid>              # two parents, subject `merge: ...`
```

Touched a migration or the decision index? Check the landed tree (both exit 0
whatever they find; count the output):

```bash
git switch --detach origin/master
make heads > "$LOG/heads.txt" 2>&1; grep -c '(head)' "$LOG/heads.txt"    # must print 1
make decision-numbers > "$LOG/dn.txt" 2>&1; grep -vc '^==' "$LOG/dn.txt" # must print 0
```

No more edits here: the next change is step 1, a new worktree. Cleanup:
`docs/maintainers/conventions.md` Cleanup. Deploying: nora-deploy.

## Tool notes

Claude Code with the rtk hook refuses every `git` call inside a
worktree-isolated subagent; the measured workaround (a `vcs` wrapper) is in
`.agents/skills/nora-land/reference.md`. Claude Code commits end with a
`Claude-Session:` footer.
