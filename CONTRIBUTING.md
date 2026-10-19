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
| second | `feat/`, `fix/`, `docs/`, `chore/`, `design/` + `<slug>` | superseded (still 14 of the 140 PRs to 2026-09-24) |
| first | `worktree-<slug>` | superseded |

Every change starts in its own worktree on its own branch and reaches `master`
by merge — a one-line doc fix included, so the merge is the moment somebody
decides. The container's bind-mount source is the deploy host's own checkout,
not this one: `nora/doctrine` and `nora/dreamer` go live when that host pulls
`master` (`AGENTS.md` "Never" list has the topology and the incident). Landing a merge does not move you back in: the next change starts in a new
worktree too.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/) —
`type(scope): description`:

The type list is **closed** — measured 2026-09-14, a closed list of 17 covers
85% of the log since 2026-08-01 byte-for-byte, and every failure it catches is
one of the three defect shapes listed after the table. Add a type by extending
this table AND `scripts/check_commit_subject.py`'s `TYPES` map in the same
commit; the checker is the enforcement, the table is the contract.

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
| `design` | design-bundle / architecture-document work (de-facto since 08-xx, now declared) |
| `config` | configuration surface — schema, example files, loader |
| `build` | build/deploy tooling — Dockerfile, compose, Makefile |
| `style` | formatting or lint with no behaviour change |
| `ci` | CI / GitHub plumbing |
| `ops` | operational toggles on the live stack (policy, config reload) |
| `doctrine` | Nora's doctrine files — voice / conduct / silence / constraints |
| `perf` | a change that only affects performance (standard Conventional Commits) |
| `security` | a security-relevant fix (matches the release-fragment `security` type) |

The description is a full clause, and specific beats generic: *"a sampling run
builds one guard per sample, not one per run"* names the defect; *"fix sampling
bug"* names nothing.

**The body carries the why**, in this repo's comprehend → measure → serialize
shape: what was broken, what was measured (numbers, before and after, when
there are any), what shipped, and what it does not cover. A body that restates
the subject is not doing its job — the subject serves `git log --oneline`, the
body serves whoever has to decide if this change is safe to build on.

Commits authored in a Claude Code session end with a `Claude-Session:` footer.

**Enforcement — `make hooks`, one `git commit` at a time.** A `commit-msg` and
`pre-push` hook run `scripts/check_commit_subject.py`, which rejects three
shapes, each measured in the log before this rule was written:

1. **No type at all** — 294 of 2195 subjects since 2026-08-01. The checker
   prints the declared list.
2. **An undeclared type** — eleven spellings lived in the log that the old
   eight-row table never declared (`design` ×69, `config` ×20, `build` ×12,
   `style` ×11, `ops` ×7, plus singles). A type in use but not declared is a
   convention that only looks agreed; now they are either in the table or
   rejected.
3. **A default or superseded merge subject** — `Merge pull request #N from …`
   (62 of them) violates the authored-merge rule below; `Merge <branch>: …`
   (141 of them) is the superseded second generation, retired the same way
   branch naming's older generations were: named, not imitated. The current
   shape is `merge: <the arc>`.

`wip:` subjects are refused too — they are rebase fuel, not history. The hooks
are installed per clone with `make hooks` and bypassed with `--no-verify`; if
you bypass, name the reason in the commit body.

The same hooks, with a `pre-commit`, also refuse a change that ADDS a real
identifier (#283): the message, the author identity and the staged blobs at
commit time, every object and tag the target remote does not have at push
time. `git commit --no-verify` moves the refusal to `git push`; `git push
--no-verify` removes it, and there is no server-side or CI check behind it.
No model judges anything: a denylist hit is final, and a shape finding
clears only by a declaration the remote already has, a value in the private
pseudonym registry, or a human acknowledgement (`NORA_PII_ACK=<id>`,
recorded as a `PII-Ack: <id> (human)` trailer). Setup (a private denylist,
and optional keep-phrase and pseudonym files, all outside the repo) and
declarations are in `.githooks/README.md`.

## Merge strategy

A real merge commit — `git merge --no-ff`, or `gh pr merge --merge`. Never
squash, never rebase-merge.

**`gh pr merge --merge` alone writes GitHub's default message**, and the
commit-msg hook never sees a merge made on GitHub, so nothing stops it. That is
how 99 of the 137 PRs merged from #321 to #478 (2026-09-17 to 09-24) landed
with the default
against the rule below. Give the subject and body on the command line:

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

**`merge: <the arc>` — the arc is the point.** The checker (see §Commit
messages) requires a non-empty arc and rejects an authored-merge prefix worn by
a GitHub-default body: `merge: Merge pull request #1 from …` is the default
wearing the sanctioned prefix, not an authored message. If you have nothing to
say about the arc, the merge is not ready to land.

## Pull requests

**Title** — the same Conventional-Commit shape as the commit subject, with
`(#N, DEC-M)` appended when it closes an issue or lands an indexed decision.

**Body** — open through the PR template (`.github/pull_request_template.md`):
one block per piece of work, each naming a concrete artifact — the file that
gates it, the test that would fail without it, the measurement if there is one.
The template carries the headings below in filing order; delete what does not
apply. The measured drift, 2026-09-14: 49 of 133 merged PRs carried `Gate on
the merged tree`, 33 carried `Also`, while two headings this table never
declared (`Summary` ×38, `Test plan` ×31) grew in — a convention that lives
only in prose grows whatever headings the author remembers. Then:

- **Also** — side findings worth a reader's attention that aren't the main
  thread.
- **Gate on the merged tree** — the real `make gate` outcome, run once on the
  final head (not per fix round — `AGENTS.md` *Gate commands*). `make gate`
  scopes its tests to the branch's diff (`scripts/affected_tests.py`, whole
  suite when unmapped or when the selector fails; only an explicit `NONE` runs
  nothing); `make full-gate` is the unconditional full serial run.
  A pre-existing unrelated failure is named explicitly with its issue number,
  never absorbed into "tests pass".
- **Deploy note** — only when the change touches `nora/doctrine` or
  `nora/dreamer`, which the deploy host bind-mounts from its own checkout and
  hot-reloads on mtime: it reaches production at that host's next pull, with no
  image rebuild.
- The `Claude-Session:` footer.

`Summary` and `Test plan` are retired headings, not synonyms: `Summary` is the
title's job (the subject serves `git log --oneline`), and `Test plan` is
`Gate on the merged tree` — the outcome, not the intention.

There is no release fragment. The commit subjects ARE the release notes:
release-please builds the CHANGELOG and the GitHub Release from the `feat`,
`fix`, `security`, `perf`, `change` and `doctrine` commits (DEC-314, the
`nora-release` skill). Write each one so a reader of the changelog can use it.

**Closing an issue.** Use `Closes #N` only when the PR fully resolves it. A
multi-PR design track stays open across incremental landings — reference it as
"issue #N" without the keyword and let the design document's own status section
track what remains.

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
replacement for the full affected-test gate above or live evaluations. It does
not deploy or grant access to Norty. Action updates are grouped by Dependabot.

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

Tooling is `scripts/mutate.py`, driven by a JSON table under
`scripts/mutations/`; `make mutate TABLE=scripts/mutations/<name>.json` runs it.
The rule stands without the script — a reverted line plus a named test needs no
tooling — but the script checks the two traps below for you, which hand-running
does not. **It is deliberately NOT part of `make gate`** — a
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
Scoping, comments and status live on the issue. **File through one of the two
issue forms** (`.github/ISSUE_TEMPLATE/`) — freeform or design-anchored, same
two shapes as below; blank issues are disabled. The forms carry the headings so
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

**Issue-shape labels** — set by the issue forms:

| label | set by | means |
|---|---|---|
| `design-anchored` | the *design-anchored* form | a design document already specified this; the issue builds to spec |
| `freeform` | the *freeform* form | the issue itself proposes the decision |

These labels distinguish the two issue forms. Area stays in the title prefix
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
