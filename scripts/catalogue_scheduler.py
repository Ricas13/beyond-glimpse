#!/usr/bin/env python3

import os
import subprocess
import sys
import time
from pathlib import Path


PYTHON = sys.executable
SYNC_SCRIPT = Path("/app/scripts/catalogue_sync.py")
DEFAULT_INTERVAL = 600


def run_once():
    completed = subprocess.run(
        [PYTHON, "-u", str(SYNC_SCRIPT), "--incremental"],
        env=os.environ.copy(),
        check=False,
    )
    return completed.returncode


def main():
    if os.environ.get("PRIMARY_SERVER", "jellyfin").strip().lower() != "jellyfin":
        print("Catalogue scheduler disabled: PRIMARY_SERVER is not jellyfin", flush=True)
        return 0

    interval = max(60, int(os.environ.get("SYNC_INTERVAL_SECONDS", DEFAULT_INTERVAL)))
    initial_delay = max(30, int(os.environ.get("SYNC_SCHEDULER_INITIAL_DELAY", "90")))
    print(
        f"Catalogue scheduler active: incremental check every {interval}s "
        f"after {initial_delay}s initial delay",
        flush=True,
    )
    time.sleep(initial_delay)

    while True:
        started = time.monotonic()
        code = run_once()
        if code:
            print(f"Scheduled catalogue sync exited with code {code}", flush=True)
        elapsed = time.monotonic() - started
        time.sleep(max(30, interval - elapsed))


if __name__ == "__main__":
    raise SystemExit(main())
