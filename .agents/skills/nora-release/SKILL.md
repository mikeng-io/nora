---
name: nora-release
description: Prepares and publishes a Nora release with an explicit manual version bump. Use for a release PR, version bump, tag, changelog, publication or release assessment; ordinary master merges never release. Not for an ordinary deploy (nora-deploy).
---

# Nora releases: manual bumping

Formal releases have three separate operator actions: prepare a version PR,
review and merge it, then publish it. The `release-please` workflow is
`workflow_dispatch` only. It never runs on a master push or auto-merges a PR.
`release-please` remains the sole tag/GitHub Release publisher; no hand tags.

Publishing still triggers `release-images.yml`, which publishes a complete
bundle to `deployments` for Komodo's enabled poller. A publish therefore reaches
the existing automatic deployment path: obtain the operator's deployment
admission before publishing. Preparing or merging metadata alone deploys nothing.
Read publication and deployment back separately through `nora-deploy`.

## 1. Assess the unreleased range and choose the bump

Read the latest stable tag, current master and any pending release PR:

```bash
git fetch origin --tags
git describe --tags --first-parent --abbrev=0 --match 'v[0-9]*' origin/master
gh pr list --label 'autorelease: pending' --json number,title,headRefName,headRefOid
git log --no-merges --format='%h %s' <last-tag>..origin/master
```

Review the complete still-unreleased range, not only the last day's merges.
Decide whether the changes are useful and complete enough to release now.
Waiting is valid; daily assessment does not imply daily publication.

Choose `patch`, `minor` or `major` explicitly. Compatible fixes are patch,
compatible new functionality is minor, and removing supported config/CLI/API
behavior is major. Assess whether an incompatible change is necessary before
shipping it. Never hide a real breaking change to keep the number small.

`scripts/release_plan.py` uses reachable user-visible runtime commits for the
notes and minimum bump: `feat` minor; `fix`, `security`, `perf`, `change`,
`doctrine`, `revert` patch; `!` or a `BREAKING CHANGE:` footer major. The manual
choice may be larger but cannot be lower than that declared minimum. Hidden
commit types and docs/design-only changes do not qualify on their own.
Read commit bodies and the actual diff; a marker is not proof of compatibility.

A Devin daily assessment follows [assessment.md](assessment.md), recommending
wait, RC, stable or unavailable with base/head, reasons and evidence. It does not
authorize preparing, merging, publishing or deploying. RC/nightly deployment is not implemented by this skill;
the current publisher still accepts stable `vX.Y.Z` only.

## 2. Prepare the release PR manually

Run from the trusted default branch via GitHub Actions, not a local deploy host:

```bash
gh workflow run release-please.yml --ref master -f action=prepare -f bump=minor
gh run list --workflow release-please.yml -L 3 --json databaseId,status,conclusion,headSha
gh pr list --label 'autorelease: pending' --json number,title,headRefName,headRefOid
```

Substitute the chosen bump. Check the dispatched run's own result, then read the
PR: one `chore(master): release X.Y.Z`, with exactly `CHANGELOG.md`,
`.release-please-manifest.json`, `pyproject.toml`, `nora/__init__.py` and `uv.lock`.
The CHANGELOG must cover all eligible changes and identify any required migration.
A successful prepare never merges, tags or publishes.

The repository-scoped release App must be configured, including its bot user ID;
see `docs/maintainers/github.md`. Missing setup is an error, never auto-release.
No qualifying changes produces `eligible=false` and no new PR. A manifest/tag
mismatch means a merged metadata PR may still need publication; do not overwrite
or force a second version. `Release-As:` footers remain unsupported.

## 3. Review and manually merge the metadata PR

```bash
gh pr view <N> --json headRefOid,mergeable,files,statusCheckRollup
gh pr diff <N>
gh pr merge <N> --merge --match-head-commit <reviewed-head> \
  --subject 'merge: prepare release vX.Y.Z (#N)' --body 'Manually selected release metadata; publication is a separate dispatch.'
gh pr view <N> --json state,mergeCommit
```

Require green checks on the reviewed head. Use a real merge, never squash,
rebase or `--auto`. The release PR branch is generated; re-preparing may replace
its contents, so re-read the head and checks before merging. Do not edit it by
hand. A merged metadata PR is not yet a published release.

## 4. Publish explicitly, then read back

Only after release/deployment admission:

```bash
gh workflow run release-please.yml --ref master -f action=publish
gh run list --workflow release-please.yml -L 3 --json databaseId,status,conclusion,headSha
gh release view vX.Y.Z --json tagName,isDraft,targetCommitish,body
git fetch origin --tags
git rev-list -n 1 vX.Y.Z
```

PASS: the publish run succeeded, the Release is not a draft, and the tag resolves
to the metadata PR's merge commit. The publish action does not prepare another
version PR. Follow `nora-deploy` to verify the image bundle, production receipt,
running images and live stamp. A tag or green build alone proves no deployment.

Never announce a release to rooms, add release fragments, move a published tag,
or publish from `nora-host`. Commit subjects remain the release-note source.
