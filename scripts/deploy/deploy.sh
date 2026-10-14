#!/usr/bin/env bash
#
# Deploy a given image tag to this host. Invoked by GitHub Actions over SSH, or by hand:
#
#   bash scripts/deploy/deploy.sh <image-tag>      # <image-tag> = 12-char git sha or "latest"
#
# Pre-reqs: bootstrap.sh has run (Docker, repo clone, /opt/llamaos/.env, secrets present).
set -euo pipefail

TAG="${1:-}"
if [ -z "$TAG" ]; then
  echo "usage: deploy.sh <image-tag>   (a 12-char git sha, or 'latest')"
  exit 2
fi

APP_DIR=/opt/llamaos
cd "$APP_DIR"
export COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

log() { echo -e "\n\033[1;36m==> $*\033[0m"; }

# Refresh the registry token (ACR personal tokens expire) so private pulls keep working.
if [ -f /etc/llamaos/registry.env ]; then
  # shellcheck disable=SC1091
  set -a; . /etc/llamaos/registry.env; set +a
  if [ -n "${REGISTRY:-}" ] && [ -n "${REGISTRY_USERNAME:-}" ] && [ -n "${REGISTRY_PASSWORD:-}" ]; then
    echo "$REGISTRY_PASSWORD" | docker login "$REGISTRY" -u "$REGISTRY_USERNAME" --password-stdin >/dev/null
  fi
fi

log "Syncing code"
git fetch --depth=1 origin main
if [[ "$TAG" =~ ^[0-9a-f]{7,40}$ ]]; then
  git checkout -q "$TAG" 2>/dev/null || git checkout -q main
else
  git checkout -q main
  git reset --hard origin/main
fi

# git checkout hands root-owned files to the uid that mounts strategies/ and data/.
mkdir -p data
chown -R 10001:10001 strategies data || true

log "Pulling images for tag '$TAG'"
export IMAGE_TAG="$TAG"
$COMPOSE pull

log "Starting stack"
$COMPOSE up -d --remove-orphans

log "Removing dangling images"
docker image prune -f >/dev/null 2>&1 || true

log "Service status"
$COMPOSE ps

log "Quick health check (api via the dashboard proxy)"
sleep 8
for i in $(seq 1 10); do
  if curl -fsS -o /dev/null -m 5 http://127.0.0.1/api/healthz 2>/dev/null \
     || curl -fsS -o /dev/null -m 5 -k https://127.0.0.1/api/healthz 2>/dev/null; then
    echo "API reachable through the proxy."; exit 0
  fi
  echo "waiting for api... ($i)"; sleep 6
done
echo "WARNING: api did not answer yet; check 'docker compose logs api dashboard caddy'."
exit 0
