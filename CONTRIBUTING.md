# Contributing to Nora

Branch, commit, PR and issue conventions. `AGENTS.md` carries the rules for
working *inside* the repo (the gate, the "Never" list, the operational
constraints); this file carries the rules for getting work *into* it.

These conventions were already being followed — consistently, across ~30 merged
PRs — but as habit rather than as a document, which is how a 2026-09-08 audit
found three generations of branch naming alive at once, fifteen merged-but-
undeleted remote branches, and an issue whose premise had gone stale. This file
writes down what was already there. Where a recognized standard covers the
ground, it is cited rather than reinvented.

- [Branch naming](#branch-naming)
- [Commit messages](#commit-messages)
- [Merge strategy](#merge-strategy)
- [Pull requests](#pull-requests)
- [Proving a fix: mutation](#proving-a-fix-mutation)
- [Issues](#issues)
- [The flow: issue → branch → PR → merge](#the-flow-issue--branch--pr--merge)
- [Issue → ADR → decision row](#issue--adr--decision-row)
- [Numbering discipline](#numbering-discipline)
- [Cleanup](#cleanup)

## Branch naming

**`mikeng-io/<kebab-case-slug>`.** The slug describes the change, not a ticket
number — `mikeng-io/media-caption-memory-extract`,
`mikeng-io/remove-note-entity-chain`.

Two earlier generations are still visible in `git log`. Don't imitate them:

| generation | shape | status |
|---|---|---|
| current | `mikeng-io/<slug>` | use this |
| second | `feat/`, `fix/`, `docs/`, `chore/` + `<slug>` | superseded |
| first | `worktree-<slug>` | superseded |

Every change starts in its own worktree on its own branch and reaches `master`
by merge — a one-line doc fix included. `master` in the primary checkout is a
bind-mount source for the running container, so an edit there is a deploy;
`AGENTS.md` and `docs/CONVENTIONS.md` carry that rule and the incident behind
it. Landing a merge does not move you back in: the next change starts in a new
worktree too.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/) —
`type(scope): description`:

| type | for |
|---|---|
| `feat` | new capability |
| `fix` | defect repair |
| `docs` | documentation |
| `test` | tests |
| `refactor` | restructuring with no behaviour change |
| `chore` | tooling, hygiene, dependencies |
| `change` | a deliberate behaviour change that is neither feature nor fix |
| `merge` | the merge commit for a branch (see below) |

The description is a full clause, and specific beats generic: *"a sampling run
builds one guard per sample, not one per run"* names the defect; *"fix sampling
bug"* names nothing.

**The body carries the why**, in this repo's comprehend → measure → serialize
shape: what was broken, what was measured (numbers, before and after, when
there are any), what shipped, and what it does not cover. A body that restates
the subject is not doing its job — the subject serves `git log --oneline`, the
body serves whoever has to decide if this change is safe to build on.

Commits authored in a Claude Code session end with a `Claude-Session:` footer.

## Merge strategy

A real merge commit — `git merge --no-ff`, or `gh pr merge --merge`. Never
squash, never rebase-merge.

The merge commit's message is **authored**, not GitHub's default *"Merge pull
request #N from …"*. Because the branch's own commits stay visible in
`master`'s history (unlike squash), the merge commit's job is to summarize the
arc: what the branch set out to fix, what was measured, what shipped. That is
what makes `git log master` readable without opening every PR.

## Pull requests

**Title** — the same Conventional-Commit shape as the commit subject, with
`(#N, DEC-M)` appended when it closes an issue or lands an indexed decision.

**Body** — one block per piece of work, each naming a concrete artifact: the
file that gates it, the test that would fail without it, the measurement if
there is one. Then:

- **Also** — side findings worth a reader's attention that aren't the main
  thread.
- **Gate on the merged tree** — the real `make gate` outcome. A pre-existing
  unrelated failure is named explicitly with its issue number, never absorbed
  into "tests pass".
- **Deploy note** — only when the change touches something bind-mounted from
  `master` (the doctrine hot-reloads on mtime), because that reaches production
  on the next message rather than the next deploy.
- The `Claude-Session:` footer.

Every branch with a user-observable change also carries a release fragment;
see `release-fragments/README.md` for the schema and the validation commands.

**Closing an issue.** Use `Closes #N` only when the PR fully resolves it. A
multi-PR design track stays open across incremental landings — reference it as
"issue #N" without the keyword and let the design document's own status section
track what remains.

## Proving a fix: mutation

**A fix is not done when the gate is green. It is done when reverting the exact
production line turns a named test RED.** Report that result alongside the gate
output in the PR body.

This is the "no self-attestation" principle given an instrument. It sits beside
*never claim a merge, deploy or fix without reading back the state you claim* —
same rule, one aimed at deployments and one at test suites: the doer does not
get to decide that it worked.

**Why it exists, because the rule reads like boilerplate without this.** On the
media-comprehension work (#116/#117), three review rounds running found the
fixes correct and guarded by **nothing**. In round three a reviewer reverted
four separate production changes at once and ran the full suite: byte-identical
to baseline. Deleting a fifth line — a store column being selected — left it
green too. `_QUERY_FRAME`, the one deliberate break of an authored-prompt
invariant with two test docstrings claiming to cover it, could be replaced with
the raw string and the whole suite passed. "Gate green" was carrying no
information about the thing under review, and the mechanical consequence was
that each round's unguarded fix was what the next round's edit silently broke.

Requiring a mutation result per fix from round four onward ended that, and did
something better: the sweep caught **weak tests before the reviewers did**, in
two consecutive rounds — a hardcoded metric label the test never checked, a
`-k` selector matching nothing, an `!=` assertion any wrong value satisfies, a
fake that failed the read as well as the write so the write path never
executed, and an assertion at a layer that could not observe the predicate it
was asserting. Five real gaps that four rounds of review had not found.

**This is mutation testing** — mutants, killed/survived, mutation score;
`mutmut` and `cosmic-ray` in Python, PIT in Java. Named so nobody thinks we
invented it. **This repo does the hand-picked form, not the automated one**: an
automated sweep over ~6000 tests is slow, and equivalent mutants (edits that do
not change behaviour) drown the signal. A table of the lines you claim to have
fixed answers the only question being asked, in minutes.

Tooling — `scripts/mutate.py`, `scripts/mutations/README.md` and a `make mutate`
taking a table argument — **lands separately on `mikeng-io/mutation-sweep`; it
is not in this tree yet.** The rule above applies regardless: it describes what
you must establish before claiming a fix, and a reverted line plus a named test
needs no script. **It is deliberately NOT part of `make gate`** — a
mutation table names exact source text, so it is the record of what a branch
proved, not a gate that runs forever. A stale table failing the gate would teach
people to delete tables rather than write them.

**Two harness rules, each learned by being burned, each of which silently turns
"caught" into a lie:**

- **`pytest` exit code 5 is «nothing collected», not «failed».** A typo'd `-k`
  selector once reported a caught mutation while testing nothing. Assert the
  baseline is green AND collecting before mutating.
- **A mutant must die from the reverted BEHAVIOUR**, not from a `NameError` in
  the replacement text. One entry referenced an unimported name; the exception
  was swallowed by the production code's own `except`, and the reverted
  behaviour never ran at all while the entry read as caught.

The failure mode in practice is not people refusing to run the sweep — it is
people writing an entry whose selector or replacement is subtly broken and
reporting "caught". **A `BROKEN ENTRY` line is a finding about your evidence,
not a nuisance to route around.**

## Issues

Not-yet-built design ideas discussed in a session get **filed as a GitHub
issue** on `mikeng-io/nora`, not left in a chat transcript or a memory file.
Scoping, comments and status live on the issue.

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
<the settled shape, as bullets>

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

Labels are not currently used on this repo's issues. That is a gap, not a
convention — adopting a taxonomy is its own decision, not something to invent
per issue.

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
                                          worktree + branch (`mikeng-io/<slug>`)
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
keyword, and let the design document's status section carry what remains. The
issue closes when the last piece lands, not when the first one does.

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
is in `master`'s merge commits. GitHub does not auto-delete branches here, so
this is manual:

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
