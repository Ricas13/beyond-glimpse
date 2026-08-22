# Security model

Beyond Glimpse is designed as a read-only public catalogue in front of a private media server. The browser receives exported metadata and optimized/on-demand artwork; it never receives the Jellyfin or Emby API token.

## Public and private data

- `/app/data` contains only catalogue JSON and public artwork/detail data.
- `/app/state` contains internal image state, the incremental SQLite catalogue, watermarks, sync telemetry and locks.
- Nginx explicitly blocks legacy/private state filenames and hidden temporary files beneath `/data` as defense in depth.
- The small public `/catalogue-status.json` startup file contains only a state/message/timestamp and no server URL, token or library metadata.

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

## Credentials and processes

- Jellyfin/Emby fetchers use environment-provided credentials rather than placing tokens in their Python command arguments.
- The new one-shot initial sync also invokes Jellyfin, Emby and Plex fetchers without token command arguments.
- Runtime Python dependencies are pinned in `requirements.txt` and installed without retaining the pip cache.
- Real `.env`, persistent `data/` and `state/` directories are excluded from Git.

Docker environment variables are still visible to sufficiently privileged users/processes on the Docker host. Treat host/root access as trusted administrative access and protect the `.env` file accordingly.

## Container privileges

The image retains Glimpse's combined Nginx + cron + Supervisor process model. Supervisor/cron/master startup currently expects root inside the container, while Nginx serves public requests through its normal unprivileged worker model.

Moving the complete container to a non-root runtime is deliberately deferred because doing it correctly requires changing cron execution, privileged-port binding and file ownership together. Adding a cosmetic `USER` directive would break scheduled syncs or Nginx startup without materially improving the trust boundary.

For Internet exposure, place Beyond Glimpse behind a maintained TLS reverse proxy such as Traefik and do not expose the Jellyfin/Emby server itself solely for catalogue browsing.
