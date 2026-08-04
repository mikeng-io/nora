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

The audit database (`data/messages.db`) is never touched by a rollback — schema
migrations are additive and idempotent (see `nora/store/db.py`), so an older binary
reading a newer `messages.db` only ignores columns it doesn't recognize.

## Watchdog

`deploy/watchdog.sh` polls `/health` and alerts if the SSE listener has been silent for
more than 300 seconds. Wire it into cron or a systemd timer, e.g. every 60 seconds.
