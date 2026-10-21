---
name: nora-deploy
description: Puts merged Nora code, doctrine or config live and proves it. Selects the installed checkout or Komodo deployment mode, serializes deployment, validates config and reads back owned services. Load for deploy, ship, rebuild, recreate, restart or live config edits. Not for trace/metric/row evidence (nora-readback).
---

# nora-deploy

Examples: SSH alias `nora-host`, checkout `~/workspace/nora`; substitute installed paths.
Background: `AGENTS.md`, `docs/maintainers/conventions-rationale.md`, `docs/runbook.md` (§3/3b).

- Run multi-line work as `ssh nora-host bash -s <<'EOF' ... EOF`, never a long
  `ssh nora-host '...'` (Host notes).
- Capture every rc before reading its log: `cmd > log 2>&1; RC=$?`. Never pipe
  a step whose success matters.
- Report each item as VERIFIED (with the reading), UNVERIFIED (why) or FAILED.

## 0. Select the installed deployment mode

Inspect production `/etc/komodo/nora-deploy/{release-deploy,release-attempt}.json`
on the host (through existing Periphery if needed); never use rehearsal state.
Unreadable state or unavailable Periphery is UNVERIFIED, not checkout mode.
An outstanding attempt requires native failure recovery, even without a success receipt.

**Receipt exists: Komodo owns deployment.** Use the existing production Stack and
runtime root, following [the procedure](../../../deploy/komodo/README.md). Checkout steps do not apply:

- Code, doctrine, Dreamer and runtime skills ship through a complete published
  release bundle and native Stack deployment. A master pull alone changes none
  of those image-owned files; never rebuild local tags or restore source mounts.
- Config/identity/policy edits retain backups, candidate validation and in-place
  copying. Hold the shared lock for file edits, then **release it before calling
  the native Stack**: its wrapper acquires the same lock and validates again.
  Use manual `DeployStack` for recreation even when Git files have not changed.
- Rollback reverts the selecting commit on `deployments`, then uses the native
  Stack. This does not roll back database schema or host config (step 4).

**Both files confirmed absent: checkout mode.** Start with
[change selection](reference.md#checkout-mode-change-selection). Recheck mode after
acquiring the checkout lock, before mutating files or services.

In checkout mode only, ensure the external memory network exists under that lock:

```bash
docker network inspect honcho_memory >/dev/null 2>&1 || docker network create --internal honcho_memory
```

Then follow the legacy reference's `up -d --force-recreate` procedure. This
bootstrap prerequisite does not authorize checkout deployment in Komodo mode.

## 1. Release and deployment

Production uses Stack `nora-releases`. Merge the fix PR, then have the maintainer
merge release-please's release PR manually while the repository is private.
The tag and GitHub Release trigger `release-images.yml`; all images must finish
before it commits `compose.json` and `release.json` to `deployments`.

The enabled `nora-release-poll` Procedure calls `DeployStackIfChanged` every ten
minutes. Wait for it, or run native `DeployStack` on `nora-releases` manually.
The installed wrapper holds the shared deploy lock, validates config, recreates
and verifies the owned services, then writes `release-deploy.json`.
v6.5.0 completed this path without host commands on 2026-10-21.

## 2. Read back the selected release

Compare `deployments`' `release.json` version, source SHA and image digests with
`/etc/komodo/nora-deploy/release-deploy.json`, every owned service's running image
and Nora's live `/app/nora/version.json` stamp. Check health and restarts too;
a successful workflow or an old receipt alone does not prove deployment.
Follow [the installed procedure](../../../deploy/komodo/README.md#failure-and-recovery).
Then prove the requested behaviour through `nora-readback` when applicable.

## 3. Config-only change

`~/workspace/nora` remains the live runtime root for `config.toml`, `policy.toml`,
`identity.toml`, `.env`, `data/` and `state/`, plus infrastructure and the computer
unit. **Do not delete or reset it.** Doctrine and Dreamer are image-owned in
Komodo mode; a host `git pull` does not update them.

Hold `flock -o -E 75 -w 900 /tmp/nora-deploy.lock` while editing files. Back up
first, edit a candidate copy, validate it through the running container, then
copy it into place in situ (preserving the mount inode).
Secrets stay in `.env`; never print them. Release the lock before manual native
`DeployStack`: the wrapper reacquires it and validates again. Use `DeployStack`
even with unchanged Git files so loaded-once config reaches recreated processes.
On failure restore the backup under the lock, then use native Stack recovery
and repeat receipt, image, health and process-config readback.

The service/file table is in [reference.md's change-selection section](reference.md#checkout-mode-change-selection),
not step 0. Its checkout build/pull commands are legacy only.

## 4. Rollback

Revert the selecting commit on `deployments` to select a previous complete image
bundle. Wait for the poller or use manual native `DeployStack`, then repeat step 2.
The wrapper refuses only an identical failed fingerprint; it does not enforce
version order. Preserve failure evidence and follow native recovery for a retry.

A revert does **not** roll back database schema: migrations upgrade to head on
startup. It also does not roll back host config. Check schema/config compatibility
before selecting an older bundle; otherwise roll forward or use a coordinated,
tested database restore. Never use legacy local retag/recreate commands here.
