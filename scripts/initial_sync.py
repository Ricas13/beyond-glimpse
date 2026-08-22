#!/usr/bin/env python3

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


APP_ROOT = Path("/app")
WEB_STATUS = APP_ROOT / "web" / "catalogue-status.json"
PYTHON = sys.executable


def utc_now_text():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def atomic_status(state, *, message=None, failed_server=None):
    payload = {
        "state": state,
        "updatedAt": utc_now_text(),
    }
    if message:
        payload["message"] = message
    if failed_server:
        payload["failedServer"] = failed_server

    tmp = WEB_STATUS.with_name(f".{WEB_STATUS.name}.tmp")
    WEB_STATUS.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(tmp, WEB_STATUS)


def configured(name):
    return bool(os.environ.get(f"{name}_URL") and os.environ.get(f"{name}_TOKEN"))


def command_for(server):
    if server == "jellyfin":
        # v2 builds only the lightweight browse/search inventory. Expensive item
        # detail metadata is fetched later, one selected item at a time, by the
        # local catalogue API.
        return [
            PYTHON,
            "-u",
            "/app/scripts/catalogue_sync.py",
            "--bootstrap",
        ]
    if server == "emby":
        return [
            PYTHON,
            "/app/scripts/sync_runner.py",
            "--server-type",
            "emby",
            "--state-dir",
            "/app/state/emby",
            "--output-dir",
            "/app/data/emby",
            "--",
            PYTHON,
            "/app/scripts/jellyfin_data_fetcher.py",
            "--output",
            "/app/data/emby",
        ]
    if server == "plex":
        return [
            PYTHON,
            "/app/scripts/plex_data_fetcher.py",
            "--output",
            "/app/data/plex",
        ]
    raise ValueError(server)


def main():
    servers = [name for name in ("jellyfin", "emby", "plex") if configured(name.upper())]
    if not servers:
        atomic_status("failed", message="No configured media server was available for initial sync.")
        return 1

    atomic_status(
        "syncing",
        message="Preparing catalogue. Jellyfin items become browsable as lightweight pages are indexed.",
    )
    print(f"Initial background sync starting for: {', '.join(servers)}", flush=True)

    for server in servers:
        print(f"Initial background sync: {server}", flush=True)
        completed = subprocess.run(command_for(server), env=os.environ.copy(), check=False)
        if completed.returncode != 0:
            atomic_status(
                "failed",
                message="Initial catalogue sync failed. Existing catalogue data, if any, was preserved.",
                failed_server=server,
            )
            print(f"Initial background sync failed for {server}: exit {completed.returncode}", flush=True)
            return completed.returncode

    atomic_status("ready", message="Catalogue is ready.")
    print("Initial background sync completed successfully", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
