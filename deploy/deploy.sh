#!/usr/bin/env bash
# Push the working tree, config and per-service secrets to the ECS host, then build and
# (re)start the compose stack. Safe to re-run for every update.
#   deploy/deploy.sh root@<ecs-ip>
set -euo pipefail
HOST=${1:?usage: deploy/deploy.sh user@host}
DIR=/opt/llamaos
cd "$(dirname "$0")/.."

test -f config/risk.yaml || { echo "config/risk.yaml is missing (cp config/risk.example.yaml)"; exit 1; }
test -f config/market_inputs.yaml || { echo "config/market_inputs.yaml is missing"; exit 1; }
SECRETS=(postgres db broker models web alerts)
for f in "${SECRETS[@]}"; do
  test -f "secrets/$f.env" || { echo "secrets/$f.env is missing"; exit 1; }
done

# Code and config. strategies/ and data/ are excluded, so --delete never touches what the
# server writes there (compiled drafts, backups, model cache).
rsync -az --delete --exclude-from=deploy/rsync-exclude ./ "$HOST:$DIR/"

# Strategy files are versioned and immutable once written: add new ones, never overwrite.
rsync -az --ignore-existing strategies/ "$HOST:$DIR/strategies/"

# Only the service env files; owner/2FA files and the host paper.env stay on this machine.
ssh "$HOST" "mkdir -p $DIR/secrets && chmod 700 $DIR/secrets"
# (macOS ships openrsync, which has no --chmod; set permissions remotely instead.)
rsync -az $(printf 'secrets/%s.env ' "${SECRETS[@]}") "$HOST:$DIR/secrets/"
ssh "$HOST" "chmod 600 $DIR/secrets/*.env"

ssh "$HOST" "set -e; cd $DIR
  mkdir -p data
  chown -R 10001:10001 strategies data      # containers run as UID 10001 and write here
  docker compose up -d --build --remove-orphans
  docker image prune -f >/dev/null
  docker compose ps"
