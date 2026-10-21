---
name: nora-release
description: Cuts a Nora release the DEC-314 way. release-please on GitHub keeps one release PR built from Conventional Commits; merging it tags vX.Y.Z and publishes the GitHub Release. Then read back automatic Komodo deployment. Load when a task says release, cut a version, tag, changelog, release PR, release-please, or what version is live. Not for an ordinary deploy (nora-deploy).
---

# nora-release

Release Please opens the release PR. **While the repository is private, the
maintainer merges it manually; auto-merge is not active.** Merging publishes the
tag and GitHub Release. `release-images.yml` then publishes the complete image
bundle to `deployments`; the enabled Komodo poller deploys it automatically.
Read back publication and deployment separately. A release posts nothing to rooms.
The startup upgrade note was retired along with `release-fragments/` and
scripts/release.py.

Public-repository App/auto-merge setup is future activation guidance in
`docs/maintainers/github.md`, not the current private-repository procedure.

## 0. Where the release notes come from

The notes are the Conventional Commit subjects merged since the last `v*` tag
(`.github/workflows/release-please.yml`, `release-please-config.json`). They
are written at commit time, in `nora-land` step 4, never at release time.

- Visible types open a release PR and get a CHANGELOG line: `feat` (minor),
  `fix`, `security`, `perf`, `change`, `doctrine`, `revert` (patch). A `!` or
  a `BREAKING CHANGE:` footer is major.
- Hidden types (`docs`, `refactor`, `test`, `chore`, `merge`, …) never release
  on their own. `merge:` subjects are not conventional types, so the branch
  commits inside a merge are what count.
- A subject is a line in a public changelog, so write it for a reader:
  `fix(music): resume after a voice reconnect`, not `fix: stuff`.

## 1. Read the release PR

```bash
gh pr list --label "autorelease: pending" --json number,title,headRefName
gh pr diff <N> | sed -n '/CHANGELOG.md/,/^diff --git a\/[^C]/p' | head -80
gh run list --workflow release-please -L 3      # the run after the last master push
```

Check: one PR titled `chore(master): release X.Y.Z`. The version is the bump
the commits imply, and the CHANGELOG lines describe what users will see.

- **No PR**: no visible commit since the last tag, so there is nothing to
  release. If a `feat`/`fix` did land, read the newest run's log. If it failed
  creating the PR, the repo setting *Actions → General → Allow GitHub Actions
  to create and approve pull requests* is off.
- **Wrong version**: land a commit on master (through `nora-land`) whose body
  ends in `Release-As: X.Y.Z`. Release-please rewrites the PR on the next push.
- **Wrong wording**: never edit the PR branch, because the next push to master
  overwrites it. Edit the GitHub Release body after step 3
  (`gh release edit vX.Y.Z --notes-file …`), or fix it in the next release's
  commits.

## 2. Maintainer merges the release PR

While private, the maintainer uses the manual path below after checks pass.
After publication and App setup, `docs/maintainers/github.md` describes future
auto-merge activation. Docs/design-only commits do not qualify for release;
`scripts/release_scope.py` checks runtime paths and visible commit types since
the latest release. Runtime doctrine/skills count.

```bash
gh pr view <N> --json headRefOid,mergeable,files --jq '.headRefOid, .mergeable, [.files[].path]'
gh pr merge <N> --merge --match-head-commit <headRefOid> \
  --subject "merge: release vX.Y.Z (#N)" --body "release-please's release PR for vX.Y.Z."
```

Check: the files are `CHANGELOG.md`, `.release-please-manifest.json`,
`pyproject.toml`, `nora/__init__.py` and `uv.lock`, and nothing else.
`--merge` only, never squash. Never `git tag` or push a `v*` tag by hand.
Release-please makes the tag, and a hand tag splits the history it reads.

## 3. Read back the tag and the Release

```bash
gh run list --workflow release-please -L 1 --json conclusion,headSha   # success, on the merge commit
gh release view vX.Y.Z --json tagName,isDraft,targetCommitish,body | head -40
git fetch --tags origin && git rev-list -n 1 vX.Y.Z                    # == the merge commit
```

PASS: the run succeeded, the Release exists and is not a draft, and the tag
is on the merge commit. A `No entries modified` warning in the run log means
the `uv.lock` updater missed. Then `test_release_config_matches_the_repo.py`
fails on master, so fix the jsonpath in its own PR.

## 4. Read back automatic deployment

Wait for `release-images.yml` to finish all images and commit `compose.json` and
`release.json` to `deployments`. Production's enabled `nora-release-poll` checks
every ten minutes with `DeployStackIfChanged` on `nora-releases`; manual native
`DeployStack` is available when needed. The installed wrapper writes
`release-deploy.json` after verified deployment. v6.5.0 used this path without
host commands on 2026-10-21.

Follow `nora-deploy` step 0 and compare the selected `deployments` manifest,
production receipt, every owned service's image/health and live Nora stamp.
A published tag alone does not prove deployment. Never rebuild the checkout in
Komodo mode; doctrine and Dreamer are baked into the image.

## Archive: pre-v6.0.0 first-release prerequisites

Historical one-time bootstrap only; not steps for an installed release stack.

```bash
gh api repos/mikeng-io/nora/actions/permissions/workflow   # can_approve_pull_request_reviews: true
ssh nora-host "grep -n '^\[version\]' ~/workspace/nora/config.toml"   # must print nothing
```

- If the setting is false, release-please cannot open its PR. Fix it under
  *Settings → Actions → General → Allow GitHub Actions to create and approve
  pull requests*.
- If the live `config.toml` still has `[version]`, DEC-314's image refuses to
  start, because the config is `extra="forbid"`. Remove the section through
  the legacy `nora-deploy` reference step 5 BEFORE the recreate. The old image is fine without it.
- Until `v6.0.0` is tagged, every build is unstamped. Merge the first release
  PR before the next deploy.

After v6.0.0, the one-time cleanup was to delete `release-as` from
`release-please-config.json` in a PR; the version-pin test guarded that cleanup.

## Never

- Tag, push a tag, or publish a Release by hand, or from `nora-host`.
- Add release notes anywhere but commit subjects. `docs/releases/V*.md` is the
  archive of the old manual scheme, not a template.
- Announce a release to a room. That feature is retired (DEC-314).
