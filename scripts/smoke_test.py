#!/usr/bin/env python3

import argparse
import json
import sqlite3
import urllib.parse
import urllib.request
from pathlib import Path


STATE = Path("/app/state/jellyfin")
LOCAL_URL = "http://127.0.0.1"


def get(url, *, timeout=20):
    request = urllib.request.Request(url, headers={"User-Agent": "Beyond-Glimpse-Smoke-Test/2.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.headers, response.read()


def get_json(url, *, timeout=20):
    status, _, body = get(url, timeout=timeout)
    return status, json.loads(body.decode("utf-8"))


def check(name, fn, failures):
    try:
        detail = fn()
        print(f"PASS  {name}: {detail}")
    except Exception as exc:
        failures.append(name)
        print(f"FAIL  {name}: {exc}")


def main():
    parser = argparse.ArgumentParser(description="Validate a live Beyond Glimpse v2 Jellyfin deployment")
    parser.add_argument("--url", help="Optional public Traefik URL, for example https://library.example.com")
    args = parser.parse_args()
    failures = []
    sample = {}

    check("internal health", lambda: f"HTTP {get(f'{LOCAL_URL}/healthz')[0]}", failures)
    check("catalogue API health", lambda: f"HTTP {get(f'{LOCAL_URL}/api/status')[0]}", failures)

    def status_ok():
        status, payload = get_json(f"{LOCAL_URL}/api/status")
        if status != 200:
            raise RuntimeError(f"HTTP {status}")
        if not payload.get("bootstrapComplete"):
            raise RuntimeError(f"bootstrap incomplete (state={payload.get('state')})")
        total = int(payload.get("movies", 0)) + int(payload.get("tvShows", 0))
        if total <= 0:
            raise RuntimeError("catalogue is empty")
        return f"{payload.get('movies', 0):,} movies, {payload.get('tvShows', 0):,} TV shows, search={payload.get('search')}"
    check("catalogue ready", status_ok, failures)

    def page_ok():
        status, payload = get_json(f"{LOCAL_URL}/api/items?type=movie&limit=20&offset=0")
        if status != 200:
            raise RuntimeError(f"HTTP {status}")
        items = payload.get("items") or []
        if not items:
            status, payload = get_json(f"{LOCAL_URL}/api/items?type=tvshow&limit=20&offset=0")
            items = payload.get("items") or []
        if not items:
            raise RuntimeError("no API item available")
        if len(items) > 20:
            raise RuntimeError(f"page returned {len(items)} items for limit 20")
        sample.update(items[0])
        return f"{len(items)} items; hasMore={payload.get('hasMore')}"
    check("paginated browse API", page_ok, failures)

    def search_ok():
        title = str(sample.get("title") or "").strip()
        token = next((part for part in title.split() if len(part) >= 3), title[:3])
        if not token:
            return "skipped (sample title has no searchable token)"
        media_type = "movie"
        status, payload = get_json(
            f"{LOCAL_URL}/api/items?type={media_type}&limit=10&q={urllib.parse.quote(token)}"
        )
        if status != 200:
            raise RuntimeError(f"HTTP {status}")
        return f"query={token!r}, {len(payload.get('items') or [])} result(s)"
    check("server-side search", search_ok, failures)

    def detail_ok():
        item_id = str(sample.get("id") or "")
        if not item_id:
            raise RuntimeError("no sample item ID")
        status, payload = get_json(
            f"{LOCAL_URL}/api/item/{urllib.parse.quote(item_id, safe='')}", timeout=70
        )
        if status != 200 or payload.get("id") != item_id:
            raise RuntimeError(f"HTTP {status} or wrong item")
        return "lazy detail endpoint returned selected item"
    check("lazy single-item detail", detail_ok, failures)

    def poster_ok():
        if not sample.get("posterTag"):
            return "skipped (sample has no Primary image tag)"
        url = (
            f"{LOCAL_URL}/poster/{urllib.parse.quote(str(sample['id']), safe='')}/"
            f"{urllib.parse.quote(str(sample['posterTag']), safe='')}.jpg"
        )
        status, headers, body = get(url, timeout=70)
        content_type = headers.get("Content-Type", "")
        if status != 200 or not content_type.startswith("image/") or not body:
            raise RuntimeError(f"HTTP {status}, type={content_type!r}, bytes={len(body)}")
        return f"HTTP {status}, {content_type}, {len(body):,} bytes"
    check("whitelisted poster proxy", poster_ok, failures)

    def private_state_ok():
        db = STATE / "catalogue-v2.db"
        if not db.exists():
            raise RuntimeError("catalogue-v2.db is missing")
        connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            count = connection.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        finally:
            connection.close()
        if count <= 0:
            raise RuntimeError("catalogue-v2.db has no items")
        return f"{db.stat().st_size:,} bytes, {count:,} items"
    check("private SQLite catalogue", private_state_ok, failures)

    if args.url:
        public = args.url.rstrip("/")
        check("Traefik health", lambda: f"HTTP {get(f'{public}/healthz')[0]}", failures)
        check("Traefik homepage", lambda: f"HTTP {get(f'{public}/')[0]}", failures)
        check("Traefik catalogue API", lambda: f"HTTP {get(f'{public}/api/status')[0]}", failures)

    print()
    if failures:
        print(f"Smoke test FAILED: {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("Smoke test PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
