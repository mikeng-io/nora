# 0040 · Every release is a patch; minor and major are owner milestones; the deploy checks the host

- **Status:** accepted (operator, 2026-11-22)
- **Date:** 2026-11-22
- **Supersedes, in part:** ADR 0031's footer-selected minor and its breaking-marker stop
- **Index:** existing DEC-314 amendment, no new ID

## Context

ADR 0031 kept library-style semver: a `Release-Bump: minor` footer selected
minor, and any `!` or `BREAKING CHANGE:` marker stopped automatic releases
until the owner chose a bump at an exact SHA. On 2026-11-21 one commit (#868,
moving the public site out of the repository) carried a breaking footer whose
only consequence was three settings on the operator's own host. The marker held
every later release, including unrelated merges, until a manual dispatch.

Nora is an application that deploys itself, not a library with dependants.
Comparable projects version that way: Magpie (`0.1.N`, patch as a build
counter), Claude Code (`2.1.N`), T3 Code (`0.1.N` plus nightlies). None of them
gate a release on a breaking marker.

## Decision

- Every releasable master push ships a patch, automatically, and nothing holds
  it. No commit type, footer or marker selects minor or major.
- Minor is a milestone the operator names; major is a product generation. Both
  are selected only by owner dispatch at an exact full master SHA.
- `!` / `BREAKING CHANGE:` now means "the host must change": the release lists
  it under ⚠ BREAKING CHANGES, linked to its commit, whose footer says what to
  do; it still ships. The release PR body carries no commit footer text,
  because the publisher parses it.
- Host readiness is the deploy's check, not the version's. A missing required
  setting refuses the deploy before any container is replaced
  (`docker-compose.yml` `${VAR:?…}` interpolation and Nora's config validation),
  so an unready host blocks only its own deploy.

## Consequences

- Releases never queue behind a host change, and the version stops carrying a
  safety decision it could not check.
- Worse: minor no longer records capability milestones unless the operator
  dispatches one, so a version number says less about scope than before.
- Worse: a host change the deploy cannot detect (for example a reverse-proxy
  route) ships and deploys as soon as it merges; its footer, one click from the
  release notes, is the only warning.
  Read the ⚠ section of each release before relying on a deploy.
- Enforced by `tests/architecture/test_release_plan.py`
  (`test_a_break_releases_automatically_as_a_patch_listed_as_breaking`,
  `test_no_footer_selects_minor_only_the_owner_does`,
  `test_the_breaking_section_links_the_commit_and_copies_no_footer_text`).
