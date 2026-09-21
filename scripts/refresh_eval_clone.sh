#!/usr/bin/env bash
# Refresh the eval SOURCE — a clone of the deployment's database that the
# evaluation suite reads moments out of and never writes.
#
# Run ON the deploy host, beside the compose file. Production (`nora`) is
# dumped, never opened by the suite: every case composes against a sandbox
# database cloned BESIDE this one (`nora.subject.sandbox.sandbox_database`),
# and `python -m evals` refuses a source whose name is not a clone's.
#
# `pg_dump | psql` rather than `CREATE DATABASE ... TEMPLATE nora`: Postgres
# refuses to template a database with any session connected, and production
# always has one.
#
# Sandboxes and the blank template are dropped and rebuilt too, so a schema
# change in the image reaches them: `nora_eval_blank` is migrated to head by
# the suite on its next run, and a leftover `nora_eval_sb_*` is a case that
# never finished.
set -euo pipefail

CONTAINER="${NORA_DB_CONTAINER:-nora-db-1}"
USER_="${POSTGRES_USER:-nora}"
SOURCE="${NORA_PROD_DB:-nora}"
CLONE="${NORA_EVAL_DB:-nora_eval}"

psql_() { docker exec -i "$CONTAINER" psql -U "$USER_" -v ON_ERROR_STOP=1 -d postgres -Atc "$1"; }

echo "dropping sandboxes and the blank template beside ${CLONE}"
for db in $(psql_ "select datname from pg_database where datname like '${CLONE}_sb_%' or datname = '${CLONE}_blank'"); do
  psql_ "select pg_terminate_backend(pid) from pg_stat_activity where datname = '${db}' and pid <> pg_backend_pid()" >/dev/null
  psql_ "drop database \"${db}\""
done

echo "recreating ${CLONE} from ${SOURCE}"
psql_ "select pg_terminate_backend(pid) from pg_stat_activity where datname = '${CLONE}' and pid <> pg_backend_pid()" >/dev/null
psql_ "drop database if exists \"${CLONE}\""
psql_ "create database \"${CLONE}\""
docker exec "$CONTAINER" pg_dump -U "$USER_" --no-owner "$SOURCE" \
  | docker exec -i "$CONTAINER" psql -U "$USER_" -q -v ON_ERROR_STOP=1 -d "$CLONE" >/dev/null

# Read-back: the clone's message count and newest row, so the run log says
# what moment the source was taken at.
docker exec "$CONTAINER" psql -U "$USER_" -d "$CLONE" -Atc \
  "select 'messages='||count(*)||' newest_ts='||max(ts)||' alembic='||(select version_num from alembic_version) from messages"
