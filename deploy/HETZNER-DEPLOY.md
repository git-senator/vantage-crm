# Single-VPS production deploy (Hetzner / Vultr / DigitalOcean)

The simple, cheap, near-hands-off path for ~30 users: one Ubuntu VPS running the
Docker Compose stack behind **Caddy** (automatic HTTPS). No Kubernetes. For the
horizontally-scaled Helm path, see `helm/`.

Files that make this work:
- `compose/docker-compose.prod.yml` — prod overlay (adds Caddy, hides web:3000)
- `compose/Caddyfile` — auto-HTTPS for `${DOMAIN}` and `files.${DOMAIN}`
- `compose/.env.prod.example` — the production env template
- `backup/backup.sh` — logical Postgres backup (schedule via cron)

## 0. Prerequisites
- An Ubuntu 24.04 VPS (Hetzner CPX31: 4 vCPU / 8 GB / 160 GB is comfortable).
- A domain with two A-records at the VPS IP: `crm.example.com` and
  `files.crm.example.com`.
- SSH access as root (or a sudo user).

## 1. Base server
```bash
# as root
apt update && apt -y upgrade
# Docker Engine + compose plugin (official convenience script)
curl -fsSL https://get.docker.com | sh
# Firewall: only SSH + HTTP(S). (Docker publishes 80/443 via Caddy only.)
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable
```

## 2. Get the code
```bash
git clone <repo-url> /opt/vantage && cd /opt/vantage
# (or rsync/scp the working tree)
```

## 3. Configure
```bash
cp deploy/compose/.env.prod.example .env
# Generate real secrets ON THE SERVER:
sed -i "s|REPLACE_WITH_openssl_rand_hex_32|$(openssl rand -hex 32)|" .env
# then edit .env: set DOMAIN / WEB_ORIGIN / COOKIE_DOMAIN / CORS_ORIGINS to the
# real domain, and replace each REPLACE_* password with `openssl rand -hex 24`.
nano .env
```

## 4. Migrate + launch
```bash
COMPOSE="docker compose -f docker-compose.yml -f deploy/compose/docker-compose.prod.yml --env-file .env"
$COMPOSE build
$COMPOSE run --rm migrate            # apply DB migrations (privileged role)
$COMPOSE up -d                        # postgres, redis, minio, api, worker, web, caddy
```
Caddy fetches TLS certs automatically once DNS resolves to this host.

## 5. Create the first workspace + owner
```bash
$COMPOSE exec api python -m app.cli.bootstrap \
  --name "Your Brokerage" --email you@example.com
# optional demo data:
# $COMPOSE exec api python -m app.cli.seed_demo --scale 0.1
```

## 6. Backups (daily)
```bash
# crontab -e  — logical dump at 03:30, keep 14 days
30 3 * * * cd /opt/vantage && ./deploy/backup/backup.sh >> /var/log/vantage-backup.log 2>&1
```

## 7. Verify
- `https://crm.example.com` → login works over HTTPS.
- Upload a file on a record → it stores and downloads (exercises `files.` host).
- `$COMPOSE exec api curl -s localhost:8000/health/ready` → ok.

## Updating later
```bash
cd /opt/vantage && git pull
$COMPOSE build && $COMPOSE run --rm migrate && $COMPOSE up -d
```

## Notes
- **Only 80/443 are public.** Postgres/Redis/MinIO bind to loopback; the API is
  internal to the compose network; Caddy is the sole entrypoint.
- **Presigned file URLs** are signed for `https://files.${DOMAIN}`; Caddy proxies
  that host to MinIO preserving the Host header so SigV4 validates. Verify an
  upload end-to-end after the first deploy.
- Swap MinIO for real S3 later by clearing `S3_ENDPOINT_URL` and setting real S3
  creds — no app change (see the base compose comment).
