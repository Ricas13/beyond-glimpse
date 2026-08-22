# Beyond Glimpse

**Beyond Glimpse** is a fast, storage-efficient public media catalogue for Jellyfin, with retained Plex and Emby compatibility. It is a performance-focused fork of [Jereme Hancock's Glimpse](https://github.com/jeremehancock/Glimpse), redesigned for very large libraries.

Current release: **v2.0.0**

## What changed in v2

The Jellyfin production path is no longer a static mirror generator. Beyond Glimpse now runs a tiny local catalogue service:

```text
Jellyfin
   │
   ├─ lightweight bootstrap / changed items / ID inventory
   │
   ▼
private SQLite/WAL catalogue
   │
   ├─ FTS5 search
   ├─ paginated browse API
   ├─ lazy one-item detail cache
   └─ exact ID+poster-tag whitelist
            │
            ▼
Traefik → Nginx → browser/PWA
                 └→ bounded 256 MiB poster cache
```

The bootstrap stores only the data required to browse and search:

- Jellyfin item ID;
- library and movie/series type;
- title;
- year;
- date added;
- genres;
- Primary poster image tag.

It does **not** prefetch Overview, cast, studios, runtime, ratings, taglines or series counts for every title. Those fields are fetched from Jellyfin only when somebody opens that item and are then cached locally.

Each bootstrap page is committed immediately, so a brand-new deployment can start showing indexed items while later libraries are still being scanned.

## Why this scales

For Jellyfin, the browser never downloads an all-movies or all-TV JSON file. It requests one page at a time:

```text
GET /api/items?type=movie&limit=96&offset=0
GET /api/items?type=movie&limit=96&offset=96
GET /api/items?type=movie&q=alien&limit=96
GET /api/items?type=tvshow&genre=Crime&limit=96
```

Opening a title performs one lazy detail request:

```text
GET /api/item/<jellyfin-item-id>
```

The result is cached in SQLite for seven days by default.

### Jellyfin v2 performance characteristics

- 48-item mobile / 96-item desktop browse pages.
- Server-side pagination rather than all-library browser arrays.
- SQLite FTS5 search, with a safe `LIKE` fallback if FTS5 is unavailable.
- Lightweight first bootstrap rather than a rich metadata rebuild.
- Bootstrap pages are independently committed and immediately browseable.
- Changed-item polling every 10 minutes by default using `MinDateLastSaved`.
- Five-minute watermark overlap by default.
- ID-only deletion/move reconciliation every 24 hours.
- No bulk poster download.
- Exact catalogue ID + image-tag validation before a poster can be fetched from Jellyfin.
- Hard 256 MiB Nginx poster cache ceiling.
- No Jellyfin token in generated Nginx configuration.
- `/api/`, `/poster/` and catalogue data are never duplicated into browser Cache Storage.

## Supported media servers

| Server | Support | Data path |
| --- | --- | --- |
| Jellyfin | Recommended | v2 SQLite catalogue service |
| Emby | Supported | retained static/full-sync compatibility path |
| Plex | Supported | retained inherited static path |

The v2 architecture currently targets Jellyfin because that is where the very-large-library optimisations are implemented.

## Recommended deployment: Traefik

```text
Internet → Traefik HTTPS → Beyond Glimpse Nginx :80
                              ├→ catalogue API 127.0.0.1:8091
                              └→ static UI/PWA
```

The supplied Traefik compose publishes no host port.

### 1. Clone

```bash
git clone https://github.com/Ricas13/beyond-glimpse.git
cd beyond-glimpse
```

### 2. Configure

```bash
cp .env.traefik.example .env
nano .env
```

Minimum Jellyfin configuration:

```text
BEYOND_GLIMPSE_HOST=library.example.com
TRAEFIK_NETWORK=media_net
TRAEFIK_CERTRESOLVER=letsencrypt

PRIMARY_SERVER=jellyfin
JELLYFIN_URL=https://jellyfin.example.com
JELLYFIN_TOKEN=replace-me
JELLYFIN_USER_ID=
```

`JELLYFIN_USER_ID` is strongly recommended on multi-user servers so catalogue permissions are deterministic. If it is empty, Beyond Glimpse preserves compatibility by selecting the first user returned by Jellyfin and stores that choice in private state.

### 3. Start

```bash
docker compose -f docker-compose.traefik.yml up -d --build
```

Nginx and the catalogue API start before the background bootstrap. Docker/Traefik health therefore becomes available immediately.

On a new deployment, the page initially says the catalogue is being indexed. As soon as the first lightweight page has committed, the browser automatically loads the available items while indexing continues.

## Jellyfin v2 configuration

### Catalogue and sync

| Variable | Default | Purpose |
| --- | ---: | --- |
| `CATALOGUE_BOOTSTRAP_PAGE_SIZE` | `1000` | Target lightweight page size for first/full scope bootstrap |
| `CATALOGUE_DELTA_PAGE_SIZE` | `500` | Changed-item page size |
| `SYNC_INTERVAL_SECONDS` | `600` | Lightweight changed-item scheduler interval |
| `SYNC_OVERLAP_SECONDS` | `300` | Watermark overlap safety window |
| `CATALOGUE_ID_PAGE_SIZE` | `2000` | ID-only reconciliation page target |
| `CATALOGUE_ID_DETAIL_BATCH` | `200` | Lightweight refetch batch for new/moved IDs |
| `RECONCILE_INTERVAL_HOURS` | `24` | Deletion/move reconciliation interval |
| `RECONCILE_MAX_DELETE_FRACTION` | `0.35` | Safety guard for unexpectedly destructive inventories |
| `DETAIL_CACHE_TTL_SECONDS` | `604800` | Lazy rich-detail cache TTL, default seven days |
| `REQUEST_TIMEOUT` | `60` | Jellyfin API read timeout |

Lightweight bootstrap/delta requests deliberately disable user data and total record counts and request only the small browse/search field set plus the Primary image tag.

If Jellyfin times out on a lightweight page, the bootstrap reduces that request's page size rather than retrying the same oversized page repeatedly.

### Posters

| Variable | Default | Purpose |
| --- | ---: | --- |
| `POSTER_PROXY_MAX_WIDTH` | `320` | Width requested from Jellyfin |
| `POSTER_PROXY_QUALITY` | `72` | JPEG quality requested from Jellyfin |

The public URL remains tag-versioned:

```text
/poster/<item-id>/<image-tag>.jpg
```

Nginx first sends that request to the localhost catalogue service. The service verifies both that the ID exists in the public catalogue and that the requested tag exactly matches the stored current tag. Only then does it make the authenticated Jellyfin image request. Nginx caches successful viewed posters with a hard `max_size=256m` limit.

## Sync model

### Initial bootstrap

1. Resolve the selected Jellyfin user and eligible movie/TV libraries.
2. Capture a server-time watermark.
3. Page each library using lightweight browse/search metadata only.
4. Commit every page to SQLite immediately.
5. Mark the bootstrap complete only after every library succeeds.
6. Remove rows that were not seen in the completed generation.

Changes made while the bootstrap is running are caught by the next changed-item pass because the watermark was captured at the start and the normal overlap window is applied.

### Normal sync

The Supervisor scheduler runs every 10 minutes by default and requests only items saved since the prior watermark. Updated browse fields and poster tags are upserted into SQLite.

### Reconciliation

Every 24 hours by default, Beyond Glimpse performs an ID-only inventory. Unchanged IDs receive no metadata request. New or moved IDs receive only the lightweight browse fields. Missing IDs are removed locally.

Two safety guards prevent a transient bad inventory from causing a mass local deletion:

- zero returned IDs cannot delete a non-empty catalogue;
- a deletion fraction greater than `RECONCILE_MAX_DELETE_FRACTION` is refused.

## Storage model

Persistent mounts:

```text
./data  → /app/data
./state → /app/state
```

For Jellyfin v2 the important persistent file is:

```text
/app/state/jellyfin/catalogue-v2.db
```

SQLite contains:

- lightweight browse rows;
- normalized genre rows;
- FTS5 search index where available;
- lazy detail cache;
- sync metadata/watermarks/progress.

There is no active Jellyfin `movies.json`, `tvshows.json` or detail-shard requirement in v2.

Viewed posters live only in the ephemeral Nginx cache:

```text
/var/cache/nginx/posters
```

with a hard 256 MiB ceiling.

## Processes

Supervisor runs:

```text
nginx                 public HTTP/static/proxy
catalogue-api         localhost SQLite browse/detail/poster service
cron                  Plex/Emby compatibility
initial-sync          one-shot startup bootstrap
catalogue-scheduler   Jellyfin lightweight changed-item scheduler
```

The legacy Jellyfin rich-sync cron line is disabled in v2.

## Health and status

Public liveness:

```text
GET /healthz
```

Catalogue state:

```text
GET /api/status
```

Operator status:

```bash
docker exec beyond-glimpse \
  python /app/scripts/status.py --server jellyfin
```

Example fields include movie/TV counts, bootstrap progress, FTS/LIKE mode, lazy-detail row count, database size and poster-cache use.

## Logs

Initial bootstrap:

```bash
docker exec beyond-glimpse tail -f /var/log/initial-sync.log
```

Catalogue API:

```bash
docker exec beyond-glimpse tail -f /var/log/catalogue-api.log
```

Scheduled changed-item sync:

```bash
docker exec beyond-glimpse tail -f /var/log/catalogue-scheduler.log
```

All v2 Python processes run unbuffered, so progress lines appear as the work happens.

## Production smoke test

After bootstrap completes:

```bash
docker exec beyond-glimpse \
  python /app/scripts/smoke_test.py
```

To validate the public Traefik route too:

```bash
docker exec beyond-glimpse \
  python /app/scripts/smoke_test.py \
  --url https://library.example.com
```

The v2 smoke test validates:

- Nginx health;
- catalogue API availability;
- completed SQLite bootstrap;
- bounded page size;
- server-side search;
- one lazy detail request;
- one whitelisted poster request when a sample poster exists;
- private SQLite catalogue state;
- optional public Traefik health/home/API routes.

See [`docs/PRODUCTION_CHECKLIST.md`](docs/PRODUCTION_CHECKLIST.md) for the full deployment acceptance process.

## Updating from v1

```bash
cd beyond-glimpse
git pull
docker compose -f docker-compose.traefik.yml up -d --build --force-recreate
```

v2 uses a new `catalogue-v2.db`, so it does not need to convert the partially built v1 rich-metadata database. The first v2 startup performs the new lightweight bootstrap from Jellyfin. Existing v1 state files can remain temporarily for rollback; they are not used by the v2 Jellyfin production path.

## Direct-port deployment

For local testing without Traefik:

```bash
docker compose up -d --build
```

The inherited default compose publishes port `9090`. Internet-facing use should use the Traefik compose.

## Security notes

Beyond Glimpse includes:

- Content Security Policy;
- anti-framing policy;
- `nosniff`;
- referrer and permissions policies;
- Nginx server tokens disabled;
- private SQLite state outside the public web tree;
- Jellyfin token excluded from browser responses and generated Nginx configuration;
- exact catalogue ID+poster-tag validation;
- no direct host port in the recommended Traefik compose;
- pinned Python runtime dependency.

The container still uses root for the Supervisor/cron/Nginx master process model inherited from Glimpse. A full non-root conversion requires a separate process-model redesign and is intentionally not faked with a cosmetic `USER` directive.

See [`docs/SECURITY.md`](docs/SECURITY.md).

## Development and CI

Pull requests and pushes to `main` run:

```text
Python 3.13 compilation
JavaScript syntax validation
unit/regression tests
full production Docker image build
Nginx configuration validation
```

Runtime Python dependencies are pinned in `requirements.txt`.

## Release history

See [`CHANGELOG.md`](CHANGELOG.md).

## Upstream and licence

Beyond Glimpse is derived from the MIT-licensed [Glimpse Media Viewer](https://github.com/jeremehancock/Glimpse) by Jereme Hancock. The original copyright and MIT licence are preserved in [`LICENSE`](LICENSE).
