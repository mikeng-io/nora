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
- Compatible rollback uses the complete reviewed image/config state and manual
  native Stack deployment, as the Komodo recovery procedure specifies; never
  legacy retag/recreate commands. Read back every owned service and the receipt.

**Both files confirmed absent: checkout mode.** Start with
[change selection](reference.md#checkout-mode-change-selection). Recheck mode after
acquiring the checkout lock, before mutating files or services.

## 1. The change is merged

```bash
gh pr view <N> --json state,mergeCommit -q '.state+" "+.mergeCommit.oid'
```

Check: `MERGED <sha>`. Keep it as `SHA`; step 3 proves nora-host's HEAD contains it.

## 2. One deployer at a time

Every checkout deploy, config edit and rollback holds the shared lock through
util-linux `flock` and refuses unlocked Docker build/up (reference.md, Lock).

Every locked script runs under `LOCK='flock -o -E 75 -w 900 /tmp/nora-deploy.lock'`
(`-o`: nothing the script starts inherits the lock; reference.md, Lock).

## 3. Build, recreate, verify (one locked script)

```bash
ssh nora-host "SHA=$SHA ${LOCK:?set LOCK, step 2} bash -s" > deploy.log 2>&1 <<'EOF'
set -u
if pgrep -af 'docker( compose)? (build|up)'; then echo "UNLOCKED_DEPLOY_RUNNING"; exit 75; fi
cd ~/workspace/nora || exit 1
git fetch -q --tags origin || { echo "FETCH_FAILED"; exit 2; }
git status -sb | head -1
AHEAD=$(git rev-list --count origin/master..HEAD)
DIRTY=$(git status --porcelain --untracked-files=no)
if [ "$AHEAD" != 0 ] || [ -n "$DIRTY" ]; then echo "CHECKOUT_NOT_CLEAN ahead=$AHEAD"; echo "$DIRTY"; exit 3; fi
git merge -q --ff-only origin/master || { echo "FF_FAILED"; exit 4; }
git merge-base --is-ancestor "$SHA" HEAD || { echo "SHA_NOT_IN_HEAD"; exit 5; }
git log --oneline -1
docker network inspect honcho_memory >/dev/null 2>&1 || docker network create --internal honcho_memory  # DEC-259: every `up` naming nora fails without it; --internal keeps nora's default route
V=; rm -f nora/version.json; python3 scripts/gen_version_manifest.py && V=$(python3 -c 'import json; print(json.load(open("nora/version.json"))["version"])'); echo "VERSION=${V:-unstamped}"  # DEC-314
NORA_VERSION=$V NORA_GIT_SHA=$(git rev-parse HEAD) NORA_BUILT_AT=$(date -u +%FT%TZ) docker compose build nora > /tmp/nora-build.log 2>&1; BUILD_RC=$?
echo "BUILD_RC=$BUILD_RC"; grep -E 'Built|ERROR' /tmp/nora-build.log | tail -n 3
[ "$BUILD_RC" = 0 ] || exit 6
cp config.toml /tmp/config.live-check.toml || exit 14   # never bind-mount from the checkout
docker run --rm -v /tmp/config.live-check.toml:/tmp/live.toml:ro --entrypoint /app/.venv/bin/nora nora:latest \
  config validate --config /tmp/live.toml </dev/null; CFG_RC=$?
echo "CFG_RC=$CFG_RC"; [ "$CFG_RC" = 0 ] || { echo "LIVE_CONFIG_REFUSED_BY_NEW_IMAGE"; exit 14; }
docker compose up -d --force-recreate nora presence analytics; UP_RC=$?
echo "UP_RC=$UP_RC"; [ "$UP_RC" = 0 ] || exit 7
python3 scripts/verify-deploy.py; VD_RC=$?
echo "VD_RC=$VD_RC"; [ "$VD_RC" = 0 ] || exit "$VD_RC"
H=$(git rev-parse --short HEAD); docker tag nora:latest "nora:sha-$H" || { echo TAG_FAILED; exit 8; }
mkdir -p ~/nora-backups && echo "$(date -Is) $H $(docker image inspect -f '{{.Id}}' nora:latest)" \
  >> ~/nora-backups/deploys.log || { echo LOG_FAILED; exit 13; }
tail -n 2 ~/nora-backups/deploys.log
EOF
DEPLOY_RC=$?; echo "DEPLOY_RC=$DEPLOY_RC"; cat deploy.log
```

Check: `DEPLOY_RC=0`; `VERSION=vX.Y.Z[-N-g<sha>]` (`unstamped` deploys by design: read why above); `BUILD_RC=0 UP_RC=0 VD_RC=0`; every `nora:latest`
container `ok`; the log's last line names this commit. Only a VERIFIED deploy
is tagged and logged, so a failed one is never a rollback target (step 6). On failure:

- Exit 75 with no output / `UNLOCKED_DEPLOY_RUNNING`: another deploy is live.
  Wait, then rerun; never kill it. A leaked lock: reference.md, Lock.
- `LIVE_CONFIG_REFUSED_BY_NEW_IMAGE`: master changed a config key the live file
  still has; nothing was recreated. Fix it through step 5, then rerun (reference.md).
- `CHECKOUT_NOT_CLEAN`: the checkout holds another session's work (22 and 7
  unpushed commits, 2026-09-09). Push it first
  (`git push origin HEAD:refs/heads/<type>/<slug>`), land it by PR, rerun.
  Never `reset`, never force.
- `FETCH_FAILED`, `FF_FAILED`, `UP_RC` nonzero: reference.md, Other step 3 failures.
- `VD_RC=1`: `STALE` names a container to recreate, `DOWN` says
  `docker logs <name> --tail 30`. Fix and rerun `verify-deploy.py` until 0.
  `VD_RC=2`: no container declares `nora:latest`.

Other images (voice, the rest): reference.md, Other images.

Then prove THIS change works: the one metric, row, trace or log line that only
the new code produces (nora-readback). The deploy is not done without it.

## 4. Doctrine or dreamer only

Run step 3's script up to `git log --oneline -1` (no tag, build or up). Check:
`curl -s localhost:9100/health | jq -r .doctrine_sha256` differs from its
value before the pull, and `nora_doctrine_reload_failures_total` in
`localhost:9100/metrics` did not move (a bad parse keeps the last good
doctrine, `docs/runbook.md` section 2). A dreamer objective shows at its next
pass: read that pass's trace (nora-readback).

## 5. Config-only change

`nora config set` inside the container fails `EBUSY` on the file bind-mount
(2026-09-09), so the CLI validates and the host edits. Edit a COPY, validate
the copy through the running container, and only then put it in place; a bad
key reaches no process, and the backup comes back on any failure:

```bash
ssh nora-host "${LOCK:?set LOCK, step 2} bash -s" > cfg.log 2>&1 <<'EOF'
set -u
cd ~/workspace/nora || exit 1
mkdir -p ~/nora-backups; B=~/nora-backups/config.toml.$(date +%Y%m%dT%H%M%S)
cp config.toml "$B" && cp config.toml /tmp/config.candidate.toml || exit 1
grep -n '^<key> *=' /tmp/config.candidate.toml    # several hits: address the line by number
sed -i '<line>s/^<key> = .*/<key> = <value>/' /tmp/config.candidate.toml
diff "$B" /tmp/config.candidate.toml               # exactly the intended line
docker compose cp /tmp/config.candidate.toml nora:/tmp/config.candidate.toml </dev/null || exit 1
docker compose exec -T nora /app/.venv/bin/nora config validate --config /tmp/config.candidate.toml </dev/null; VAL_RC=$?
echo "VAL_RC=$VAL_RC"; [ "$VAL_RC" = 0 ] || { echo "INVALID, live file untouched"; exit 9; }
cp /tmp/config.candidate.toml config.toml          # cp writes in place: same inode as the mount
docker compose up -d --force-recreate nora presence analytics; UP_RC=$?
python3 scripts/verify-deploy.py; VD_RC=$?
echo "UP_RC=$UP_RC VD_RC=$VD_RC"
if [ "$UP_RC" != 0 ] || [ "$VD_RC" != 0 ]; then
  cp "$B" config.toml && docker compose up -d --force-recreate nora presence analytics
  echo "RESTORED $B UP_RC=$?"; python3 scripts/verify-deploy.py; echo "RESTORE_VD_RC=$?"; exit 10
fi
docker compose exec -T nora /app/.venv/bin/python -c 'from pathlib import Path; from nora.config import load; print(load(Path("config.toml"), require_secrets=False).<section>.<key>)' </dev/null
EOF
```

Check: one-line diff, `VAL_RC=0`, `UP_RC=0 VD_RC=0`, no `RESTORED`, new value.
After a `RESTORED`, production is healthy only if `RESTORE_VD_RC=0`. Add `stt
voice-wake` to both recreates for a `[speech.*]` key. Backups go to `~/nora-backups/`, never into the repo.

- `</dev/null` on each `docker compose exec` in a heredoc (reference.md).
- Never `sed -i` the live file: it writes a new inode, a running container
  keeps reading the old one, and `/health`'s `stale` flag cannot see it.
- `policy.toml` / `identity.toml`: same steps, recreate the services in the
  step 0 table, then `curl -s localhost:9100/health | jq .config` must show
  `"stale": false` for the file (stale means edited after the process started).
- Never bind-mount from the deploy checkout to validate anything: Docker
  creates a missing source directory as root and the next pull fails.

## 6. Rollback

Step 3 logs every verified deploy to `~/nora-backups/deploys.log` and tags its
image `nora:sha-<sha>`. The target is the newest logged image that is not
running and was never rolled back FROM (by image id, in two passes, not by
line: after one deploy, the line before is the running image). The image
runs `alembic upgrade head` at start, so an older image cannot run against a
schema a newer deploy migrated: the script refuses and you roll FORWARD with a
revert PR (nora-land, then step 3).

```bash
ssh nora-host "${LOCK:?set LOCK, step 2} bash -s" > rollback.log 2>&1 <<'EOF'
set -u
cd ~/workspace/nora || exit 1
CUR=$(docker inspect -f '{{.Image}}' nora) || exit 1
L=~/nora-backups/deploys.log; PREV=$(awk -v c="$CUR" 'NR==FNR {if ($2=="ROLLBACK") bad[$3]=1; next} $2!="ROLLBACK" && $3!=c && !($3 in bad) {p=$2} END {print p}' "$L" "$L" 2>/dev/null)
[ -n "$PREV" ] && docker image inspect "nora:sha-$PREV" >/dev/null || { echo "NO_PREVIOUS"; exit 11; }
MIG=$(git diff --name-only "$PREV" HEAD -- nora/infra/sql/migrations/versions)
[ -z "$MIG" ] || { echo "MIGRATION_SINCE $PREV: roll forward"; echo "$MIG"; exit 12; }
docker tag "nora:sha-$PREV" nora:latest && docker compose up -d --force-recreate nora presence analytics; echo "UP_RC=$?"
python3 scripts/verify-deploy.py; echo "VD_RC=$?"
echo "$(date -Is) ROLLBACK $CUR -> $PREV" >> ~/nora-backups/deploys.log
EOF
```

Check: `UP_RC=0 VD_RC=0`. `NO_PREVIOUS` (no logged deploy yet) or
`MIGRATION_SINCE`: roll forward instead. The checkout, doctrine and config
stay at the new commit: revert `config.toml` and `policy.toml` keys the old
image does not know first (step 5), or it crash-loops. Then land a revert PR
and deploy it with step 3, so the checkout and the image agree again.

## 7. Orphans and host notes

A recreate failing with `container name "/<name>" is already in use`, zsh
word-splitting, filtered DNS, the build network and the container's python:
`.agents/skills/nora-deploy/reference.md`. Read it before the first `ssh nora-host`.
