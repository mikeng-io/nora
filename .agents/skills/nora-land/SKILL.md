---
name: nora-land
description: Lands a Nora change from first edit to merged master, covering a new worktree from origin/master, the test loop, the gate read by its exit code, a Conventional Commit, the release fragment, a PR from the template, independent review, an authored merge commit and the post-merge read-back. Load when starting work, committing, opening a PR or merging. Not for deploying to norty (nora-deploy).
---

# nora-land

Worktree, sync, (gate, commit)*, fragment, push, PR, review, merge, read-back.
Rules and reasons: `CONTRIBUTING.md`, `AGENTS.md`. Every step ends in a check:
the log or the read-back, never an exit code alone. Go end to end; stop only
for a blocker or a fact only the operator holds (a decision is not input).

```bash
SLUG=<kebab-slug>; WT=<new-worktree-path>        # WT outside every checkout
LOG=${TMPDIR:-/tmp}/land-$SLUG; mkdir -p "$LOG"   # fixed, OUTSIDE the worktree
```

Shell state may not survive between calls (Claude Code's Bash): re-set `SLUG`
and `LOG` in every call; carry results in files (`$LOG/gate.rc`), not variables.

## 1. Start: a new worktree on a new branch

```bash
git fetch origin
git worktree add -b "mikeng-io/$SLUG" "$WT" origin/master
cd "$WT" || exit 1
uv sync --extra evals        # = make setup; without it pytest dies at COLLECTION
git status -sb; git config core.hooksPath
```

Check: `## mikeng-io/<slug>...origin/master`, no ahead/behind; hooksPath ends
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
grep -cE '^uv run (ruff|mypy|lint-imports|pytest)' "$LOG/gate.log"   # 4 = every step ran
grep -E 'passed|failed|error' "$LOG/gate.log" | tail -n 5
```

PASS needs `GATE_RC=0`, 4 steps, and a pytest summary with no `failed`: make
stops at the first red step, so a run that died in ruff never reached pytest.

- Never `make gate | tail`: the rc is tail's (2026-09-22, merged a red master).
- In the background, read `$LOG/gate.rc`, never the completion notice: it
  reports the last command's rc (2026-09-22: notice 0, real rc 2, twice).

**Docs-only** (every path is `.md` outside `nora/doctrine` and `nora/dreamer`,
or under `docs/`, `design/`, `release-fragments/`):

```bash
git add <paths>    # the doc gate scans `git ls-files`: an unstaged new doc is never read (#391)
uv run pytest -q tests/architecture/test_doc_references_resolve.py tests/test_release_notes.py \
  tests/architecture/test_agent_skills.py > "$LOG/docs.log" 2>&1; echo "GATE_RC=$?" > "$LOG/gate.rc"
```

`design/09-decision-index.md` changed: add `tests/architecture/test_decision_index_tests_exist.py`
and `tests/architecture/test_decision_index_ids_are_unique.py`. Run `make gate`
once before the merge anyway (2026-09-22: a docs-only merge reddened master).

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

## 5. Release fragment

Any change outside `release-fragments/`, docs included, adds a NEW file under
`release-fragments/unreleased/`, not the parent directory (audit 2026-09-24:
guessed wrong repeatedly). Schema: `release-fragments/README.md`.

- `type`: `feature`, `fix`, `change`, `security` or `none`; no `internal` (#172).
- Rendering types need a `summary`; `details`/`limitations` are string arrays.
- Internal work: `{"type": "none", "reason": "<why no user sees it>", "details": [], "limitations": []}`.
  A `none` with non-empty `details` or `limitations` is refused.

Commit it, then validate (it reads committed `base...HEAD`, wants the file ADDED):

```bash
uv run python scripts/check_release_fragments.py --base origin/master > "$LOG/frag.log" 2>&1; FRAG_RC=$?
```

Same as `make release-check`. PASS is `FRAG_RC=0`; 2 is no new fragment or an
invalid one. It lists every bad file in the directory, including ones your
branch never touched: fix those in their own commit, name them in the PR.

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
  merged tree`, `Deploy note`, `Closes`; delete what does not apply. Never
  `Summary` or `Test plan`, both retired (still in 11 of the last 140 PRs).
- Gate section: the real outcome (GATE_RC, pass count, origin/master sha merged
  in); a pre-existing unrelated failure is named with its issue number.
- Deploy note: only for `nora/doctrine` or `nora/dreamer`, bind-mounted on the
  deploy host and live at its next pull, no rebuild.
- `Closes #N` only when this PR fully resolves it; otherwise `issue #N`.
- A fix reports its mutation result (`CONTRIBUTING.md` Proving a fix).

## 7. Review, by someone who is not the author

- A fresh subagent, another tool or another model, never the session that
  wrote it (which model: `reference.md`). Give it the diff (`gh pr diff <N>`) and the PR's claims.
- Verify each finding before acting on or dismissing it: open the line, run the
  test, revert the fix and watch the named test fail.
- A clean review is not a pass: one plus a green gate missed an empty-body read
  returned as `ok`; the live read-back caught it (2026-09-24).
- Review fixes: steps 3 and 4 again, push, re-check `headRefOid`.

## 8. Merge: a real merge commit, authored message

```bash
git status -sb     # nothing ahead: pushed head == reviewed head
gh pr merge <N> --merge --match-head-commit "$(git rev-parse HEAD)" \
  --subject "merge: <the arc> (#N)" --body-file "$LOG/merge-body.md"
```

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
`CONTRIBUTING.md` Cleanup. Deploying: nora-deploy.

## Tool notes

Claude Code with the rtk hook refuses every `git` call inside a
worktree-isolated subagent; the measured workaround (a `vcs` wrapper) is in
`.agents/skills/nora-land/reference.md`. Claude Code commits end with a
`Claude-Session:` footer.
