#!/usr/bin/env python3

import argparse
from pathlib import Path


OLD_JELLYFIN = '    echo "$CRON_SCHEDULE root cd /app && JELLYFIN_EXCLUDE_LIBRARIES=\\\"$JELLYFIN_EXCLUDE_LIBRARIES\\\" $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --url \\\"$JELLYFIN_URL\\\" --token \\\"$JELLYFIN_TOKEN\\\" --output /app/data/jellyfin >> /var/log/cron.log 2>&1" >>/etc/cron.d/media-cron'
NEW_JELLYFIN = '    echo "$CRON_SCHEDULE root cd /app && JELLYFIN_URL=\\\"$JELLYFIN_URL\\\" JELLYFIN_TOKEN=\\\"$JELLYFIN_TOKEN\\\" JELLYFIN_EXCLUDE_LIBRARIES=\\\"$JELLYFIN_EXCLUDE_LIBRARIES\\\" JELLYFIN_USER_ID=\\\"$JELLYFIN_USER_ID\\\" POSTER_MAX_WIDTH=\\\"${POSTER_MAX_WIDTH:-500}\\\" BACKDROP_MAX_WIDTH=\\\"${BACKDROP_MAX_WIDTH:-1280}\\\" IMAGE_QUALITY=\\\"${IMAGE_QUALITY:-82}\\\" DOWNLOAD_BACKDROPS=\\\"${DOWNLOAD_BACKDROPS:-false}\\\" REQUEST_TIMEOUT=\\\"${REQUEST_TIMEOUT:-60}\\\" STATE_DIR=\\\"/app/state/jellyfin\\\" $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --output /app/data/jellyfin >> /var/log/cron.log 2>&1" >>/etc/cron.d/media-cron'

OLD_EMBY = '    echo "$CRON_SCHEDULE root cd /app && EMBY_EXCLUDE_LIBRARIES=\\\"$EMBY_EXCLUDE_LIBRARIES\\\" $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --url \\\"$EMBY_URL\\\" --token \\\"$EMBY_TOKEN\\\" --output /app/data/emby >> /var/log/cron.log 2>&1" >>/etc/cron.d/media-cron'
NEW_EMBY = '    echo "$CRON_SCHEDULE root cd /app && EMBY_URL=\\\"$EMBY_URL\\\" EMBY_TOKEN=\\\"$EMBY_TOKEN\\\" EMBY_EXCLUDE_LIBRARIES=\\\"$EMBY_EXCLUDE_LIBRARIES\\\" EMBY_USER_ID=\\\"$EMBY_USER_ID\\\" POSTER_MAX_WIDTH=\\\"${POSTER_MAX_WIDTH:-500}\\\" BACKDROP_MAX_WIDTH=\\\"${BACKDROP_MAX_WIDTH:-1280}\\\" IMAGE_QUALITY=\\\"${IMAGE_QUALITY:-82}\\\" DOWNLOAD_BACKDROPS=\\\"${DOWNLOAD_BACKDROPS:-false}\\\" REQUEST_TIMEOUT=\\\"${REQUEST_TIMEOUT:-60}\\\" STATE_DIR=\\\"/app/state/emby\\\" $PYTHON_PATH /app/scripts/jellyfin_data_fetcher.py --output /app/data/emby >> /var/log/cron.log 2>&1" >>/etc/cron.d/media-cron'


def prepare(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    changed = False
    replacements = ((OLD_JELLYFIN, NEW_JELLYFIN), (OLD_EMBY, NEW_EMBY))

    for old, new in replacements:
        if old in source:
            source = source.replace(old, new, 1)
            changed = True
        elif new not in source:
            raise RuntimeError("Could not find expected media sync cron line in entrypoint.sh")

    if changed:
        path.write_text(source, encoding="utf-8")
    return changed


def main():
    parser = argparse.ArgumentParser(description="Prepare Beyond Glimpse entrypoint for persistent large-library settings")
    parser.add_argument("path", nargs="?", default="/app/entrypoint.sh")
    args = parser.parse_args()
    path = Path(args.path)
    changed = prepare(path)
    print(f"Prepared {path}" if changed else f"Already prepared: {path}")


if __name__ == "__main__":
    main()
