# 0024 · Releases are cut by release-please from Conventional Commits; the upgrade note and release fragments retire

- **Status:** proposed (2026-10-16), on operator direction: "refactor the current nora release to
  real semantic release with github, those message notification no longer need", with the
  release-PR (GitOps) shape, a first tag of v6.0.0, and fragments removed entirely chosen by the
  operator the same day
- **Date:** 2026-10-16
- **Reverses, in part:** DEC-97 part (4), the upgrade note, and its "plain `V1`/`V2`/`V3`, no
  semver" scheme. Retires R-42.
- **Index:** DEC-314

## Amendment: unattended publication (2026-10-19)

The operator requests automatic releases for real code changes and free public
repository features. A repository-scoped GitHub App now queues Release Please's
PR for auto-merge after required CI/security checks. A runtime-path and commit-type
check prevents documentation/design-only work from opening/updating a release PR;
those directories are also excluded from release notes. Runtime doctrine and
skills qualify because they change behavior. No deployment is triggered. The
manual path remains while private or until the App is configured.

This replaces the manual approval in original Decision 2 below. The trade-off is
that a checked code merge can now publish without another human action. Branch
rules and an App token (rather than bypassing checks with the workflow token)
are required. See `../github-maintenance.md` for setup and GitHub's documented
bot-token restrictions. Reopen this decision if an ineligible change publishes a
version or a release PR bypasses its required checks.

## Context

Read from the tree on 2026-10-16:

- **A release took four hand steps.** Fragments accumulated in `release-fragments/unreleased/`
  (293 files since V5, one required per branch by scripts/check_release_fragments.py). At
  release time someone curated them into `docs/releases/V<n>.md`, and scripts/release.py on
  the deploy host tagged `V<n>` with that file's `## Group message`, built, deployed, verified,
  and pushed the tag. V5 (2026-09-26) is the last one; the scheme was used five times in two
  months, and V2's tag sat on the host alone for six weeks.
- **The tag message existed for the upgrade note.** nora/app/announce.py relayed the annotation
  into every allowed room once per version (DEC-97, R-42), with its own ledger table, header,
  counter and config section. The operator no longer wants that message.
- **The commits already carry the information.** Every commit subject is a Conventional Commit,
  enforced by `scripts/check_commit_subject.py` in the commit-msg and pre-push hooks, with a
  closed type list. A fragment's `type` (feature, fix, change, security) restated the subject's.
- **The published convention** for "version from Conventional Commits, notes from subjects" is
  semantic-release and release-please. release-please's manifest mode keeps one release PR open
  and releases when it is merged. semantic-release releases on every push. Both bump `feat` to
  minor, `fix` to patch, and `!`/`BREAKING CHANGE` to major. A release PR opens only when the
  changelog has a visible entry (release-please `src/strategies/base.ts`, "No user facing
  commits found"), so internal commits never release on their own.

## Decision

1. **release-please cuts releases on GitHub** (`.github/workflows/release-please.yml`,
   `release-please-config.json`, `.release-please-manifest.json`). On each push to master it
   updates one release PR. Merging that PR tags `vX.Y.Z` and publishes the GitHub Release with
   the version's `CHANGELOG.md` section. The PR bumps `pyproject.toml`, `nora/__init__.py` and
   `uv.lock` together. The first release is `v6.0.0` (`release-as`, deleted once it ships),
   counting commits from the V5 tag (`bootstrap-sha`).
2. **The release PR, not every push**, is the release. The operator's merge stays the decision
   to release, as scripts/release.py was. That is why release-please was chosen over
   semantic-release.
3. **Visible changelog types:** `feat`, `fix`, `security`, `perf`, `change`, `doctrine`,
   `revert`. Every other declared type is listed hidden.
   `tests/architecture/test_release_config_matches_the_repo.py` keeps the list equal to the
   hook's types.
4. **A deploy is not a release, and every deploy is stamped.** `scripts/build-image.sh` stamps
   `nora/version.json` with `git describe --tags --match 'v[0-9]*'`. The manifest's `notes`
   per version are its `CHANGELOG.md` section. A tree that cannot be stamped honestly builds
   unstamped. The DEC-97 build gate (`scripts/check_version_stamp.py`) is unchanged.
5. **Retired:** the version announcer and R-42, `[version] announce`, the `AnnouncementHeader`,
   `nora_version_announcements_total`, release fragments and their gate, scripts/release.py,
   scripts/build-release-image.sh, and the `make release*` targets. `version_announcements`
   stays in Postgres, unread, as `artifacts` did under ADR 0023. The `version` tool stays.

## Consequences

- Releasing is reading and merging one PR. The notes need no second writing.
- Every deploy reports an honest version (`v6.1.0-3-gabc1234`) where it used to report
  `unavailable`.
- Agents load the `nora-release` skill for the procedure. `nora-land` step 5 now says the
  subject is the release note.

**Worse:**

- **The notes are only as good as the subjects.** A fragment had a `summary` written for a
  reader. A subject is written for `git log`, and a vague one becomes a vague changelog line.
  Mitigation: `nora-land` step 5 and CONTRIBUTING ask for reader-facing subjects, and the
  GitHub Release body can be edited after the fact. Nothing enforces it.
- **The group no longer hears about a new version.** That is the operator's choice, and it
  is recorded here so nobody rebuilds the announcer to fix a gap.
- **The first changelog is long.** v6.0.0 lists every visible commit since V5 (1,466 commits
  in the range).
- **Releases depend on GitHub Actions**, including the repo setting that allows Actions to
  create pull requests. A failed run shows no release PR and nothing else.
- **The 293 unreleased fragments are deleted unread.** Their text is in git history, and the
  commits they described are in the v6.0.0 changelog.

## Rejected

- **semantic-release on every push.** It is fully automatic, but it removes the operator's
  release decision and would release on any `fix` merge.
- **Release notes from PR titles** (GitHub's generated notes, release-drafter). The repo merges
  with authored `merge:` subjects and PR titles are not type-checked by any hook. The commit
  subjects are.
- **Keeping fragments as the note source.** That keeps the curation step this change exists to
  remove.
- **Dropping the announcement table.** Reversible-first: the ADR 0023 precedent keeps a retired
  table until someone chooses the delete.
