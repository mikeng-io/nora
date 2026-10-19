# nora-deploy: reference host notes

- A host login shell may be zsh, where an unquoted `$VAR` holding several args
  reaches the program as one. Use `bash -s` with a heredoc or `bash -c` to make
  the command interpreter explicit.
- On hosts with filtered DNS, a blocked domain may resolve to `0.0.0.0` and fail
  like a refused connection. `getent ahosts <domain>` against
  `dig +short <domain> @1.1.1.1` tells them apart; the fix is the operator's.
- Every compose `build:` sets `network: host` (operational constraint 1), so
  `docker compose build` needs no flag. `scripts/build-image.sh`, which
  `docs/runbook.md` 3b uses, builds the same unstamped `nora:latest`.
- Container python is `/app/.venv/bin/python`. `uv run` in the container
  installs dev extras over the live venv (2026-09-17).

## Compose rename orphans

When a recreate fails with `container name "/<name>" is already in use`:

```bash
ssh nora-host 'docker inspect <name> --format "{{.State.Status}} {{.State.StartedAt}}"'
```

`created 0001-01-01T00:00:00Z` means a compose rename orphan that never
started: `docker rm <name>`, then rerun SKILL.md step 3's script (it holds the lock and
re-runs the `up` and `verify-deploy.py`). Any other
state: stop and read it. Remove only the container the error names; other
`Created` containers on the host are not yours.

## Heredoc stdin

`ssh nora-host bash -s <<'EOF'` feeds the script on stdin, and the docker client
drains stdin whenever interactive is on, which `docker compose exec` has by
default (`-T` only drops the TTY), even when the container never reads it. The
exec swallows the REST OF THE SCRIPT and ssh exits 0 (2026-09-25: validation
printed `ok`, nothing after it ran, the live config was untouched). Put
`</dev/null` on every `docker compose exec`, `docker exec -i` and `docker
compose run` in a heredoc (`cp`, `logs`, `up -d` and `inspect` measured
harmless), and read the log for `VAL_RC=` / `UP_RC=`, never the ssh rc alone.

## Other step 3 failures

- `FETCH_FAILED` with `Permission denied (publickey)`: rerun with `ssh -A`.
- `FF_FAILED` on untracked files in the way: move them aside and tell the
  operator; never `git clean` (they may be someone's).
- `UP_RC` nonzero with `container name ... is already in use`: step 7.

## LIVE_CONFIG_REFUSED_BY_NEW_IMAGE

Step 3 validates the live `config.toml` against the freshly built image before
recreating anything. On 2026-10-01 DEC-247 (commit f4f2439c) removed
`max_memory_calls` and `max_tool_calls` from `[dreamer.budgets.cognition]`.
The live file still had them, and the next deploy, which was someone else's,
crash-looped nora, presence and analytics for about 3 minutes. Apply the key
change through step 5's validated copy, then rerun step 3. A merge that removes
or renames a config key should say so in its PR's Deploy note and in a
`change(config): …` commit, so the release notes carry it.

## Other images

Other images: ADD the service to the build and up lines (voice:
`nora voice-sidecar voice-wake` together). `verify-deploy.py` sees only
`nora:latest`, so read each other image back per container, as
`docs/runbook.md` 3b shows: `docker inspect --format '{{.Image}}' <svc>` must
equal `docker image inspect <svc-image> --format '{{.Id}}'`.

## A revert PR keeps its migration

`MIGRATION_SINCE` sends a rollback forward, and the revert PR that follows must KEEP
the migration file (`0088_orphan_delete` is the live case): the database is already at
that revision, and every container fails at start on a revision its image does not
know. Revert the code that uses the table, never the file that creates it.

## Lock

Every locked script runs as `ssh nora-host "$LOCK bash -s"` with
`LOCK='flock -o -E 75 -w 900 /tmp/nora-deploy.lock'`. `-o` closes the lock's
descriptor before the script runs, so nothing it starts can inherit the lock:
on 2026-10-07 an `exec 9>` lock was inherited by a long-lived `sbx` process and
a containerd shim, and every later deploy timed out on a lock no deploy held.
Exit 75 is a held lock. If no `docker build` or `docker compose up` is running
and `fuser -v /tmp/nora-deploy.lock` names a long-lived process, the lock
leaked from a script without `-o`: tell that process's owner; never kill it.
