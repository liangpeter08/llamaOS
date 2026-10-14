#!/usr/bin/env bash
#
# Roll out a prebuilt image tag on the ECS. Invoked by GitHub Actions over SSH, or by hand:
#
#   bash deploy/server-deploy.sh <image-tag>     # 12-char git SHA, or "latest"
#
# Assumes bootstrap-server.sh has run: Docker installed, /opt/llamaos is a git clone, and
# the gitignored secrets/*.env + config/{risk,market_inputs}.yaml are present (seeded once
# with deploy/push-secrets.sh).
set -euo pipefail

TAG="${1:-}"
if [ -z "$TAG" ]; then echo "usage: server-deploy.sh <image-tag> (12-char git SHA or 'latest')"; exit 2; fi

APP_DIR=/opt/llamaos
cd "$APP_DIR"
export COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
log() { echo -e "\n\033[1;36m==> $*\033[0m"; }

# Credentials are injected by the GitHub Action as env vars; a persisted .docker/config.json
# also works. Refresh the ACR token because personal-instance tokens expire.
if [ -n "${REGISTRY:-}" ] && [ -n "${REGISTRY_USERNAME:-}" ] && [ -n "${REGISTRY_PASSWORD:-}" ]; then
  echo "$REGISTRY_PASSWORD" | docker login "$REGISTRY" -u "$REGISTRY_USERNAME" --password-stdin >/dev/null
fi

log "Syncing code to $TAG"
git fetch --depth=1 origin main
if [[ "$TAG" =~ ^[0-9a-f]{7,40}$ ]]; then
  git checkout -q "$TAG" 2>/dev/null || git checkout -q main
else
  git checkout -q main && git reset --hard origin/main
fi
mkdir -p data
chown -R 10001:10001 strategies data 2>/dev/null || true

log "Pulling images"
export IMAGE_TAG="$TAG"
$COMPOSE pull

log "Starting stack"
$COMPOSE up -d --remove-orphans
docker image prune -f >/dev/null 2>&1 || true

log "Status"
$COMPOSE ps

log "Health (api via the Caddy -> dashboard proxy)"
sleep 8
for i in $(seq 1 10); do
  if curl -fsS -o /dev/null -m 5 -k https://127.0.0.1/api/healthz 2>/dev/null \
     || curl -fsS -o /dev/null -m 5 http://127.0.0.1/api/healthz 2>/dev/null; then
    echo "API healthy."; exit 0
  fi
  echo "waiting for api... ($i)"; sleep 6
done
echo "WARNING: api did not answer yet — inspect: $COMPOSE logs api dashboard caddy"
exit 0
