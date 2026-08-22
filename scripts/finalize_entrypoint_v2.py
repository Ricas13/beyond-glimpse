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

source = "\n".join(lines) + "\n"
old_count = '            drawer_count=$(grep -c "<!-- Server Drawer Overlay" "$file" 2>/dev/null || echo "0")'
new_count = '''            drawer_count=$(grep -c "<!-- Server Drawer Overlay" "$file" 2>/dev/null || true)
            drawer_count=${drawer_count:-0}'''
if old_count in source:
    source = source.replace(old_count, new_count, 1)
    changed += 1

path.write_text(source, encoding="utf-8")
if changed:
    print(f"Applied {changed} Beyond Glimpse v2 entrypoint finalization change(s)")
else:
    print("Beyond Glimpse v2 entrypoint was already finalized")
