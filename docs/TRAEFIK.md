# Traefik deployment

Beyond Glimpse is designed to work cleanly behind Traefik.

The public request path is:

```text
client → Traefik HTTPS/TLS → Beyond Glimpse Nginx :80
                              ├→ static UI/PWA
                              ├→ /api/* → catalogue service 127.0.0.1:8091
                              └→ /poster/* → bounded cache → catalogue whitelist → Jellyfin
```

Traefik handles public routing and TLS. Nginx remains the only public-facing process inside the container; the catalogue service listens only on container loopback.

## Quick deployment

1. Copy the example environment file:

   ```bash
   cp .env.traefik.example .env
   ```

2. Set at least:

   - `BEYOND_GLIMPSE_HOST`
   - `TRAEFIK_NETWORK`
   - `TRAEFIK_CERTRESOLVER`
   - `PRIMARY_SERVER=jellyfin`
   - `JELLYFIN_URL`
   - `JELLYFIN_TOKEN`
   - preferably `JELLYFIN_USER_ID` on multi-user servers.

3. Make sure the external Docker network already exists and is shared with Traefik.

4. Start/recreate Beyond Glimpse:

   ```bash
   docker compose -f docker-compose.traefik.yml up -d --build --force-recreate
   ```

The Traefik compose publishes no host port. Traefik reaches Nginx directly through the external Docker network.

## First-start behaviour

Supervisor starts Nginx and the localhost catalogue API before the one-shot Jellyfin bootstrap. `/healthz` can therefore become healthy without waiting for the catalogue scan.

The v2 bootstrap requests only lightweight browse/search metadata and commits each successful page immediately. On a new deployment, the web UI initially reports that indexing is in progress; once rows exist, it can browse those committed items before later pages/libraries finish.

Follow bootstrap progress:

```bash
docker exec beyond-glimpse tail -f /var/log/initial-sync.log
```

Public non-secret catalogue status:

```text
GET /api/status
```

Operator status:

```bash
docker exec beyond-glimpse python /app/scripts/status.py --server jellyfin
```

## Catalogue API path

Jellyfin v2 browsing does not require public `movies.json`/`tvshows.json` files. Nginx proxies `/api/` to `127.0.0.1:8091` inside the same container.

Typical browser requests are:

```text
/api/items?type=movie&limit=96&offset=0
/api/items?type=movie&q=alien&limit=96
/api/items?type=tvshow&genre=Crime&limit=96
/api/genres?type=movie
/api/item/<catalogue-id>
```

`/api/items` and `/api/genres` are served entirely from private SQLite state. `/api/item/<id>` first requires the ID to exist in that catalogue, then fetches rich Jellyfin detail only if its private cache is missing/stale.

The browser service worker bypasses Cache Storage for `/api/`, so server-side pagination/detail responses do not accumulate an additional browser catalogue cache.

## Jellyfin poster path

Posters are still tag-versioned:

```text
/poster/<jellyfin-item-id>/<image-tag>.jpg
```

The request path is now:

```text
browser
  → Nginx bounded poster cache
  → localhost catalogue service
  → verify item ID exists + exact stored Primary image tag matches
  → authenticated Jellyfin image request
```

The generated Nginx include contains no Jellyfin API token. Credentials remain in the Python catalogue service environment and are used only after whitelist validation.

Defaults:

- poster width: 320 px;
- JPEG quality: 72;
- Nginx poster cache maximum: **256 MiB**;
- inactive cached posters expire after 30 days;
- poster cache is ephemeral container storage, not persistent `/app/data`.

A changed Jellyfin image tag changes the public URL/cache key naturally.

Tune image output with:

```text
POSTER_PROXY_MAX_WIDTH=320
POSTER_PROXY_QUALITY=72
```

The hard cache ceiling remains in `config/nginx.conf` so operator configuration cannot accidentally create an unbounded artwork store.

## Sync scheduling

Jellyfin v2 no longer uses the inherited six-hour rich-sync cron line. The image finalization step disables that Jellyfin cron entry, and Supervisor runs the dedicated lightweight scheduler instead.

Default changed-item cadence:

```text
SYNC_INTERVAL_SECONDS=600
```

The scheduler invokes `catalogue_sync.py --incremental`. The sync script performs the ID-only deletion/move reconciliation only when `RECONCILE_INTERVAL_HOURS` is due (24 hours by default).

Cron remains present for retained Plex/Emby compatibility paths.

## Health checks

Nginx liveness:

```text
GET /healthz
```

Catalogue state/API health:

```text
GET /api/status
```

The Docker `HEALTHCHECK` and Traefik backend healthcheck intentionally use `/healthz`: routing remains healthy even while a first bootstrap or later sync is running. Catalogue readiness/progress is separately observable through `/api/status`.

## Nginx security headers and Traefik

Beyond Glimpse sends browser security headers from Nginx, including CSP, framing protection and `nosniff`.

Ordinary Traefik routing/TLS middleware is fine. If Traefik applies a global security-header middleware, pay particular attention to `Content-Security-Policy`:

- multiple CSP headers are all enforced by browsers;
- a stricter Traefik CSP that forbids the inherited inline Glimpse JavaScript can break the UI.

Either let Beyond Glimpse own CSP or make sure the Traefik policy is compatible with `config/nginx.conf`.

`Strict-Transport-Security` is best applied at Traefik because that is where TLS terminates. The private Traefik→Nginx Docker-network hop remains HTTP.

## Status and storage

Private v2 catalogue state:

```text
/app/state/jellyfin/catalogue-v2.db
```

Human-readable status:

```bash
docker compose -f docker-compose.traefik.yml exec beyond-glimpse \
  python /app/scripts/status.py --server jellyfin
```

JSON status:

```bash
docker compose -f docker-compose.traefik.yml exec beyond-glimpse \
  python /app/scripts/status.py --server jellyfin --json
```

The v2 report includes:

- bootstrap/sync state;
- movie/TV counts and current bootstrap progress;
- FTS5/LIKE search mode;
- cached rich-detail row count;
- SQLite/state size;
- last bootstrap/incremental/reconciliation timestamps;
- Nginx poster cache usage against the 256 MiB ceiling;
- last sync error where present.

## Production smoke test

After bootstrap completes:

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

```bash
# container liveness
docker inspect --format '{{json .State.Health}}' beyond-glimpse

# bootstrap
docker exec beyond-glimpse tail -n 200 /var/log/initial-sync.log

# local API
docker exec beyond-glimpse tail -n 200 /var/log/catalogue-api.log

# scheduler
docker exec beyond-glimpse tail -n 200 /var/log/catalogue-scheduler.log

# catalogue/storage status
docker exec beyond-glimpse python /app/scripts/status.py --server jellyfin
```
