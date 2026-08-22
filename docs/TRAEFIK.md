# Traefik deployment

Beyond Glimpse is designed to work cleanly behind Traefik.

The request path is:

`client -> Traefik (HTTPS/TLS) -> Beyond Glimpse Nginx (HTTP port 80)`

Nginx is not a second reverse proxy in this layout; it is the lightweight static web server inside the Beyond Glimpse container. Traefik handles public routing and TLS.

## Quick deployment

1. Copy the example environment file:

   ```bash
   cp .env.traefik.example .env
   ```

2. Set at least:

   - `BEYOND_GLIMPSE_HOST`
   - `TRAEFIK_NETWORK`
   - `TRAEFIK_CERTRESOLVER`
   - `JELLYFIN_URL`
   - `JELLYFIN_TOKEN`
   - `PRIMARY_SERVER=jellyfin`

3. Make sure the external Docker network already exists and is shared with Traefik.

4. Start Beyond Glimpse:

   ```bash
   docker compose -f docker-compose.traefik.yml up -d --build
   ```

The Traefik compose does **not** publish port 9090 or port 80 on the host. Traefik reaches the container on the shared Docker network.

## Health checks

Beyond Glimpse exposes:

```text
GET /healthz
```

which returns a small non-sensitive JSON response when the internal Nginx server is alive.

The Docker image has a native `HEALTHCHECK`, and the Traefik service definition also checks `/healthz`. This gives two useful signals:

- Docker reports the container as healthy/unhealthy.
- Traefik does not route to an unhealthy Beyond Glimpse backend.

During the first large-library import, Nginx may not start until the initial sync has completed. The container can therefore appear unready during that first import and will automatically become healthy when the web server starts.

## Nginx security headers and Traefik

Beyond Glimpse already sends its browser security headers from Nginx, including CSP, framing protection and `nosniff`.

It is fine to use ordinary Traefik routing/TLS middleware in front of it. If your Traefik installation applies a **global security-header middleware**, pay particular attention to `Content-Security-Policy`:

- duplicate harmless headers are usually fine;
- multiple CSP headers are all enforced by the browser;
- a stricter Traefik CSP that forbids the inherited inline Glimpse JavaScript can break the application.

Either let Beyond Glimpse own its CSP or make sure the Traefik CSP is compatible with the policy in `config/nginx.conf`.

`Strict-Transport-Security` (HSTS) is best added at Traefik rather than Nginx, because Traefik is the component that actually terminates HTTPS. The internal Nginx hop is intentionally plain HTTP on the private Docker network.

## Sync status

Detailed sync telemetry stays private under the persistent state volume:

```text
/app/state/jellyfin/sync-status.json
```

It deliberately is not exposed through Nginx.

For a human-readable summary:

```bash
docker compose -f docker-compose.traefik.yml exec beyond-glimpse \
  python /app/scripts/status.py --server jellyfin
```

For machine-readable JSON:

```bash
docker compose -f docker-compose.traefik.yml exec beyond-glimpse \
  python /app/scripts/status.py --server jellyfin --json
```

The status command reports:

- last sync state and mode;
- duration;
- full vs incremental reason;
- changed catalogue record count when available;
- movie and TV-show counts;
- incremental watermark and last full reconciliation;
- poster/backdrop file counts and storage;
- private state storage;
- recent failure output when a sync fails.

Every wrapped sync also emits one single-line `[SYNC SUMMARY]` JSON record to the container/cron log for log collectors.

## Useful operator commands

Container health:

```bash
docker inspect --format '{{json .State.Health}}' beyond-glimpse
```

Recent logs:

```bash
docker logs beyond-glimpse --tail 200
```

Detailed catalogue/storage status:

```bash
docker exec beyond-glimpse python /app/scripts/status.py --server jellyfin
```
