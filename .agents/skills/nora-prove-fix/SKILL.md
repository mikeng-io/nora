---
name: nora-prove-fix
description: Proves a Nora code fix is guarded by reverting it and watching a named test go red, then green again, by hand or with a mutation table (make mutate). Load when a task says prove the fix, falsify, revert and expect red, mutation, mutate table, survived, or before writing "reverting X reddens Y" in a PR. Not for measuring a prompt or model behaviour change; that is nora-eval.
---

# nora-prove-fix

Regression protection is proved when reverting the exact production line turns
a named test red, and restoring it turns that test green. This proves the guard,
not every task acceptance obligation; `make gate` green is not that claim. The
rule and its history are `docs/maintainers/conventions.md` "Proving a fix: mutation"; the runner is
`scripts/mutate.py` and the table format is `scripts/mutations/README.md`.

## Procedure

1. **Commit the fix and its test first.** A real Conventional-Commit subject: the
   commit-msg hook rejects `wip:`. `git checkout` restores whatever is committed,
   so a probe on an uncommitted fix destroys the fix and misreads the result (#168).
   ```bash
   git add <files>
   git commit -m "fix(<scope>): <what>"; echo "COMMIT_RC=$?"
   git status --porcelain
   ```
   Check: `COMMIT_RC=0` and the status output is empty.

2. **Baseline: the named test is green and collects.** pytest exit 5 means nothing
   was collected, not failed.
   ```bash
   uv run pytest -q <tests/…py::test_name> > /tmp/base.log 2>&1; echo "RC=$?"
   grep -E "passed|failed|error" /tmp/base.log
   ```
   Check: `RC=0` and at least one `passed`.

3. **Revert the fix, as its own command.** Never chain it behind anything that can
   fail: `&&` after a rejected commit once skipped the revert, and the green that
   followed meant "never ran" (2026-09-17).
   ```bash
   git checkout HEAD~1 -- <production file>
   git diff --stat HEAD -- <production file>
   git grep -n '<a line the fix added>' -- <production file>
   ```
   Check: the diff shows the file changed and the grep no longer finds the fix.
   For one clause of a larger change, use a table (step 7) instead of a whole-file
   revert.

4. **Red.**
   ```bash
   uv run pytest -q <same selector> > /tmp/mut.log 2>&1; echo "RC=$?"
   grep -E "passed|failed|error|Error" /tmp/mut.log
   ```
   Check: `RC=1` and the failure is the reverted behaviour's assertion. A
   `NameError`, `ImportError` or collection error is a broken probe, not a catch.

5. **Restore and read it back.**
   ```bash
   git checkout HEAD -- <production file>
   git status --porcelain
   ```
   Check: empty status.

6. **Green again.** Re-run step 2's command. Check: `RC=0`, same count as baseline.

7. **Table form, for several clauses.** One entry per fix you claim: `name`, `file`,
   `old` (text as committed, exactly once), `new` (text before the fix), `target`
   (pytest selector, or `go:<module dir> <go test args>`).
   ```bash
   make mutate TABLE=scripts/mutations/<name>.json > /tmp/mutate.log 2>&1; echo "RC=$?"
   grep -E "RED|GREEN|BROKEN|restore|ALL MUTATIONS" /tmp/mutate.log
   ```
   Check: `RC=0` and `ALL MUTATIONS CAUGHT`. `GREEN (BAD)` is a survivor;
   `BROKEN ENTRY` (empty selector, baseline red, anchor found 0 or 2+ times, mutant
   did not run) is a finding about your evidence, not noise to route around. It is
   not part of `make gate`: run it yourself.

8. **Inspect tables that name a file you touched**, not just yours. Anchors are
   literal source, so a later edit silently breaks an older table's entries
   (#443 broke two of another table's; #457 re-pointed nine).
   ```bash
   for f in $(git diff --name-only origin/master...HEAD); do
     grep -l "\"file\": \"$f\"" scripts/mutations/*.json
   done | sort -u
   ```
   Run the entries needed for this change's proof and those whose behavior,
   anchors or selectors it changes. Repair breakage this diff introduces.
   Compare other failures with the base before assigning ownership; unrelated
   pre-existing broken entries go to their existing issue/design or PR limitation,
   not automatic repair in this branch. If they prevent the required proof,
   report that obligation unavailable until resolved. Never claim an entire table
   passed when only selected entries ran or any failure remains.

## When a mutant survives

- **Ask what the test's expected value is derived from.** If it comes from the
  constant, function or table the mutant edits, the test agrees with any answer:
  point it at an independent observation (the call site, the rendered output, the
  stored row) instead of adding a second test beside it (three survivors, #318
  and #331).
- **A test can be green on the defect.** A band pinned against the prompt's own
  worked example was green on the value that wedged production (2026-09-18).
  Pin the property production violated, and replace the test rather than
  re-pointing its numbers.
- **Pair every zero-assertion with a positive control.** `== 0` is also true of a
  stage that raised; assert the positive outcome and read back what was written
  (`docs/maintainers/conventions-rationale.md`, "Pair every zero-assertion with a positive control").
  Falsify per clause, not per change.

## After a rename

- **Mutation tables:** apply the rename to `scripts/mutations/` (both `old` and
  the selector in `target`), then count stale anchors before and after; the
  absolute count proves nothing because tables rot without renames too.
  ```bash
  uv run python -c 'import json,pathlib as p
  for t in sorted(p.Path("scripts/mutations").glob("*.json")):
      for e in json.loads(t.read_text()):
          f=p.Path(e["file"]); n=f.read_text().count(e["old"]) if f.exists() else -1
          if n!=1: print(t.name, n, e["name"][:60])' | wc -l
  ```
  Leave `nora/infra/sql/migrations/versions/` out of any rename: that history
  records what ran.
- **Prompt tags and attributes:** grep the OLD name across `tests/`. A forgery
  probe whose payload names a tag the code no longer emits passes while testing
  nothing (#314). Then falsify each probe: delete the escape and watch it redden.
  ```bash
  git grep -n '<old-tag-name>' -- tests/
  ```
  Check: no hits, or each hit updated and falsified.

## Writing it in the PR

Write "reverting X reddens Y" only for a revert you watched turn that named test
red in that run; four such claims in one session were false (2026-09-11). If you
reasoned it from the mechanism, write "expected", not "verified". Formats seen on
merged PRs:

```
Mutation: `make mutate TABLE=scripts/mutations/<name>.json` on <sha>, from a
clean committed tree: N/N RED, ALL MUTATIONS CAUGHT, 0 BROKEN.
Tables naming touched files: <table>: ALL MUTATIONS CAUGHT, 0 BROKEN; ...
By hand: reverting <file>'s <clause> turns `<test_name>` red; restored, green.
```

## Tool notes

- **Claude Code:** the rtk hook rewrites `git` and `pytest` and compacts their
  output; use `rtk proxy <cmd>` when you need the raw failure text. Inside a
  worktree-isolated subagent every `git` form is refused (`git -C` too); use
  the wrapper in nora-land's Tool notes.
- **Claude Code:** a backgrounded command's notice reports the LAST command's exit
  code (a trailing `echo` or `tee`), so write `RC=$?` to a file and read the file.
