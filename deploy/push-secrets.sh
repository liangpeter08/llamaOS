#!/usr/bin/env bash
#
# Copy the gitignored secrets/config from THIS laptop to the ECS over SSH. Use it once to
# seed the git-clone-based host for the GitHub Actions path (the rsync path already sends
# these via deploy/deploy.sh, so you don't need both). Run from the repo root:
#
#   bash deploy/push-secrets.sh root@<ecs-ip>
#
# Sends exactly the non-git files the stack reads:
#   secrets/{postgres,db,broker,models,web,alerts}.env
#   config/risk.yaml  config/market_inputs.yaml
set -euo pipefail

DEST="${1:-}"
if [ -z "$DEST" ]; then echo "usage: bash deploy/push-secrets.sh root@<ecs-ip>"; exit 2; fi
case "$DEST" in *@*) ;; *) DEST="root@$DEST";; esac

SECRETS=(postgres db broker models web alerts)
CONFIGS=(risk.yaml market_inputs.yaml)

secret_paths=()
for f in "${SECRETS[@]}"; do
  [ -f "secrets/$f.env" ] || { echo "missing locally: secrets/$f.env"; missing=1; }
  secret_paths+=("secrets/$f.env")
done
config_paths=()
for f in "${CONFIGS[@]}"; do
  [ -f "config/$f" ] || { echo "missing locally: config/$f"; missing=1; }
  config_paths+=("config/$f")
done
if [ "${missing:-0}" = 1 ]; then
  echo "Fill in the missing files from their *.example counterparts first."; exit 1
fi

ssh "$DEST" "mkdir -p /opt/llamaos/secrets /opt/llamaos/config && chmod 700 /opt/llamaos/secrets"
scp "${secret_paths[@]}" "$DEST:/opt/llamaos/secrets/"
scp "${config_paths[@]}" "$DEST:/opt/llamaos/config/"
ssh "$DEST" 'chmod 600 /opt/llamaos/secrets/*.env
  chown -R 10001:10001 /opt/llamaos/strategies /opt/llamaos/data 2>/dev/null || true'
echo "Secrets and gitignored config pushed. Verify: ssh $DEST 'ls -l /opt/llamaos/secrets'"
