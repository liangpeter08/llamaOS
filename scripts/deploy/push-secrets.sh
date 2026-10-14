#!/usr/bin/env bash
#
# Copy the gitignored secrets/config from THIS laptop to the ECS over SSH. Run it from
# the repo root on your laptop (never commit these files):
#
#   bash scripts/deploy/push-secrets.sh root@<ECS_IP>
#
# It transfers exactly the files the compose stack reads that are NOT in git:
#   secrets/{postgres,db,broker,models,web,alerts}.env
#   config/risk.yaml  config/market_inputs.yaml
set -euo pipefail

DEST="${1:-}"
if [ -z "$DEST" ]; then echo "usage: bash scripts/deploy/push-secrets.sh root@<ECS_IP>"; exit 2; fi
case "$DEST" in *@*) ;; *) DEST="root@$DEST";; esac

SECRET_FILES=(postgres.env db.env broker.env models.env web.env alerts.env)
CONFIG_FILES=(risk.yaml market_inputs.yaml)

# Pre-flight: all files must exist locally.
missing=0
for f in "${SECRET_FILES[@]}"; do [ -f "secrets/$f" ] || { echo "missing locally: secrets/$f"; missing=1; }; done
for f in "${CONFIG_FILES[@]}"; do [ -f "config/$f" ] || { echo "missing locally: config/$f"; missing=1; }; done
[ "$missing" -eq 0 ] || { echo "Fill in the missing files from their *.example counterparts first."; exit 1; }

echo "==> Ensuring remote directories"
ssh "$DEST" "mkdir -p /opt/llamaos/secrets /opt/llamaos/config"

echo "==> Copying secrets"
scp "${SECRET_FILES[@]/#/secrets/}" "$DEST:/opt/llamaos/secrets/"

echo "==> Copying gitignored config (risk.yaml, market_inputs.yaml)"
scp "${CONFIG_FILES[@]/#/config/}" "$DEST:/opt/llamaos/config/"

echo "==> Locking permissions and fixing ownership"
ssh "$DEST" 'chmod 600 /opt/llamaos/secrets/*.env && chown -R 10001:10001 /opt/llamaos/strategies /opt/llamaos/data 2>/dev/null || true'

echo "Done. Verify on the host with: ls -l /opt/llamaos/secrets"
