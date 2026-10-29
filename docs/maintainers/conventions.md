# Maintainer conventions

Public setup and contribution rules are in [Contributing](../../CONTRIBUTING.md).
Agent procedures are in [AGENTS.md](../../AGENTS.md); the historical reasoning
is in [Conventions rationale](conventions-rationale.md).

## Merge strategy

A real merge commit — `git merge --no-ff`, or `gh pr merge --merge`. Never
squash, never rebase-merge.

Supply an authored subject and body; GitHub merge commits do not run local hooks:

```bash
gh pr merge <N> --merge --match-head-commit <sha> \
  --subject "merge: <the arc>" --body "<what shipped, what was measured>"
```

The `nora-land` skill carries this step in order.

The merge commit's message is **authored**, not GitHub's default *"Merge pull
request #N from …"*. Because the branch's own commits stay visible in
`master`'s history (unlike squash), the merge commit's job is to summarize the
arc: what the branch set out to fix, what was measured, what shipped. That is
what makes `git log master` readable without opening every PR.

**`merge: <the arc>` — the arc is the point.** The checker (see CONTRIBUTING.md §Commit
messages) requires a non-empty arc and rejects an authored-merge prefix worn by
a GitHub-default body: `merge: Merge pull request #1 from …` is the default
wearing the sanctioned prefix, not an authored message. If you have nothing to
say about the arc, the merge is not ready to land.

### PR ownership and tracking

Assign a PR to the person actually responsible for following it through review;
an author may self-assign when taking that responsibility. Request reviewers
who have agreed to review the relevant area. Do not guess maintainers or add
CODEOWNERS entries without confirmed ownership.

Use existing relevant labels when triaging. The issue-form labels below describe
issue shape, not a mandatory PR classification. A milestone represents an agreed
release or project scope: attach it only when that scope includes the change.
An unassigned, unlabelled or milestone-free draft is not a failing code check;
resolve ownership when taking it into review, rather than assigning everyone to
a nominal maintainer. Do not create releases or milestones just to fill fields.

### Automated checks

The lightweight CI workflow runs static checks and architecture/documentation
checks on pull requests and pushes to master. It uses hosted runners, read-only
repository permission and no production secrets. It is a fast baseline, not a
replacement for the affected-test gate in CONTRIBUTING.md or live evaluations. It does
not deploy or grant access to a deployment host. Action updates are grouped by Dependabot.

The `Contribution content` workflow checks live PR titles and bodies on opening,
editing, new commits and label changes. Its `PR content` check is attached to the
current PR head and is required by the public branch-protection setup. Titles
reuse the commit checker; bodies require populated `What` and `Gate on the merged
tree` sections, template heading order and no unfilled prompts. Comments and fenced
examples are ignored. This checks structure, not the truth of a claimed gate result.

Release Please and Dependabot PRs use their generated formats only when authored
by the recognized bot on a branch in this repository. A fork cannot claim that
exception. The release App identity is verified against `RELEASE_APP_ID`.

Issues use the Bug report, Feature request, freeform or design-anchored fields.
Public reports need a descriptive title and the fields shown in their form. Incomplete issues receive
`needs-info` and one bot comment listing corrections, updated as needed and
removed together when corrected. The same validator checks CLI/API submissions,
including unfilled prompts in required sections. This records format completeness,
not evidence quality or permission to dispatch work. The title
already supplies Area, so CLI/API users need not duplicate that field. Explicit
uncertainty such as `#TBD` in decisions/dependencies remains valid. Issues are never
automatically closed or locked by this check.

Common spelling mistakes (pinned local codespell) and long lines/trailing whitespace
produce advisory suggestions only. English-only writing and automatic grammar
judgements are not enforced. No text is sent to an external language service.
The privileged metadata jobs execute only reviewed `master` code, never PR source,
and never interpolate contribution text into shell commands.

## Proving a fix: mutation

A fix is not done when the gate is green: reverting the exact production line
must turn a named test RED. Report the result alongside the gate in the PR body.
This measures the guard independently of the implementation.

The rule came from media-comprehension fixes that survived review with no test
observing their behaviour. Reverting production changes left the suite green;
subsequent mutation checks exposed weak selectors, assertions and fakes. A green
suite alone could not prove the repaired behaviour was guarded.

Use a hand-picked mutation, or `make mutate TABLE=scripts/mutations/<name>.json`.
The table records what a branch proved and is deliberately outside `make gate`.
First establish a collecting, green baseline. Pytest exit 5 means nothing was
collected, not a failed test. The mutant must fail because the reverted behaviour
is observed, not through a broken replacement or `NameError`. A `BROKEN ENTRY`
is a finding about the evidence and must be corrected before claiming proof.

## Issues

Not-yet-built design ideas discussed in a session get **filed as a GitHub
issue** on `mikeng-io/nora`, not left in a chat transcript or a memory file.
Scope and task-specific deltas live on the issue; evidence comments link the run
or review record. Project status is the scheduling view of that same issue.
**File through an issue form**
(`.github/ISSUE_TEMPLATE/`); blank issues are disabled.

[ORCHESTRATION.md](../../ORCHESTRATION.md) owns engineering assignment and artifact
boundaries. Design docs own architecture, rationale, requirements and acceptance,
with links showing delivery coverage. Each issue owns one bounded outcome,
scope/non-goals/delta and acceptance, or references revisioned design acceptance
sections. “What's specified” below is an anchor plus the task's delta, never a
copy of the full design. Project cards carry scheduling metadata and links:
issue, status, priority, required execution/review tiers, run/evidence/unblock
references. They are views of their issues, not separate task bodies. Keep
assignees, milestones, relationships and Development links native. Run/review
records own actual worker/model/toolset/options, input revision and measured
evidence; no checked-in task database or run records. Generic rules stay in the
root policy instead of being copied into issues, cards or design bundles.

Use **Bug report** for a reproducible problem: Description, Steps to reproduce,
Expected behavior, Actual behavior, and Version and environment. Use **Feature
request** for an improvement: Problem and Proposed solution. Both accept a plain
descriptive title and need no internal design document. Never include private
data; follow SECURITY.md for vulnerabilities.

The **freeform** and **design-anchored** forms below remain available for internal
design and implementation work. The forms carry the headings so
the convention survives the moment nobody remembers it.

**Title** — `<Area>: <the gap>`, stated as a fact about what is wrong or
missing, not as a task: *"Note tier: discharge pipeline — route settled note
content on close"*, not *"Add discharge pipeline"*.

**Body** — two shapes are in use; pick the one that fits.

*Design-anchored*, when a design document already specified this:

```
**Design:** `design/<bundle>/<NN-name>.md` §<n> (<what it covers>).

## The gap
<what is broken or missing today, with file:line references>

## What's specified (decided, not open)
<revisioned design acceptance anchors and only this task's scope/delta>

## Not built
<what does not exist yet — "nothing" is a valid answer>

## Depends on / feeds
<cross-issue dependencies; "#TBD" until the other issue exists>
```

*Freeform*, when this issue is proposing the decision:

```
## What
## Why the current shape blocks it
## Shape
## Decisions to record
## Not in this issue
```

That last section matters more than it looks: it scopes out the adjacent thing
the next reader will suggest folding in.

**Issue-shape labels** — set by the issue forms:

| label | set by | means |
|---|---|---|
| `design-anchored` | the *design-anchored* form | a design document already specified this; the issue builds to spec |
| `freeform` | the *freeform* form | the issue itself proposes the decision |
| `bug` | the *Bug report* form | a reproducible problem |
| `enhancement` | the *Feature request* form | a proposed improvement |

These labels distinguish the four issue forms. Area stays in the title prefix
and the form's Area field. Additional PR labels should have a clear triage use;
labels and milestones on GitHub are repository metadata, not created by these
forms. Required form fields do not replace review of the issue's substance.


## The flow: issue → branch → PR → merge

```
idea in a session
   │
   ├─ small, obvious, and doable now? ──▶ worktree + branch + PR.  No issue.
   │                                       The PR body is the record.
   │
   └─ not being built now, or needs scoping? ──▶ FILE AN ISSUE
                                                   │
                    design document written? ──────┤  (large or contested work only)
                                                   ▼
                                          worktree + branch (`<type>/<slug>`)
                                                   ▼
                                          PR, referencing the issue
                                                   ▼
                                    merge commit (--no-ff, authored message)
                                                   ▼
                            issue closed  ──or──  left open for the next PR
```

**When to create an issue.** File one when the work is *not being done right
now* — a design idea from a session, a defect you found while doing something
else, a gap you decided to defer. The test is displacement, not size: anything
that would otherwise survive only in a chat transcript or a memory file gets an
issue, because those are not readable by the next session. Work you are about
to do in the same sitting does not need one; its PR is the record.

**When to update an issue.** When the scope changes, when a dependency gets
filed (replace the `#TBD`), when a measurement changes the picture, and when a
PR lands part of it. An issue that has taken three PRs and says nothing about
them is a stale issue. Update the body for scope, comment for evidence.

**When to close an issue.** Three ways, and each needs a stated reason:

| reason | how |
|---|---|
| **done** | `Closes #N` in the PR that finishes it — GitHub closes it on merge |
| **superseded** | close with a comment naming what replaced it and where the evidence is |
| **won't do** | close with a comment saying what was decided instead, and why |

Never close an issue silently, and never close one because it got old. A stale
issue is a signal about the backlog; a silently closed one is a decision nobody
can find. If the premise stopped holding — the subsystem was deleted, the design
changed underneath it — that is *superseded*, and the comment should say what
reading would have caught it sooner.

**When NOT to close.** A multi-PR design track stays open across incremental
landings. Reference it as "issue #N" in those PR bodies, without the closing
keyword. The design's delivery coverage links each requirement to its issue and
accepted PR/evidence; the issue carries its remaining slice and the Project its
scheduling status. The issue closes when the last piece lands, not the first.

**Linking, in both directions.** Each link exists so someone standing at one
artifact can reach the others:

| from | to | how |
|---|---|---|
| PR | issue | `Closes #N` when it finishes it, plain `issue #N` when it does not |
| commit body | issue | `issue #N` — so `git log` alone reaches the reasoning |
| issue | design doc | `**Design:** design/<bundle>/<file>.md §n` at the top of the body |
| issue | dependent issue | `#TBD` until it exists, then edited in |
| ADR | issue / decision row | the `Index:` line names the DEC row, or `none` with a reason |
| decision row | test | the enforcing test's path, in the row's last column |

The branch name is deliberately *not* in that table: branches are named for the
change, not the ticket, and a branch outlives neither the PR nor the issue.

## Issue → ADR → decision row

Three artifacts, three different jobs, and using the wrong one is how reasoning
gets lost:

| artifact | records | lives in |
|---|---|---|
| **GitHub issue** | a *candidate* — something not yet decided or not yet built | `mikeng-io/nora` |
| **ADR** | a *decision with a cost* — an alternative rejected, a trade accepted | `docs/adr/NNNN-*.md` |
| **decision row** | a *landed* decision and the test that enforces it | `design/09-decision-index.md` |

An open issue is not a decision. A trivial decision gets an index row and
nothing more. An ADR is warranted when the decision will look wrong to someone
who does not know what it replaced — [Nygard
form](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions),
Context / Decision / Consequences, and **the consequences must include what got
worse**. `docs/adr/README.md` carries the full convention.

## Numbering discipline

Never take a DEC-, R-, ADR- or issue number from a chat agreement or from
memory — re-read `design/09-decision-index.md` (or `gh issue list`) at merge
time, not at claim time. Two sessions that carefully coordinate numbers can
still both be wrong. If a number you referenced as a placeholder turns out to
be taken, fix the reference, not the number.

## Cleanup

A branch whose PR merged and whose worktree is gone is dead weight; the history
is in `master`'s merge commits. GitHub automatically deletes merged head branches;
local branches and worktrees still need manual cleanup:

```bash
git worktree list                       # what is checked out where — never delete a branch with a live worktree
git branch --merged master              # local candidates
git branch -r --merged origin/master    # remote candidates
git branch -d <branch>                  # refuses if unmerged, which is the point
git push origin --delete <branch>
```

Before deleting, check for a live session on that branch as well as a worktree.
A branch carrying uncommitted changes from minutes ago is not dead work — it is
somebody else's turn in progress.
