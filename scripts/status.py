#!/usr/bin/env python3

import argparse
import json
import os
import sqlite3
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


def collect_v2(state_dir, proxy_cache_root):
    db = state_dir / "catalogue-v2.db"
    state_files, state_bytes = tree_stats(state_dir)
    proxy_files, proxy_bytes = tree_stats(proxy_cache_root)
    result = {
        "server": "jellyfin",
        "architecture": "catalogue-v2",
        "state": "never",
        "movies": 0,
        "tvShows": 0,
        "detailRows": 0,
        "search": "unknown",
        "stateFiles": state_files,
        "stateBytes": state_bytes,
        "databaseBytes": file_size(db),
        "posterProxyCacheFiles": proxy_files,
        "posterProxyCacheBytes": proxy_bytes,
        "posterProxyCacheLimitBytes": 256 * 1024 * 1024,
        "publicBytes": 0,
    }
    if db.exists():
        connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        try:
            meta = dict(connection.execute("SELECT key,value FROM meta"))
            result.update(
                {
                    "state": meta.get("sync_state", "starting"),
                    "bootstrapComplete": meta.get("bootstrap_complete") == "1",
                    "progressItems": int(meta.get("progress_items", "0") or 0),
                    "progressLibrary": meta.get("progress_library", ""),
                    "lastBootstrap": int(meta.get("last_bootstrap", "0") or 0),
                    "lastIncremental": int(meta.get("last_incremental", "0") or 0),
                    "lastReconcile": int(meta.get("last_reconcile", "0") or 0),
                    "watermark": int(meta.get("watermark", "0") or 0),
                    "search": "fts5" if meta.get("fts_enabled") == "1" else "like",
                    "syncError": meta.get("sync_error", ""),
                }
            )
            result["movies"] = connection.execute(
                "SELECT COUNT(*) FROM items WHERE media_type='movie'"
            ).fetchone()[0]
            result["tvShows"] = connection.execute(
                "SELECT COUNT(*) FROM items WHERE media_type='tvshow'"
            ).fetchone()[0]
            result["detailRows"] = connection.execute(
                "SELECT COUNT(*) FROM item_details"
            ).fetchone()[0]
        finally:
            connection.close()
    result["totalAppStorageBytes"] = state_bytes + proxy_bytes
    return result


def collect_legacy(server, data_root, state_root, proxy_cache_root):
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
            "architecture": "legacy-static",
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


def collect(server, data_root=Path("/app/data"), state_root=Path("/app/state"), proxy_cache_root=Path("/var/cache/nginx/posters")):
    if server == "jellyfin" and (state_root / "jellyfin" / "catalogue-v2.db").exists():
        return collect_v2(state_root / "jellyfin", proxy_cache_root)
    return collect_legacy(server, data_root, state_root, proxy_cache_root)


def print_text(data):
    print(f"Beyond Glimpse status — {data.get('server', 'unknown')} [{data.get('architecture', 'unknown')}]")
    print(f"Sync: {data.get('state', 'never')}")
    print(f"Catalogue: {data.get('movies', 0):,} movies | {data.get('tvShows', 0):,} TV shows")

    if data.get("architecture") == "catalogue-v2":
        print(
            f"Bootstrap: {'complete' if data.get('bootstrapComplete') else 'in progress'} | "
            f"progress {data.get('progressItems', 0):,} items"
            + (f" | {data.get('progressLibrary')}" if data.get('progressLibrary') else "")
        )
        print(
            f"Search: {data.get('search')} | cached rich details: {data.get('detailRows', 0):,}"
        )
        print(
            "Storage: "
            f"database {human_bytes(data.get('databaseBytes'))} | "
            f"state total {human_bytes(data.get('stateBytes'))}"
        )
        if data.get("syncError"):
            print(f"Last sync error: {data['syncError']}")
    else:
        index_bytes = (data.get("moviesJsonBytes") or 0) + (data.get("tvShowsJsonBytes") or 0)
        print(
            "Storage: "
            f"indexes {human_bytes(index_bytes)} | "
            f"details {human_bytes(data.get('detailBytes'))} | "
            f"local posters {human_bytes(data.get('posterBytes'))} | "
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
