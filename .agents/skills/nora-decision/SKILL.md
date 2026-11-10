---
name: nora-decision
description: Records Nora decisions, ADRs, issues and roadmap directions. Allocates DEC/R/ADR numbers from git history at merge time and checks duplicates. Load for filing, planning, citing or reversing them; nora-design owns design bundles, nora-land owns landing and nora-tool owns tool text.
---

# Recording a decision

Git and GitHub are the log. The ADR is the one decision record kept as history;
the decision index lists the decisions that hold now; the design bundle states
the design (`nora-design`). Conventions: `docs/maintainers/conventions.md`
(«Issues», «Issue → ADR → decision row»), `docs/adr/README.md`. Run from the
branch's worktree.

## 0. Pick the artifact

| what you have | what to write |
|---|---|
| a defect you can fix in this session | nothing new: fix it; the PR is the record |
| a change to the system's design | the bundle, rewritten in place (`nora-design`) |
| work, a plan, an open question or a roadmap item | an issue (or an edit to the open issue that owns it) |
| a decision that will look wrong to someone who does not know what it replaced | an ADR |
| a landed rule and the test that enforces it | a decision-index row |

## 1. Filing an issue

1. **Can you fix it now within the agreed slice?** Then fix it (`nora-land`,
   `nora-prove-fix`). Operator agreement means continue; an issue is not
   delivery. A residue too small to act on goes in the PR's limitations.
2. **Look for a home first.**
   ```bash
   gh issue list --state all --search "<two or three words>" --limit 20
   ```
   Check: an open issue on the subject means you edit it and file nothing.
3. **Select the form** in `.github/ISSUE_TEMPLATE/` («Issues» in the
   conventions). For work a bundle designs, the body starts
   `**Design:** design/<topic>/<file>.md`. Write the body to a file.
4. **Scrub the draft.** Issues and PR bodies are outside the history scrub.
   Describe what a room did; never quote a message or name a member; cite
   `file:line`, row ids, metric names. Check: read the file once for quoted
   chat, a member's name, a phone number or a platform id.
5. **File and read back.**
   ```bash
   gh issue create --title "<form title>" --body-file <draft file>
   gh issue view <N> --json title,body,labels
   ```
   Check: the form's shape label is set and the body matches the form.

## Roadmap planning

Follow [roadmap conventions](../../../docs/maintainers/conventions.md#roadmap).
Read the bundle and the code before calling work unbuilt. Reuse the outcome's
issue or create one through step 1, add it to Universal Roadmap's Nora view as
Level=Outcome, and set lifecycle/horizon with a reason. Read back membership and
fields. Planning grants no implementation, spending or deployment authority.

## 2. Writing an ADR

1. **Search before you design.** Cite the published convention (vendor docs,
   the spec, papers), including sources that argue against you.
2. **Next number:**
   ```bash
   git fetch origin master
   git ls-tree --name-only origin/master docs/adr/ | grep -oE '/[0-9]{4}-' | tr -d '/-' | sort -n | uniq -c
   gh pr list --state open --limit 100 --json number,files \
     --jq '.[] | select(any(.files[]; .path | startswith("docs/adr/"))) | .number'
   ```
   One above the highest. Check: a count above 1 is a collision. Re-run at
   merge time (§4).
3. **Shape.** `NNNN-kebab-title.md` in `docs/adr/`, header as in 0015: **Status**
   (`proposed`), **Date**, optional **Supersedes**, **Index** (the row, or
   `none` with a reason). Sections: Context, Decision, Consequences with a
   **Worse** list, Rejected.
4. **Status moves, text does not.** `proposed` → `accepted` (the operator's
   call) → `superseded by NNNN`. Check: `git diff origin/master -- docs/adr/`
   touches no accepted ADR beyond its Status line.

## 3. Writing a decision-index row

1. **Where.** The last table of `design/09-decision-index.md`,
   `| ID | Statement | Enforcing test |`. A row is for a LANDED decision.
2. **Statement.** The rule in bold, then the reading that would reopen it
   («This holds unless ...»). No provenance, dates or history: the commit and
   the ADR hold those.
3. **Enforcing test.** Backticked test paths that exist, or `NOT ENFORCED`. A
   behavioural rule (R-N) registers its test in `RULE_TESTS` in
   `tests/test_acceptance_rules.py`.
4. **Check:**
   ```bash
   uv run pytest -q tests/architecture/test_decision_index_tests_exist.py \
     tests/architecture/test_decision_index_ids_are_unique.py \
     "tests/test_acceptance_rules.py::test_every_enforced_rule_has_a_named_test" \
     tests/architecture/test_doc_references_resolve.py > "${TMPDIR:-/tmp}/decision-tests.log" 2>&1; RC=$?
   echo "RC=$RC"; grep -E 'passed|failed|error' "${TMPDIR:-/tmp}/decision-tests.log"
   ```
   PASS is `RC=0` and a passed count.
5. **A decision that no longer holds is deleted** from the index, with its
   test or its `RULE_TESTS` entry. A changed decision is a rewritten row. Its
   number is never reused (§4 reads history).

## 4. Taking the number, at merge time

Two branches can take the same number and git merges both rows without a
conflict, so the number is chosen immediately before merge (`nora-land` §8).

1. **Bring master in** with an authored `merge:` subject.
2. **Highest number ever used**, including deleted rows:
   ```bash
   for ns in DEC R; do
     n=$(git log -p origin/master -- design docs | grep -ohE "(^|[^A-Za-z])$ns-[0-9]+" | grep -oE '[0-9]+' | sort -n | tail -n 1)
     echo "$ns highest=${n:-EMPTY}"
   done
   ```
   Check: both print a number; `EMPTY` is an ERROR (fetch or path). Yours is
   one above.
3. **Rows open PRs are adding:**
   ```bash
   for n in $(gh pr list --state open --limit 100 --json number --jq '.[].number'); do
     gh pr diff "$n" | grep -oE '^\+\| (DEC|R)-[0-9]+[a-z]? \|' | sed "s/^/#$n /"
   done
   ```
4. **Duplicates.** `tests/architecture/test_decision_index_ids_are_unique.py`
   passes on the merged tree, and `make decision-numbers` prints nothing under
   either heading.
5. **If your number is taken,** renumber YOUR row and every reference to it
   (`git grep -nE 'DEC-<n>([^0-9]|$)'`, the ADR's Index line, the PR title).

## 5. Citing or reversing a decision

1. **Read the row on origin/master**, not a summary or memory:
   `git grep -nE 'DEC-<n>([^0-9]|$)' origin/master -- design/09-decision-index.md`.
   No row means the decision no longer holds; `git log -S 'DEC-<n>'` finds why.
2. **Reverse only on new evidence** (a measurement, a read-back, a probe).
   State it in one line and write the new reopen condition.
3. **Rewrite every place that states the old answer**: the row (§3.5), the
   bundle (`nora-design`), the root guides. Grep the withdrawn answer's own
   words: `git grep -n -i '<old vocabulary>' -- design docs nora *.md .agents`.
   Check: every hit is rewritten or deleted; none is marked.

## Tool notes

- **Claude Code with the rtk hook:** `git` and `ls` output can come back
  condensed. For any number you parse, run the command through
  `rtk proxy <command>`.
