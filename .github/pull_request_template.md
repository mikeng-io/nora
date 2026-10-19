<!--
PR title: Conventional-Commit shape, `(#N, DEC-M)` appended when it closes an
issue or lands an indexed decision. The reasoning for every heading below lives
in CONTRIBUTING.md §Pull requests — this file is the shape, not the rules.
Before requesting review: assign the person owning follow-through, use relevant
existing labels, and attach a milestone only for agreed scope. Do not invent
maintainers or releases to fill metadata.
Trivial PRs: body may be just What + Gate on the merged tree. Delete what does
not apply.
-->

## What

<!-- One block per piece of work. Each block names a concrete artifact: the
     file that gates it, the test that would fail without it, the measurement
     if there is one. A body that restates the title is not doing its job. -->

## Also

<!-- Side findings worth a reader's attention that are not the main thread.
     Delete if none. -->

## Gate on the merged tree

<!-- The real `make gate` outcome, run once on the final head (`make gate`
     scopes tests to the diff, `scripts/affected_tests.py`; `make full-gate`
     is the unconditional full run). A pre-existing unrelated failure is named
     explicitly with its issue number — never absorbed into "tests pass". -->

## Deploy note

<!-- ONLY when this touches nora/doctrine or nora/dreamer: the deploy host
     bind-mounts them from its own checkout and hot-reloads on mtime, so they
     go live at its next pull, with no image rebuild. Delete if not
     applicable. -->

## Closes

<!-- `Closes #N` here in the body ONLY when this PR fully resolves the issue.
     A multi-PR design track references it as "issue #N" in the prose instead,
     and stays open until the last piece lands. Delete if none. -->

<!-- Commits authored in a Claude Code session carry a `Claude-Session:`
     footer. The commit subjects are the release notes (DEC-314): write the
     user-visible ones as changelog lines. -->
