# Production validation checklist

Use this after deploying Beyond Glimpse behind Traefik against a real Jellyfin server.

## 1. Build and start

```bash
docker compose -f docker-compose.traefik.yml up -d --build
```

Nginx and Traefik health should become available before the first catalogue import completes.

```bash
docker inspect --format '{{json .State.Health}}' beyond-glimpse
curl -fsS https://YOUR_HOST/healthz
```

A brand-new browser session may show **Catalogue is being prepared…** while the one-shot initial sync runs. Existing catalogue data remains available during later container restarts.

## 2. Follow the first import

```bash
docker logs -f beyond-glimpse
```

Useful one-shot sync log:

```bash
docker exec beyond-glimpse tail -n 100 /var/log/initial-sync.log
```

Operator status:

```bash
docker exec beyond-glimpse python /app/scripts/status.py --server jellyfin
```

Expected after completion:

- `Sync: success`;
- non-zero movie and/or TV-show count;
- local Jellyfin poster storage at or near zero;
- backdrop storage at zero when `DOWNLOAD_BACKDROPS=false`;
- poster proxy cache no greater than the configured 256 MiB ceiling;
- private `catalog.db` present under `/app/state/jellyfin`.

## 3. Run the automated smoke test

Inside the container:

```bash
docker exec beyond-glimpse python /app/scripts/smoke_test.py
```

To verify the public Traefik route too:

```bash
docker exec beyond-glimpse python /app/scripts/smoke_test.py \
  --url https://YOUR_HOST
```

The smoke test checks:

- internal Nginx health;
- initial catalogue completion;
- compact movie/TV indexes;
- lazy detail shard lookup;
- a real on-demand Jellyfin poster through the Nginx proxy;
- private SQLite/sync state;
- optional public Traefik health/homepage.

## 4. Validate incremental behaviour

After the first successful full import, manually run the Jellyfin sync wrapper:

```bash
docker exec beyond-glimpse \
  python /app/scripts/sync_runner.py \
  --server-type jellyfin \
  --state-dir /app/state/jellyfin \
  --output-dir /app/data/jellyfin \
  -- python /app/scripts/ultralight_jellyfin.py --output /app/data/jellyfin
```

With no major configuration change, the log should report `Sync mode: incremental` rather than rebuilding the catalogue.

Run the status command again and record the duration and changed-record count.

## 5. Validate delete/move reconciliation

The periodic maintenance interval is controlled by `FULL_RECONCILE_HOURS` (24 by default). Despite the retained variable name, this is now an ID-only inventory, not a full metadata rebuild.

When due, logs should contain a line similar to:

```text
ID reconciliation: 52418 current IDs, 1 deleted, 0 new, 0 moved
```

For a controlled test, remove one disposable test title from an included Jellyfin library and verify it disappears after the next ID reconciliation.

## 6. Browser checks

Test at least one desktop browser and one phone:

- initial catalogue appears quickly;
- scrolling remains smooth;
- only bounded batches of cards are added;
- search responds without multi-second stalls;
- genres work;
- opening a title loads synopsis/cast/detail data;
- posters appear on demand;
- no full-page reload is required after first catalogue preparation.

## 7. Resource baseline

Capture:

```bash
docker stats --no-stream beyond-glimpse
docker exec beyond-glimpse python /app/scripts/status.py --server jellyfin
```

Keep these as your initial baseline. Optimise further only if real measurements expose a bottleneck.

## Rollback / recovery

Force a true full rebuild once:

```text
FORCE_FULL_SYNC=true
```

After the successful rebuild, return it to `false`.

If the new image itself needs to be rolled back, redeploy the previous Git tag/image while keeping `./data` and `./state` backed up. Catalogue writes are atomic and private SQLite state is stored separately from Nginx-served data.
