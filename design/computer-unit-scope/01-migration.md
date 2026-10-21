# Offline group computer cutover

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
python3 - "$D/disk-plan.json" <<'PY'
import json,subprocess,sys
plan=json.load(open(sys.argv[1]))
for old,new in plan['mapping'].items():
    user='c-'+new[:12]
    for path,entry in plan['sources'][old]['entries'].items():
        if entry['kind']!='file': continue
        code='import hashlib,pathlib,sys; assert hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest()==sys.argv[2]'
        subprocess.run(['sbx','exec','-u',user,'nora-computer','python3','-c',code,
                        '/workspace/'+new+'/rooms/'+old+'/'+path,entry['sha256']],check=True)
    subprocess.run(['sbx','exec','-u','c-'+old[:12],'nora-computer',
                    'test','!','-r','/workspace/'+new+'/rooms/'+old],check=True)
print('owner readback and isolation verified')
PY
systemctl --user start nora-computer.service
"$SOURCE/deploy/computer/nora-computer" status
```

Keep Nora admission stopped until all these commands pass. Then deploy the
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
a cross-filesystem rename. Keep it and the verified disk/database backups.

If interrupted between rename and journal write, reconcile the quarantine entry
against the **original** plan/journal and database backup, verifying its complete
snapshot and the destination's digests/ownership before any retry. Fresh inventory
and planning refuse every nonempty quarantine, including a completed retirement;
they cannot silently omit a source whose journal update was interrupted. Retain
the original plan and journal for verified repeats. Do not delete or overwrite a
conflicting destination. A partial copy is intentionally refused.

For rollback, use the two exact archives preflighted above, with admission and
computerd still stopped. In the same locked shell (or after re-establishing its
variables and failure flags):

```bash
WORKSPACE_BACKUP=$(cat "$D/workspace-backup-path.txt")
python3 "$SOURCE/deploy/computer/archive_check.py" "$WORKSPACE_BACKUP"
python3 "$SOURCE/deploy/computer/archive_check.py" "$D/browser-profiles.tar.gz"
(cd "$D" && sha256sum -c browser-profiles.tar.gz.sha256)
"$SOURCE/deploy/computer/nora-computer" restore "$WORKSPACE_BACKUP" --force
"$SOURCE/deploy/computer/nora-computer" restore "$D/browser-profiles.tar.gz" --force
```

Restore the saved session metadata/database using the deployment recovery
procedure, then restore the compatible old image/config. The second restore
returns all legacy browser directories to their original scope paths; it does
not choose a profile for the new unit browser. Quarantine and migrated copies
remain held while the old mapping is restored; do not expose them through a new
mapping. Never resume admission with half the mapping bound. Keep all backups
until successful live readback; restoring a chosen legacy browser profile into
the new unit browser is a separate explicit operator choice.
