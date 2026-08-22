# Large-library sync

Beyond Glimpse v2 stores the active Jellyfin catalogue in private SQLite state rather than generating a complete public metadata mirror.

```text
/app/state/jellyfin/catalogue-v2.db
```

The browser sees catalogue data only through the local read-only HTTP surface exposed by Nginx under `/api/`.

## Lightweight bootstrap

A new v2 database triggers a lightweight bootstrap. For every eligible movie/TV library the sync requests only browse/search data:

- ID;
- title;
- year;
- date created/added;
- genres;
- Primary image tag.

It deliberately does not request Overview, People/cast, Studios, runtime, ratings, taglines or series counts for the complete library.

Default target page size:

```text
CATALOGUE_BOOTSTRAP_PAGE_SIZE=1000
```

If a lightweight page times out, the request is retried at a smaller page size rather than repeating the same oversized request.

Each successful page is committed immediately. Already-indexed rows are therefore available to `/api/items` while the bootstrap continues through later pages/libraries.

The bootstrap captures a Jellyfin server-time watermark at its beginning. Only after all eligible libraries complete successfully does it:

1. remove rows not seen in the completed generation;
2. mark `bootstrap_complete=1`;
3. persist the start watermark;
4. mark the catalogue ready.

Changes made while bootstrap is in progress are picked up by the subsequent incremental pass because normal sync applies a watermark overlap.

## Lightweight changed-item sync

The Supervisor catalogue scheduler runs every 10 minutes by default:

```text
SYNC_INTERVAL_SECONDS=600
```

Normal runs use Jellyfin's `MinDateLastSaved` filter and request the same lightweight browse/search fields as bootstrap.

Defaults:

```text
CATALOGUE_DELTA_PAGE_SIZE=500
SYNC_OVERLAP_SECONDS=300
```

The overlap protects timestamp precision and clock-edge changes. The watermark advances only after all changed-item library requests succeed.

Updating a title, year, genres or poster in Jellyfin updates the corresponding local browse row/tag without touching unrelated records or fetching rich modal metadata.

## Lazy rich metadata

Rich metadata is outside the bulk sync path. When a visitor opens a catalogue item:

```text
GET /api/item/<item-id>
```

Beyond Glimpse first verifies that the ID exists in its public catalogue. If the private detail cache is missing or stale, it requests that one Jellyfin item with the detail fields required by the modal and stores the result in `item_details`.

Default cache TTL:

```text
DETAIL_CACHE_TTL_SECONDS=604800
```

That is seven days. If Jellyfin is temporarily unavailable and a stale detail row exists, the stale cached detail can still be served.

## ID-only deletion/move reconciliation

Changed-item polling does not reliably describe deleted titles. Every 24 hours by default, v2 therefore takes an ID-only inventory:

```text
RECONCILE_INTERVAL_HOURS=24
CATALOGUE_ID_PAGE_SIZE=2000
```

The inventory requests no extra fields, images, user data or total count. It compares current Jellyfin IDs/library ownership with SQLite:

- unchanged IDs require no metadata request;
- missing IDs become local delete candidates;
- new/moved IDs receive only lightweight browse metadata;
- no bulk rich metadata is fetched.

### Destructive-delete guards

A transient or unexpectedly scoped Jellyfin response must not wipe the local catalogue. Reconciliation refuses to delete when:

- the existing catalogue is non-empty but Jellyfin returns zero total IDs; or
- the candidate deletion fraction is greater than `RECONCILE_MAX_DELETE_FRACTION`.

Default:

```text
RECONCILE_MAX_DELETE_FRACTION=0.35
```

A refused reconciliation leaves the existing catalogue intact and reports an operator-visible sync error.

## Selected user and library scope

`JELLYFIN_USER_ID` is strongly recommended on multi-user servers. When it is absent, Beyond Glimpse selects the first Jellyfin user on first use and stores that selected ID in private state for deterministic subsequent runs.

The selected user plus eligible library IDs/types form a configuration signature. If that scope changes, Beyond Glimpse performs a new **lightweight bootstrap**, not a full rich-metadata rebuild.

`JELLYFIN_EXCLUDE_LIBRARIES` may contain comma-separated library names or IDs.

## Concurrency and state

SQLite uses WAL mode and a busy timeout so public catalogue reads can continue while sync pages are committed.

A non-blocking lock at:

```text
/app/state/jellyfin/catalogue-v2.lock
```

prevents overlapping bootstrap/incremental/reconciliation writers. If the 10-minute scheduler fires while the initial bootstrap is still running, that invocation safely skips; a later scheduled run picks up changes from the bootstrap watermark.

## Manual operations

Run a normal changed-item pass:

```bash
docker exec beyond-glimpse \
  python -u /app/scripts/catalogue_sync.py --incremental
```

Run an ID-only reconciliation now:

```bash
docker exec beyond-glimpse \
  python -u /app/scripts/catalogue_sync.py --reconcile
```

Rebuild the current Jellyfin scope using the lightweight bootstrap:

```bash
docker exec beyond-glimpse \
  python -u /app/scripts/catalogue_sync.py --bootstrap
```

This bootstrap does **not** bulk-fetch rich modal metadata or posters.

## Status

```bash
docker exec beyond-glimpse \
  python /app/scripts/status.py --server jellyfin
```

The v2 status includes catalogue counts, bootstrap progress/current library, FTS/LIKE search mode, lazy-detail cache count, database/state size, last sync/reconciliation timestamps and poster-cache use.

Emby and Plex retain their compatibility sync paths; this v2 lightweight catalogue service currently applies to Jellyfin.
