<!--
PR title: Conventional-Commit shape, `(#N, DEC-M)` appended when it closes an
issue or lands an indexed decision. The reasoning for every heading below lives
in CONTRIBUTING.md §Pull requests — this file is the shape, not the rules.
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

<!-- The real `make gate` outcome. A pre-existing unrelated failure is named
     explicitly with its issue number — never absorbed into "tests pass". -->

## Deploy note

<!-- ONLY when this touches something bind-mounted from master (doctrine
     hot-reloads on mtime) — that reaches production on the next message,
     not the next deploy. Delete if not applicable. -->

## Closes

<!-- `Closes #N` here in the body ONLY when this PR fully resolves the issue.
     A multi-PR design track references it as "issue #N" in the prose instead,
     and stays open until the last piece lands. Delete if none. -->

<!-- Commits authored in a Claude Code session carry a `Claude-Session:`
     footer; every branch with a user-observable change also carries a release
     fragment (release-fragments/README.md). -->
