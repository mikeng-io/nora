---
name: nora-decision
description: Records Nora decisions, ADRs, GitHub reports/roadmap and Linear execution work. Allocates DEC/R/ADR numbers from git history at merge time and checks duplicates. Load for filing, planning, citing or reversing them; nora-design owns design bundles, nora-land owns landing and nora-tool owns tool text.
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
| executable work or a bounded investigation | a Linear work brief, reusing its existing home (`nora-dispatch`) |
| capability direction or roadmap outcome | a GitHub Project roadmap item linking execution |
| a repository defect/problem report | a GitHub issue; link any implementation work in Linear |
| a decision that will look wrong to someone who does not know what it replaced | an ADR |
| a landed rule and the test that enforces it | a decision-index row |

## 1. Filing a defect report or work brief

1. **Can you finish it within the agreed slice?** Then do it (`nora-land`,
   `nora-prove-fix`); a small direct change uses its PR as the record. Creating
   a ticket is not delivery. Tiny residues belong in PR limitations.
2. **Look for a home first.** Search Linear for execution, GitHub Project for roadmap
   outcomes, and GitHub Issues for reports. Keep one work identity; do not create a
   GitHub Issue to make a Linear task eligible for execution.
3. **Select the destination.** GitHub reports use `.github/ISSUE_TEMPLATE/`
   and the form conventions. Linear execution follows the [execution convention](../../../docs/maintainers/conventions.md#linear-execution).
   Use the work brief in `nora-dispatch` and native properties; no second spec/plan.
4. **Scrub before writing.** No private conversations, identities, credentials
   or production rows in either service. Link permitted source/evidence rather
   than copying logs. Read the actual draft for private values.
5. **Write and read back.** Confirm the intended workspace/team, work/report
   identity, description and native relations. A missing connector is blocked;
   do not silently fall back to a new GitHub execution issue. For GitHub reports:
   ```bash
   gh issue create --title "<form title>" --body-file <draft file>
   gh issue view <N> --json title,body,labels
   ```

Update the current Linear description in place. Comments are discussion or
precise evidence, not a growing queue. Never hide current completion conditions
in an archive. A GitHub defect can link its Linear work without mirroring it.
Existing work moves only after its successor, active owner and holds are read
back; preserve history and use a cross-link instead of bulk-copying comments.

## Roadmap planning

Follow [roadmap conventions](../../../docs/maintainers/conventions.md#roadmap).
Read the design and current implementation before changing an outcome. Reuse
its GitHub Project item; keep only capability intent, success signal, horizon
and Linear execution links. New outcomes can use Project draft items without
creating repository issues. Verify product/view routing before adding drafts.
Update outcome progress only on evidence; Linear Done or merged children alone
cannot mark it Achieved. Do not migrate the roadmap into Linear or create a
parallel initiative. Read back changed fields and links. Planning grants no
execution, spending or deployment authority.

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

- Runtime quirks (output-compressing hooks, background notices, sandboxed git): `.agents/runtimes/`.
  For any number you parse, read uncompacted output.
