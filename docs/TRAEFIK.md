# Traefik deployment

Beyond Glimpse is designed to work cleanly behind Traefik.

The request path is:

`client -> Traefik (HTTPS/TLS) -> Beyond Glimpse Nginx (HTTP port 80)`

Nginx is not a second public reverse proxy in this layout; it is the lightweight static web server inside the Beyond Glimpse container. Traefik handles public routing and TLS.

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

## First-start behaviour

Nginx and cron start under Supervisor before the one-shot initial catalogue sync. This means `/healthz` becomes available to Docker/Traefik without waiting for a large first import.

On a brand-new install the web UI displays a preparation message while the initial sync runs and retries automatically when the catalogue is ready. On later restarts, an existing good catalogue remains browseable while the startup refresh runs in the background.

Initial sync log:

```bash
docker exec beyond-glimpse tail -n 100 /var/log/initial-sync.log
```

## Ultra-light Jellyfin poster path

Jellyfin posters are not bulk-downloaded during sync. The browser requests a tag-versioned URL such as:

```text
/poster/<jellyfin-item-id>/<image-tag>.jpg
```

The internal Nginx server fetches that poster from Jellyfin only when it is actually needed and caches the resized result locally.

Defaults:

- poster width: 320 px;
- JPEG quality: 72;
- Nginx poster cache maximum: **256 MiB**;
- inactive cached posters expire after 30 days;
- the cache is ephemeral container storage, not part of persistent `/app/data`.

The image tag is part of the public poster URL and the Nginx cache key, so when Jellyfin changes an image the URL changes automatically rather than requiring a cache purge.

You can tune the requested poster dimensions in `.env`:

```text
POSTER_PROXY_MAX_WIDTH=320
POSTER_PROXY_QUALITY=72
```

The hard cache ceiling is intentionally kept in `config/nginx.conf` so a deployment cannot accidentally grow an unbounded artwork store.

## Catalogue payload model

For Jellyfin, `movies.json` and `tvshows.json` are compact browse/search indexes. They contain only the fields required to render and filter the grid.

Modal-only metadata is stored in deterministic static shards under:

```text
/data/jellyfin/details/movies/
/data/jellyfin/details/tvshows/
```

The browser downloads a detail shard only when a title is opened. This avoids sending every synopsis, actor and modal field to every visitor during page startup.

## Health checks

Beyond Glimpse exposes:

```text
GET /healthz
```

which returns a small non-sensitive JSON response when the internal Nginx server is alive.

The Docker image has a native `HEALTHCHECK`, and the Traefik service definition also checks `/healthz`.

Because Nginx starts before the one-shot initial catalogue sync, the backend can become healthy while a new catalogue is still being prepared. The browser handles that state separately and automatically retries catalogue loading.

## Nginx security headers and Traefik

Beyond Glimpse already sends its browser security headers from Nginx, including CSP, framing protection and `nosniff`.

It is fine to use ordinary Traefik routing/TLS middleware in front of it. If your Traefik installation applies a **global security-header middleware**, pay particular attention to `Content-Security-Policy`:

- duplicate harmless headers are usually fine;
- multiple CSP headers are all enforced by the browser;
- a stricter Traefik CSP that forbids the inherited inline Glimpse JavaScript can break the application.

Either let Beyond Glimpse own its CSP or make sure the Traefik CSP is compatible with the policy in `config/nginx.conf`.

`Strict-Transport-Security` (HSTS) is best added at Traefik rather than Nginx, because Traefik is the component that actually terminates HTTPS. The internal Nginx hop is intentionally plain HTTP on the private Docker network.

## Sync and storage status

Detailed sync telemetry stays private under the persistent state volume:

```text
/app/state/jellyfin/sync-status.json
```

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
- duration and changed-record count;
- movie and TV-show counts;
- incremental watermark and last deletion/ID reconciliation;
- compact index size;
- detail-shard count and size;
- local poster/backdrop storage;
- Nginx poster proxy cache usage against its 256 MiB ceiling;
- private state storage;
- total Beyond Glimpse application storage;
- recent failure output when a sync fails.

Every wrapped sync also emits one single-line `[SYNC SUMMARY]` JSON record to the container/cron log for log collectors.

## Production smoke test

Once the first catalogue is ready:

```bash
docker exec beyond-glimpse python /app/scripts/smoke_test.py
```

To validate the public Traefik route too:

```bash
docker exec beyond-glimpse python /app/scripts/smoke_test.py \
  --url https://YOUR_HOST
```

See `docs/PRODUCTION_CHECKLIST.md` for the complete acceptance sequence.

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
