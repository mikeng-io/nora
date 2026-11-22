---
name: nora-release
description: Follows Nora's automatic patch release pipeline, or prepares an owner-selected minor or major milestone at an exact source SHA. Use for release PRs, version bumps, tags, changelogs or publication. Not for an ordinary deploy (nora-deploy).
---

# Nora releases

Every ordinary master push starts `release-please.yml`. The workflow publishes
merged pending metadata first through release-please, then plans the complete
remaining git-reachable range. It maintains one generated release PR and enables
GitHub auto-merge on its exact head, with an authored merge subject. Required
checks govern the merge; the App has no bypass. This standing authority covers generated metadata only, never application
or implementation PRs. There is no daily wait, paid agent or release evaluator.
There is no AI release evaluator: never invoke or enable Devin release automation.

## 1. The version convention

Nora is an application that deploys itself, not a library, so a version is a
build counter with named milestones:

| part | means | moved by |
|---|---|---|
| patch | every releasable master push | the pipeline, always; nothing holds it |
| minor | a milestone Mike names (a cutover, a capability landing) | Mike, by owner dispatch at an exact SHA |
| major | a product generation | Mike only, by owner dispatch |

No commit type, footer or breaking marker selects minor or major, and none stops
the pipeline. Docs, tests, CI, agent tooling and repository-only maintenance
paths do not trigger a release; unknown paths still qualify, and runtime
Markdown remains eligible. Commit subjects are the changelog lines; internal-only
releases list their subjects under Maintenance. Old-dated commits merged later
remain included. `Release-As:` is unsupported. Existing versions and tags stay.

A change that needs the host to change marks it with `!` or a
`BREAKING CHANGE:` footer that says what the host must do. The release lists it
under ⚠ BREAKING CHANGES, linked to that commit, and ships as a patch. Readiness is the deploy's check,
not the version's: a missing required setting refuses the deploy before any
container is replaced (`docker-compose.yml` `${VAR:?…}` interpolation and Nora's
config validation, `nora-deploy`), so an unready host blocks only its own deploy.

## 2. Follow automatic preparation and publication

```bash
gh run list --workflow release-please.yml -L 5 --json databaseId,status,conclusion,headSha
gh pr list --label 'autorelease: pending' --json number,title,headRefName,headRefOid
gh pr view <N> --json headRefOid,mergeable,files,statusCheckRollup,autoMergeRequest
```

The generated PR has `chore(master): release X.Y.Z`, its recorded
`Release-Candidate` in the body, and exactly `CHANGELOG.md`,
`.release-please-manifest.json`, `pyproject.toml`, `nora/__init__.py`, `uv.lock`.
It must be one metadata commit directly on that source. The required contribution
check refuses a changed base, a changed head or added Update branch commit.
Never edit the generated branch or use Update branch. When master advances,
its push run re-prepares the whole range; strict branch checks prevent stale
metadata from merging. Actions serializes runs and each run reads latest master,
so coalesced pushes do not omit changes. Concurrent changes may collect into one
pending release.

Missing release App setup fails closed. Only its repository-scoped installation
token is used; no personal token, admin bypass or fallback to `GITHUB_TOKEN`.
See [App setup](../../../docs/maintainers/github.md#automatic-releases).
A metadata merge triggers publication and no next bump by itself. If publication
fails, rerun that workflow: publication comes first, and manifest/tag mismatch
blocks planning a duplicate version until the existing release is published.

## 3. Owner-only milestone and recovery dispatch

The original and rerun actor must both be the repository owner, on master.
Select the exact full current master SHA; drift is refused, never silently
reinterpreted as a new owner decision:

```bash
gh workflow run release-please.yml --ref master -f action=prepare -f bump=<patch|minor|major> -f candidate=<full-master-sha>
```

The generated metadata follows the same required checks and exact-head
auto-merge; its master push lets release-please publish the owner's selection. Manual prepare can also select patch for recovery. A merged but unpublished PR can be resumed without preparing again:

```bash
gh workflow run release-please.yml --ref master -f action=publish -f candidate=<full-current-master-sha>
```

Publish alone does not prepare another PR. No hand tags, release fragments,
room announcements or publication from the deployment host.

## 4. Read publication and deployment separately

```bash
gh release view vX.Y.Z --json tagName,isDraft,targetCommitish,body
git fetch origin --tags
git rev-list -n 1 vX.Y.Z
```

Require a successful publisher run, a non-draft Release and a tag resolving to
the generated metadata merge. A stable published Release automatically feeds
`release-images.yml`, then the complete `deployments` bundle and installed Komodo
poller under the existing release-image control. The operator's standing release
and deployment authority includes this normal patch path and owner-dispatched
milestones. Follow `nora-deploy` for image/source/receipt/live-stamp
readback; a tag or build alone proves no deployment.

The separate owner-only Deploy master image entry stays default-off behind
`MASTER_DEPLOY_ENABLED` until its installed-wrapper readiness is verified. This
release policy enables no flag, adds no RC/nightly channel and changes no deploy
mechanism or selective build policy.
