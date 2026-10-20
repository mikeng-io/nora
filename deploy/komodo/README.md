# Release deployment with Komodo

Status: repository implementation, **disabled until installation and rehearsal**.
The release publisher requires `RELEASE_IMAGES_ENABLED=true`; the example Komodo
Procedure has its schedule disabled. Merging this change deploys nothing.

## Ownership and flow

1. Release Please publishes a stable `vX.Y.Z` release. Its GitHub App token
   triggers `Release images`; the `GITHUB_TOKEN` fallback cannot trigger that
   follow-on workflow. Manual dispatch accepts an already-published stable tag
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
   candidate before deployment and clears it only after verified success.
   Failed/interrupted candidates are refused on subsequent checks; a newer
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
`release-deploy.json` receipt and outstanding `release-attempt.json` marker live
there, so a Docker bind sees the same file
as the process that prepared it. Config copies are private and removed after
validation. Do not install trusted executables inside the fetched release clone.
The runtime root itself can remain read-only to Periphery. Also bind the
**existing host lock file** `/tmp/nora-deploy.lock` to that exact container path,
as in `periphery.override.example.yml`. Container-local `/tmp` would otherwise
create an unrelated lock and allow simultaneous manual/Komodo deployment. Create
the lock once as the deployment operator if absent; never replace or delete it.

Komodo normally captures resolved `docker compose config`. The wrapper handles
`config` too, using `--no-interpolate --no-env-resolution --no-path-resolution`.
Images and container names stay visible without revealing host environment values.
The real deploy resolves config only in captured subprocess output, never printed
into Core logs. An external env file uses `track=false` so Komodo does not store
its contents. `.komodo.env` is a separate generated file containing only the
runtime-root setting; it must never replace the existing private `.env`.

## Installation and activation

These are operator actions after reviewing the final code and disposable test.
They are not performed by the publisher or by merging its PR.

1. Back up the current Compose/Periphery settings, runtime config and database;
   confirm that the database backup can be restored. Record the existing project
   name, bind paths, named volumes and enabled site profile. Keep the existing
   manual deployment path available while the Komodo schedule is off.
2. The stock Periphery image has Docker and `flock`, but no Python. Build the
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
   that a failed/interrupted candidate is refused before another pull/recreate
   and leaves the previous success receipt.
   Local/unit checks do not establish that this Core/Periphery installation did it.
7. After an explicit production-cutover approval, run the Procedure manually once
   against the intended release. Read every owned service image ID/health, the
   Nora version/source stamp, persistent volumes and final receipt. Only then
   enable the ten-minute Procedure schedule. Change checks continue after failure,
   but the same candidate cannot be reapplied automatically.

Once cut over, use the Komodo Stack for release deployment. Do not concurrently
run the legacy checkout-based rebuild procedure: it would restore local tags and
live instruction mounts. Config/identity/policy edits remain operator-controlled;
validate a candidate first and run the native Stack deployment to recreate the
owned cohort under the same lock. A host config edit does not create a new release.

## Failure and recovery

A failed build/pull/config check leaves the running services untouched. A failed
recreate/readback can leave partially updated services; the outstanding marker
blocks reapplication of that candidate, with no new success receipt. The native
Procedure keeps checking and reports failure; it does not pretend the candidate
was deployed. Pause its schedule while investigating if needed. Inspect details
privately on the host. A running process alone is insufficient: readback names expected services
before comparing image IDs, so an absent collector or old digest cannot disappear
from the denominator. Services with health checks must become healthy and all
owned services must have zero restarts after recreation. Nora's running manifest
must match the selected release version and source SHA.

There is no blind automatic rollback. Startup runs database migrations, so an
older image may be incompatible. For a compatible rollback, review a deployment-
branch commit selecting a previous complete image/config/instruction state and
run the native Stack manually with polling paused. After inspecting the failure
and deciding to retry the same candidate, remove only the shared control
directory's `release-attempt.json` marker while holding the shared deploy lock,
then run the Procedure manually. Never clear it concurrently with an attempt. The publisher refuses old
versions; rollback is a separate operator-controlled promotion. When schemas are
incompatible, roll forward or use the tested database restore procedure. A failed
rollback stays failed and must alert. Preserve the last successful receipt for
comparison; it is not proof the previous version is still running after failure.

## Sources

- [Komodo Compose Stacks](https://komo.do/docs/deploy/compose)
- [Komodo scheduled Procedures](https://komo.do/docs/automate/procedures)
- [Komodo 2.3.3 wrapper and post-hook semantics](https://github.com/moghtech/komodo/blob/v2.3.3/bin/periphery/src/api/compose.rs)
- [Docker Compose config privacy flags](https://docs.docker.com/reference/cli/docker/compose/config/)
