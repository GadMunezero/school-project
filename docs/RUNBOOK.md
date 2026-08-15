# Beta runbook: one server, start to finish

A deployment for a closed beta — one VPS, a domain, TLS, and a backup you have actually restored.
For the architecture behind it, read [DEPLOYMENT.md](DEPLOYMENT.md); this is the sequence.

Sized for tens of users, not thousands. Everything here runs on a single 4 GB machine and splits
apart later without rewriting anything: the API is stateless, and Postgres and object storage are
the only pieces that hold anything irreplaceable.

---

## 1. The machine

Ubuntu 24.04, 2 vCPU, 4 GB RAM, 40 GB disk. Point an `A` record at it before starting — Let's
Encrypt validates over HTTP, so DNS has to resolve first.

```bash
ssh root@your-server

adduser --disabled-password --gecos "" tradeloom
usermod -aG docker tradeloom          # after Docker is installed
apt update && apt install -y docker.io docker-compose-v2 postgresql-client-16

ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable
```

Only 22, 80 and 443 are open. Postgres and Redis listen inside the Docker network and must never
be published to the host.

---

## 2. Configuration

```bash
su - tradeloom
git clone https://github.com/GadMunezero/school-project.git tradeloom
cd tradeloom
cp .env.example .env
```

Edit `.env`. The values that matter before first boot:

| Setting | Value |
| --- | --- |
| `TRADELOOM_ENV` | `production` |
| `SECRET_KEY` | `python3 -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `COOKIE_SECURE` | `true` |
| `DEBUG` | `false` |
| `DATABASE_URL` | your Postgres URL |
| `BACKEND_URL` / `FRONTEND_URL` | `https://your-domain` |
| `CORS_ORIGINS` | `https://your-domain` — exact string match, no trailing slash |
| `SIGNUP_MODE` | `invite` |
| `SMTP_*` | a real provider; verification and password reset are useless without one |
| `S3_*` | object storage credentials |

`Settings.validate_for_production()` refuses to boot with development defaults, an unwritten
policy document, or missing S3 credentials. It is meant to be hit — a misconfigured deployment
should fail at start rather than quietly serve insecure cookies.

**Write your legal documents now**, not later. `content/legal/terms.md` and `privacy.md` ship as
placeholders that say so, and production will not start while the `UNWRITTEN-PLACEHOLDER` marker
is still on the first line. See the README section on it.

---

## 3. TLS

Get the certificate before starting the stack, using standalone mode while port 80 is free:

```bash
sudo apt install -y certbot
sudo certbot certonly --standalone -d your-domain -m you@example.com --agree-tos

sudo mkdir -p /srv/tradeloom-certs
sudo cp /etc/letsencrypt/live/your-domain/{fullchain.pem,privkey.pem} /srv/tradeloom-certs/
sudo chown -R tradeloom:tradeloom /srv/tradeloom-certs
```

Add a TLS server block to `docker/nginx.conf` alongside the existing one — keep the `location`
blocks exactly as they are, and change only the listener:

```nginx
server {
    listen 80;
    server_name your-domain;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    http2 on;
    server_name your-domain;

    ssl_certificate     /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;

    # …the existing location blocks, unchanged…
}
```

Mount the certificates into the proxy in `docker-compose.yml`:

```yaml
  proxy:
    ports: ["80:80", "443:443"]
    volumes:
      - ./docker/nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - /srv/tradeloom-certs:/etc/nginx/certs:ro
```

Renewal, monthly, with a reload rather than a restart:

```cron
0 4 1 * * certbot renew --quiet --pre-hook "docker compose -f /home/tradeloom/tradeloom/docker-compose.yml stop proxy" --post-hook "cp /etc/letsencrypt/live/your-domain/*.pem /srv/tradeloom-certs/ && docker compose -f /home/tradeloom/tradeloom/docker-compose.yml start proxy"
```

---

## 4. First boot

```bash
docker compose up -d --build
docker compose logs -f api          # watch for the production validation
```

The API entrypoint waits for Postgres, applies migrations and ensures the storage bucket. It does
**not** seed demo data — a production database starts empty, deliberately.

Make yourself staff and mint the first invite:

```bash
docker compose exec api python -m tradeloom.cli create-admin --email you@example.com
docker compose exec api python -m tradeloom.cli invite --note "First tester"
```

Then check the two probes answer:

```bash
curl -fsS https://your-domain/api/v1/health/live
curl -fsS https://your-domain/api/v1/health/ready
```

---

## 5. Backups, and proving they work

```bash
mkdir -p /srv/backups
crontab -e
```

```cron
0 3 * * *  cd /home/tradeloom/tradeloom && BACKUP_DIR=/srv/backups DATABASE_URL="postgresql://…" scripts/backup.sh >> /var/log/tradeloom-backup.log 2>&1
0 4 * * 0  cd /home/tradeloom/tradeloom && RESTORE_ADMIN_URL="postgresql://…/postgres" scripts/restore-check.sh "$(ls -t /srv/backups/*.dump | head -1)" >> /var/log/tradeloom-restore.log 2>&1
```

`scripts/backup.sh` writes a compressed custom-format dump, refuses to keep an empty one, and
prunes anything older than `RETAIN_DAYS` (30 by default).

`scripts/restore-check.sh` is the half people skip. It restores the dump into a scratch database,
runs `alembic check` against it, counts the core tables, and drops the scratch database again. It
never touches the database the dump came from.

An untested backup is a hypothesis. This drill is what turned up a foreign key the models declared
and the initial migration never created — invisible on SQLite, missing in production for the
entire life of the schema.

**Copy dumps off the machine.** A backup that only exists on the server it is backing up is not a
backup. `rclone`, `aws s3 cp`, or a syncthing share — anything, as long as it is elsewhere and
encrypted. A dump contains every user's complete trading history.

---

## 6. Watching it

`docs/DEPLOYMENT.md` lists the signals worth alerting on. The minimum for a beta:

- an uptime check on `/api/v1/health/ready` from outside the machine
- `SENTRY_DSN` set, so an unhandled error reaches you rather than waiting to be reported
- the **Feedback** tab in Administration, read daily — in a beta this is your bug tracker
- `docker compose logs -f api | grep -v health` when something feels wrong

---

## 7. Updating

```bash
cd /home/tradeloom/tradeloom
BACKUP_DIR=/srv/backups DATABASE_URL="postgresql://…" scripts/backup.sh   # first, always
git pull
docker compose up -d --build
docker compose logs -f api
```

Migrations run on start. If one fails the API will not serve, which is the intended outcome —
restore the dump you just took rather than trying to patch a half-migrated database.

---

## What this does not cover

Stated plainly, because a runbook that implies more than it delivers is worse than a short one:

- **No high availability.** One machine. It goes down, the service goes down.
- **Self-hosted Postgres has no PITR here.** Nightly dumps mean up to a day of loss. A managed
  database with continuous archiving fixes that and is worth it before real money is involved.
- **No log aggregation.** Logs are structured JSON on stdout; shipping them somewhere queryable
  is a separate decision.
- **No staging environment.** For a beta, the beta is staging. Once people rely on it, that stops
  being true.
