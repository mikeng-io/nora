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
"$SOURCE/deploy/computer/nora-computer" backup
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
against the plan and database backup before any retry; do not delete or overwrite
a conflicting destination. A partial copy is intentionally refused. Restore the
workspace using `nora-computer restore <verified archive> --force` and the saved
session metadata/database using the deployment recovery procedure, then restore
the compatible old image/config. Never resume admission with half the mapping
bound. Keep all backups until successful live readback; restoring a chosen legacy
browser profile is a separate explicit operator choice.
