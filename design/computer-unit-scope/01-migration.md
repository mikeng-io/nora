# Offline group computer cutover

Status: implemented by [#164](https://github.com/mikeng-io/nora/pull/164)
(merge `811d6b08c`), released in
[v6.5.0](https://github.com/mikeng-io/nora/releases/tag/v6.5.0)
(`34c101982`) and deployed through Komodo. The operator-verified 2026-10-21
cutover moved 3 room workspaces into 2 unit workspaces, read 3405 files as their
new owner and bound 6 sessions. This revision records that supplied live evidence;
it performs no host access. The original contract remains a historical design.
[#168](https://github.com/mikeng-io/nora/pull/168) (merge `e6fdf3d86`)
corrected both health gates below to match the actual single-spaced status line.

## Lessons from the live cutover

The “Also” section of #168 records these operational limits:

- sbx cannot resolve scope users created after sandbox boot. Host-side execution
  as any unit or future DM user must enter as root and switch inside the VM:
  `sbx exec -u root nora-computer runuser -u <scope-user> -- …`.
  Batch owner reads per scope, rather than starting one sbx exec per file.
- `deploy/computer/archive_check.py::problems` refuses absolute/out-of-scope
  links: venv interpreter links and Chrome's `browser/*/SingletonSocket` were
  moved aside, as were stale `.tmp/` sockets refused by
  `deploy/computer/migrate_unit_scope.py::snapshot`. They were preserved under
  `/var/lib/computer-unit-migration/`, not silently dropped. This is a shared
  unit/DM backup follow-up, not fixed by the migration: a workspace venv can
  make an otherwise completed backup fail its restore check. The required
  backup preparation and restore proof are in
  [DM acceptance](../private-dm/04-build-and-acceptance.md#2-deterministic-proof-and-actual-inputs).
  Keep archive validation fail-closed; never allow arbitrary external links.
- In Komodo mode, the HOST command below must use `docker run` on the existing
  Nora container's immutable image digest, with the installed environment,
  policy, database network and migration mounts. The shown `docker compose run`
  form is checkout-mode only: in Komodo it tries to build `nora:latest`.
- Each sbx exec leaked roughly ten host gnome-keyring file descriptors. #168
  records an immediate soft-limit increase from 1024 to 4096 via prlimit; the
  operator reports a systemd `LimitNOFILE` drop-in as the persistent mitigation.
  Batching reduces calls; raising the limit does not fix the leak.

These commands are an operator runbook, **not evidence of a live migration**.
Do not enable the new Nora image until every check below succeeds. Pause the
Komodo release poll first if installed. Use the installed deployment mode from
`.agents/skills/nora-deploy/SKILL.md`; the separately managed computer needs no
image rebuild because computerd itself is unchanged. Ping the operator before
deployment. Run under the existing deploy lock, with a reviewed merged checkout
as `SOURCE`, the existing runtime directory as `RUNTIME`, and backups/evidence
outside both. No command here is run by the implementation lane.

## Pause, drain, back up

On norty, with the deployment poll paused and no other deploy in progress:

```bash
set -euo pipefail
: "${SOURCE:?absolute path to reviewed merged source}"
: "${RUNTIME:?existing deployment runtime root}"
: "${COMPUTER_SCOPE_SHA:?reviewed merged commit}"
test "$(git -C "$SOURCE" rev-parse HEAD)" = "$COMPUTER_SCOPE_SHA"
D="$HOME/nora-backups/computer-unit-scope-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -m 700 "$D"
umask 077
export SOURCE RUNTIME COMPUTER_SCOPE_SHA D
export NORA_ENV_FILE="$RUNTIME/.env" NORA_COMPOSE_DIR="$RUNTIME"
# Enter the locked shell; run the following stages inside it.
flock -o -E 75 -w 900 /tmp/nora-deploy.lock bash
```

Inside the locked shell, set its own failure flags before running any stage
(the parent shell's `set` options are not inherited):

```bash
set -euo pipefail
umask 077
cd "$RUNTIME"
```

Allow active subagent jobs to finish before stopping Nora. Then stop admission
and recheck the durable outbox. The query must print **0**; otherwise leave Nora
stopped and reconcile the jobs before proceeding. Stopping Nora drains its
in-process computer leases; the check below prevents treating interrupted jobs
as completed.

```bash
docker compose stop -t 900 nora presence analytics
test "$(docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM compute_jobs WHERE state IN ('"'"'pending'"'"', '"'"'firing'"'"')"')" = 0
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$D/database.sql"
sbx exec -u root nora-computer ps -eo uid,pid,ppid,args > "$D/vm-processes-before.txt"
systemctl --user stop nora-computer.service
sbx exec -u root nora-computer mkdir -p /var/lib/computer-unit-migration
sbx exec -u root nora-computer chmod 700 /var/lib/computer-unit-migration
sbx cp "$SOURCE/deploy/computer/migrate_unit_scope.py" nora-computer:/opt/computerd/migrate_unit_scope.py
```

Record tmux/browser/process state in the private evidence above; browser profiles
are flushed by clean termination, not merged or resumed as running processes.
The live image includes `pkill` (procps). Send TERM to the old scope users,
including tmux/Chrome, **only after jobs are drained**. The migration itself
refuses any remaining old/new-user process. Never use a broad `pkill -f`.

```bash
sbx exec -u root nora-computer /opt/computerd/venv/bin/python -c '
import pathlib,pwd,subprocess
for p in pathlib.Path("/workspace").iterdir():
    if len(p.name) != 32 or any(c not in "0123456789abcdef" for c in p.name): continue
    try: user=pwd.getpwnam("c-"+p.name[:12])
    except KeyError: continue
    result=subprocess.run(["pkill","-TERM","-u",str(user.pw_uid)])
    if result.returncode not in (0,1): raise SystemExit(result.returncode)
'
```

The normal workspace backup **excludes every scope's browser directory**. After
the clean termination above, take a separate complete browser archive, including
hidden files and symlink text, with no browser exclusions. All processes must
have exited before taking either backup; if they have not, leave admission
paused and finish draining them. Keep both archives host-private: the browser
archive contains credentials and is outside the nightly rotation.

```bash
# Refuse any remaining scope processes before archiving the quiescent disk.
sbx exec -u root nora-computer /opt/computerd/venv/bin/python -c '
import sys
sys.path.insert(0,"/opt/computerd")
from pathlib import Path
from migrate_unit_scope import inventory, _drained
scopes=inventory(Path("/workspace"))
_drained({s:s for s in scopes})
'
WORKSPACE_BACKUP=$(NORA_COMPUTER_BACKUP_DIR="$D/workspace" "$SOURCE/deploy/computer/nora-computer" backup)
test -n "$WORKSPACE_BACKUP"
test -f "$WORKSPACE_BACKUP"
printf '%s\n' "$WORKSPACE_BACKUP" > "$D/workspace-backup-path.txt"
VM_BACKUP=$(sbx exec -u root nora-computer mktemp -d /tmp/nora-unit-rollback.XXXXXX)
sbx exec -u root nora-computer bash -c '
set -euo pipefail
umask 077
for d in /workspace/*/browser; do
    if [ -e "$d" ] || [ -L "$d" ]; then
        printf "%s\0" "${d#/workspace/}"
    fi
done > "$1/browser-paths"
tar --null -C /workspace -czpf "$1/browser-profiles.tar.gz" -T "$1/browser-paths"
# GNU tar compares the archived bytes, modes, owners and links with the disk.
tar -C /workspace -dzf "$1/browser-profiles.tar.gz"
' bash "$VM_BACKUP"
BROWSER_SHA=$(sbx exec -u root nora-computer sha256sum "$VM_BACKUP/browser-profiles.tar.gz" | cut -d' ' -f1)
sbx cp "nora-computer:$VM_BACKUP/browser-profiles.tar.gz" "$D/browser-profiles.tar.gz"
chmod 600 "$D/browser-profiles.tar.gz"
printf '%s  browser-profiles.tar.gz\n' "$BROWSER_SHA" > "$D/browser-profiles.tar.gz.sha256"
(cd "$D" && sha256sum -c browser-profiles.tar.gz.sha256)
tar -tzf "$D/browser-profiles.tar.gz" > "$D/browser-archive-members.txt"
# Preflight the EXACT archives that rollback will feed to restore.
python3 "$SOURCE/deploy/computer/archive_check.py" "$WORKSPACE_BACKUP" > "$D/workspace-restore-check.txt"
python3 "$SOURCE/deploy/computer/archive_check.py" "$D/browser-profiles.tar.gz" > "$D/browser-restore-check.txt"
sbx exec -u root nora-computer rm -rf "$VM_BACKUP"
sbx exec -u root nora-computer test ! -e "$VM_BACKUP"
```

Any backup, comparison or restore-validator failure aborts before data is copied
or retired. If a validator refuses an absolute/out-of-room link, retain its
report and both original archives; do not disable the validator or extract the
refused archive as root. No migration data has moved, so keep the legacy disk
and old image/config. The operator must review the named links and preserve
their original text in private evidence, then explicitly choose whether to
relocate them off the working disk or replace them with safe in-room links.
Only after that choice, take new backups and repeat both preflights. If the
legacy disk itself needs recovery from a refused archive, inspect it in a
disposable isolated VM as an unprivileged user and recover regular files into
fresh room directories without following links; recreate reviewed links
separately. The refused archive is never passed to the privileged restore path.
On failure, also remove any retained `$VM_BACKUP` after verifying the host copy.

## Inventory and dry-run

Only opaque hashes enter the VM. `host-plan.json` remains host-private. Policy
aliases are mapped too; an unlisted scope or historical session stops the whole
plan for explicit operator mapping. No default unit is guessed.

```bash
VM=(sbx exec -u root nora-computer /opt/computerd/venv/bin/python /opt/computerd/migrate_unit_scope.py)
"${VM[@]}" inventory > "$D/inventory.json"
HOST=(docker compose run --rm --no-deps -T --user "$(id -u):$(id -g)" -v "$SOURCE:/migration:ro" -v "$D:/migration-state" -e PYTHONPATH=/migration --entrypoint /app/.venv/bin/python nora /migration/scripts/migrate_computer_unit_scope.py)
"${HOST[@]}" plan --policy /app/policy.toml --inventory /migration-state/inventory.json --output /migration-state/host-plan.json --vm-mapping /migration-state/mapping.json
sbx cp "$D/mapping.json" nora-computer:/var/lib/computer-unit-migration/mapping.json
"${VM[@]}" plan --input /var/lib/computer-unit-migration/mapping.json > "$D/disk-plan.json"
sbx cp "$D/disk-plan.json" nora-computer:/var/lib/computer-unit-migration/disk-plan.json
```

Inspect both plans before applying. `plan` inventories bytes, hidden paths,
symlink text, modes and SHA-256 digests without making users or moving files.
A source that changes after this inventory is rejected. A destination present
without a completed journal entry is a conflict, even if its filenames match.

## Copy, verify, bind, retire

The copy preserves every old directory under `rooms/<old-scope>/`; it does not
flatten projects. Ownership is changed without following symlinks. Verification
reads every digest and owner before sources can be retired. Successful repeats
verify the completed move and do not overwrite it.

```bash
ARGS=(--input /var/lib/computer-unit-migration/disk-plan.json --journal /var/lib/computer-unit-migration/journal.json)
"${VM[@]}" apply "${ARGS[@]}" > "$D/verified.json"
"${VM[@]}" verify "${ARGS[@]}" > "$D/verified.json"
"${HOST[@]}" bind --input /migration-state/host-plan.json --receipt /migration-state/verified.json
"${HOST[@]}" bind --input /migration-state/host-plan.json --receipt /migration-state/verified.json --apply
"${HOST[@]}" bind --input /migration-state/host-plan.json --receipt /migration-state/verified.json
# The last dry-run must print: would bind 0 sessions.
"${VM[@]}" retire "${ARGS[@]}" > "$D/retired.json"
"${VM[@]}" verify "${ARGS[@]}" > "$D/verified-after-retirement.json"
sbx cp nora-computer:/var/lib/computer-unit-migration/journal.json "$D/journal.json"
```

The host bind changes only two keys of existing session detail, in one database
transaction. Original scopes, IDs, room, actor, tag, status and timestamps stay;
audit rows are untouched. Multiple open legacy tasks remain open. A new call
continues the most recently active, UUID descending on a tie. The existing
unique index still enforces one newly opened task per unit. No Alembic migration
is needed. The sweeper uses effective workspace scope for state and leases.

## Read back and deploy

Read every regular migrated file as the new owner, comparing its digest with
the frozen plan, and prove the old scope user cannot enter the unit directory.
These commands print only digests/status, never file bodies:

```bash
python3 - "$D/disk-plan.json" > "$D/live-owner-readback.json" <<'PY'
import hashlib,json,subprocess,sys
plan=json.load(open(sys.argv[1]))
for old,new in plan['mapping'].items():
    user='c-'+new[:12]
    files={p:e['sha256'] for p,e in plan['sources'][old]['entries'].items() if e['kind']=='file'}
    code="""import hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1])
for path,digest in json.load(sys.stdin).items():
    assert hashlib.sha256((root/path).read_bytes()).hexdigest()==digest
"""
    subprocess.run(['sbx','exec','-u','root','nora-computer','runuser','-u',user,'--',
                    'python3','-c',code,'/workspace/'+new+'/rooms/'+old],
                   input=json.dumps(files),text=True,check=True)
    subprocess.run(['sbx','exec','-u','root','nora-computer','runuser','-u','c-'+old[:12],'--',
                    'test','!','-r','/workspace/'+new+'/rooms/'+old],check=True)
print(json.dumps({'plan_sha256':hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest(),
                  'owner_readback_verified':True},sort_keys=True))
PY
```

Only after this live owner/isolation readback succeeds, retire the quarantined
source copies. This command requires the matching readback receipt and original
journal, refuses any unaccounted or unverified-retired source, and rechecks every
destination's digests/ownership before removing anything. It checks all remaining
quarantine snapshots before deleting them and verifies the directory is removed.
The original workspace/browser rollback archives and database backup in `$D`
are retained; this step never deletes them. Keep admission and computerd stopped.

```bash
sbx cp "$D/live-owner-readback.json" nora-computer:/var/lib/computer-unit-migration/live-owner-readback.json
"${VM[@]}" retire-quarantine "${ARGS[@]}" --live-readback /var/lib/computer-unit-migration/live-owner-readback.json > "$D/quarantine-retired.json"
sbx exec -u root nora-computer test ! -e /workspace/.unit-migration-sources
"${VM[@]}" inventory > "$D/inventory-after-retirement.json"
# Prove the resulting working disk produces a restorable normal backup.
POST_MIGRATION_BACKUP=$(NORA_COMPUTER_BACKUP_DIR="$D/post-migration" "$SOURCE/deploy/computer/nora-computer" backup)
test -n "$POST_MIGRATION_BACKUP"
test -f "$POST_MIGRATION_BACKUP"
python3 "$SOURCE/deploy/computer/archive_check.py" "$POST_MIGRATION_BACKUP" > "$D/post-migration-restore-check.txt"
systemctl --user start nora-computer.service
status_line=$("$SOURCE/deploy/computer/nora-computer" status)
printf '%s\n' "$status_line"
case "$status_line" in
  *" computerd=healthy egress=ok "*) ;;
  *) echo "computerd not healthy; leaving Nora admission paused" >&2; exit 1 ;;
esac
```

Keep Nora admission stopped until all these commands pass, and resume it only
after the status gate above passes. Then deploy the
reviewed merged image through **nora-deploy's installed mode**, after the operator
ping. In checkout mode the skill builds/recreates `nora presence analytics` under
this lock. In Komodo mode release this lock before the native manual `DeployStack`
for the existing Stack (its wrapper takes the same lock); keep polling paused
until its receipt/image/health readback passes. Do not substitute a checkout
build for an installed Komodo deployment. Actual Stack identity and release SHA
are host state and must be read by the deployment lane, not invented here.

After deployment, check unit session lists/context from each group transport,
creation-room provenance, unit lease contention, new browser profile/tab
continuity and separation from another unit. Live model acceptance and live
migration remain unmeasured by the offline tests. The tool wording changed only
to reflect unit sharing; paid routing evals and skill reach/cost reads were not
run in the implementation lane.

## Interrupted or failed cutover

Leave admission and the release poll paused. A conflicting/partial destination,
changed source, non-drained process, metadata mismatch or wrong digest/ownership
is an error, never an empty success. The root-only
`/workspace/.unit-migration-sources/` quarantine stays on the same disk, avoiding
a cross-filesystem rename. Keep it until the verified retirement step above;
always retain the verified disk/database backups.

If interrupted between rename and journal write, reconcile the quarantine entry
against the **original** plan/journal and database backup, verifying its complete
snapshot and the destination's digests/ownership before any retry. Fresh inventory
and planning refuse every nonempty quarantine, including a completed retirement;
they cannot silently omit a source whose journal update was interrupted. After
successful live readback, `retire-quarantine` clears completed retirement and
unblocks fresh inventory/planning. Retain
the original plan and journal for verified repeats. Do not delete or overwrite a
conflicting destination. A partial copy is intentionally refused.

Rollback covers failure both before computerd starts and after it is running.
Use the same locked shell, or re-establish the exported variables and acquire
the same `flock ... bash` shell above; set its own strict flags. Stop admission,
drain jobs, stop computerd (also safe when already stopped), and terminate and
verify all scope-user processes before restoring either archive. A failed drain
aborts rollback with admission paused; never restore under active writers.

```bash
set -euo pipefail
cd "$RUNTIME"
docker compose stop -t 900 nora presence analytics
test "$(docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM compute_jobs WHERE state IN ('"'"'pending'"'"', '"'"'firing'"'"')"')" = 0
systemctl --user stop nora-computer.service
sbx exec -u root nora-computer /opt/computerd/venv/bin/python -c '
import pathlib,pwd,subprocess,sys
sys.path.insert(0,"/opt/computerd")
from migrate_unit_scope import SCOPE, _drained
scopes=[p.name for p in pathlib.Path("/workspace").iterdir() if SCOPE.fullmatch(p.name)]
for scope in scopes:
    try: user=pwd.getpwnam("c-"+scope[:12])
    except KeyError: continue
    result=subprocess.run(["pkill","-TERM","-u",str(user.pw_uid)])
    if result.returncode not in (0,1): raise SystemExit(result.returncode)
_drained({s:s for s in scopes})
'
WORKSPACE_BACKUP=$(cat "$D/workspace-backup-path.txt")
python3 "$SOURCE/deploy/computer/archive_check.py" "$WORKSPACE_BACKUP"
python3 "$SOURCE/deploy/computer/archive_check.py" "$D/browser-profiles.tar.gz"
(cd "$D" && sha256sum -c browser-profiles.tar.gz.sha256)
"$SOURCE/deploy/computer/nora-computer" restore "$WORKSPACE_BACKUP" --force
"$SOURCE/deploy/computer/nora-computer" restore "$D/browser-profiles.tar.gz" --force
```

Restore the saved session metadata/database using the deployment recovery
procedure, then restore the compatible old image/config with Nora admission
still stopped. Start computerd and read back its status; admission resumes only
after this gate passes, through the installed deployment recovery procedure:

```bash
systemctl --user start nora-computer.service
status_line=$("$SOURCE/deploy/computer/nora-computer" status)
printf '%s\n' "$status_line"
case "$status_line" in
  *" computerd=healthy egress=ok "*) ;;
  *) echo "computerd not healthy; leaving Nora admission paused" >&2; exit 1 ;;
esac
```

The second archive restore
returns all legacy browser directories to their original scope paths; it does
not choose a profile for the new unit browser. Quarantine and migrated copies
remain held while the old mapping is restored; do not expose them through a new
mapping. Never resume admission with half the mapping bound. Keep all backups
until successful live readback; restoring a chosen legacy browser profile into
the new unit browser is a separate explicit operator choice.
