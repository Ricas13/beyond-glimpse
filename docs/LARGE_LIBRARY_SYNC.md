# Large-library sync

Beyond Glimpse keeps its public catalogue under `/app/data` and its private sync state under `/app/state`.

## Jellyfin incremental mode

Jellyfin incremental sync is enabled by default. The first run is always a full reconciliation. After that, normal scheduled runs use Jellyfin's `MinDateLastSaved` metadata filter and the previous successful server-time watermark, with a small overlap window to avoid timestamp-edge misses.

Defaults:

- `INCREMENTAL_SYNC=true`
- `FULL_RECONCILE_HOURS=24`
- `SYNC_OVERLAP_SECONDS=300`
- `PAGE_SIZE=500`

The watermark comes from Jellyfin's HTTP `Date` response header at the beginning of the sync, rather than from the container clock. It is only advanced after the public catalogue has been written successfully.

## Why a full reconciliation still runs

A changed-items query can find new or updated metadata, but it cannot reliably report titles that were deleted or moved out of a library. Beyond Glimpse therefore performs a full reconciliation every 24 hours by default. This pass also handles library/exclusion changes and removes stale artwork.

Changing the server URL, selected user, eligible library set, or artwork sizing also automatically forces a full reconciliation.

## Private catalogue state

Incremental state is stored in `/app/state/<server>/catalog.db` using SQLite. The database contains the exported media metadata, library ownership, image tags, and sync watermarks. It is not served by Nginx.

A non-blocking file lock at `/app/state/<server>/sync.lock` prevents overlapping scheduled runs.

## Recovery and troubleshooting

To force the next run to rebuild everything, set:

```yaml
- FORCE_FULL_SYNC=true
```

Return it to `false` after the successful run.

To disable incremental behaviour entirely:

```yaml
- INCREMENTAL_SYNC=false
```

Emby continues to use full reconciliation by default; the incremental implementation is intentionally enabled only for Jellyfin until equivalent behaviour is verified there.
