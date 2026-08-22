#!/usr/bin/env python3

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


STATUS_SCHEMA_VERSION = 1


def utc_now_text():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def atomic_write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def parse_sync_line(status, line):
    match = re.search(r"Sync mode:\s+(full|incremental)\s+\((.*)\)", line)
    if match:
        status["mode"] = match.group(1)
        status["reason"] = match.group(2).strip()

    match = re.search(r"Incremental sync changed\s+(\d+)\s+catalogue records", line)
    if match:
        status["changedRecords"] = int(match.group(1))

    match = re.search(r"Removed\s+(\d+)\s+stale cached images", line)
    if match:
        status["staleImagesRemoved"] = int(match.group(1))

    match = re.search(r"Completed\s+(full|incremental)\s+sync:\s+(\d+)\s+movies,\s+(\d+)\s+TV shows", line)
    if match:
        status["mode"] = match.group(1)
        status["movies"] = int(match.group(2))
        status["tvShows"] = int(match.group(3))


def read_catalog_state(state_dir: Path):
    db_path = state_dir / "catalog.db"
    result = {}
    if not db_path.exists():
        return result

    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            rows = dict(connection.execute("SELECT key, value FROM meta"))
            result["watermark"] = rows.get("watermark")
            result["lastFullReconcile"] = rows.get("last_full_reconcile")
            result["catalogSchemaVersion"] = rows.get("schema_version")

            movie_count = connection.execute(
                "SELECT COUNT(*) FROM items WHERE media_type='movie'"
            ).fetchone()[0]
            tv_count = connection.execute(
                "SELECT COUNT(*) FROM items WHERE media_type='tvshow'"
            ).fetchone()[0]
            result["movies"] = int(movie_count)
            result["tvShows"] = int(tv_count)
        finally:
            connection.close()
    except sqlite3.Error as exc:
        result["catalogStateWarning"] = str(exc)[:300]

    try:
        result["catalogDbBytes"] = db_path.stat().st_size
    except OSError:
        pass
    return result


def file_size(path: Path):
    try:
        return path.stat().st_size
    except OSError:
        return 0


def public_file_snapshot(output_dir: Path):
    return {
        "moviesJsonBytes": file_size(output_dir / "movies.json"),
        "tvShowsJsonBytes": file_size(output_dir / "tvshows.json"),
    }


def run(server_type, state_dir: Path, output_dir: Path, command):
    started = time.monotonic()
    status_path = state_dir / "sync-status.json"
    status = {
        "schemaVersion": STATUS_SCHEMA_VERSION,
        "serverType": server_type,
        "state": "running",
        "startedAt": utc_now_text(),
        "completedAt": None,
        "durationSeconds": None,
        "exitCode": None,
        "mode": None,
        "reason": None,
        "changedRecords": None,
        "staleImagesRemoved": 0,
    }
    atomic_write_json(status_path, status)

    last_output = []
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    assert process.stdout is not None
    for line in process.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        parse_sync_line(status, line)
        stripped = line.strip()
        if stripped:
            last_output.append(stripped)
            if len(last_output) > 12:
                last_output.pop(0)

    exit_code = process.wait()
    status.update(read_catalog_state(state_dir))
    status.update(public_file_snapshot(output_dir))
    status["completedAt"] = utc_now_text()
    status["durationSeconds"] = round(time.monotonic() - started, 3)
    status["exitCode"] = exit_code
    status["state"] = "success" if exit_code == 0 else "failed"

    if exit_code != 0:
        status["lastOutput"] = last_output[-6:]

    atomic_write_json(status_path, status)
    print("[SYNC SUMMARY] " + json.dumps(status, ensure_ascii=False, separators=(",", ":")), flush=True)
    return exit_code


def main():
    parser = argparse.ArgumentParser(description="Run a Beyond Glimpse media sync with private status telemetry")
    parser.add_argument("--server-type", required=True, choices=("jellyfin", "emby"))
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("a sync command is required after --")

    state_dir = Path(args.state_dir)
    output_dir = Path(args.output_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    sys.exit(run(args.server_type, state_dir, output_dir, command))


if __name__ == "__main__":
    main()
