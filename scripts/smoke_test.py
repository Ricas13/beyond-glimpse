#!/usr/bin/env python3

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


DATA = Path("/app/data/jellyfin")
STATE = Path("/app/state/jellyfin")
LOCAL_URL = "http://127.0.0.1"


def get(url, *, timeout=10):
    request = urllib.request.Request(url, headers={"User-Agent": "Beyond-Glimpse-Smoke-Test/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.headers, response.read()


def check(name, fn, failures):
    try:
        detail = fn()
        print(f"PASS  {name}: {detail}")
    except Exception as exc:
        failures.append(name)
        print(f"FAIL  {name}: {exc}")


def load_index(name):
    path = DATA / name
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError(f"{path} is not a JSON array")
    return payload


def shard_key(item_id):
    value = str(item_id or "").lower()
    if len(value) >= 2 and all(ch in "0123456789abcdef" for ch in value[:2]):
        return value[:2]
    return "zz"


def main():
    parser = argparse.ArgumentParser(description="Validate a live Beyond Glimpse Jellyfin deployment")
    parser.add_argument("--url", help="Optional public Traefik URL, for example https://discover.example.com")
    args = parser.parse_args()
    failures = []

    check(
        "internal health",
        lambda: f"HTTP {get(f'{LOCAL_URL}/healthz')[0]}",
        failures,
    )

    status_path = Path("/app/web/catalogue-status.json")
    def catalogue_status():
        status = json.loads(status_path.read_text(encoding="utf-8"))
        state = status.get("state")
        if state != "ready":
            raise RuntimeError(f"catalogue state is {state!r}")
        return state
    check("catalogue ready", catalogue_status, failures)

    indexes = {}
    def indexes_ok():
        indexes["movies"] = load_index("movies.json")
        indexes["tvshows"] = load_index("tvshows.json")
        total = len(indexes["movies"]) + len(indexes["tvshows"])
        if total == 0:
            raise RuntimeError("both catalogue indexes are empty")
        return f"{len(indexes['movies']):,} movies, {len(indexes['tvshows']):,} TV shows"
    check("compact indexes", indexes_ok, failures)

    def detail_ok():
        candidates = [("movies", item) for item in indexes.get("movies", [])]
        candidates += [("tvshows", item) for item in indexes.get("tvshows", [])]
        if not candidates:
            raise RuntimeError("no item available to test")
        plural, item = candidates[0]
        item_id = str(item.get("id", ""))
        path = DATA / "details" / plural / f"{shard_key(item_id)}.json"
        shard = json.loads(path.read_text(encoding="utf-8"))
        if item_id not in shard:
            raise RuntimeError(f"{item_id} is missing from {path.name}")
        return path.name
    check("lazy detail shard", detail_ok, failures)

    def poster_ok():
        candidates = indexes.get("movies", []) + indexes.get("tvshows", [])
        item = next((value for value in candidates if value.get("posterTag")), None)
        if item is None:
            return "skipped (no poster-tagged item in catalogue)"
        url = (
            f"{LOCAL_URL}/poster/{urllib.parse.quote(str(item['id']), safe='')}/"
            f"{urllib.parse.quote(str(item['posterTag']), safe='')}.jpg"
        )
        status, headers, body = get(url, timeout=30)
        content_type = headers.get("Content-Type", "")
        if status != 200 or not content_type.startswith("image/") or not body:
            raise RuntimeError(f"HTTP {status}, type={content_type!r}, bytes={len(body)}")
        return f"HTTP {status}, {content_type}, {len(body):,} bytes"
    check("on-demand poster proxy", poster_ok, failures)

    def private_state_ok():
        db = STATE / "catalog.db"
        status = STATE / "sync-status.json"
        if not db.exists() or not status.exists():
            raise RuntimeError("catalog.db or sync-status.json is missing")
        return f"catalog.db {db.stat().st_size:,} bytes"
    check("private sync state", private_state_ok, failures)

    if args.url:
        public = args.url.rstrip("/")
        check("Traefik health", lambda: f"HTTP {get(f'{public}/healthz')[0]}", failures)
        check("Traefik homepage", lambda: f"HTTP {get(f'{public}/')[0]}", failures)

    print()
    if failures:
        print(f"Smoke test FAILED: {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("Smoke test PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
