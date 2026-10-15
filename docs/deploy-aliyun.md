# Deploying to Alibaba Cloud ECS (hesynth.xyz)

The whole stack (Postgres, broker, models, api, watchdog, operations, dashboard, Caddy) runs with
Docker Compose on one ECS instance. Only Caddy publishes ports (80 and 443); Postgres stays on the
internal Docker network.

## 1. Create the instance (Aliyun console)

- **Region: outside mainland China.** US (Virginia) is the best fit: it sits next to Alpaca's
  servers, needs no ICP filing for hesynth.xyz, and can reach the Anthropic API, Docker Hub, PyPI and
  npm. Hong Kong works for ICP but the Anthropic API is not available there.
- **Instance:** 2 vCPU / 4 GB is the minimum (the bootstrap adds 4 GB of swap). Choose 8 GB to run
  local Laya comfortably. The economy `e` family or a Simple Application Server is the cheapest.
- **Image:** Ubuntu 24.04 64-bit. **Disk:** 40 GB ESSD Entry. **Public IP:** assign one (or attach an EIP).
- **Login:** an SSH key pair (add `~/.ssh/id_ed25519.pub`).
- **Security group, inbound:** TCP 22 (restricted to your own IP), TCP 80, TCP 443. Nothing else.

## 2. Point the domain

At the registrar or in Alibaba Cloud DNS, add:

| Type | Host | Value |
|---|---|---|
| A | `@` | ECS public IP |
| A | `www` | ECS public IP |

Check with `dig +short hesynth.xyz`. Caddy requests the Let's Encrypt certificate on first start, so
DNS must resolve before step 4.

## 3. Bootstrap the server (once)

```bash
ssh root@<ip> 'bash -s' < deploy/bootstrap-server.sh
```

This installs Docker and Compose, caps container logs and adds swap.

## 4. Deploy (first time and every update)

```bash
deploy/deploy.sh root@<ip>
```

This rsyncs code, `config/` and the six `secrets/*.env` service files to `/opt/llamaos`, adds new
strategy files without overwriting any on the server, and runs `docker compose up -d --build`. The
first build takes about 10 minutes (torch and Next.js).

### Strategy files only

```bash
deploy/sync-strategies.sh -n          # dry run: what is new, what conflicts
deploy/sync-strategies.sh             # send new strategies/*.yaml to root@hesynth.xyz ($LLAMAOS_HOST overrides)
deploy/sync-strategies.sh --watch orb5_*.yaml   # only these files, and put their tickers on the watchlist
```

No rebuild or restart. It sends only files the server doesn't have, refuses a file whose content
differs from the server's copy (save it as a new version instead), then registers the new files as
drafts and validates them inside the running `api` container, against the live risk controls and
the deployed code. A file that uses a builtin newer than the deployed code fails there until you
run `deploy/deploy.sh`.

## 5. Data

Pick one:

- **Copy your local paper DB** (owner account, 2FA, accounts, history):
  `deploy/migrate-db.sh root@<ip>` (it reads the source URL from `secrets/paper.env`).
  `secrets/broker.env` must carry the same `BROKER_VAULT_KEY`, or stored Alpaca accounts can't be decrypted.
- **Start fresh:** `ssh root@<ip> 'cd /opt/llamaos && docker compose exec api python -m services.api.create_owner --username admin'`

Then open https://hesynth.xyz and log in.

## Operations

```bash
ssh root@<ip> 'cd /opt/llamaos && docker compose ps'
ssh root@<ip> 'cd /opt/llamaos && docker compose logs -f --tail=100 broker'
```

`operations` writes a daily `pg_dump` to `/opt/llamaos/data/backups`, which is on the same disk.
Set `BACKUP_TARGET` in `secrets/alerts.env` (or enable ECS automatic snapshots) to keep a copy off the host.
