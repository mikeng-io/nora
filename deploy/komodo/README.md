# Release deployment with Komodo

Status: **installed in production**, verified on 2026-10-21. Stack `nora-releases`
is deployed by the enabled `nora-release-poll` Procedure, which calls
`DeployStackIfChanged` every ten minutes. v6.5.0 deployed this way without host
commands. The example resources keep their schedule disabled for new installations;
the first-install bootstrap below is not the normal production deploy procedure.

## Ownership and flow

1. Every ordinary master push automatically prepares patch/minor release metadata.
   GitHub auto-merges only the generated metadata PR after required checks and
   exact-source guards; release-please then publishes the stable `vX.Y.Z` tag and
   GitHub Release. Breaking ranges stop for owner-only exact-candidate major
   preparation (`nora-release`). Published Releases trigger `Release images`
   under its existing control; missing App setup fails closed. Manual image
   dispatch can rebuild an already-published stable tag. The separate default-off
   **Deploy master** entry selects current master without a formal release. Both
   entries share the existing complete-bundle publisher and installed route.
2. Hosted AMD64 runners account for all ten artifacts named by the root Compose file:
   Nora, app, site, STT, voice sidecar/wake, Signal/WhatsApp/Instagram bridges,
   and the alert bridge. Signal's signed tarball preparation is retained. The
   Nora manifest must name the exact selected source and truthful version. CI gets registry
   publication permission, never deployment-host credentials.
3. Only after every artifact is built or proven reusable, the publisher writes
   `compose.json` and `release.json` to the `deployments` branch in one Git commit. All owned images
   are selected by digest. Releases and snapshots share one publication lane.
   Source ancestry prevents a delayed older/divergent build in either channel
   replacing a newer selection; stable duplicate/older-version protection remains.
   An identical source is refused, including on a rerun; this is no deployment receipt.
   Concurrent branch updates must fast-forward. Partial image builds cannot
   promote partial state. Version tags are convenient labels, not deployment
   references. Failed/partial publication may leave unused registry images.
4. A native Git-backed Komodo Stack owns fetching and Compose. A scheduled Procedure
   calls `DeployStackIfChanged`; no inbound webhook or public dashboard is needed.
   A private operator copy of `resources.example.toml` owns the Stack and
   Procedure configuration. Its Resource Sync is separate from the generated
   deployment branch and applied manually. The wrapper records an outstanding
   candidate immediately before recreation and clears it only after verified success.
   Failed/interrupted recreation or readback is refused on subsequent checks; a newer
   manifest can still roll forward. There is no self-modifying scheduled script.
5. An installed command wrapper holds the existing deploy lock through pull,
   candidate config validation, recreation, service readback and success receipt.
   Verification is inside the Compose operation, not Komodo's post-deploy hook:
   a failed check must prevent Komodo recording deployment success.

The owned cohort is every repository-built service, plus `presence` and
`analytics` sharing Nora's image. Those three share one artifact; other services
change independently.
The public `site` remains opt-in (`--site` in the installed wrapper command).
Third-party database, object storage, embedder and monitoring images are excluded
from automatic recreation; `--no-deps` prevents implicit upgrades. The separately
managed computer service is outside this root Compose project.

## Manual Deploy master

This entry is **default-off**. Before enabling repository Actions variable
`MASTER_DEPLOY_ENABLED=true`, the operator must update the reviewed installed
`/etc/komodo/nora-deploy/komodo_deploy.py` under the existing shared deploy lock,
preserving the receipt, outstanding attempt and private diagnostics. That script
is outside the fetched Stack clone: merging or publishing does not update it.
The old wrapper refuses snapshot versions. Read back the installed script against
the reviewed bytes and, in a disposable project, verify snapshot acceptance,
source/stamp mismatch refusal, candidate config validation and final receipt/stamp
readback through the actual installed command. Keep the flag absent/false until
that prerequisite is verified. `RELEASE_IMAGES_ENABLED` retains its existing
independent control over formal release publication. No flag is enabled by merging.

Only the repository owner may dispatch or rerun this entry on default branch master;
both the original and triggering actor are checked. Automatic release handling
does not invoke this entry.
This is an automation-policy restriction: actor checks cannot distinguish a human
owner from automation using that owner's credentials.

```bash
gh workflow run release-images.yml --ref master -f target=master
# Optionally bind a previously assessed current source; stale selection is refused:
gh workflow run release-images.yml --ref master -f target=master -f candidate=<full-master-sha>
# Existing published-tag rebuild (also the default target):
gh workflow run release-images.yml --ref master -f target=release -f tag=v7.11.0
```

Preparation resolves the current full master SHA once and refuses a moved dispatch
or mismatched assessed SHA. Successful repository `CI` from a master push on that
exact SHA is required before builds, and checked again before building/promoting.
Missing, pending, failed or unavailable CI stops publication. Every job uses that
pinned source even if master advances later. Publisher tooling stays pinned to
the workflow revision, so rebuilding an older source cannot restore old guards.

The workflow summary records channel, source and `git describe` version, for example
`v7.11.0-3-gabc1234`. Master images use `sha-<full-source-sha>` labels and immutable
digests, leaving stable version image labels untouched. A source exactly at a tag
retains that truthful version but still records `channel: master` and uses the SHA
image label. This creates no tag, GitHub Release or version bump. Every image receipt
must agree on the selected source, version and channel; each artifact separately
retains its original build identity and input evidence.

Successful publication means `deployments` selects the bundle, verified by API
readback. It does not mean the host deployed it. The installed ten-minute poller
or an operator's native `DeployStack` applies it under the same wrapper/lock.
Compare the selected bundle with the host receipt, every owned image and Nora's
running version/source stamp before reporting deployment. Rollback uses the
existing complete-bundle selection procedure below; a selection revert does not
roll back database schema or host config.

## Incremental artifacts and native recreation

This source requires a one-time reviewed installed-wrapper update under the shared
lock; merging does not update `/etc/komodo/nora-deploy`. Preserve receipts, failed
attempts and private diagnostics. Parent/operator installation and live acceptance
remain separate from local disposable proof. No master-deploy flag change is needed.

The complete manifest still contains twelve services and ten artifacts. Its top-level
version/source/channel describes the **selected release**; `artifacts` records each
immutable image's **original** version/source/channel and build inputs. Reuse copies
that artifact record verbatim, without a build, new tag or rewritten OCI labels.
The registry digest must still be accessible. A legacy manifest with no artifact
fingerprints causes an honest one-time full rebuild; malformed/unavailable prior
state refuses publication. Preparation pins the previous `deployments` Git commit
once and all jobs use that immutable baseline, even if the branch later moves.

The fingerprint covers the complete Git context tree (including lockfiles, shared
COPY inputs, ignore files, Dockerfile, file modes and symlinks), the actual retained
publisher bytes, workflow blob, fixed platform and verified Signal download bytes.
Builds use a clean Git tree archive, plus only the generated Nora stamp or verified
Signal downloads. An untracked checkout file cannot silently enter the artifact.
Compose build contexts/arguments/options must match the small publisher contract;
unknown options refuse publication. A changed Dockerfile builds until its exact
recipe hash is reviewed in `REUSE_RECIPES`; the current recipes pin external base
images and lock language dependencies. This is recipe equivalence, not reproducible
rebuilding against mutable apt repositories: remote package-index freshness is not
an implicit upgrade trigger. Change the dependency/base/recipe input to request one.

Changed images also import/export a persistent BuildKit registry layer cache at
`ghcr.io/mikeng-io/nora-<image>:buildcache-linux-amd64`. The pinned Buildx setup uses
the `docker-container` driver and permits the required host build network. Separate
cache manifests use `mode=max` to retain intermediate multi-stage layers; an absent
cache allows the first build. Cache tags never enter deployment receipts or image
selection. See Docker's [Actions cache](https://docs.docker.com/build/ci/github-actions/cache/)
and [registry cache](https://docs.docker.com/build/cache/backends/registry/) documentation.
Publisher/workflow changes invalidate prior artifact reuse proof once.

WhatsApp installs curl and locked npm dependencies before copying bridge source.
Signal verifies/extracts its signed archive in a build stage; runtime retains curl,
Java 25 and the verified distribution, without the archive or build GPG. Nora's root
recipe still installs after copying source/stamp, and formal releases change both
Python dependency manifests too. Layer caching does not promise dependency-install
hits for Nora; no manifest normalization or new staging environment is introduced.

The context hash deliberately overcounts ignored files. In particular Nora and the
site have repository-root contexts, so an unrelated root change can rebuild the site.
There is no Dockerfile/ignore parser or list of selected COPY paths. This conservative
ceiling holds unless measured unnecessary site builds justify a narrower native context.
Nora always rebuilds, because its global version/source/build-time stamp is baked in;
formal app-only releases therefore also update Nora, presence and analytics.

Normal `up` has no `--force-recreate`: native Compose compares the image and effective
service configuration, retaining unchanged container IDs. All candidate images are
pulled and host config validated first. The existing lock, failed-attempt marker,
private diagnostics and whole-cohort image/health/zero-restart/stamp verification
still apply. Historical restart counts on retained containers are not reset or ignored.

### Loaded-once host file reload

Bind file **contents** do not change Compose's configuration hash. Manual `DeployStack`
alone therefore does not apply loaded-once edits. After backup, candidate validation
and in-place copy, while still holding the shared lock, write a mode-0600 one-shot
request beside the installed wrapper. It carries file names only, never file contents,
values or private hashes. For example, for a validated `config.toml` edit:

```bash
# INSIDE the existing shared lock, after the validated in-place copy:
(umask 077; printf '%s\n' '{"files":["config.toml"]}' > /etc/komodo/nora-deploy/config-reload.json)
```

Release the lock, then run native `DeployStack` on `nora-releases`. Supported names
are `config.toml`, `identity.toml`, `policy.toml`; combine them in `files` when needed.
The wrapper derives readers from the resolved owned-service mounts, reconciles the
normal cohort, then force-recreates only those readers. Infrastructure stays excluded.
With the current Compose file, config selects Nora/presence/analytics/STT/voice-wake;
identity selects Nora/app/presence/analytics; policy selects Nora. Read back the
process-loaded value and start time, not just the mounted file.

The request is consumed only after the entire transaction is verified. Failed or
interrupted application retains it and the outstanding attempt; native failure
recovery below still applies. Restore the backup in place under the lock on failure,
inspect the attempt privately, then release the lock before native Stack recovery.
Do not remove a pending request merely to evade the loaded-once requirement.

## Runtime state and secret handling

The generated Compose file removes `build` entries and doctrine/Dreamer source
mounts. The image's own instructions become authoritative. Relative host binds
become `${NORA_RUNTIME_ROOT:?}/...`, preserving the existing runtime directory,
private config and transport account stores outside Komodo's managed clone.
The default native monitoring exclusions are `site` and `embedder`, since the
secret-safe discovery pass includes inactive profiles. Remove `site` from those
exclusions when enabling `--site`; the wrapper then enables the profile for both
resolution and recreation. Remove `embedder` only where that separate infrastructure
profile is already running.

Production's runtime root is still `~/workspace/nora`: it holds `config.toml`,
`policy.toml`, `identity.toml`, `.env`, `data/` and `state/` outside git. Database,
RustFS, Prometheus, Alertmanager and embedder still use that Compose root, and
the computer unit's `ExecStart` points there. **Do not delete or reset this live
runtime root.** Doctrine and Dreamer are baked into the release image; a host
`git pull` does not change them.

Set the runtime root to the **existing** directory; do not substitute a new empty
state directory, move account stores, or change the Compose project name. Named
volumes and existing networks keep their identities. External data/model root
overrides must remain absolute host paths.

For containerized Periphery, mount the existing runtime root read-only at the
same absolute path inside Periphery. Its Docker socket already provides host
Docker access; native Compose additionally needs to read the external `.env`
and the wrapper needs the host config. Install the reviewed `komodo_deploy.py`
and `verify-deploy.py` together under `/etc/komodo/nora-deploy`, shared at the
same path between host and Periphery. Candidate config copies and the final
`release-deploy.json` receipt, outstanding `release-attempt.json` marker and private
`failure-*.log` subprocess diagnostics live there, so a Docker bind sees the same file
as the process that prepared it. Config copies are private and removed after
validation. Diagnostics retain failed subprocess stdout/stderr with mode `0600`,
including failures during verifier Docker reads; they can contain secrets. Keep
the control directory owner-local and accessible only to the deployment account
(mode `0700`, also usable by Periphery at the shared path). Never upload these
files to Git, Komodo, chat or a log collector. Public failures name only the stage,
exit status and private diagnostic path. If recording fails, deployment still
fails with the original command exit status and reports diagnostics unavailable.
Do not install trusted executables inside the fetched release clone.
An application release does not update these installed scripts. Back up and replace
the reviewed wrapper in this shared control directory under the shared deploy lock;
preserve the receipt, outstanding marker and diagnostics when updating it.
The runtime root itself can remain read-only to Periphery. Also bind the
**existing host lock file** `/tmp/nora-deploy.lock` to container
`/run/nora-deploy.lock`, as in `periphery.override.example.yml`. This keeps the
same host inode while avoiding Linux sticky-directory protection: a root-run
Periphery cannot open another user's lock in `/tmp` when `fs.protected_regular=2`.
A container-local file would create an unrelated lock and allow simultaneous
manual/Komodo deployment. Create
the lock once as the deployment operator if absent; never replace or delete it.

Komodo normally captures resolved `docker compose config`. The wrapper handles
`config` too, using `--no-interpolate --no-env-resolution --no-path-resolution`.
Images and container names stay visible without revealing host environment values.
The real deploy resolves config only in captured subprocess output, never printed
into Core logs. An external env file uses `track=false` so Komodo does not store
its contents. `.komodo.env` is a separate generated file containing only the
runtime-root setting; it must never replace the existing private `.env`.

## First-install bootstrap: installation and activation

These are operator actions after reviewing the final code and disposable test.
They are not performed by the publisher or by merging its PR.

1. Back up the current Compose/Periphery settings, runtime config and database;
   confirm that the database backup can be restored. Record the existing project
   name, bind paths, named volumes and enabled site profile. Keep the existing
   manual deployment path available while the Komodo schedule is off.
2. The stock Periphery image has Docker and `flock`, but no Python. The audited
   Compose CLI is 5.5.0, also checksum-pinned in CI: 2.38.2 rejects required-path
   interpolation during secret-safe config capture. Build the
   reviewed `Periphery.Dockerfile` (the audited base plus stdlib Python), select
   that local image explicitly in the private Periphery Compose config, and
   retain its existing keys/settings. The private
   `periphery.override.example.yml` adds runtime and shared-lock mounts to the
   existing base Compose; keep both Compose files in subsequent operations.
   Install the two reviewed Python scripts
   in the shared control directory and add the same-path, read-only runtime mount
   to Periphery. Only recreate Periphery for this setup step. Confirm it can
   traverse/read the selected runtime
   config and private `.env`. Do not print either file.
3. Configure existing read-only Git/registry accounts in Komodo if the repository
   or GHCR packages are private. New GHCR packages are private by default; public
   repository visibility does not imply public image visibility. Do not make
   packages public merely to avoid configuring pull access. No new host credential
   belongs in GitHub Actions. Keep Komodo dashboard access on the private network.
4. Enable `RELEASE_IMAGES_ENABLED` only when ready to publish. Dispatch the workflow
   for a stable release containing this implementation; check every image job,
   the generated branch, exact source/version stamps and all immutable digests.
5. Import an operator-filled copy of the example resources **with the schedule
   still off**. Use the correct existing project/root/server and add `--site`
   only where enabled, with matching monitoring exclusions. Configure and test
   the Komodo failure-alert destination.
   Verify the native generated Compose command reaches the wrapper for both
   `config` and `up`. Never bypass the wrapper to make an error green.
6. Rehearse on a disposable Compose project: successful update, failed image
   pull, refused config, stale/missing service, failed health, correct stamp,
   private sentinel absent from Core logs, and a compatible rollback. Confirm
   that pull/config failures can retry the identical candidate, while a
   failed/interrupted recreation or readback is refused before another pull/recreate
   and leaves the previous success receipt.
   Local/unit checks do not establish that this Core/Periphery installation did it.
7. After an explicit production-cutover approval, run the Procedure manually once
   against the intended release. Read every owned service image ID/health, the
   Nora version/source stamp, persistent volumes and final receipt. Only then
   enable the ten-minute Procedure schedule. Change checks continue after failure,
   but a candidate that reached recreation cannot be reapplied after failure automatically.

Once cut over, use the Komodo Stack for release deployment. Do not concurrently
run the legacy checkout-based rebuild procedure: it would restore local tags and
live instruction mounts. Config/identity/policy edits remain operator-controlled;
validate a candidate first, request the scoped file reload above, and run the
native Stack deployment under the same lock. A host config edit does not create
a new release.

## Failure and recovery

A failed build/pull/config check leaves the running services untouched. A failed
pull or config-validation attempt creates no outstanding marker, so a later
change check may retry the identical candidate under the shared lock. A failed
recreate/readback can leave partially updated services; the outstanding marker
blocks reapplication of that candidate, with no new success receipt. The native
Procedure keeps checking and reports failure; it does not pretend the candidate
was deployed. Pause its schedule while investigating if needed. Inspect details
privately in the control directory's `failure-*.log`; keep their raw contents on
the owner host. A running process alone is insufficient: readback names expected services
before comparing image IDs, so an absent collector or old digest cannot disappear
from the denominator. Services with health checks must become healthy and all
owned services must have zero restarts after recreation. Nora's running manifest
must match the selected release version and source SHA.

There is no blind automatic rollback. Startup runs database migrations, so an
older image may be incompatible. For a compatible rollback, revert the selecting
commit on `deployments` to select a previous complete image bundle, then wait for
the poller or run native `DeployStack` manually. The wrapper refuses only an
identical failed fingerprint and does not enforce version order. A revert does
not roll back the database schema (migrations upgrade to head on start), nor does
it roll back host config; review both for compatibility first. After inspecting the failure
and deciding to retry the same candidate, remove only the shared control
directory's `release-attempt.json` marker while holding the shared deploy lock,
then release the lock and run the Procedure manually (it acquires the same lock).
Existing markers, including those written before preflight by an older wrapper,
are never automatically cleared by installing the new wrapper: first establish
whether recreation occurred and review running state. Never clear a marker
concurrently with an attempt. The publisher refuses old
versions and non-forward source selections; rollback is a separate operator-controlled
promotion. When schemas are
incompatible, roll forward or use the tested database restore procedure. A failed
rollback stays failed and must alert. Preserve the last successful receipt for
comparison; it is not proof the previous version is still running after failure.

## Sources

- [Komodo Compose Stacks](https://komo.do/docs/deploy/compose)
- [Komodo scheduled Procedures](https://komo.do/docs/automate/procedures)
- [Komodo 2.3.3 wrapper and post-hook semantics](https://github.com/moghtech/komodo/blob/v2.3.3/bin/periphery/src/api/compose.rs)
- [Docker Compose config privacy flags](https://docs.docker.com/reference/cli/docker/compose/config/)
