---
name: nora-decision
description: Records a Nora decision the repo's way, choosing the next DEC, R or ADR number from origin/master at merge time, writing the decision-index row with its enforcing test, an ADR in Nygard form, or an issue. Load when a task mentions DEC, R-, a decision row, an ADR, filing an issue, superseding or reversing a decision, or citing one. Not for tool text (nora-tool) or landing the PR (nora-land).
---

# Recording a decision

The reasons live in `docs/maintainers/conventions.md` (sections «Issues», «Issue → ADR → decision
row», «Numbering discipline»), `docs/adr/README.md`, the header of
`design/09-decision-index.md`, and `AGENTS.md` («Decisions change on EVIDENCE, not
on argument», and the «Never take a DEC or R number from a chat agreement» rule).
This skill is the order of the steps and the check after each. Run from the
branch's worktree.

## 0. Pick the artifact

| what you have | what to write |
|---|---|
| a defect you can fix in this session | nothing new: fix it; the PR is the record |
| work a merged design bundle, an ADR or an open issue already names | an edit to THAT (the bundle's sequence, its `_index.yaml` open questions, the issue body) |
| work you decided not to do now | an issue, filed in the same turn you decided, saying why |
| a decision that will look wrong to someone who does not know what it replaced | an ADR |
| a landed decision and the test that enforces it | a decision-index row |

## 1. Filing an issue

1. **Can you fix it now?** Then fix it (gate, then `nora-prove-fix`). File only
   what you have decided not to fix. A residue too small to act on goes in the
   PR body's limitations, not a new issue.
2. **Look for a home first.**
   ```bash
   gh issue list --state all --search "<two or three words>" --limit 20
   git fetch origin master
   git grep -n -i "<the same words>" origin/master -- design docs/adr
   ```
   Check: any hit in a bundle's sequence, an ADR or an open issue means you edit
   that and file nothing. (2026-09-16: #272 duplicated a sequence
   `design/culture-tier/` already held; closed the same day.)
3. **Select and read the existing form** in `.github/ISSUE_TEMPLATE/`, following
   `docs/maintainers/conventions.md` «Issues». Bug report: its five fields;
   Feature request: Problem and Proposed solution. Both use descriptive titles.
   For internal work, keep design-anchored (building a specified design) or
   freeform (proposing a decision), with title `<Area>: <the gap>`.
   **Write the body to a file**, using the selected form's fields.
4. **Scrub the draft.** Issues, comments and PR bodies are outside the history
   scrub, which rewrites git objects only. Describe what a room did; never quote
   a message or name a member; cite `file:line`, row ids, tag ids, metric names.
   Check: read the file once for quoted chat, a member's name, a phone number or
   a platform id before sending. (2026-09-13: three issues rewritten for this.)
5. **File and read back.**
   ```bash
   gh issue create --title "<selected form title>" --body-file <draft file>
   gh issue view <N> --json title,body,labels
   ```
   Apply the selected form's shape label: `bug`, `enhancement`, `design-anchored`
   or `freeform`. When reformatting, remove conflicting shape labels and preserve
   every other label. Check the read-back body and labels match the selected form.

## 2. Writing an ADR

1. **Search before you design.** Look for the published convention (vendor docs,
   the spec, papers) and cite it, including sources that argue against you:
   0015's Context is the pattern. A good measurement says where the cost is, not
   whether the problem is already solved (2026-09-22).
2. **Next number, from all three places:**
   ```bash
   git fetch origin master
   git ls-tree --name-only origin/master docs/adr/ | grep -oE '/[0-9]{4}-' | tr -d '/-' | sort -n | uniq -c
   ls docs/adr/ | grep -oE '^[0-9]{4}' | sort -n | uniq -c
   gh pr list --state open --limit 100 --json number,files \
     --jq '.[] | select(any(.files[]; .path | startswith("docs/adr/"))) | .number'
   ```
   Take one above the highest number any of them shows. Check: a count above 1
   on any line is a collision, not a pattern to follow (two `0013` files landed
   2026-09-17, #330 and #331). Re-run at merge time; step 4 applies to ADRs too.
3. **Shape.** `NNNN-kebab-title.md` in `docs/adr/`. Copy the header of 0015 or 0016:
   title line `# NNNN` plus the title; bullets **Status** (`proposed`), **Date**,
   optional **Supersedes / Extends / Reverses, in part**, and **Index** (the DEC
   or R row, `none` with a reason, or the placeholder «a DEC number is taken at
   merge»). Sections: Context (the measurement and the sources), Decision,
   Consequences with a **Worse** list (required: `docs/adr/README.md`), Rejected.
4. **Status moves, text does not.** `proposed` → `accepted` (the operator's call)
   → `superseded by NNNN`. An accepted ADR is changed by a new ADR; the only edit
   to the old file is its Status line (0015's accepting commit edited 0007's).
   Check: `git diff origin/master -- docs/adr/` touches no accepted ADR beyond
   its Status line.

## 3. Writing a decision-index row

1. **Where.** Append to the last table of `design/09-decision-index.md`: R and
   DEC rows share it in landing order (R-95 to R-98 sit among DEC rows). Columns
   `| ID | Statement | Enforcing test |`. A row is for a LANDED decision; a
   proposed ADR that ships no behaviour keeps its Index placeholder (0016 merged
   that way, #476).
2. **Statement.** The decision in bold first, then provenance (operator and
   date, the design section), what it supersedes or reverses, what is not in
   scope, and the reading that would reopen it («This holds unless ...», as
   DEC-201 and DEC-202 write it).
3. **Enforcing test.** Backticked test paths (with `::test_name`) that exist, or
   the row says `NOT ENFORCED`, `DEPRECATED` or `REVERSED`. A new behavioural
   rule (R-N) also registers its test in `RULE_TESTS` in
   `tests/test_acceptance_rules.py`.
4. **Check:**
   ```bash
   uv run pytest -q tests/architecture/test_decision_index_tests_exist.py \
     tests/architecture/test_decision_index_ids_are_unique.py \
     "tests/test_acceptance_rules.py::test_every_enforced_rule_has_a_named_test" \
     tests/architecture/test_doc_references_resolve.py > "${TMPDIR:-/tmp}/decision-tests.log" 2>&1; RC=$?
   echo "RC=$RC"; grep -E 'passed|failed|error' "${TMPDIR:-/tmp}/decision-tests.log"
   ```
   PASS is `RC=0` and a passed count; anything else is FAIL or ERROR.
5. **Retiring a row.** Mark it `DEPRECATED` (or `DEPRECATED IN PART`, `REVERSED`);
   never renumber it and never delete it: other documents cite the number.

## 4. Taking the number, at merge time

The number is chosen when the PR merges, from origin/master, never when you start:
two branches can take the same number and git merges both rows without a conflict.
`nora-land` owns the merge; do this immediately before it.

1. **Bring master in** the way `nora-land` does it (an authored `merge:` subject;
   the commit hook refuses a bare `git merge origin/master`).
2. **Highest on origin/master.** Bundles mint and reserve DEC numbers in their own
   decisions files (`design/ambient-loop/03-decisions.md` reserves DEC-163 to 175,
   `design/culture-tier/03-decisions.md` defines DEC-181 to 185, none of them in the
   index), so read both the index and the tree:
   ```bash
   for ns in DEC R; do
     idx=$(git show origin/master:design/09-decision-index.md | grep -oE "^\| $ns-[0-9]+[a-z]? \|" | grep -oE '[0-9]+' | sort -n | tail -n 1)
     any=$(git grep -ohE "(^|[^A-Za-z])$ns-[0-9]+" origin/master -- design docs | grep -oE '[0-9]+' | sort -n | tail -n 1)
     echo "$ns  index=${idx:-EMPTY}  design+docs=${any:-EMPTY}"
   done
   ```
   Check: both columns print a number. `EMPTY` is an ERROR (fetch or path),
   never «nothing taken». Your number is one above the larger.
3. **Rows open PRs are adding:**
   ```bash
   for n in $(gh pr list --state open --limit 100 --json number --jq '.[].number'); do
     gh pr diff "$n" | grep -oE '^\+\| (DEC|R)-[0-9]+[a-z]? \|' | sed "s/^/#$n /"
   done
   ```
4. **Duplicates.** On the merged tree, `tests/architecture/test_decision_index_ids_are_unique.py`
   passes (`scripts/affected_tests.py` maps `design/09-decision-index.md` into
   `make gate` whenever a diff touches it, and to the whole suite otherwise —
   run `make full-gate` to always include it regardless of what changed) and
   `make decision-numbers` prints nothing under either heading. On
   origin/master itself:
   ```bash
   for ns in DEC R; do
     d=$(git show origin/master:design/09-decision-index.md | grep -oE "^\| $ns-[0-9]+[a-z]? \|" | tr -d '| ' | sort | uniq -d)
     echo "$ns duplicates: [${d}]"
   done
   ```
   `uniq -d` exits 0 with or without duplicates, so read the string: `[]` is
   clean. The trailing `\|` in the pattern is what keeps `R-6b` from reading as a
   second `R-6`; without it the check reports false duplicates.
5. **If your number is taken,** renumber YOUR row and every reference to it, never
   the other one: `git grep -nE 'DEC-<n>([^0-9]|$)'` on the branch, the ADR's
   Index line, the commit bodies, and the PR title (`gh pr edit <N> --title`).
   Check: the same grep afterwards finds only your decision. (#444's title says
   DEC-199; its row landed as DEC-200. DEC-155 was allocated twice, #251 and #259.)

## 5. Citing, or reversing, a decision

1. **Read the row on origin/master**, not a chat summary or memory:
   `git grep -nE 'DEC-<n>([^0-9]|$)' origin/master -- design/09-decision-index.md`.
   Check the row and every later row naming it for `DEPRECATED`, `REVERSED`,
   «Amended» or «supersedes».
2. **A document is not uniformly current.** Before citing a summary table or
   overview, look for a later dated section on the same subject, then blame it:
   ```bash
   git blame -L <start>,<end> -- <file>
   git log -1 --format='%h %cs %s' <sha from the blame>
   ```
   A section older than the decision it contradicts is dead text, not a finding
   (2026-09-17: stale overview prose nearly got a correctly built tier deleted).
3. **Reverse only on new evidence** (a measurement, a read-back, a probe). State
   it in one line and write the new reopen condition. A better-argued restatement
   is not evidence.
4. **Sweep every quoting file.** A reversal lands in the deciding file and misses
   the files that quoted the old answer. Grep the withdrawn answer's own words
   (the endpoint, the number, the deleted verb):
   `git grep -n -i '<old vocabulary>' -- design docs nora AGENTS.md CONTRIBUTING.md`.
   Check: every hit is changed or carries a marker (mark, do not delete).

## Tool notes

- **Claude Code with the rtk hook:** output of `git` and `ls` can come back
  condensed («filtered»). For any number you parse, run the command through
  `rtk proxy <command>` so the output is raw.
