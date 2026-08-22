# Production validation checklist

Use this after deploying Beyond Glimpse v2 behind Traefik against a real Jellyfin server.

## 1. Build and start

```bash
docker compose -f docker-compose.traefik.yml up -d --build --force-recreate
```

Nginx and the localhost catalogue API should become available before the first lightweight bootstrap completes.

```bash
docker inspect --format '{{json .State.Health}}' beyond-glimpse
curl -fsS https://YOUR_HOST/healthz
curl -fsS https://YOUR_HOST/api/status
```

A brand-new browser may initially show that the catalogue is being indexed. As soon as the bootstrap commits its first page, already-indexed titles can be browsed while later pages/libraries continue.

## 2. Follow the bootstrap

```bash
docker exec beyond-glimpse tail -f /var/log/initial-sync.log
```

You should see unbuffered progress similar to:

```text
Catalogue v2 bootstrap: 19 libraries, lightweight page target 1000
[1/19] Movies (movie)
  indexed 1000 in library / 1000 total (last page 1000, limit 1000)
```

At any time, check the private SQLite state:

```bash
docker exec beyond-glimpse python /app/scripts/status.py --server jellyfin
```

or the public non-secret API state:

```bash
curl -fsS https://YOUR_HOST/api/status
```

Expected after completion:

- architecture `catalogue-v2` in operator status;
- `Bootstrap: complete`;
- non-zero movie and/or TV-show counts;
- search mode `fts5` on standard Python/SQLite builds (safe `like` fallback is also supported);
- private `/app/state/jellyfin/catalogue-v2.db` present;
- no requirement for Jellyfin `movies.json`, `tvshows.json` or detail shards;
- poster cache no greater than the 256 MiB ceiling.

## 3. Run the automated smoke test

```bash
docker exec beyond-glimpse python /app/scripts/smoke_test.py
```

To verify the public Traefik route too:

```bash
docker exec beyond-glimpse python /app/scripts/smoke_test.py \
  --url https://YOUR_HOST
```

The v2 smoke test checks:

- internal Nginx health;
- catalogue API availability;
- completed SQLite bootstrap;
- bounded pagination;
- server-side search;
- one lazy rich-detail request;
- one exact ID+tag whitelisted poster request when available;
- private SQLite catalogue state;
- optional public Traefik health/home/API routes.

## 4. Validate lightweight incremental behaviour

Run one normal changed-item pass manually:

```bash
docker exec beyond-glimpse \
  python -u /app/scripts/catalogue_sync.py --incremental
```

With a completed bootstrap and unchanged user/library scope, it should report a lightweight incremental window rather than performing another bootstrap.

If possible, edit one disposable Jellyfin title or poster, run the command again, and confirm the local catalogue row/tag updates without a full-library rich fetch.

The normal Supervisor scheduler performs this pass every 10 minutes by default (`SYNC_INTERVAL_SECONDS=600`).

## 5. Validate delete/move reconciliation

Run an ID-only reconciliation manually:

```bash
docker exec beyond-glimpse \
  python -u /app/scripts/catalogue_sync.py --reconcile
```

Expected output includes per-library inventory counts followed by a line similar to:

```text
ID reconciliation complete: 52418 current, 1 deleted, 0 new, 0 moved, 0 lightweight refreshes
```

For a controlled test, remove one disposable title from an included Jellyfin library and verify it disappears after reconciliation.

Do not disable the reconciliation guards. A zero-ID inventory or a deletion fraction greater than `RECONCILE_MAX_DELETE_FRACTION` is intentionally refused rather than mass-deleting local state.

## 6. Validate lazy details

Open several titles in the web UI and then run:

```bash
docker exec beyond-glimpse python /app/scripts/status.py --server jellyfin
```

`cached rich details` should increase only for titles actually opened. The initial library size should not determine this count.

## 7. Browser checks

Test at least one desktop browser and one phone:

- titles appear before the entire first bootstrap finishes once pages are committed;
- scrolling remains smooth and loads additional server pages;
- browser memory/DOM does not grow from loading the entire catalogue at startup;
- search returns server-side results quickly;
- genre filters work;
- sort modes work;
- opening a title loads synopsis/cast/detail data;
- posters appear on demand;
- tab switching between movies/TV requests the appropriate API data.

## 8. Resource baseline

Capture:

```bash
docker stats --no-stream beyond-glimpse
docker exec beyond-glimpse python /app/scripts/status.py --server jellyfin
docker exec beyond-glimpse du -sh /app/state/jellyfin /var/cache/nginx/posters
```

Use these measurements as the real baseline before making further optimisations.

## Logs

```bash
# first/bootstrap sync
docker exec beyond-glimpse tail -n 200 /var/log/initial-sync.log

# local catalogue API
docker exec beyond-glimpse tail -n 200 /var/log/catalogue-api.log

# scheduled lightweight syncs
docker exec beyond-glimpse tail -n 200 /var/log/catalogue-scheduler.log
```

## Rollback / recovery

To repeat a lightweight bootstrap of the current Jellyfin scope:

```bash
docker exec beyond-glimpse \
  python -u /app/scripts/catalogue_sync.py --bootstrap
```

If v2 itself needs to be rolled back, redeploy the previous Git tag/image while preserving `./data` and `./state`. v2 uses its own `catalogue-v2.db`, so the previous v1 state files can coexist for rollback and are not required by the v2 Jellyfin path.
