# Deploying rwcheck on EC2

This guide walks through hosting `https://rwcheck.khanlab.bio` on an AWS EC2 instance using Docker Compose and Caddy (automatic TLS via Let's Encrypt).



## Prerequisites

- An EC2 instance running **Amazon Linux 2023** or **Ubuntu 22.04+**
- Instance type: **t3.small** or larger (2 GB RAM recommended)
- A domain you control — `rwcheck.khanlab.bio` — with DNS managed in Route 53 (or any DNS provider)
- Ports **80** and **443** open in the EC2 security group


## 1. Open ports in the EC2 security group

In the AWS Console → EC2 → Security Groups, add two inbound rules to the instance's security group:

| Type | Protocol | Port | Source |
|---|---|---|---|
| HTTP | TCP | 80 | 0.0.0.0/0 |
| HTTPS | TCP | 443 | 0.0.0.0/0 |

SSH (port 22) should already be open.


## 2. Point DNS to the EC2 instance

Create an **A record** in your DNS provider:

| Name | Type | Value |
|---|---|---|
| `rwcheck.khanlab.bio` | A | `<EC2 public IP>` |

DNS propagation typically takes 1–5 minutes with Route 53, up to an hour with other providers. Verify with:

```bash
dig +short rwcheck.khanlab.bio
```

> **Note:** Let's Encrypt will fail if DNS has not propagated before you run `compose-up`. Verify DNS resolves first.


## 3. Install Docker on the EC2 instance

SSH into the instance, then install Docker:

**Amazon Linux 2023**

```bash
sudo dnf update -y
sudo dnf install -y docker
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
newgrp docker
```

**Ubuntu 22.04+**

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker $USER
newgrp docker
```

Verify:

```bash
docker --version
docker compose version
```


## 4. Clone the repository

```bash
git clone https://github.com/khan-lab/rwcheck.git
cd rwcheck
```

---

## 5. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` — at minimum set your email address for Let's Encrypt notifications:

```bash
# .env
ACME_EMAIL=admin@khanlab.bio
RW_CSV_URL=https://gitlab.com/crossref/retraction-watch-data/-/raw/main/retraction_watch.csv
RATE_LIMIT=60/minute
UPDATE_INTERVAL_HOURS=24
PUBLIC_HOST=https://rwcheck.khanlab.bio
```

`.env` is gitignored and must **never be committed**.



## 6. Build and start

```bash
# Build the rwcheck API image
make compose-build

# Start Caddy + rwcheck in the background
make compose-up
```

On first start:

1. Caddy obtains a TLS certificate for `rwcheck.khanlab.bio` via Let's Encrypt (~10–30 seconds).
2. The rwcheck container downloads the Retraction Watch CSV and builds the SQLite database (~60–120 seconds depending on network).

Watch the logs until both services are ready:

```bash
make compose-logs
# Ctrl-C to stop following
```

Look for:

```
caddy   | ... certificate obtained successfully
rwcheck | INFO:     Application startup complete.
```



## 7. Verify the deployment

```bash
# Health check
curl https://rwcheck.khanlab.bio/health
# → {"status":"ok","db_exists":true}

# Dataset metadata
curl https://rwcheck.khanlab.bio/meta

# DOI lookup
curl "https://rwcheck.khanlab.bio/check/doi/10.1038%2Fnature12345"
```

The browser UI is at `https://rwcheck.khanlab.bio` and the OpenAPI (Swagger) docs are at `https://rwcheck.khanlab.bio/docs`.



## 8. Keeping the service running across reboots

Docker Compose services are configured with `restart: unless-stopped`, so they restart automatically after a reboot. To also start Docker itself on boot:

```bash
sudo systemctl enable docker
```

Verify after a reboot:

```bash
sudo reboot
# reconnect, then:
docker compose -f ~/rwcheck/docker-compose.yml ps
```



## Maintenance

### View logs

```bash
make compose-logs

# Single service
docker compose logs -f rwcheck
docker compose logs -f caddy
```

### Pull the latest code and redeploy

```bash
git pull
make compose-build
docker compose up -d --no-deps rwcheck   # rolling restart; keeps Caddy running
```

### Force a database rebuild

```bash
docker compose exec rwcheck \
  python -c "from scripts.build_db import build_db; build_db(csv_path=None, url=None, force=True)"
```

### Stop the service

```bash
make compose-down          # stops containers, preserves volumes
docker compose down -v     # stops containers AND deletes data volumes (destructive)
```

### TLS certificate renewal

Caddy renews Let's Encrypt certificates automatically before expiry. Certificates are stored in the `caddy_data` Docker volume and persist across restarts. No manual intervention is required.



## Troubleshooting

### Certificate not issued

- Confirm DNS resolves to the EC2 IP: `dig +short rwcheck.khanlab.bio`
- Confirm ports 80 and 443 are open: `curl -I http://rwcheck.khanlab.bio`
- Check Caddy logs: `docker compose logs caddy`
- Confirm `ACME_EMAIL` is set in `.env`

To use Let's Encrypt staging CA during testing (avoids rate limits), edit `docker/Caddyfile`:

```
rwcheck.khanlab.bio {
    tls {env.ACME_EMAIL} {
        ca https://acme-staging-v02.api.letsencrypt.org/directory
    }
    reverse_proxy rwcheck:8000
    ...
}
```

Then reload: `docker compose restart caddy`

### Database not built

```bash
docker compose logs rwcheck | grep -i "error\|build\|csv"
```

If the CSV download failed (network issue), restart the container:

```bash
docker compose restart rwcheck
```

### Port already in use

If something else is already listening on port 80 or 443:

```bash
sudo ss -tlnp | grep -E ':80|:443'
```

Stop the conflicting service before running `compose-up`.

### Out of disk space

The Retraction Watch CSV is ~60 MB; the SQLite database is ~150 MB. Check disk usage:

```bash
df -h
docker system df
```

Clean up unused Docker images and build cache:

```bash
docker system prune -f
```
