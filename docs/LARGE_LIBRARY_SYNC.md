# Large-library sync

Beyond Glimpse keeps its public catalogue under `/app/data` and its private sync state under `/app/state`.

## Jellyfin incremental mode

Jellyfin incremental sync is enabled by default. The first run is always a full metadata reconciliation. After that, normal scheduled runs use Jellyfin's `MinDateLastSaved` metadata filter and the previous successful server-time watermark, with a small overlap window to avoid timestamp-edge misses.

Defaults:

- `INCREMENTAL_SYNC=true`
- `FULL_RECONCILE_HOURS=24`
- `SYNC_OVERLAP_SECONDS=300`
- `PAGE_SIZE=500`

The watermark comes from Jellyfin's HTTP `Date` response header at the beginning of the sync, rather than from the container clock. It is only advanced after the public catalogue has been written successfully.

## Lightweight deletion reconciliation

A changed-items query can find new or updated metadata, but it cannot reliably report titles that were deleted or moved out of a library. Beyond Glimpse therefore performs an **ID-only inventory** every `FULL_RECONCILE_HOURS` (24 hours by default).

That maintenance pass deliberately requests no extra item fields, no user data, no artwork and no total-record count. It compares Jellyfin's current movie/series IDs with the private SQLite catalogue:

- missing IDs are deleted locally;
- unchanged IDs require no metadata work;
- genuinely new or moved IDs are refetched by ID in small batches;
- only those targeted records receive full metadata.

The variable name `FULL_RECONCILE_HOURS` is retained for configuration compatibility, but the periodic maintenance pass is no longer a full metadata rebuild.

A true full reconciliation still occurs when it is required for correctness, including a new/schema-changed state database, a changed server URL, selected user, eligible library set, artwork configuration, `INCREMENTAL_SYNC=false`, or `FORCE_FULL_SYNC=true`.

## Private catalogue state

Incremental state is stored in `/app/state/<server>/catalog.db` using SQLite. The database contains the exported media metadata, library ownership, image tags, and sync watermarks. It is not served by Nginx.

A non-blocking file lock at `/app/state/<server>/sync.lock` prevents overlapping scheduled runs.

The database records both the incremental metadata watermark and the last successful ID/deletion reconciliation. Operator telemetry reports the ID inventory count plus deleted/new/moved counts when that maintenance pass runs.

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

Emby continues to use full reconciliation by default; the incremental and ID-only reconciliation implementation is intentionally enabled only for Jellyfin until equivalent behaviour is verified there.
