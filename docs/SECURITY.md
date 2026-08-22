# Security model

Beyond Glimpse is a read-only public catalogue in front of a private media server. The browser never receives the Jellyfin/Emby/Plex API token.

## Jellyfin v2 public/private boundary

The active Jellyfin catalogue is stored only in:

```text
/app/state/jellyfin/catalogue-v2.db
```

It is outside the Nginx document root. The browser reaches it only through the localhost catalogue API proxied under `/api/`.

The API exposes a deliberately small read-only public surface:

- `/api/status`
- `/api/items`
- `/api/genres`
- `/api/item/<catalogue-id>`

The Python service listens on `127.0.0.1:8091`, not on a Docker-published port. Nginx remains the public container entry point.

The public `/catalogue-status.json` startup file contains only state/message/timestamp information and no token or server URL.

## Lazy details

Browse rows contain only lightweight catalogue fields. Rich details are fetched from Jellyfin for one selected catalogue item at a time and cached in private SQLite state.

`/api/item/<id>` first requires that the ID exists in the local public catalogue. An arbitrary Jellyfin ID therefore cannot be used as a general authenticated metadata proxy.

## Poster boundary

Public posters use:

```text
/poster/<item-id>/<image-tag>.jpg
```

Nginx sends the request to the localhost catalogue service. Before contacting Jellyfin, the service requires:

1. the item ID exists in the local public catalogue; and
2. the requested image tag exactly matches the current stored Primary image tag.

This closes the v1 theoretical guessed-ID poster-proxy gap.

Jellyfin credentials are no longer rendered into `/etc/nginx/poster-proxy.inc`. The Python service reads credentials from its process environment and performs the authenticated upstream request only after catalogue/tag validation.

Nginx still provides the hard-bounded 256 MiB poster cache. The browser service worker never stores `/poster/` or `/api/` responses in Cache Storage.

## Reconciliation safety

Periodic deletion/move detection uses a lightweight ID inventory. The local catalogue refuses destructive reconciliation when:

- Jellyfin returns zero IDs for an already non-empty catalogue; or
- the deletion fraction exceeds `RECONCILE_MAX_DELETE_FRACTION` (35% by default).

This favours retaining stale local catalogue rows over mass-deleting state after a transient or unexpectedly scoped Jellyfin response.

## Browser hardening

Nginx sends:

- `Content-Security-Policy`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`
- restrictive `Permissions-Policy`
- `Cross-Origin-Opener-Policy: same-origin`

The inherited Glimpse page still uses inline CSS/JavaScript, so the compatibility CSP permits `'unsafe-inline'` for script/style. Scripts, network connections, workers and manifests remain same-origin; objects/plugins and framing are blocked.

Beyond Glimpse's injected catalogue runtime renders media metadata with DOM APIs and `textContent`, not metadata interpolation through `innerHTML`.

## Credentials and processes

- Jellyfin/Emby fetchers receive credentials from environment variables rather than Python command arguments.
- The v2 initial bootstrap invokes `catalogue_sync.py` without token arguments.
- The generated Nginx poster configuration contains no Jellyfin token.
- Runtime Python dependencies are pinned in `requirements.txt`.
- Real `.env`, persistent `data/` and `state/` directories are excluded from Git.

Docker environment variables remain visible to sufficiently privileged users/processes on the Docker host. Protect `.env` and treat host/root access as trusted administrative access.

## Container privileges

The image retains the combined Nginx + cron + Supervisor process model inherited from Glimpse. Supervisor/cron/master startup currently expects root inside the container; Nginx serves public requests through its normal unprivileged worker model.

A correct full non-root conversion requires changing cron execution, privileged-port binding and file ownership together. A cosmetic `USER` directive is intentionally not used because it would break the process model without materially changing the host/root trust boundary.

For Internet exposure, use the supplied Traefik deployment or another maintained TLS reverse proxy and avoid publishing the Beyond Glimpse container port directly.
