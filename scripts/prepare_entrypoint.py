#!/usr/bin/env python3

import argparse
from pathlib import Path


OLD_JELLYFIN = '    echo "$CRON_SCHEDULE root cd /app && JELLYFIN_EXCLUDE_LIBRARIES=\\\"$JELLYFIN_EXCLUDE_LIBRARIES\\\" $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --url \\\"$JELLYFIN_URL\\\" --token \\\"$JELLYFIN_TOKEN\\\" --output /app/data/jellyfin >> /var/log/cron.log 2>&1" >>/etc/cron.d/media-cron'
PREVIOUS_JELLYFIN = '    echo "$CRON_SCHEDULE root cd /app && JELLYFIN_URL=\\\"$JELLYFIN_URL\\\" JELLYFIN_TOKEN=\\\"$JELLYFIN_TOKEN\\\" JELLYFIN_EXCLUDE_LIBRARIES=\\\"$JELLYFIN_EXCLUDE_LIBRARIES\\\" JELLYFIN_USER_ID=\\\"$JELLYFIN_USER_ID\\\" POSTER_MAX_WIDTH=\\\"${POSTER_MAX_WIDTH:-500}\\\" BACKDROP_MAX_WIDTH=\\\"${BACKDROP_MAX_WIDTH:-1280}\\\" IMAGE_QUALITY=\\\"${IMAGE_QUALITY:-82}\\\" DOWNLOAD_BACKDROPS=\\\"${DOWNLOAD_BACKDROPS:-false}\\\" REQUEST_TIMEOUT=\\\"${REQUEST_TIMEOUT:-60}\\\" STATE_DIR=\\\"/app/state/jellyfin\\\" $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --output /app/data/jellyfin >> /var/log/cron.log 2>&1" >>/etc/cron.d/media-cron'
INCREMENTAL_JELLYFIN = '    echo "$CRON_SCHEDULE root cd /app && JELLYFIN_URL=\\\"$JELLYFIN_URL\\\" JELLYFIN_TOKEN=\\\"$JELLYFIN_TOKEN\\\" JELLYFIN_EXCLUDE_LIBRARIES=\\\"$JELLYFIN_EXCLUDE_LIBRARIES\\\" JELLYFIN_USER_ID=\\\"$JELLYFIN_USER_ID\\\" PAGE_SIZE=\\\"${PAGE_SIZE:-500}\\\" POSTER_MAX_WIDTH=\\\"${POSTER_MAX_WIDTH:-500}\\\" BACKDROP_MAX_WIDTH=\\\"${BACKDROP_MAX_WIDTH:-1280}\\\" IMAGE_QUALITY=\\\"${IMAGE_QUALITY:-82}\\\" DOWNLOAD_BACKDROPS=\\\"${DOWNLOAD_BACKDROPS:-false}\\\" REQUEST_TIMEOUT=\\\"${REQUEST_TIMEOUT:-60}\\\" INCREMENTAL_SYNC=\\\"${INCREMENTAL_SYNC:-true}\\\" FULL_RECONCILE_HOURS=\\\"${FULL_RECONCILE_HOURS:-24}\\\" SYNC_OVERLAP_SECONDS=\\\"${SYNC_OVERLAP_SECONDS:-300}\\\" FORCE_FULL_SYNC=\\\"${FORCE_FULL_SYNC:-false}\\\" STATE_DIR=\\\"/app/state/jellyfin\\\" $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --output /app/data/jellyfin >> /var/log/cron.log 2>&1" >>/etc/cron.d/media-cron'
CURRENT_JELLYFIN = '    echo "$CRON_SCHEDULE root cd /app && JELLYFIN_URL=\\\"$JELLYFIN_URL\\\" JELLYFIN_TOKEN=\\\"$JELLYFIN_TOKEN\\\" JELLYFIN_EXCLUDE_LIBRARIES=\\\"$JELLYFIN_EXCLUDE_LIBRARIES\\\" JELLYFIN_USER_ID=\\\"$JELLYFIN_USER_ID\\\" PAGE_SIZE=\\\"${PAGE_SIZE:-500}\\\" POSTER_MAX_WIDTH=\\\"${POSTER_MAX_WIDTH:-500}\\\" BACKDROP_MAX_WIDTH=\\\"${BACKDROP_MAX_WIDTH:-1280}\\\" IMAGE_QUALITY=\\\"${IMAGE_QUALITY:-82}\\\" DOWNLOAD_BACKDROPS=\\\"${DOWNLOAD_BACKDROPS:-false}\\\" REQUEST_TIMEOUT=\\\"${REQUEST_TIMEOUT:-60}\\\" INCREMENTAL_SYNC=\\\"${INCREMENTAL_SYNC:-true}\\\" FULL_RECONCILE_HOURS=\\\"${FULL_RECONCILE_HOURS:-24}\\\" SYNC_OVERLAP_SECONDS=\\\"${SYNC_OVERLAP_SECONDS:-300}\\\" FORCE_FULL_SYNC=\\\"${FORCE_FULL_SYNC:-false}\\\" STATE_DIR=\\\"/app/state/jellyfin\\\" $PYTHON_PATH /app/scripts/sync_runner.py --server-type jellyfin --state-dir /app/state/jellyfin --output-dir /app/data/jellyfin -- $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --output /app/data/jellyfin >> /var/log/cron.log 2>&1" >>/etc/cron.d/media-cron'
NEW_JELLYFIN = '    echo "$CRON_SCHEDULE root cd /app && JELLYFIN_URL=\\\"$JELLYFIN_URL\\\" JELLYFIN_TOKEN=\\\"$JELLYFIN_TOKEN\\\" JELLYFIN_EXCLUDE_LIBRARIES=\\\"$JELLYFIN_EXCLUDE_LIBRARIES\\\" JELLYFIN_USER_ID=\\\"$JELLYFIN_USER_ID\\\" PAGE_SIZE=\\\"${PAGE_SIZE:-500}\\\" BACKDROP_MAX_WIDTH=\\\"${BACKDROP_MAX_WIDTH:-1280}\\\" IMAGE_QUALITY=\\\"${IMAGE_QUALITY:-82}\\\" DOWNLOAD_BACKDROPS=\\\"${DOWNLOAD_BACKDROPS:-false}\\\" REQUEST_TIMEOUT=\\\"${REQUEST_TIMEOUT:-60}\\\" INCREMENTAL_SYNC=\\\"${INCREMENTAL_SYNC:-true}\\\" FULL_RECONCILE_HOURS=\\\"${FULL_RECONCILE_HOURS:-24}\\\" SYNC_OVERLAP_SECONDS=\\\"${SYNC_OVERLAP_SECONDS:-300}\\\" FORCE_FULL_SYNC=\\\"${FORCE_FULL_SYNC:-false}\\\" STATE_DIR=\\\"/app/state/jellyfin\\\" $PYTHON_PATH /app/scripts/sync_runner.py --server-type jellyfin --state-dir /app/state/jellyfin --output-dir /app/data/jellyfin -- $PYTHON_PATH /app/scripts/ultralight_jellyfin.py --output /app/data/jellyfin >> /var/log/cron.log 2>&1" >>/etc/cron.d/media-cron'

OLD_EMBY = '    echo "$CRON_SCHEDULE root cd /app && EMBY_EXCLUDE_LIBRARIES=\\\"$EMBY_EXCLUDE_LIBRARIES\\\" $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --url \\\"$EMBY_URL\\\" --token \\\"$EMBY_TOKEN\\\" --output /app/data/emby >> /var/log/cron.log 2>&1" >>/etc/cron.d/media-cron'
PREVIOUS_EMBY = '    echo "$CRON_SCHEDULE root cd /app && EMBY_URL=\\\"$EMBY_URL\\\" EMBY_TOKEN=\\\"$EMBY_TOKEN\\\" EMBY_EXCLUDE_LIBRARIES=\\\"$EMBY_EXCLUDE_LIBRARIES\\\" EMBY_USER_ID=\\\"$EMBY_USER_ID\\\" POSTER_MAX_WIDTH=\\\"${POSTER_MAX_WIDTH:-500}\\\" BACKDROP_MAX_WIDTH=\\\"${BACKDROP_MAX_WIDTH:-1280}\\\" IMAGE_QUALITY=\\\"${IMAGE_QUALITY:-82}\\\" DOWNLOAD_BACKDROPS=\\\"${DOWNLOAD_BACKDROPS:-false}\\\" REQUEST_TIMEOUT=\\\"${REQUEST_TIMEOUT:-60}\\\" STATE_DIR=\\\"/app/state/emby\\\" $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --output /app/data/emby >> /var/log/cron.log 2>&1" >>/etc/cron.d/media-cron'
INCREMENTAL_EMBY = '    echo "$CRON_SCHEDULE root cd /app && EMBY_URL=\\\"$EMBY_URL\\\" EMBY_TOKEN=\\\"$EMBY_TOKEN\\\" EMBY_EXCLUDE_LIBRARIES=\\\"$EMBY_EXCLUDE_LIBRARIES\\\" EMBY_USER_ID=\\\"$EMBY_USER_ID\\\" PAGE_SIZE=\\\"${PAGE_SIZE:-500}\\\" POSTER_MAX_WIDTH=\\\"${POSTER_MAX_WIDTH:-500}\\\" BACKDROP_MAX_WIDTH=\\\"${BACKDROP_MAX_WIDTH:-1280}\\\" IMAGE_QUALITY=\\\"${IMAGE_QUALITY:-82}\\\" DOWNLOAD_BACKDROPS=\\\"${DOWNLOAD_BACKDROPS:-false}\\\" REQUEST_TIMEOUT=\\\"${REQUEST_TIMEOUT:-60}\\\" STATE_DIR=\\\"/app/state/emby\\\" $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --output /app/data/emby >> /var/log/cron.log 2>&1" >>/etc/cron.d/media-cron'
NEW_EMBY = '    echo "$CRON_SCHEDULE root cd /app && EMBY_URL=\\\"$EMBY_URL\\\" EMBY_TOKEN=\\\"$EMBY_TOKEN\\\" EMBY_EXCLUDE_LIBRARIES=\\\"$EMBY_EXCLUDE_LIBRARIES\\\" EMBY_USER_ID=\\\"$EMBY_USER_ID\\\" PAGE_SIZE=\\\"${PAGE_SIZE:-500}\\\" POSTER_MAX_WIDTH=\\\"${POSTER_MAX_WIDTH:-500}\\\" BACKDROP_MAX_WIDTH=\\\"${BACKDROP_MAX_WIDTH:-1280}\\\" IMAGE_QUALITY=\\\"${IMAGE_QUALITY:-82}\\\" DOWNLOAD_BACKDROPS=\\\"${DOWNLOAD_BACKDROPS:-false}\\\" REQUEST_TIMEOUT=\\\"${REQUEST_TIMEOUT:-60}\\\" STATE_DIR=\\\"/app/state/emby\\\" $PYTHON_PATH /app/scripts/sync_runner.py --server-type emby --state-dir /app/state/emby --output-dir /app/data/emby -- $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --output /app/data/emby >> /var/log/cron.log 2>&1" >>/etc/cron.d/media-cron'

INITIAL_JELLYFIN_OLD = '    JELLYFIN_EXCLUDE_LIBRARIES="$JELLYFIN_EXCLUDE_LIBRARIES" $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --url "$JELLYFIN_URL" --token "$JELLYFIN_TOKEN" --output /app/data/jellyfin'
INITIAL_JELLYFIN_CURRENT = '    JELLYFIN_EXCLUDE_LIBRARIES="$JELLYFIN_EXCLUDE_LIBRARIES" STATE_DIR="/app/state/jellyfin" $PYTHON_PATH /app/scripts/sync_runner.py --server-type jellyfin --state-dir /app/state/jellyfin --output-dir /app/data/jellyfin -- $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --output /app/data/jellyfin'
INITIAL_JELLYFIN_NEW = '    JELLYFIN_EXCLUDE_LIBRARIES="$JELLYFIN_EXCLUDE_LIBRARIES" STATE_DIR="/app/state/jellyfin" $PYTHON_PATH /app/scripts/sync_runner.py --server-type jellyfin --state-dir /app/state/jellyfin --output-dir /app/data/jellyfin -- $PYTHON_PATH /app/scripts/ultralight_jellyfin.py --output /app/data/jellyfin'

INITIAL_EMBY_OLD = '    EMBY_EXCLUDE_LIBRARIES="$EMBY_EXCLUDE_LIBRARIES" $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --url "$EMBY_URL" --token "$EMBY_TOKEN" --output /app/data/emby'
INITIAL_EMBY_NEW = '    EMBY_EXCLUDE_LIBRARIES="$EMBY_EXCLUDE_LIBRARIES" STATE_DIR="/app/state/emby" $PYTHON_PATH /app/scripts/sync_runner.py --server-type emby --state-dir /app/state/emby --output-dir /app/data/emby -- $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --output /app/data/emby'

PROXY_MARKER = 'echo "Running initial data fetch"'
PROXY_SETUP_MARKER = '# Configure the private Jellyfin poster proxy before Nginx starts.'
PROXY_SETUP = '''# Configure the private Jellyfin poster proxy before Nginx starts.
$PYTHON_PATH /app/scripts/configure_poster_proxy.py
nginx -t

echo "Running initial data fetch"'''

SYNC_END_MARKER = '# Make sure the data directory is accessible by nginx'
BACKGROUND_SYNC_REPLACEMENT = '''echo "Initial catalogue sync will run in the background under Supervisor"
printf '%s\\n' '{"state":"starting","message":"Catalogue startup is beginning."}' > /app/web/catalogue-status.json
chown www-data:www-data /app/web/catalogue-status.json 2>/dev/null || true

'''

APP_TITLE_OLD = 'APP_TITLE=${APP_TITLE:-"Glimpse"}'
APP_TITLE_NEW = 'APP_TITLE=${APP_TITLE:-"Beyond Glimpse"}'


def replace_one(source, candidates, new):
    if new in source:
        return source, False
    for old in candidates:
        if old in source:
            return source.replace(old, new, 1), True
    raise RuntimeError("Could not find expected media sync cron line in entrypoint.sh")


def replace_optional_many(source, candidates, new):
    if new in source:
        return source, False
    for old in candidates:
        if old in source:
            return source.replace(old, new, 1), True
    return source, False


def replace_initial_sync_block(source):
    if BACKGROUND_SYNC_REPLACEMENT in source:
        return source, False
    start = source.find(PROXY_MARKER)
    if start < 0:
        raise RuntimeError("Could not find initial data fetch marker in entrypoint.sh")
    end = source.find(SYNC_END_MARKER, start)
    if end < 0:
        raise RuntimeError("Could not find end of initial data fetch block in entrypoint.sh")
    return source[:start] + BACKGROUND_SYNC_REPLACEMENT + source[end:], True


def prepare(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    source, jellyfin_changed = replace_one(
        source,
        (CURRENT_JELLYFIN, INCREMENTAL_JELLYFIN, PREVIOUS_JELLYFIN, OLD_JELLYFIN),
        NEW_JELLYFIN,
    )
    source, emby_changed = replace_one(
        source,
        (INCREMENTAL_EMBY, PREVIOUS_EMBY, OLD_EMBY),
        NEW_EMBY,
    )
    source, initial_jellyfin_changed = replace_optional_many(
        source,
        (INITIAL_JELLYFIN_CURRENT, INITIAL_JELLYFIN_OLD),
        INITIAL_JELLYFIN_NEW,
    )
    source, initial_emby_changed = replace_optional_many(source, (INITIAL_EMBY_OLD,), INITIAL_EMBY_NEW)

    proxy_changed = False
    if PROXY_SETUP_MARKER not in source:
        if PROXY_MARKER not in source:
            raise RuntimeError("Could not find initial data fetch marker in entrypoint.sh")
        source = source.replace(PROXY_MARKER, PROXY_SETUP, 1)
        proxy_changed = True

    source, background_changed = replace_initial_sync_block(source)

    branding_changed = False
    replacements = (
        (APP_TITLE_OLD, APP_TITLE_NEW),
        ('"name": "Glimpse Media Viewer"', '"name": "$app_title"'),
        ('"short_name": "Glimpse"', '"short_name": "$app_title"'),
        (
            '"description": "A sleek, responsive web application for browsing your Plex/Jellyfin/Emby media server"',
            '"description": "A fast, storage-efficient media catalogue powered by Beyond Glimpse"',
        ),
    )
    for old, new in replacements:
        if old in source:
            source = source.replace(old, new)
            branding_changed = True

    changed = (
        jellyfin_changed
        or emby_changed
        or initial_jellyfin_changed
        or initial_emby_changed
        or proxy_changed
        or background_changed
        or branding_changed
    )
    if changed:
        path.write_text(source, encoding="utf-8")
    return changed


def main():
    parser = argparse.ArgumentParser(description="Prepare Beyond Glimpse entrypoint for production")
    parser.add_argument("path", nargs="?", default="/app/entrypoint.sh")
    args = parser.parse_args()
    path = Path(args.path)
    changed = prepare(path)
    print(f"Prepared {path}" if changed else f"Already prepared: {path}")


if __name__ == "__main__":
    main()
