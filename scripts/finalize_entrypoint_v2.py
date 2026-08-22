#!/usr/bin/env python3

from pathlib import Path
import sys


path = Path(sys.argv[1] if len(sys.argv) > 1 else "/app/entrypoint.sh")
source = path.read_text(encoding="utf-8")
lines = source.splitlines()
changed = 0
for index, line in enumerate(lines):
    if "/app/scripts/ultralight_jellyfin.py" in line and "/etc/cron.d/media-cron" in line:
        lines[index] = '    echo "# Jellyfin v2 sync is managed by the Supervisor catalogue scheduler" >>/etc/cron.d/media-cron'
        changed += 1

if changed:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Disabled {changed} legacy Jellyfin cron line(s) for catalogue v2")
else:
    print("No legacy Jellyfin cron line remained to disable")
