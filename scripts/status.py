#!/usr/bin/env python3

import argparse
import json
import os
from pathlib import Path


def human_bytes(value):
    value = float(value or 0)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024


def tree_stats(root: Path):
    files = 0
    total = 0
    if not root.exists():
        return files, total
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            total += path.stat().st_size
            files += 1
        except OSError:
            pass
    return files, total


def read_json(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def collect(server, data_root=Path("/app/data"), state_root=Path("/app/state")):
    data_dir = data_root / server
    state_dir = state_root / server
    status = read_json(state_dir / "sync-status.json")

    poster_files, poster_bytes = tree_stats(data_dir / "posters")
    backdrop_files, backdrop_bytes = tree_stats(data_dir / "backdrops")
    state_files, state_bytes = tree_stats(state_dir)

    result = dict(status)
    result.update(
        {
            "server": server,
            "posterFiles": poster_files,
            "posterBytes": poster_bytes,
            "backdropFiles": backdrop_files,
            "backdropBytes": backdrop_bytes,
            "stateFiles": state_files,
            "stateBytes": state_bytes,
            "publicBytes": poster_bytes
            + backdrop_bytes
            + (data_dir / "movies.json").stat().st_size if (data_dir / "movies.json").exists() else poster_bytes + backdrop_bytes,
        }
    )
    if (data_dir / "tvshows.json").exists():
        result["publicBytes"] += (data_dir / "tvshows.json").stat().st_size
    return result


def print_text(data):
    print(f"Beyond Glimpse status — {data.get('server', 'unknown')}")
    print(f"Sync: {data.get('state', 'never')} {data.get('mode') or ''}".rstrip())
    if data.get("completedAt"):
        print(f"Completed: {data['completedAt']} in {data.get('durationSeconds', '?')}s")
    if data.get("reason"):
        print(f"Reason: {data['reason']}")
    print(f"Catalogue: {data.get('movies', 0):,} movies | {data.get('tvShows', 0):,} TV shows")
    if data.get("changedRecords") is not None:
        print(f"Changed records: {data['changedRecords']:,}")
    if data.get("lastFullReconcile"):
        print(f"Last full reconcile: {data['lastFullReconcile']}")
    if data.get("watermark"):
        print(f"Watermark: {data['watermark']}")
    print(
        "Storage: "
        f"posters {human_bytes(data.get('posterBytes'))} ({data.get('posterFiles', 0):,} files) | "
        f"backdrops {human_bytes(data.get('backdropBytes'))} ({data.get('backdropFiles', 0):,} files) | "
        f"state {human_bytes(data.get('stateBytes'))}"
    )
    if data.get("state") == "failed":
        print(f"Exit code: {data.get('exitCode')}")
        for line in data.get("lastOutput") or []:
            print(f"  {line}")


def main():
    parser = argparse.ArgumentParser(description="Show Beyond Glimpse sync and storage status")
    parser.add_argument(
        "--server",
        choices=("jellyfin", "emby", "plex"),
        default=os.environ.get("PRIMARY_SERVER", "jellyfin").lower(),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    data = collect(args.server)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_text(data)


if __name__ == "__main__":
    main()
