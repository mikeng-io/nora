# 0027 · The release PR is planned from git reachability, and the merge rules are GitHub rulesets

- **Status:** proposed (2026-10-22), on operator direction: "if file changes are detected within
  the path, it will trigger the pipeline for a completely automatic release", and pull request
  conventions "have to match; if not, the PR should be blocked and unable to merge"
- **Date:** 2026-10-22
- **Extends:** ADR 0024 (release-please cuts releases), which keeps tagging and publishing.
  Replaces its release-PR step.
- **Index:** DEC-314

## Manual release amendment (operator, 2026-10-28)

Issue #316 replaces the automatic cadence described below: master pushes no
longer run release planning or publication. A manual prepare dispatch selects
patch/minor/major, keeping git-reachability scope and a declared semantic minimum.
The maintainer reviews and merges the metadata PR; a separate manual publish
dispatch lets release-please tag/publish it. Release-PR auto-merge is removed.
Publication still triggers the image/Komodo path, requiring deployment admission.
Daily Devin assessment recommends only; RC/nightly publication remains separate
unbuilt work. The historical automatic-release consequences below describe the
pre-amendment behavior. Branch rules and immutable published tags are unchanged.

## Context

Read from the repository and the Actions logs on 2026-10-22:

- **#188 merged and no release followed.** `scripts/release_scope.py` said a release was due
  (`skip-github-pull-request: false`). release-please then logged `Considering: 2 commits` and
  `No user facing commits found since 496a356 - skipping`. The two commits were the `merge:`
  commits, a hidden type. The branch's seven `security`/`fix(security)` commits were never read.
- **The cause is the order release-please reads history in.** It lists commits newest committer
  date first and stops at the last release commit. `496a356` (v6.6.0) is dated 15:21 +08. Every
  #188 branch commit is dated earlier (11:47 to 15:06), because the branch was written before the
  release and merged after it. v6.6.0 itself included the Character-card commits only because
  they were dated after the previous release. A long-lived branch merged with `--no-ff` after a
  release is the repository's normal merge shape (`CONTRIBUTING.md`: merge commits, never
  squash).
- **`git log base..HEAD` has no such blind spot.** It lists what is reachable from HEAD and not
  from the tag, whatever the dates. Run on the real history it returns the seven commits and
  plans 6.6.1. `release_scope.py` already used it, which is why the two tools disagreed.
- **Merge rules were not enforceable.** The repository is private on a personal Free plan, so the
  branch-protection and ruleset APIs answered 403. `PR content` and `checks` were advisory: a
  PR whose branch was `mikeng-io/aikido-high-triage` merged. Nothing checked a branch name at
  all. After GitHub Pro was enabled the same endpoints answer.

## Decision

1. **`scripts/release_plan.py` opens the release PR.** It reads the user-visible commits that
   change runtime inputs from `git log --no-merges <tag>..HEAD` (the same
   `release_scope.releasable` that decides eligibility), picks the bump (`!` or `BREAKING CHANGE`
   major, `feat` minor, otherwise patch), and writes the manifest, `nora/__init__.py`,
   `pyproject.toml`, `uv.lock` and the `CHANGELOG.md` section. The PR title, body, branch and
   `autorelease: pending` label are release-please's own, byte for byte
   (`tests/architecture/test_release_plan.py` renders 6.6.0 back to the section release-please
   wrote). A tag that disagrees with the manifest, or a file that does not hold the version
   being replaced, is an error.
2. **release-please still tags and publishes.** The workflow runs the action with
   `skip-github-pull-request: true`, so it only reacts to a merged release PR, and does it
   before planning so a just-merged release is never planned twice.
3. **The merge rules are rulesets, applied by `scripts/configure_github_rules.py`.** `master`
   needs a pull request, merge commits only, resolved conversations, an up-to-date branch, and
   the `checks` and `pr-metadata` jobs; force-push and deletion are blocked; `v*` tags
   cannot be moved or deleted. There are **no bypass actors**: the owner cannot merge a failing
   PR either. The script reads every setting back and fails on any drift.
4. **`PR content` also checks the branch name** against `<type>/<kebab-slug>` with a declared
   commit type (`CONTRIBUTING.md`, Branch naming). Release-bot and Dependabot branches are
   exempt as before. The weekly wiki branch is renamed `docs/openwiki-update`.
5. **Required checks must report on every pull request.** CodeQL and dependency review are
   skipped while the repository is private and their matrix job never reports its real name;
   the wiki build runs only on some paths. None is required. Aikido covers what CodeQL would
   have. Aikido is added once a PR shows the check name it reports under.

## Consequences

- A release follows any merge that changes runtime paths with a visible commit, whatever the
  branch's age, and the changelog lists every commit the merge brought.
- A non-conforming title, body or branch cannot merge, for the owner too.

**Worse:**

- **The workflow depends on release-please's PR format.** It tags only a PR whose title, body
  and label look like its own. The first run after this lands is the real test; if the tag step
  does not fire, `gh release create vX.Y.Z --target <merge sha>` publishes by hand and
  `release-images` still triggers.
- **`Release-As:` footers are no longer read.** Forcing a version means editing the manifest
  in the release PR's branch, which the next master push overwrites; there is no pin.
- **No owner bypass means a broken required check blocks everything, including the fix.** The
  only way out is editing the ruleset in GitHub's settings, which the owner can still do.
- **An up-to-date branch is required.** Each merge makes every other open PR stale until it is
  updated, and its checks run again.
- **A check made through the Checks API cannot be required.** The first apply required the
  `PR content` check run, which the contribution workflow creates with `POST /check-runs`.
  GitHub's merge evaluation reported "Required status check 'PR content' is expected" for it
  although it was green, and no PR could merge. The job that runs the same script,
  `pr-metadata`, fails on a bad title, body or branch and is accepted, so that is what is
  required. The `PR content` run stays as the readable summary.
- **Two release tools exist side by side** until release-please's own PR step is deleted from
  the config, which this ADR does not do.

## Rejected

- **Requiring a rebase before merge.** It fixes the dates by rewriting every reviewed commit,
  which defeats `--match-head-commit`, and it only works once checks block.
- **Dropping release-please for an own tag-and-release script.** The most robust option and a
  larger replacement of a tool ADR 0024 chose; the planner is the part that was wrong.
- **Mergify or a merge bot without GitHub Pro.** Mergify's protections are a check, and a check
  blocks an owner click only when a ruleset requires it, which the Free plan refused.
- **Requiring CodeQL, dependency review or the wiki build.** Each fails the "reports on every
  PR" test above.
