# Beyond Glimpse

**Beyond Glimpse** is a fast, storage-efficient media catalogue for Jellyfin, with retained Plex and Emby compatibility. It is a performance-focused fork of [Jereme Hancock's Glimpse](https://github.com/jeremehancock/Glimpse), redesigned to remain responsive on very large libraries without permanently storing an artwork copy for every title.

Current release: **v1.0.0**

## Why Beyond Glimpse

The original Glimpse design works well for ordinary libraries, but large catalogues can make full metadata refreshes, artwork storage and browser rendering expensive. Beyond Glimpse keeps the familiar catalogue UI while changing the heavy data paths.

For Jellyfin, the default architecture is now:

```text
Jellyfin
   │
   ├─ changed metadata only ───────────────┐
   │                                      ▼
   │                              private SQLite state
   │                                      │
   └─ poster only when somebody views it  │
                                          ▼
Traefik → Nginx → compact browse indexes + lazy detail shards
              └→ hard-bounded 256 MiB on-demand poster cache
```

### Main Jellyfin optimisations

- Incremental metadata sync using Jellyfin's `MinDateLastSaved` support.
- Server-time watermarks with a safety overlap window.
- Periodic **ID-only** deletion/move reconciliation instead of recurring full metadata rebuilds.
- No per-series season/episode request fan-out.
- No bulk poster download during sync.
- Posters fetched only when viewed, resized by Jellyfin and cached by Nginx.
- Hard **256 MiB** poster proxy cache ceiling.
- Backdrops disabled by default.
- Compact browse indexes containing only fields required for catalogue browsing/search.
- Modal-only metadata stored in 256 deterministic lazy detail shards.
- 96-card desktop / 48-card mobile incremental rendering.
- Debounced search and bounded DOM growth.
- Shell-only PWA cache; catalogue/posters are not duplicated into an unbounded browser cache.
- Atomic catalogue writes, sync locking, retries/backoff and private SQLite state.

## Supported media servers

| Server | Support | Optimisation level |
| --- | --- | --- |
| Jellyfin | Recommended | Full Beyond Glimpse ultra-light path |
| Emby | Supported | Retained full-sync/local-artwork path |
| Plex | Supported | Retained inherited path |

The largest performance/storage improvements currently target Jellyfin.

## Recommended deployment: Traefik

The intended production topology is:

```text
Internet → Traefik (HTTPS/TLS) → Beyond Glimpse Nginx (HTTP :80)
```

Nginx is the lightweight static/application server **inside** the Beyond Glimpse container. Traefik remains the public reverse proxy and TLS terminator.

The supplied Traefik compose publishes **no host port**.

### 1. Clone

```bash
git clone https://github.com/Ricas13/beyond-glimpse.git
cd beyond-glimpse
```

### 2. Create your environment file

```bash
cp .env.traefik.example .env
nano .env
```

At minimum configure:

```text
BEYOND_GLIMPSE_HOST=discover.example.com
TRAEFIK_NETWORK=media_net
TRAEFIK_CERTRESOLVER=letsencrypt

PRIMARY_SERVER=jellyfin
JELLYFIN_URL=http://jellyfin:8096
JELLYFIN_TOKEN=replace-me
```

`JELLYFIN_USER_ID` is optional but recommended when you want deterministic user permissions rather than automatic first-user selection.

### 3. Ensure the Traefik network exists

For the default example:

```bash
docker network inspect media_net >/dev/null 2>&1 || docker network create media_net
```

Traefik itself must also be attached to that external Docker network.

### 4. Build and start

```bash
docker compose -f docker-compose.traefik.yml up -d --build
```

Nginx starts **before** the first catalogue sync. This means Docker/Traefik health becomes available immediately even when the initial import is large.

On a brand-new deployment the browser shows:

```text
Catalogue is being prepared… This page will update automatically.
```

The page retries automatically when the first import finishes. On later restarts, the previous good catalogue remains browseable while the startup refresh runs in the background.

## Direct-port deployment

For local testing or deployments without Traefik:

```bash
docker compose up -d --build
```

The default compose publishes:

```text
http://SERVER_IP:9090
```

For Internet-facing use, the Traefik deployment is preferred.

## Jellyfin configuration

### Core settings

| Variable | Default | Purpose |
| --- | ---: | --- |
| `PRIMARY_SERVER` | `jellyfin` in Traefik example | Default catalogue/server |
| `JELLYFIN_URL` | — | Jellyfin base URL |
| `JELLYFIN_TOKEN` | — | Jellyfin API key |
| `JELLYFIN_USER_ID` | empty | Optional deterministic Jellyfin user |
| `JELLYFIN_EXCLUDE_LIBRARIES` | empty | Comma-separated library names or IDs |
| `PAGE_SIZE` | `500` | Metadata page size |
| `REQUEST_TIMEOUT` | `60` | API request timeout in seconds |

### Ultra-light artwork

| Variable | Default | Purpose |
| --- | ---: | --- |
| `POSTER_PROXY_MAX_WIDTH` | `320` | Width requested from Jellyfin when a poster is first viewed |
| `POSTER_PROXY_QUALITY` | `72` | JPEG quality requested from Jellyfin |
| `DOWNLOAD_BACKDROPS` | `false` | Whether to persist modal backdrops |
| `BACKDROP_MAX_WIDTH` | `1280` | Backdrop width if enabled |
| `IMAGE_QUALITY` | `82` | Persistent backdrop image quality |

Jellyfin posters are **not** bulk-downloaded into `/app/data`. Viewed posters are cached under Nginx's ephemeral cache directory and the cache manager is configured with a hard `max_size=256m`.

The poster URL is image-tag versioned, so a changed Jellyfin poster naturally produces a new cache key.

### Incremental sync

| Variable | Default | Purpose |
| --- | ---: | --- |
| `INCREMENTAL_SYNC` | `true` | Enable changed-only Jellyfin metadata sync |
| `FULL_RECONCILE_HOURS` | `24` | Interval for the lightweight ID inventory |
| `SYNC_OVERLAP_SECONDS` | `300` | Watermark overlap safety margin |
| `FORCE_FULL_SYNC` | `false` | Force one true full metadata rebuild |
| `CRON_SCHEDULE` | `0 */6 * * *` | Scheduled refresh cadence |

Despite the retained compatibility name `FULL_RECONCILE_HOURS`, the normal periodic maintenance operation is now **ID-only**. It requests the current movie/series IDs without extra fields, images, user data or record counts and then:

- removes IDs that disappeared;
- leaves unchanged IDs alone;
- fetches full metadata only for genuinely new/moved IDs.

A true full metadata rebuild still occurs when required for correctness, for example a new/schema-changed state DB, changed server/user/library configuration, `INCREMENTAL_SYNC=false`, or `FORCE_FULL_SYNC=true`.

## Storage model

Persistent mounts:

```text
./data  → /app/data
./state → /app/state
```

For Jellyfin, `/app/data/jellyfin` normally contains:

```text
movies.json               compact browse index
tvshows.json              compact browse index
details/movies/*.json     lazy modal detail shards
details/tvshows/*.json    lazy modal detail shards
backdrops/                normally empty (disabled by default)
posters/                  legacy files are pruned; bulk posters are not created
```

Private state under `/app/state/jellyfin` includes:

```text
catalog.db
sync.lock
sync-status.json
image-state.json (legacy/artwork state where applicable)
```

Nginx explicitly blocks private SQLite/state/temp filenames from the public `/data` tree as defence in depth.

## Health and observability

Health endpoint:

```text
GET /healthz
```

It contains no library or server secrets.

Docker health:

```bash
docker inspect --format '{{json .State.Health}}' beyond-glimpse
```

Human-readable sync/storage status:

```bash
docker exec beyond-glimpse \
  python /app/scripts/status.py --server jellyfin
```

JSON status:

```bash
docker exec beyond-glimpse \
  python /app/scripts/status.py --server jellyfin --json
```

The report includes catalogue counts, sync mode/reason, duration, changed records, ID-reconciliation counts, index/detail/state size and poster proxy cache usage.

## Production smoke test

After the first catalogue import:

```bash
docker exec beyond-glimpse \
  python /app/scripts/smoke_test.py
```

To validate the Traefik route too:

```bash
docker exec beyond-glimpse \
  python /app/scripts/smoke_test.py \
  --url https://discover.example.com
```

The test validates:

- internal Nginx health;
- first catalogue completion;
- compact indexes;
- a lazy detail shard;
- a real on-demand poster request through Nginx/Jellyfin;
- private sync state;
- optional public Traefik health/homepage.

See [`docs/PRODUCTION_CHECKLIST.md`](docs/PRODUCTION_CHECKLIST.md) for the complete real-server acceptance process.

## Logs

Container logs:

```bash
docker logs beyond-glimpse --tail 200
```

Initial/background startup sync:

```bash
docker exec beyond-glimpse tail -n 100 /var/log/initial-sync.log
```

Scheduled sync output:

```bash
docker exec beyond-glimpse tail -n 100 /var/log/cron.log
```

Every wrapped Jellyfin/Emby sync emits a compact `[SYNC SUMMARY]` JSON log line as well as private `sync-status.json` telemetry.

## Updating

```bash
cd beyond-glimpse
git pull
docker compose -f docker-compose.traefik.yml up -d --build
```

Catalogue files are written atomically and state is stored separately from public data.

When upgrading from original Glimpse or an early Beyond Glimpse build, the first Jellyfin run may intentionally perform one full metadata reconciliation to migrate the catalogue schema. It does **not** bulk-download posters. Existing legacy local Jellyfin posters/backdrops are pruned according to the current configuration after a successful sync.

## Recovery

To force one true Jellyfin rebuild:

```text
FORCE_FULL_SYNC=true
```

Redeploy/run the sync, then return it to:

```text
FORCE_FULL_SYNC=false
```

Do not permanently run forced full syncs on large libraries.

## Security notes

Beyond Glimpse ships with:

- Content Security Policy;
- anti-framing policy;
- `nosniff`;
- referrer and permissions policies;
- Nginx server tokens disabled;
- private state blocked from public data routes;
- API tokens excluded from Jellyfin/Emby Python command arguments;
- pinned Python runtime dependency;
- no direct host port in the recommended Traefik compose.

Traefik should own HTTPS/HSTS because it terminates TLS. If Traefik also injects a global CSP, make sure it is compatible with Beyond Glimpse's Nginx CSP: browsers enforce multiple CSP headers together.

The container still uses root for Supervisor/cron/master process compatibility; Nginx serves public files through its normal unprivileged worker model. Converting the complete multi-process container to non-root would require a separate process-model redesign and is intentionally not mixed into v1.0.0.

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

Beyond Glimpse changes focus on large-library scalability, storage efficiency, production deployment and security while retaining the original project's interface and multi-server compatibility.
