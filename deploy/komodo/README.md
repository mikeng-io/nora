# Release deployment with Komodo

Status: **installed in production**, verified on 2026-10-21. Stack `nora-releases`
is deployed by the enabled `nora-release-poll` Procedure, which calls
`DeployStackIfChanged` every ten minutes. v6.5.0 deployed this way without host
commands. The example resources keep their schedule disabled for new installations;
the first-install bootstrap below is not the normal production deploy procedure.

## Ownership and flow

1. A fix PR merges to master. Follow `nora-release`: explicitly dispatch prepare
   with the assessed source and chosen bump, review and merge the metadata PR,
   then dispatch publish separately. Merging alone creates no tag or Release.
   The release App publishes the stable `vX.Y.Z` tag and GitHub Release, which
   triggers `Release images`; missing App setup fails closed. Manual image
   dispatch accepts an already-published stable tag
   from master. The release must contain this publisher implementation.
2. Hosted AMD64 runners build all ten images named by the root Compose file:
   Nora, app, site, STT, voice sidecar/wake, Signal/WhatsApp/Instagram bridges,
   and the alert bridge. Signal's signed tarball preparation is retained. The
   Nora manifest must name the exact release commit and version. CI gets registry
   publication permission, never deployment-host credentials.
3. Only after every build succeeds, the publisher writes `compose.json` and
   `release.json` to the `deployments` branch in one Git commit. All owned images
   are selected by digest. A delayed older release cannot replace a newer one;
   concurrent branch updates must fast-forward. Partial image builds cannot
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
`analytics` sharing Nora's image. Core and voice services always move together.
The public `site` remains opt-in (`--site` in the installed wrapper command).
Third-party database, object storage, embedder and monitoring images are excluded
from automatic recreation; `--no-deps` prevents implicit upgrades. The separately
managed computer service is outside this root Compose project.

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
validate a candidate first and run the native Stack deployment to recreate the
owned cohort under the same lock. A host config edit does not create a new release.

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
versions; rollback is a separate operator-controlled promotion. When schemas are
incompatible, roll forward or use the tested database restore procedure. A failed
rollback stays failed and must alert. Preserve the last successful receipt for
comparison; it is not proof the previous version is still running after failure.

## Sources

- [Komodo Compose Stacks](https://komo.do/docs/deploy/compose)
- [Komodo scheduled Procedures](https://komo.do/docs/automate/procedures)
- [Komodo 2.3.3 wrapper and post-hook semantics](https://github.com/moghtech/komodo/blob/v2.3.3/bin/periphery/src/api/compose.rs)
- [Docker Compose config privacy flags](https://docs.docker.com/reference/cli/docker/compose/config/)
