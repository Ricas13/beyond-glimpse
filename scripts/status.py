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


def file_size(path: Path):
    try:
        return path.stat().st_size
    except OSError:
        return 0


def collect(
    server,
    data_root=Path("/app/data"),
    state_root=Path("/app/state"),
    proxy_cache_root=Path("/var/cache/nginx/posters"),
):
    data_dir = data_root / server
    state_dir = state_root / server
    status = read_json(state_dir / "sync-status.json")

    poster_files, poster_bytes = tree_stats(data_dir / "posters")
    backdrop_files, backdrop_bytes = tree_stats(data_dir / "backdrops")
    detail_files, detail_bytes = tree_stats(data_dir / "details")
    state_files, state_bytes = tree_stats(state_dir)
    proxy_files, proxy_bytes = tree_stats(proxy_cache_root) if server == "jellyfin" else (0, 0)
    movies_json_bytes = file_size(data_dir / "movies.json")
    tvshows_json_bytes = file_size(data_dir / "tvshows.json")

    result = dict(status)
    result.update(
        {
            "server": server,
            "posterFiles": poster_files,
            "posterBytes": poster_bytes,
            "posterProxyCacheFiles": proxy_files,
            "posterProxyCacheBytes": proxy_bytes,
            "posterProxyCacheLimitBytes": 256 * 1024 * 1024 if server == "jellyfin" else 0,
            "backdropFiles": backdrop_files,
            "backdropBytes": backdrop_bytes,
            "detailFiles": detail_files,
            "detailBytes": detail_bytes,
            "stateFiles": state_files,
            "stateBytes": state_bytes,
            "moviesJsonBytes": movies_json_bytes,
            "tvShowsJsonBytes": tvshows_json_bytes,
            "publicBytes": poster_bytes + backdrop_bytes + detail_bytes + movies_json_bytes + tvshows_json_bytes,
        }
    )
    result["totalAppStorageBytes"] = result["publicBytes"] + state_bytes + proxy_bytes
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

    reconciliation = data.get("idReconciliation")
    if reconciliation:
        print(
            "ID reconciliation: "
            f"{reconciliation.get('currentIds', 0):,} current | "
            f"{reconciliation.get('deleted', 0):,} deleted | "
            f"{reconciliation.get('new', 0):,} new | "
            f"{reconciliation.get('moved', 0):,} moved"
        )
    if data.get("lastIdReconcile"):
        print(f"Last ID reconciliation: {data['lastIdReconcile']}")
    elif data.get("lastFullReconcile"):
        print(f"Last deletion reconciliation: {data['lastFullReconcile']}")
    if data.get("watermark"):
        print(f"Watermark: {data['watermark']}")

    index_bytes = (data.get("moviesJsonBytes") or 0) + (data.get("tvShowsJsonBytes") or 0)
    print(
        "Storage: "
        f"indexes {human_bytes(index_bytes)} | "
        f"details {human_bytes(data.get('detailBytes'))} ({data.get('detailFiles', 0):,} shards) | "
        f"local posters {human_bytes(data.get('posterBytes'))} | "
        f"backdrops {human_bytes(data.get('backdropBytes'))} | "
        f"state {human_bytes(data.get('stateBytes'))}"
    )
    if data.get("posterProxyCacheLimitBytes"):
        print(
            "Poster proxy cache: "
            f"{human_bytes(data.get('posterProxyCacheBytes'))} / "
            f"{human_bytes(data.get('posterProxyCacheLimitBytes'))} "
            f"({data.get('posterProxyCacheFiles', 0):,} cached files)"
        )
    print(f"Total app storage: {human_bytes(data.get('totalAppStorageBytes'))}")

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
