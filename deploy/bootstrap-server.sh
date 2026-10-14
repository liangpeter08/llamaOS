#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 22.04/24.04 ECS instance.
#
# Minimal (rsync "build on server" path — no GitHub Actions / registry):
#   ssh root@<ecs-ip> 'bash -s' < deploy/bootstrap-server.sh
#
# Full (GitHub Actions auto-deploy path — clones the repo and logs into the registry):
#   ssh root@<ecs-ip> 'REGISTRY=registry.cn-hongkong.aliyuncs.com REGISTRY_NS=<ns> \
#       REGISTRY_USERNAME=<u> REGISTRY_PASSWORD=<p> bash -s' < deploy/bootstrap-server.sh
#   (repo is public; set REPO_URL=https://<user>:<token>@github.com/... if it ever becomes private)
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

APP_DIR=/opt/llamaos
REPO_URL="${REPO_URL:-https://github.com/liangpeter08/llamaOS.git}"
BRANCH="${BRANCH:-main}"

apt-get update -q
apt-get install -yq ca-certificates curl rsync git ufw
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh

# Cap container logs so a chatty service can't fill the disk.
if [ ! -f /etc/docker/daemon.json ]; then
  echo '{"log-driver":"json-file","log-opts":{"max-size":"20m","max-file":"5"}}' > /etc/docker/daemon.json
fi
systemctl enable docker
systemctl restart docker

# Swap: image builds (torch, Next.js) and local Laya can spike past RAM on a 4 GB instance.
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 4G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
# Aliyun images ship vm.swappiness=0, which leaves swap unused until the OOM killer fires.
sysctl -w vm.swappiness=10 >/dev/null
echo 'vm.swappiness=10' > /etc/sysctl.d/99-llamaos-swap.conf

# Clone the repo (GitHub Actions path). The rsync path reuses the same directory.
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --depth=1 origin "$BRANCH"
  git -C "$APP_DIR" checkout -q "$BRANCH"
  git -C "$APP_DIR" reset --hard "origin/$BRANCH"
else
  mkdir -p "$APP_DIR"
  git clone --depth=1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"
mkdir -p data secrets
# Containers run as uid 10001; models/api compile drafts into strategies/ and cache to data/.
chown -R 10001:10001 "$APP_DIR/strategies" "$APP_DIR/data" 2>/dev/null || true

# Registry (GitHub Actions path): compose interpolation + a persisted login for pulls.
if [ -n "${REGISTRY_NS:-}" ]; then
  umask 077
  printf 'REGISTRY_NAMESPACE=%s/%s\n' "${REGISTRY:-registry.cn-hongkong.aliyuncs.com}" "$REGISTRY_NS" > "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  if [ -n "${REGISTRY_USERNAME:-}" ] && [ -n "${REGISTRY_PASSWORD:-}" ]; then
    echo "$REGISTRY_PASSWORD" | docker login "${REGISTRY:-registry.cn-hongkong.aliyuncs.com}" \
      -u "$REGISTRY_USERNAME" --password-stdin
  fi
fi

# Firewall (the Aliyun security group must also allow these).
ufw allow 22/tcp  >/dev/null 2>&1 || true
ufw allow 80/tcp  >/dev/null 2>&1 || true
ufw allow 443/tcp >/dev/null 2>&1 || true
ufw --force enable >/dev/null 2>&1 || true

docker --version
docker compose version
free -h
echo "Bootstrap done. Next: seed secrets (deploy/push-secrets.sh from your laptop) then deploy."
