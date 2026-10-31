---
name: nora-land
description: Lands a Nora change from first edit to merged master, covering a new worktree from origin/master, the test loop, the gate read by its exit code, a Conventional Commit that is the release note, a PR from the template, an adaptive review, an authored merge commit and the post-merge read-back. Load when starting work, committing, opening a PR or merging. Not for deploying to nora-host (nora-deploy).
---

# nora-land

Worktree, sync, (check, commit)*, gate once, push, PR, review, merge, read-back.
Rules and reasons: `CONTRIBUTING.md`, `AGENTS.md`. Every step ends in a check:
the log or the read-back, never an exit code alone. Go end to end; stop only
for a blocker or a fact only the operator holds (a decision is not input).
Two roles, one procedure (ORCHESTRATION.md): a builder under a bounded brief
runs named checks (1–2), commits (4–5), pushes/opens the PR (6) and STOPS.
The orchestrator gates the final head (3), then reviews, merges and reads back (7–9).

```bash
TYPE=<commit-type>; SLUG=<kebab-slug>; WT=<new-worktree-path>        # WT outside every checkout
LOG=${TMPDIR:-/tmp}/land-$SLUG; mkdir -p "$LOG"   # fixed, OUTSIDE the worktree
```

Shell state may not survive between calls: re-set `TYPE`/`SLUG`/`LOG` each call; carry results in files, not variables.

## 1. Start: a new worktree on a new branch

```bash
git fetch origin
git worktree add -b "$TYPE/$SLUG" "$WT" origin/master
cd "$WT" || exit 1
uv sync --extra evals        # = make setup; without it pytest dies at COLLECTION
git status -sb; git config core.hooksPath
```

Check: `## <type>/<slug>...origin/master`, no ahead/behind; hooksPath ends `.githooks` (empty: `make hooks`).

- Cut from `origin/master` after a fetch, never local `master`: it lags, and a
  `reset --soft` onto the real tip commits a revert of the gap (2026-09-17,
  #315). After a squash, `git show --stat HEAD` lists only files you touched.
- Never edit in the primary checkout; one committing agent per worktree (an `--amend` landed on another's commit, 2026-09-22).
- Never `cd "$WT" 2>/dev/null && ...`: a stale path runs in the old directory.

## 2. Iterate

```bash
uv run python -m scripts.pytest_gate <test paths>   # uncommitted edits: name the tests
make test-affected                   # judges the LAST COMMIT only (HEAD~1...HEAD)
make test-fast                       # whole suite, parallel
```

It never sees uncommitted edits (`scripts/affected_tests.py`). The gate is the
final check, not the loop (2026-08-21: eight gate runs in one session).

## 3. Gate — once per candidate tree

```bash
make gate > "$LOG/gate.log" 2>&1; GATE_RC=$?; echo "GATE_RC=$GATE_RC" > "$LOG/gate.rc"
grep -E 'gate receipt:|^uv run (ruff|mypy|lint-imports)' "$LOG/gate.log"
grep -E '^affected_tests:|passed|failed|error' "$LOG/gate.log" | tail -n 5
```

`make gate`: code/mixed changes run static steps and affected tests against
`origin/master`; docs-only changes run reader tests without static steps. Unknown
paths/errors remain broad. Local exact-candidate/context success receipts can be
reused; a `REUSED` result is prior acceptance, never a newly executed test count.
Read the worktree Git directory's `nora-gate-receipt.json` (key, command, completion and rc),
alongside the matching original evidence. Dirty/context changes cannot
reuse it. `make gate-reuse` only checks reuse (nonzero miss, no checks), so a
readback cannot accidentally start a cold suite. `make gate-fresh` forces a run;
CI and `make full-gate` are always fresh, full-gate serial/unconditional.

PASS: `GATE_RC=0`, then either fresh checks/reader results (three static steps for
code), or the runner's validated matching success receipt. A changed candidate,
base or execution context reruns acceptance. Receipt refreshes rerun nothing.

- Never `make gate | tail`: the rc is tail's (2026-09-22, merged a red master).
- In the background read `$LOG/gate.rc`, not the notice (last command's rc).
  Kill only a PID you recorded, never `pkill -f`: it kills other sessions' gates.

**Docs-only:** stage new docs so tracked-reference readers see them, then use
`make gate` as above. It selects document readers and omits static steps. Runtime
Markdown under `nora/`, `evals/`, `scripts/` and test data is not ordinary docs;
under `tests/` only README guides outside fixtures use doc readers.
Never hand-pick readers; an unmapped/shared-helper path remains broad.

## 4. Commit

```bash
git add <paths> && git commit -F "$LOG/msg.txt"   # commit-msg runs check_commit_subject.py
git log -1 --format='%h %s'
```

- Subject `type(scope): description`, a full clause; the closed type list is
  `CONTRIBUTING.md` «Commit messages» — hooks reject undeclared types.
- Body: what was broken, what was measured, what shipped, what it does not cover.
- Hooks: `commit-msg` runs `scripts/check_commit_subject.py`, `pre-push`
  re-checks every unpushed commit. Refused: no type, an undeclared type, `wip:`,
  GitHub-default merge subjects. `--no-verify` only with the reason in the body.
- Commit on the step-2 checks the edit ran; the gate is once per candidate,
  not a commit precondition. A focused check is never reported as the gate.
  Name paths, not `git add -A`.

## 5. The subject is the release note

Every ordinary master push prepares patch/minor from complete reachability;
only generated metadata auto-merges on its exact head. Application PRs retain explicit merges.

- Subjects are changelog lines; hidden internal types still trigger PATCH.
- PATCH includes existing-capability additions, even `feat`. A complete new product
  capability or new/deprecated supported API/CLI gets footer `Release-Bump: minor`.
- `!` / `BREAKING CHANGE:` stops automatic version writes. Major requires owner
  dispatch at an exact full current SHA; no daily evaluator or release fragments.

## 6. Merged tree, push, PR

```bash
git fetch origin; git rev-list --count HEAD..origin/master    # 0: branch is the merged tree
git merge origin/master -m "merge: origin/master into $SLUG, <what came in>"   # if >0: focused checks; orchestrator gates
git push -u origin HEAD
cp .github/pull_request_template.md "$LOG/pr.md"   # fill in, delete the comments
gh pr create --base master --title "<type(scope): description> (#N, DEC-M)" --body-file "$LOG/pr.md"
gh pr view <N> --json baseRefName,headRefOid       # master, and == git rev-parse HEAD
```

- A builder under a brief STOPS at its handoff: the Gate section names the
  focused checks run and the final gate as the orchestrator's, pending.
- A bare `git merge origin/master` writes a default subject the hook refuses.
- Title: append `(#N, DEC-M)` only when it closes an issue or lands a decision.
- Body: the template headings exactly, in order: `What`, `Also`, `Gate on the
  merged tree`, `Authored by`, `Closes`; delete what does not apply (`What`,
  `Gate on the merged tree` and `Authored by` are required). `Authored by` names
  human or agent, the tool and model for an agent, and who reviewed it. Labels and
  the assignee are set by the PR triage workflow; do not set them by hand. Never
  `Summary` or `Test plan`, both retired (still in 11 of the last 140 PRs).
- Gate section: the real outcome (GATE_RC, pass count, origin/master sha merged
  in), the review level and, after step 7, the triage line; a pre-existing
  unrelated failure is named with its issue number. After the LAST push refresh
  the body to `headRefOid` — a head review did not cover is named plus its
  delta, never implied fully reviewed; a receipt refresh reruns nothing, a
  changed tree re-runs step 3. Read it back: `gh pr view <N> --json body,headRefOid`.
- A deployment bind-mounting `nora/doctrine` or `nora/dreamer` hot-reloads on
  mtime; mention this operator option when relevant.
- `Closes #N` only when this PR fully resolves it; otherwise `issue #N`.
- A fix reports its mutation result (`docs/maintainers/conventions.md` Proving a fix).

## 7. Review, when the change earns one

- Whether and at which level: `ORCHESTRATION.md` «Capability and review requirements». Level
  none: go straight to step 8. The PR's Gate section names the row.
- Otherwise follow `ORCHESTRATION.md` «Resolve a worker at launch» to select a
  fresh reviewer with actual advertised options: neither builder nor
  brief-author, a different model vendor from the builder. Give it the diff
  (`gh pr diff <N>`) and the PR's claims.
- Triage every finding (`ORCHESTRATION.md` «Bounded independent review»). Only a
  BLOCKER buys another round; at most two, with the second checking the fix diff only.
- Verify each finding before acting on or dismissing it: open the line, run the
  test, revert the fix and watch the named test fail.
- A clean review is not a pass: one plus a green gate missed an empty-body read
  returned as `ok`; the live read-back caught it (2026-09-24).
- Review fixes: step 2's checks, step 4 again, step 3 on the changed head,
  push, re-check `headRefOid`, refresh the Gate section.

## 8. Merge: a real merge commit, authored message

```bash
git status -sb     # nothing ahead: pushed head == reviewed head
git fetch origin && test "$(git rev-list --count HEAD..origin/master)" = 0 && echo NOT_BEHIND &&
gh pr merge <N> --merge --match-head-commit "$(git rev-parse HEAD)" \
  --subject "merge: <the arc> (#N)" --body-file "$LOG/merge-body.md"
```

- `NOT_BEHIND` is read right before the merge, not at the gate: master moves
  during a 6-minute gate (2026-09-25, #495 merged 3 behind). Chained, so a
  failed fetch or a behind branch never reaches the merge. Behind: bring
  master in, gate, push, re-check.
- Without `--subject`, `gh pr merge --merge` writes GitHub's default `Merge
  pull request #N from ...` (99 of 137 PRs #321–#478). No hook sees a server-side
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

Touched a migration or the decision index? Check the landed tree (both exit 0 either way; count output):

```bash
git switch --detach origin/master
make heads > "$LOG/heads.txt" 2>&1; grep -c '(head)' "$LOG/heads.txt"    # must print 1
make decision-numbers > "$LOG/dn.txt" 2>&1; grep -vc '^==' "$LOG/dn.txt" # must print 0
```

No more edits here: the next change is step 1, a new worktree. Cleanup:
`docs/maintainers/conventions.md` Cleanup. Deploying: nora-deploy.

## Tool notes
Claude Code's rtk hook refuses every `git` call in a worktree-isolated
subagent; the measured `vcs` workaround is in `nora-land/reference.md`.
Claude Code commits end with a `Claude-Session:` footer.
