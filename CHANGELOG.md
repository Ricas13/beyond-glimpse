# Changelog

All notable Beyond Glimpse changes are documented here.

## 1.0.1 — 2026-08-22

Production resilience fix for very large Jellyfin libraries.

- Full rich-metadata catalogue builds now start at 100 items per request by default instead of using the 500-item incremental page size.
- Rich metadata requests automatically halve their page size after a Jellyfin read timeout and retry the same offset without losing progress.
- HTTP read timeouts are no longer retried three times with the same oversized request; connect/status-code retries remain enabled.
- `FULL_SYNC_PAGE_SIZE` is now configurable separately from `PAGE_SIZE`.
- Incremental changed-metadata requests retain the existing large page size for efficiency.
- ID-only periodic reconciliation remains unchanged at its large lightweight inventory page size.

## 1.0.0 — 2026-08-22

First production release of the Beyond Glimpse fork.

### Large-library performance

- Modern Jellyfin `Authorization: MediaBrowser` authentication.
- Incremental Jellyfin metadata sync using `MinDateLastSaved` and server-time watermarks.
- ID-only periodic reconciliation for deletes and moves instead of recurring full metadata rebuilds.
- Sync locking, retry/backoff, atomic catalogue writes and private SQLite state.
- Removed per-series season/episode API request fan-out.
- Large metadata page size and disabled unnecessary total-count/user-data work.

### Storage

- Jellyfin posters are no longer bulk-downloaded during sync.
- Posters are fetched on demand through a tag-versioned Nginx proxy and stored only in a hard-bounded 256 MiB ephemeral cache.
- Default poster proxy output is 320 px JPEG at quality 72.
- Backdrop downloading is disabled by default.
- Public catalogue data is split into compact browse indexes and lazy detail shards.
- Legacy Jellyfin poster/backdrop/state files are pruned or migrated after successful upgrade syncs.

### Browser performance

- 96-card desktop and 48-card mobile incremental rendering batches.
- Debounced search and cached normalized titles.
- Modal-only metadata loaded lazily from deterministic detail shards.
- Service worker caches the application shell only and no longer builds an unbounded poster/catalogue cache.

### Production and security

- Traefik deployment with no direct host port exposure.
- Docker and Traefik `/healthz` checks.
- Public-web CSP, anti-framing, `nosniff`, referrer, permissions and opener policies.
- Public sync state/SQLite/temp files blocked by Nginx.
- Jellyfin/Emby tokens removed from Python process command arguments.
- Private sync telemetry and operator storage/status reporting.
- Python runtime dependency pinned.
- First catalogue sync moved to a one-shot Supervisor job so Nginx/Traefik become available immediately.
- Browser displays a catalogue-preparation state and automatically retries while the first sync is running.

### Compatibility

- Jellyfin receives the optimized ultra-light path.
- Emby remains supported on the existing full-sync path.
- Plex remains supported on the inherited path.
- Existing Glimpse MIT licence and upstream attribution are preserved.
