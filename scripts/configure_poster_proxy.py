#!/usr/bin/env python3

import os
from pathlib import Path


OUTPUT = Path("/etc/nginx/poster-proxy.inc")


def build_config(url, token, max_width=320, quality=72):
    # Jellyfin credentials remain in the Python catalogue service environment and
    # are no longer rendered into Nginx configuration. We still require both to
    # enable the public poster route so an unconfigured deployment fails closed.
    if not url or not token:
        return "location /poster/ { return 404; }\n"

    # Validate operator values early even though the catalogue service ultimately
    # applies them to the Jellyfin request.
    max(64, min(1000, int(max_width)))
    max(30, min(95, int(quality)))

    return '''location ~ ^/poster/([0-9A-Za-z-]+)/([0-9A-Za-z._~-]+)\\.jpg$ {
    set $poster_item_id $1;
    set $poster_image_tag $2;
    proxy_cache poster_cache;
    proxy_cache_key "$uri";
    proxy_cache_lock on;
    proxy_cache_lock_timeout 10s;
    proxy_cache_valid 200 30d;
    proxy_cache_valid 404 5m;
    proxy_cache_use_stale error timeout updating http_500 http_502 http_503 http_504;
    proxy_ignore_headers Set-Cookie Cache-Control Expires;
    proxy_hide_header Set-Cookie;
    expires 30d;
    proxy_set_header Host 127.0.0.1;
    proxy_pass http://127.0.0.1:8091/internal/poster/$poster_item_id/$poster_image_tag.jpg;
}
'''


def main():
    config = build_config(
        os.environ.get("JELLYFIN_URL", ""),
        os.environ.get("JELLYFIN_TOKEN", ""),
        os.environ.get("POSTER_PROXY_MAX_WIDTH", "320"),
        os.environ.get("POSTER_PROXY_QUALITY", "72"),
    )
    OUTPUT.write_text(config, encoding="utf-8")
    if "proxy_pass" in config:
        print("Configured catalogue-whitelisted Jellyfin poster proxy")
    else:
        print("Poster proxy disabled")


if __name__ == "__main__":
    main()
