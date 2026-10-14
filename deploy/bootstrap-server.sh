#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 22.04/24.04 ECS instance. Run from your machine:
#   ssh root@<ecs-ip> 'bash -s' < deploy/bootstrap-server.sh
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update -q
apt-get install -yq ca-certificates curl rsync
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

mkdir -p /opt/llamaos
docker --version
docker compose version
free -h
