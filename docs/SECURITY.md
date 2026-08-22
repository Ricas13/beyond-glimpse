# Security model

Beyond Glimpse is designed as a read-only public catalogue in front of a private media server. The browser receives exported metadata and optimized artwork; it never receives the Jellyfin or Emby API token.

## Public and private data

- `/app/data` contains only catalogue JSON and public artwork.
- `/app/state` contains internal image state, the incremental SQLite catalogue, watermarks, and sync locks.
- Nginx explicitly blocks legacy/private state filenames and hidden temporary files beneath `/data` as defense in depth.

## Browser hardening

The Nginx configuration sends:

- `Content-Security-Policy`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`
- a restrictive `Permissions-Policy`
- `Cross-Origin-Opener-Policy: same-origin`

The inherited Glimpse page still uses inline CSS and JavaScript, so the current CSP permits `'unsafe-inline'` for script and style compatibility. It nevertheless limits scripts, network connections, workers and manifests to the same origin, blocks plugins/objects, and prevents framing.

## Media metadata rendering

Beyond Glimpse's injected runtime replaces the catalogue, search, genre, missing-image and detail-modal paths that previously interpolated media metadata with `innerHTML`. Titles, genres, cast names, roles and user search text are rendered with DOM APIs and `textContent` instead.

## Container privileges

The image still inherits Glimpse's combined Nginx + cron + Supervisor process model. These services currently expect privileged startup inside the container. Moving to an unprivileged runtime should be treated as a separate architectural change rather than adding a cosmetic `USER` directive that breaks scheduled syncs or Nginx startup.

For Internet exposure, place Beyond Glimpse behind a maintained TLS reverse proxy and do not expose the Jellyfin/Emby server itself solely for catalogue browsing.
