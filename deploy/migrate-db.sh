#!/usr/bin/env bash
# Replace the server database with a copy of a local one (default: the host paper stack's DB
# from secrets/paper.env). Stops the app services while it runs; Postgres must already be up.
#   deploy/migrate-db.sh root@<ecs-ip> [postgresql://local-url]
set -euo pipefail
HOST=${1:?usage: deploy/migrate-db.sh user@host [local_database_url]}
DIR=/opt/llamaos
cd "$(dirname "$0")/.."
SRC=${2:-$(grep '^DATABASE_URL=' secrets/paper.env | cut -d= -f2-)}
APPS="broker watchdog models api operations"

read -r -p "This DROPS the database on $HOST and replaces it with ${SRC%%:*}://…/${SRC##*/}. Continue? [y/N] " a
[ "$a" = y ] || exit 1

ssh "$HOST" "set -e; cd $DIR; docker compose stop $APPS
  docker compose exec -T postgres sh -c 'dropdb -U \$POSTGRES_USER --force --if-exists \$POSTGRES_DB && createdb -U \$POSTGRES_USER \$POSTGRES_DB'"

pg_dump --no-owner --no-privileges "$SRC" | gzip | ssh "$HOST" "cd $DIR && gunzip |
  docker compose exec -T postgres sh -c 'psql -q -v ON_ERROR_STOP=1 -U \$POSTGRES_USER -d \$POSTGRES_DB'"

ssh "$HOST" "cd $DIR && docker compose start $APPS && docker compose ps"
