#!/usr/bin/env python3

import os
import re
from pathlib import Path
from urllib.parse import urlparse


OUTPUT = Path("/etc/nginx/poster-proxy.inc")
SAFE_TOKEN = re.compile(r"^[A-Za-z0-9._~:-]+$")


def quote_nginx(value):
    return value.replace("\\", "\\\\").replace("'", "\\'")


def build_config(url, token, max_width=320, quality=72):
    if not url or not token:
        return "location /poster/ { return 404; }\n"

    parsed = urlparse(url.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("JELLYFIN_URL must be an absolute http(s) URL")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("poster proxy currently requires JELLYFIN_URL without a base path/query")
    if not SAFE_TOKEN.fullmatch(token):
        raise ValueError("JELLYFIN_TOKEN contains characters unsafe for generated Nginx configuration")

    width = max(64, min(1000, int(max_width)))
    image_quality = max(30, min(95, int(quality)))
    upstream = f"{parsed.scheme}://{parsed.netloc}"
    ssl = ""
    if parsed.scheme == "https":
        ssl = f"    proxy_ssl_server_name on;\n    proxy_ssl_name {quote_nginx(parsed.hostname or '')};\n"

    authorization = (
        f'MediaBrowser Token="{token}", Client="Beyond Glimpse", Device="Server", '
        'DeviceId="beyond-glimpse-poster-proxy", Version="1.2"'
    )

    return f'''location ~ ^/poster/([0-9A-Za-z-]+)/([0-9A-Za-z._~-]+)\\.jpg$ {{
    set $poster_item_id $1;
    set $poster_image_tag $2;
    proxy_set_header Authorization '{quote_nginx(authorization)}';
    proxy_set_header Accept 'image/jpeg,image/*';
    proxy_set_header Host {quote_nginx(parsed.netloc)};
{ssl}    proxy_cache poster_cache;
    proxy_cache_key "$uri";
    proxy_cache_lock on;
    proxy_cache_lock_timeout 10s;
    proxy_cache_valid 200 30d;
    proxy_cache_valid 404 5m;
    proxy_cache_use_stale error timeout updating http_500 http_502 http_503 http_504;
    proxy_ignore_headers Set-Cookie Cache-Control Expires;
    proxy_hide_header Set-Cookie;
    expires 30d;
    proxy_pass {upstream}/Items/$poster_item_id/Images/Primary?tag=$poster_image_tag&maxWidth={width}&quality={image_quality}&format=jpg;
}}
'''


def main():
    config = build_config(
        os.environ.get("JELLYFIN_URL", ""),
        os.environ.get("JELLYFIN_TOKEN", ""),
        os.environ.get("POSTER_PROXY_MAX_WIDTH", "320"),
        os.environ.get("POSTER_PROXY_QUALITY", "72"),
    )
    OUTPUT.write_text(config, encoding="utf-8")
    print("Configured bounded Jellyfin poster proxy" if "/poster/" in config and "proxy_pass" in config else "Poster proxy disabled")


if __name__ == "__main__":
    main()
