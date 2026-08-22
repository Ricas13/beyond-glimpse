# Changelog

All notable Beyond Glimpse changes are documented here.

## 2.2.0 — 2026-08-22

Lazy TV season and episode browsing.

- TV series modals now show season tabs and a compact episode table.
- Season 1 is selected automatically when present; clicking another season swaps the episode table in place.
- Season lists and one selected season's episodes are fetched only when a series modal is opened.
- Episode rows include episode number, title, air date, runtime and a short overview.
- Season/episode responses are cached persistently in the existing SQLite state database with a six-hour default TTL.
- Stale cached child metadata can still be served when Jellyfin is temporarily unavailable.
- Episode requests are constrained to public catalogue series and verified season membership, preventing arbitrary Jellyfin season enumeration.
- No season or episode metadata is added to the 72k-title bootstrap path.

## 2.1.0 — 2026-08-22

Per-library Jellyfin browsing.

- Movies and TV Shows keep their all-content tabs and gain adjacent library dropdowns.
- Added `/api/libraries` with local SQLite counts and a cached Jellyfin Virtual Folders lookup.
- Browse, search, sort, genre filtering and infinite scroll can be scoped to one selected library server-side.
- Library selection does not bulk-download a library into the browser or trigger Jellyfin item scans.

## 2.0.3 — 2026-08-22

Frontend runtime placement fix for the inherited Glimpse page.

- Fixed the production HTML injector selecting the first literal `</body>` text instead of the real closing body tag.
- Upstream Glimpse contains an HTML comment that literally mentions `</body>` before the actual page close; earlier v2 builds placed the external runtime script tags inside that comment, so browsers never executed them.
- Runtime and startup script tags are now normalized and inserted immediately before the final closing `</body>` tag.
- Existing v2.0.0-v2.0.2 generated pages with misplaced script tags are repaired in place.
- Bumped the browser runtime/startup URLs and PWA shell cache to force a clean corrected frontend load.
- Added regression tests reproducing the exact upstream comment and the previously broken generated layout.

## 2.0.2 — 2026-08-22

Browser startup and first-paint improvements.

- Jellyfin detection now reads the credential-free server-themed document title directly instead of relying on a function-source shim.
- The first paginated Jellyfin media page is requested before genre aggregation so navigation metadata cannot block initial cards.
- Removed the temporary v2.0.1 server-hint shim during generated-page upgrades.

## 2.0.1 — 2026-08-22

Browser compatibility fix for the new v2 Jellyfin catalogue service.

- Fixed root-page Jellyfin detection when upstream Glimpse uses generic `data/movies.json` paths in its inherited `loadMedia()` function.
- The production HTML now adds a credential-free primary-server source hint before the v2 runtime takes over.
- Plex and Emby detection remain compatible because the hint is derived from the server-themed document title generated at container startup.
- Bumped the catalogue runtime URL and service-worker shell cache so mobile browsers cannot retain the broken v2.0.0 frontend script.
- Added regression coverage for hint injection, ordering and idempotence.

## 2.0.0 — 2026-08-22

Major Jellyfin architecture redesign for very large catalogues.

### Catalogue service

- Jellyfin no longer pre-generates complete public movie/TV JSON files before the site is useful.
- Added a private SQLite/WAL catalogue database with indexed browse fields and FTS5 title/genre search.
- Added a localhost-only Python catalogue API behind Nginx for true server-side pagination, search, genre filters and sorting.
- Browser requests only 48 mobile / 96 desktop items at a time and fetches more as the user scrolls.
- The initial bootstrap stores only lightweight browse/search fields: ID, library/type, title, year, date added, genres and Primary image tag.
- Each bootstrap page is committed immediately, so already-indexed items can be browsed while later libraries continue syncing.

### Lazy metadata

- Overview, cast, studio, runtime, ratings, tagline and series counts are no longer fetched for the whole library.
- Rich metadata is fetched from Jellyfin only when a visitor opens an item and is cached locally with a configurable TTL.
- Removed Jellyfin detail shards from the active production path.

### Sync efficiency

- Added a 10-minute lightweight changed-item scheduler using `MinDateLastSaved` with overlap protection.
- Retained a periodic ID-only deletion/move reconciliation safety net.
- New/moved reconciliation IDs receive only lightweight metadata, never bulk rich metadata.
- Added guards against zero-ID inventories and unexpectedly destructive reconciliation deletes.
- Configuration changes to user/library scope trigger a new lightweight bootstrap rather than a rich metadata rebuild.
- Legacy Jellyfin rich-sync cron generation is disabled in v2; Plex/Emby cron compatibility remains.

### Posters and security

- Poster requests now pass through the local catalogue service before Jellyfin.
- The requested item ID must exist in the public catalogue and the requested image tag must exactly match the stored tag.
- Jellyfin API credentials are no longer rendered into generated Nginx poster configuration.
- Nginx retains the existing hard-bounded 256 MiB ephemeral poster cache.

### Operations

- Added Supervisor-managed `catalogue-api` and `catalogue-scheduler` processes.
- `status.py` reports v2 SQLite counts, search mode, bootstrap progress and lazy-detail cache size.
- `smoke_test.py` validates the paginated API, server-side search, lazy detail fetch, whitelisted poster path and private SQLite state.
- Existing Plex and Emby static catalogue compatibility paths are retained.

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
