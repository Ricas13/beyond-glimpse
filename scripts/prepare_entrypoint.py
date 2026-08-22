#!/usr/bin/env python3

import argparse
from pathlib import Path


OLD_JELLYFIN = '    echo "$CRON_SCHEDULE root cd /app && JELLYFIN_EXCLUDE_LIBRARIES=\\\"$JELLYFIN_EXCLUDE_LIBRARIES\\\" $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --url \\\"$JELLYFIN_URL\\\" --token \\\"$JELLYFIN_TOKEN\\\" --output /app/data/jellyfin >> /var/log/cron.log 2>&1" >>/etc/cron.d/media-cron'
PREVIOUS_JELLYFIN = '    echo "$CRON_SCHEDULE root cd /app && JELLYFIN_URL=\\\"$JELLYFIN_URL\\\" JELLYFIN_TOKEN=\\\"$JELLYFIN_TOKEN\\\" JELLYFIN_EXCLUDE_LIBRARIES=\\\"$JELLYFIN_EXCLUDE_LIBRARIES\\\" JELLYFIN_USER_ID=\\\"$JELLYFIN_USER_ID\\\" POSTER_MAX_WIDTH=\\\"${POSTER_MAX_WIDTH:-500}\\\" BACKDROP_MAX_WIDTH=\\\"${BACKDROP_MAX_WIDTH:-1280}\\\" IMAGE_QUALITY=\\\"${IMAGE_QUALITY:-82}\\\" DOWNLOAD_BACKDROPS=\\\"${DOWNLOAD_BACKDROPS:-false}\\\" REQUEST_TIMEOUT=\\\"${REQUEST_TIMEOUT:-60}\\\" STATE_DIR=\\\"/app/state/jellyfin\\\" $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --output /app/data/jellyfin >> /var/log/cron.log 2>&1" >>/etc/cron.d/media-cron'
INCREMENTAL_JELLYFIN = '    echo "$CRON_SCHEDULE root cd /app && JELLYFIN_URL=\\\"$JELLYFIN_URL\\\" JELLYFIN_TOKEN=\\\"$JELLYFIN_TOKEN\\\" JELLYFIN_EXCLUDE_LIBRARIES=\\\"$JELLYFIN_EXCLUDE_LIBRARIES\\\" JELLYFIN_USER_ID=\\\"$JELLYFIN_USER_ID\\\" PAGE_SIZE=\\\"${PAGE_SIZE:-500}\\\" POSTER_MAX_WIDTH=\\\"${POSTER_MAX_WIDTH:-500}\\\" BACKDROP_MAX_WIDTH=\\\"${BACKDROP_MAX_WIDTH:-1280}\\\" IMAGE_QUALITY=\\\"${IMAGE_QUALITY:-82}\\\" DOWNLOAD_BACKDROPS=\\\"${DOWNLOAD_BACKDROPS:-false}\\\" REQUEST_TIMEOUT=\\\"${REQUEST_TIMEOUT:-60}\\\" INCREMENTAL_SYNC=\\\"${INCREMENTAL_SYNC:-true}\\\" FULL_RECONCILE_HOURS=\\\"${FULL_RECONCILE_HOURS:-24}\\\" SYNC_OVERLAP_SECONDS=\\\"${SYNC_OVERLAP_SECONDS:-300}\\\" FORCE_FULL_SYNC=\\\"${FORCE_FULL_SYNC:-false}\\\" STATE_DIR=\\\"/app/state/jellyfin\\\" $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --output /app/data/jellyfin >> /var/log/cron.log 2>&1" >>/etc/cron.d/media-cron'
NEW_JELLYFIN = '    echo "$CRON_SCHEDULE root cd /app && JELLYFIN_URL=\\\"$JELLYFIN_URL\\\" JELLYFIN_TOKEN=\\\"$JELLYFIN_TOKEN\\\" JELLYFIN_EXCLUDE_LIBRARIES=\\\"$JELLYFIN_EXCLUDE_LIBRARIES\\\" JELLYFIN_USER_ID=\\\"$JELLYFIN_USER_ID\\\" PAGE_SIZE=\\\"${PAGE_SIZE:-500}\\\" POSTER_MAX_WIDTH=\\\"${POSTER_MAX_WIDTH:-500}\\\" BACKDROP_MAX_WIDTH=\\\"${BACKDROP_MAX_WIDTH:-1280}\\\" IMAGE_QUALITY=\\\"${IMAGE_QUALITY:-82}\\\" DOWNLOAD_BACKDROPS=\\\"${DOWNLOAD_BACKDROPS:-false}\\\" REQUEST_TIMEOUT=\\\"${REQUEST_TIMEOUT:-60}\\\" INCREMENTAL_SYNC=\\\"${INCREMENTAL_SYNC:-true}\\\" FULL_RECONCILE_HOURS=\\\"${FULL_RECONCILE_HOURS:-24}\\\" SYNC_OVERLAP_SECONDS=\\\"${SYNC_OVERLAP_SECONDS:-300}\\\" FORCE_FULL_SYNC=\\\"${FORCE_FULL_SYNC:-false}\\\" STATE_DIR=\\\"/app/state/jellyfin\\\" $PYTHON_PATH /app/scripts/sync_runner.py --server-type jellyfin --state-dir /app/state/jellyfin --output-dir /app/data/jellyfin -- $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --output /app/data/jellyfin >> /var/log/cron.log 2>&1" >>/etc/cron.d/media-cron'

OLD_EMBY = '    echo "$CRON_SCHEDULE root cd /app && EMBY_EXCLUDE_LIBRARIES=\\\"$EMBY_EXCLUDE_LIBRARIES\\\" $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --url \\\"$EMBY_URL\\\" --token \\\"$EMBY_TOKEN\\\" --output /app/data/emby >> /var/log/cron.log 2>&1" >>/etc/cron.d/media-cron'
PREVIOUS_EMBY = '    echo "$CRON_SCHEDULE root cd /app && EMBY_URL=\\\"$EMBY_URL\\\" EMBY_TOKEN=\\\"$EMBY_TOKEN\\\" EMBY_EXCLUDE_LIBRARIES=\\\"$EMBY_EXCLUDE_LIBRARIES\\\" EMBY_USER_ID=\\\"$EMBY_USER_ID\\\" POSTER_MAX_WIDTH=\\\"${POSTER_MAX_WIDTH:-500}\\\" BACKDROP_MAX_WIDTH=\\\"${BACKDROP_MAX_WIDTH:-1280}\\\" IMAGE_QUALITY=\\\"${IMAGE_QUALITY:-82}\\\" DOWNLOAD_BACKDROPS=\\\"${DOWNLOAD_BACKDROPS:-false}\\\" REQUEST_TIMEOUT=\\\"${REQUEST_TIMEOUT:-60}\\\" STATE_DIR=\\\"/app/state/emby\\\" $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --output /app/data/emby >> /var/log/cron.log 2>&1" >>/etc/cron.d/media-cron'
INCREMENTAL_EMBY = '    echo "$CRON_SCHEDULE root cd /app && EMBY_URL=\\\"$EMBY_URL\\\" EMBY_TOKEN=\\\"$EMBY_TOKEN\\\" EMBY_EXCLUDE_LIBRARIES=\\\"$EMBY_EXCLUDE_LIBRARIES\\\" EMBY_USER_ID=\\\"$EMBY_USER_ID\\\" PAGE_SIZE=\\\"${PAGE_SIZE:-500}\\\" POSTER_MAX_WIDTH=\\\"${POSTER_MAX_WIDTH:-500}\\\" BACKDROP_MAX_WIDTH=\\\"${BACKDROP_MAX_WIDTH:-1280}\\\" IMAGE_QUALITY=\\\"${IMAGE_QUALITY:-82}\\\" DOWNLOAD_BACKDROPS=\\\"${DOWNLOAD_BACKDROPS:-false}\\\" REQUEST_TIMEOUT=\\\"${REQUEST_TIMEOUT:-60}\\\" STATE_DIR=\\\"/app/state/emby\\\" $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --output /app/data/emby >> /var/log/cron.log 2>&1" >>/etc/cron.d/media-cron'
NEW_EMBY = '    echo "$CRON_SCHEDULE root cd /app && EMBY_URL=\\\"$EMBY_URL\\\" EMBY_TOKEN=\\\"$EMBY_TOKEN\\\" EMBY_EXCLUDE_LIBRARIES=\\\"$EMBY_EXCLUDE_LIBRARIES\\\" EMBY_USER_ID=\\\"$EMBY_USER_ID\\\" PAGE_SIZE=\\\"${PAGE_SIZE:-500}\\\" POSTER_MAX_WIDTH=\\\"${POSTER_MAX_WIDTH:-500}\\\" BACKDROP_MAX_WIDTH=\\\"${BACKDROP_MAX_WIDTH:-1280}\\\" IMAGE_QUALITY=\\\"${IMAGE_QUALITY:-82}\\\" DOWNLOAD_BACKDROPS=\\\"${DOWNLOAD_BACKDROPS:-false}\\\" REQUEST_TIMEOUT=\\\"${REQUEST_TIMEOUT:-60}\\\" STATE_DIR=\\\"/app/state/emby\\\" $PYTHON_PATH /app/scripts/sync_runner.py --server-type emby --state-dir /app/state/emby --output-dir /app/data/emby -- $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --output /app/data/emby >> /var/log/cron.log 2>&1" >>/etc/cron.d/media-cron'

INITIAL_JELLYFIN_OLD = '    JELLYFIN_EXCLUDE_LIBRARIES="$JELLYFIN_EXCLUDE_LIBRARIES" $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --url "$JELLYFIN_URL" --token "$JELLYFIN_TOKEN" --output /app/data/jellyfin'
INITIAL_JELLYFIN_NEW = '    JELLYFIN_EXCLUDE_LIBRARIES="$JELLYFIN_EXCLUDE_LIBRARIES" STATE_DIR="/app/state/jellyfin" $PYTHON_PATH /app/scripts/sync_runner.py --server-type jellyfin --state-dir /app/state/jellyfin --output-dir /app/data/jellyfin -- $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --output /app/data/jellyfin'

INITIAL_EMBY_OLD = '    EMBY_EXCLUDE_LIBRARIES="$EMBY_EXCLUDE_LIBRARIES" $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --url "$EMBY_URL" --token "$EMBY_TOKEN" --output /app/data/emby'
INITIAL_EMBY_NEW = '    EMBY_EXCLUDE_LIBRARIES="$EMBY_EXCLUDE_LIBRARIES" STATE_DIR="/app/state/emby" $PYTHON_PATH /app/scripts/sync_runner.py --server-type emby --state-dir /app/state/emby --output-dir /app/data/emby -- $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --output /app/data/emby'


def replace_one(source, candidates, new):
    if new in source:
        return source, False
    for old in candidates:
        if old in source:
            return source.replace(old, new, 1), True
    raise RuntimeError("Could not find expected media sync cron line in entrypoint.sh")


def replace_optional(source, old, new):
    if new in source or old not in source:
        return source, False
    return source.replace(old, new, 1), True


def prepare(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    source, jellyfin_changed = replace_one(
        source,
        (INCREMENTAL_JELLYFIN, PREVIOUS_JELLYFIN, OLD_JELLYFIN),
        NEW_JELLYFIN,
    )
    source, emby_changed = replace_one(
        source,
        (INCREMENTAL_EMBY, PREVIOUS_EMBY, OLD_EMBY),
        NEW_EMBY,
    )
    source, initial_jellyfin_changed = replace_optional(source, INITIAL_JELLYFIN_OLD, INITIAL_JELLYFIN_NEW)
    source, initial_emby_changed = replace_optional(source, INITIAL_EMBY_OLD, INITIAL_EMBY_NEW)
    changed = jellyfin_changed or emby_changed or initial_jellyfin_changed or initial_emby_changed
    if changed:
        path.write_text(source, encoding="utf-8")
    return changed


def main():
    parser = argparse.ArgumentParser(description="Prepare Beyond Glimpse entrypoint for large-library sync and telemetry")
    parser.add_argument("path", nargs="?", default="/app/entrypoint.sh")
    args = parser.parse_args()
    path = Path(args.path)
    changed = prepare(path)
    print(f"Prepared {path}" if changed else f"Already prepared: {path}")


if __name__ == "__main__":
    main()
