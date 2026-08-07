# Nora deployment (bare-metal / systemd)

This document and the systemd unit (`deploy/nora.service`) describe a **bare-metal**
deployment path. It is **not** what the live instance currently runs — Nora runs as a
Docker container today (`Dockerfile` + `docker-compose.yml` at the repo root; see the
"Deployment" section of the root `README.md`). This path is kept for reference and as a
rollback option if the container approach ever needs to be abandoned; the commands below
(`systemctl start/stop nora`) only apply if you are actually running Nora this way.

## Install

    sudo ./deploy/install.sh
    sudo ./deploy/egress.sh
    sudo systemctl start nora

## Rollback

If a deploy misbehaves, roll back to the previous version:

    sudo systemctl stop nora
    cd /home/nora/workspace/nora && git checkout <previous-tag>
    sudo systemctl start nora

The audit database is never touched by a rollback — but schema changes are versioned
Alembic revisions (`nora/infra/sql/migrations_runner.py`), not additive
`CREATE TABLE IF NOT EXISTS` statements, so an older binary reading a newer schema is
not guaranteed to work; see `docs/runbook.md` §3 for the persistence-phase rollback
procedure (`git revert` + `DROP TABLE alembic_version`). Since the 2026-08-07 Postgres
cutover (DEC-51) the store lives in the `db` compose service, not `data/messages.db` —
this bare-metal path predates that and would need its own database decision if revived.

## Egress

`deploy/egress.sh` is the BARE-METAL egress floor: iptables `uid-owner nora` rules for
the pre-container deployment, restricting a host `nora` user to the three loopback
service ports. **It does not constrain the container deployment at all** — container
traffic traverses the `DOCKER-USER`/FORWARD chains under different uids, and the live
outbound set has since grown (Google Routes/Geocoding, Life360 via Cloudflare, Langfuse
OTLP; SearXNG/Firecrawl when `[search]` is enabled). A container-era egress floor is an
OPEN operator decision (audit hygiene, 2026-08-07): the shape would be `DOCKER-USER`
rules or an egress proxy scoped per service — `db` needs no egress at all, `presence`
needs exactly Life360 + the Geocoding API, `nora` needs its named externals. Nothing
enforces that today; do not read this file as evidence that it does.

## Watchdog

`deploy/watchdog.sh` polls `/health` and alerts if the SSE listener has been silent for
more than 300 seconds. Wire it into cron or a systemd timer, e.g. every 60 seconds.
