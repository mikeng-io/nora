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
is a finding about the evidence and must be corrected before claiming that proof.
Repair this diff's broken guards; compare historical failures with the base and
keep unrelated existing debt outside the change. Missing required proof remains
unavailable, never a pass. `nora-prove-fix` owns the bounded table procedure.

## Issues

GitHub Issues hold repository defect/problem reports and community requests.
They are not the engineering execution queue or roadmap. Linear owns work,
priorities, dependencies and outcomes; a report can link its Linear work without
copying the task description or status back and forth. Small direct changes
need no ticket: the PR records the operator's intent and result.

[ORCHESTRATION.md](../../ORCHESTRATION.md#artifact-ownership) defines artifact
ownership; `nora-work` routes the lifecycle and `nora-decision` records reports
or deferred work. Follow the relevant procedure rather than copying it here.

Use **Bug report** for a reproducible problem: Description, Steps to reproduce,
Expected behavior, Actual behavior, and Version and environment. Use **Feature
request** for an improvement: Problem and Proposed solution. Both accept a plain
descriptive title and need no internal design document. Never include private
data; follow SECURITY.md for vulnerabilities.

The **freeform** and **design-anchored** forms remain supported for reports
and retained references. They do not create an execution plan or dispatch
authority. Internal work uses Linear even when it links one of these reports.

**Title** — `<Area>: <the gap>`, stated as a fact about what is wrong or
missing, not as a task: *"Note tier: discharge pipeline — route settled note
content on close"*, not *"Add discharge pipeline"*.

**Body** — two shapes are in use; pick the one that fits.

*Design-anchored*, when a design document already specified this:

```
**Design:** `design/<topic>/<file>.md` (<what it covers>).

## The gap
<what is broken or missing today, with file:line references>

## What's specified (decided, not open)
<the design this builds to, by link, and this task's acceptance>

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
and the form's Area field; issue triage maps recognized values to the existing
nine `area:` labels, using the same vocabulary as PR triage. The form field takes
precedence when a recognized title agrees; an absent field uses the title prefix.
Unknown or conflicting input withdraws only those nine managed area labels.
Other labels are preserved; no classification grants dispatch or merge authority.
Correct an automatic classification by editing Area/title, not by overriding its
managed label. Additional PR labels should have a clear triage use;
labels and milestones on GitHub are repository metadata, not created by these
forms. Required form fields do not replace review of the issue's substance.


## The flow: work → branch → PR → merge

Direct operator work goes to a worktree/PR. Work needing durable ownership uses
one Linear work brief, refined only enough to execute (`nora-dispatch`). An
architecture change updates its design owner first or in the same PR. The PR
links the work; review/CI and the authored merge establish source delivery.
The outcome owner then reads back the required completion conditions.

Use `nora-dispatch` for refinement, scope changes and session handoff; its
procedure owns those steps. Tracker integrations reflect delivery progress,
not proof of whole-outcome acceptance.

For a real GitHub report, use `Closes #N` only when the PR fully resolves that
report; otherwise link it without closing. Close superseded/not-planned reports
with their reason and successor when present. Do not close retained planning
issues during migration until their Linear successors, obligations and active
owners have been read back. Never delete issue/PR history to reset a board.

Branches remain `<type>/<slug>`; link Linear by PR URL/body rather than requiring
a ticket-shaped branch name. ADRs link the relevant work/report; decision rows
link their enforcing tests. No new task database or status-sync daemon.

## Issue → ADR → decision row

These artifacts have different jobs; confusing them loses the intent:

| artifact | records | lives in |
|---|---|---|
| **Linear work brief** | a proposed outcome and its completion boundary | Linear |
| **GitHub issue** | a repository defect/problem report | `mikeng-io/nora` |
| **ADR** | a *decision with a cost* — an alternative rejected, a trade accepted | `docs/adr/NNNN-*.md` |
| **decision row** | a *landed* decision and the test that enforces it | `design/09-decision-index.md` |

An open work item or defect report is not a decision. A trivial decision gets
an index row and nothing more. An ADR is warranted when the decision will look wrong to someone
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

## Local comments and configuration sources

Keep comments short and descriptive. Maintained docs own architecture, rationale
and decisions. The [configuration contract](../configuration.md) amends the old
environment-only credential-source guidance; `*_env` references retain their names.
Private `secret.toml` and generated delivery files never enter Git. Config and
secret changes require validated controlled recreation; identity/policy retain
their existing validated reload.


## Roadmap

Linear owns the organization roadmap and engineering work. Use a project for a
substantial outcome spanning independently deliverable work; use an initiative
only for useful cross-project grouping. Smaller outcomes remain issues or
parent/sub-issues. Do not introduce every hierarchy level for every change.
Project overviews state intent and design links; work briefs own bounded
completion conditions. Milestones are optional real checkpoints, not mandatory
SDLC documents. Dates stay empty until committed.

Use `nora-work` for lifecycle routing and `nora-decision` for work records.
Read actual team states rather than assuming particular names or automations.

GitHub Projects are not a parallel roadmap or execution queue. Reset obsolete
Nora planning views by archiving/closing them after the selected unfinished work
has read-back-verified Linear successors. Preserve reports, PRs, evidence and
running owners; do not bulk-import historical comments or close unrelated work.
An unavailable Linear connector blocks cutover, not ongoing work already owned
by a session. Existing work retains its current authority until linked cutover.
MCP access is not automatic dispatch; native agent integrations or an explicitly
configured adapter own event-driven starts. No new paid coding, Loops, retry,
merge or deploy authority follows from planning, migration or status changes.
