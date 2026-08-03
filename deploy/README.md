# Nora deployment

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
