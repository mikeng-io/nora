# nora-deploy: host notes (norty)

- The login shell is zsh: an unquoted `$VAR` holding several args reaches the
  program as one (2026-09-24). Use `bash -s` with a heredoc or `bash -c`.
- A blocked domain resolves to `0.0.0.0` through filtered DNS and fails like a
  refused connection (pypi.org, 2026-09-17). `getent ahosts <domain>` against
  `dig +short <domain> @1.1.1.1` tells them apart; the fix is the operator's.
- Every compose `build:` sets `network: host` (operational constraint 1), so
  `docker compose build` needs no flag. `scripts/build-image.sh`, which
  `docs/runbook.md` 3b uses, builds the same unstamped `nora:latest`.
- Container python is `/app/.venv/bin/python`. `uv run` in the container
  installs dev extras over the live venv (2026-09-17).

## Compose rename orphans

When a recreate fails with `container name "/<name>" is already in use`:

```bash
ssh norty 'docker inspect <name> --format "{{.State.Status}} {{.State.StartedAt}}"'
```

`created 0001-01-01T00:00:00Z` means a compose rename orphan that never
started: `docker rm <name>`, then rerun SKILL.md step 3's script (it holds the lock and
re-runs the `up` and `verify-deploy.py`). Any other
state: stop and read it. Remove only the container the error names; other
`Created` containers on the host are not yours.
