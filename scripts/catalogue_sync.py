#!/usr/bin/env python3

import argparse
import fcntl
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from catalogue_core import (
    JellyfinClient,
    delete_items,
    get_meta,
    light_item_from_api,
    open_db,
    set_meta,
    upsert_light_item,
)


DEFAULT_BOOTSTRAP_PAGE_SIZE = 1000
DEFAULT_DELTA_PAGE_SIZE = 500
DEFAULT_ID_PAGE_SIZE = 2000
DEFAULT_ID_DETAIL_BATCH = 200
DEFAULT_RECONCILE_HOURS = 24
DEFAULT_OVERLAP_SECONDS = 300
MIN_LIGHT_PAGE_SIZE = 100
LOCK_PATH = Path("/app/state/jellyfin/catalogue-v2.lock")


def api_datetime(epoch):
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def is_read_timeout(exc):
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return True
    text = f"{exc.__class__.__name__}: {exc}".lower()
    current = exc.__cause__ or exc.__context__
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text += f" {current.__class__.__name__}: {current}".lower()
        current = current.__cause__ or current.__context__
    return "read timed out" in text or "readtimeouterror" in text


def config_signature(user_id, libraries):
    payload = {
        "user": user_id,
        "libraries": sorted(
            (str(lib["id"]), str(lib["media_type"])) for lib in libraries
        ),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def set_sync_state(connection, state, **extra):
    set_meta(connection, "sync_state", state)
    set_meta(connection, "sync_updated_at", int(time.time()))
    for key, value in extra.items():
        set_meta(connection, key, value)
    connection.commit()


def fetch_light_page(client, params, page_limit, floor=MIN_LIGHT_PAGE_SIZE):
    current = max(1, int(page_limit))
    floor = min(max(1, int(floor)), current)
    while True:
        params = dict(params)
        params["Limit"] = current
        try:
            return client.get_json("/Items", params=params), current
        except requests.RequestException as exc:
            if not is_read_timeout(exc):
                raise
            next_limit = max(floor, current // 2)
            if next_limit >= current:
                raise
            print(
                f"Lightweight Jellyfin page timed out at offset {params.get('StartIndex', 0)} "
                f"(limit {current}); retrying with {next_limit}",
                flush=True,
            )
            current = next_limit


def bootstrap(client, connection, *, reason="initial bootstrap"):
    user_id = client.resolve_user_id(connection)
    libraries = client.eligible_libraries()
    signature = config_signature(user_id, libraries)
    generation = int(time.time() * 1000)
    sync_server_time = client.get_server_time()
    page_limit = max(100, int(os.environ.get("CATALOGUE_BOOTSTRAP_PAGE_SIZE", DEFAULT_BOOTSTRAP_PAGE_SIZE)))

    set_sync_state(
        connection,
        "bootstrap",
        sync_reason=reason,
        bootstrap_complete="0",
        config_signature=signature,
        current_generation=generation,
        eligible_libraries=len(libraries),
        progress_items=0,
        progress_library="",
    )

    print(
        f"Catalogue v2 bootstrap: {len(libraries)} libraries, lightweight page target {page_limit}",
        flush=True,
    )
    total = 0

    for library_index, library in enumerate(libraries, start=1):
        start_index = 0
        current_limit = page_limit
        library_total = 0
        print(
            f"[{library_index}/{len(libraries)}] {library['name']} ({library['media_type']})",
            flush=True,
        )
        while True:
            params = client.light_params(
                user_id,
                library_id=library["id"],
                media_type=library["media_type"],
                start_index=start_index,
                limit=current_limit,
            )
            data, current_limit = fetch_light_page(client, params, current_limit)
            items = data.get("Items") or []
            if not items:
                break

            connection.execute("BEGIN IMMEDIATE")
            try:
                accepted = 0
                for item in items:
                    record = light_item_from_api(
                        item, library["id"], library["media_type"], generation
                    )
                    if record is None:
                        continue
                    upsert_light_item(connection, record)
                    accepted += 1
                total += accepted
                library_total += accepted
                start_index += len(items)
                set_meta(connection, "progress_items", total)
                set_meta(connection, "progress_library", library["name"])
                set_meta(connection, "progress_library_items", library_total)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

            print(
                f"  indexed {library_total:,} in library / {total:,} total "
                f"(last page {len(items)}, limit {current_limit})",
                flush=True,
            )
            # Jellyfin may cap pages below the requested limit. The empty page is
            # the reliable end-of-library signal, so deliberately keep paging.

    stale_ids = [
        row[0]
        for row in connection.execute(
            "SELECT id FROM items WHERE generation <> ?", (generation,)
        )
    ]
    connection.execute("BEGIN IMMEDIATE")
    try:
        removed = delete_items(connection, stale_ids)
        set_meta(connection, "bootstrap_complete", "1")
        set_meta(connection, "watermark", sync_server_time)
        set_meta(connection, "last_reconcile", int(time.time()))
        set_meta(connection, "last_bootstrap", int(time.time()))
        set_meta(connection, "sync_state", "ready")
        set_meta(connection, "sync_updated_at", int(time.time()))
        set_meta(connection, "progress_library", "")
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    movie_count = connection.execute(
        "SELECT COUNT(*) FROM items WHERE media_type='movie'"
    ).fetchone()[0]
    tv_count = connection.execute(
        "SELECT COUNT(*) FROM items WHERE media_type='tvshow'"
    ).fetchone()[0]
    print(
        f"Catalogue v2 bootstrap complete: {movie_count:,} movies, {tv_count:,} TV shows; "
        f"removed {removed:,} stale items",
        flush=True,
    )
    return total


def fetch_delta_for_library(client, connection, user_id, library, min_saved, generation):
    start_index = 0
    current_limit = max(50, int(os.environ.get("CATALOGUE_DELTA_PAGE_SIZE", DEFAULT_DELTA_PAGE_SIZE)))
    changed = 0
    while True:
        params = client.light_params(
            user_id,
            library_id=library["id"],
            media_type=library["media_type"],
            start_index=start_index,
            limit=current_limit,
            min_saved=min_saved,
        )
        data, current_limit = fetch_light_page(client, params, current_limit, floor=50)
        items = data.get("Items") or []
        if not items:
            break
        connection.execute("BEGIN IMMEDIATE")
        try:
            for item in items:
                record = light_item_from_api(
                    item, library["id"], library["media_type"], generation
                )
                if record is not None:
                    upsert_light_item(connection, record)
                    changed += 1
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        start_index += len(items)
    return changed


def fetch_inventory_ids(client, user_id, library):
    start_index = 0
    limit = max(500, int(os.environ.get("CATALOGUE_ID_PAGE_SIZE", DEFAULT_ID_PAGE_SIZE)))
    ids = []
    include_type = "Movie" if library["media_type"] == "movie" else "Series"
    while True:
        params = {
            "ParentId": library["id"],
            "StartIndex": start_index,
            "Limit": limit,
            "Recursive": "true",
            "IncludeItemTypes": include_type,
            "EnableTotalRecordCount": "false",
            "EnableImages": "false",
            "EnableUserData": "false",
            "UserId": user_id,
        }
        data = client.get_json("/Items", params=params)
        items = data.get("Items") or []
        if not items:
            break
        for item in items:
            item_id = item.get("Id")
            if item_id:
                ids.append(str(item_id))
        start_index += len(items)
    return ids


def refresh_inventory_items(client, connection, user_id, seen, refresh_ids, generation):
    batch_size = max(25, int(os.environ.get("CATALOGUE_ID_DETAIL_BATCH", DEFAULT_ID_DETAIL_BATCH)))
    refreshed = 0
    for offset in range(0, len(refresh_ids), batch_size):
        batch = refresh_ids[offset : offset + batch_size]
        params = client.light_params(user_id, ids=batch, start_index=0, limit=len(batch))
        data, _ = fetch_light_page(client, params, len(batch), floor=min(25, len(batch)))
        items = data.get("Items") or []
        connection.execute("BEGIN IMMEDIATE")
        try:
            for item in items:
                item_id = str(item.get("Id") or "")
                target = seen.get(item_id)
                if not target:
                    continue
                library_id, media_type = target
                record = light_item_from_api(item, library_id, media_type, generation)
                if record is not None:
                    upsert_light_item(connection, record)
                    refreshed += 1
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return refreshed


def reconcile(client, connection, user_id, libraries, generation):
    print("Starting ID-only deletion/move reconciliation", flush=True)
    seen = {}
    total_seen = 0
    for index, library in enumerate(libraries, start=1):
        ids = fetch_inventory_ids(client, user_id, library)
        total_seen += len(ids)
        for item_id in ids:
            seen[item_id] = (str(library["id"]), library["media_type"])
        print(
            f"  inventory {index}/{len(libraries)}: {library['name']} -> {len(ids):,} IDs",
            flush=True,
        )

    existing = {
        str(row["id"]): (str(row["library_id"]), row["media_type"])
        for row in connection.execute("SELECT id,library_id,media_type FROM items")
    }
    if existing and total_seen == 0:
        raise RuntimeError("reconciliation safety guard: Jellyfin returned zero IDs for a non-empty catalogue")

    deleted = sorted(set(existing) - set(seen))
    new = set(seen) - set(existing)
    moved = {
        item_id
        for item_id in set(existing).intersection(seen)
        if existing[item_id] != seen[item_id]
    }

    max_delete_fraction = float(os.environ.get("RECONCILE_MAX_DELETE_FRACTION", "0.35"))
    if existing and deleted and len(deleted) / len(existing) > max_delete_fraction:
        raise RuntimeError(
            f"reconciliation safety guard: refusing to delete {len(deleted):,}/{len(existing):,} "
            f"items ({len(deleted)/len(existing):.1%})"
        )

    refresh_ids = sorted(new | moved)
    refreshed = refresh_inventory_items(
        client, connection, user_id, seen, refresh_ids, generation
    )

    connection.execute("BEGIN IMMEDIATE")
    try:
        removed = delete_items(connection, deleted)
        set_meta(connection, "last_reconcile", int(time.time()))
        set_meta(connection, "last_reconcile_ids", total_seen)
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    print(
        f"ID reconciliation complete: {total_seen:,} current, {removed:,} deleted, "
        f"{len(new):,} new, {len(moved):,} moved, {refreshed:,} lightweight refreshes",
        flush=True,
    )
    return removed + refreshed


def incremental(client, connection):
    if get_meta(connection, "bootstrap_complete", "0") != "1":
        return bootstrap(client, connection, reason="catalogue database is new")

    user_id = client.resolve_user_id(connection)
    libraries = client.eligible_libraries()
    signature = config_signature(user_id, libraries)
    if signature != get_meta(connection, "config_signature"):
        return bootstrap(client, connection, reason="user/library configuration changed")

    watermark = int(get_meta(connection, "watermark", "0") or 0)
    if not watermark:
        return bootstrap(client, connection, reason="watermark is missing")

    overlap = max(0, int(os.environ.get("SYNC_OVERLAP_SECONDS", DEFAULT_OVERLAP_SECONDS)))
    min_saved = api_datetime(max(0, watermark - overlap))
    sync_server_time = client.get_server_time()
    generation = int(get_meta(connection, "current_generation", "0") or 0)

    set_sync_state(connection, "incremental", sync_reason=f"changes since {min_saved}")
    print(f"Incremental lightweight sync since {min_saved}", flush=True)
    changed = 0
    for library in libraries:
        count = fetch_delta_for_library(
            client, connection, user_id, library, min_saved, generation
        )
        if count:
            print(f"  {library['name']}: {count:,} changed candidates", flush=True)
        changed += count

    set_meta(connection, "watermark", sync_server_time)
    set_meta(connection, "last_incremental", int(time.time()))
    connection.commit()

    reconcile_hours = max(1, int(os.environ.get("RECONCILE_INTERVAL_HOURS", DEFAULT_RECONCILE_HOURS)))
    last_reconcile = int(get_meta(connection, "last_reconcile", "0") or 0)
    if int(time.time()) - last_reconcile >= reconcile_hours * 3600:
        reconcile(client, connection, user_id, libraries, generation)

    set_sync_state(connection, "ready", sync_reason="incremental complete")
    print(f"Incremental lightweight sync complete: {changed:,} changed candidates", flush=True)
    return changed


def run(mode):
    connection = open_db()
    try:
        client = JellyfinClient()
        if mode == "bootstrap":
            return bootstrap(client, connection)
        if mode == "incremental":
            return incremental(client, connection)
        if mode == "reconcile":
            user_id = client.resolve_user_id(connection)
            libraries = client.eligible_libraries()
            generation = int(get_meta(connection, "current_generation", "0") or 0)
            return reconcile(client, connection, user_id, libraries, generation)
        raise ValueError(mode)
    except Exception as exc:
        try:
            set_sync_state(connection, "failed", sync_error=str(exc)[:1000])
        except Exception:
            pass
        raise
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description="Beyond Glimpse v2 lightweight Jellyfin catalogue sync")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--bootstrap", action="store_true")
    group.add_argument("--reconcile", action="store_true")
    group.add_argument("--incremental", action="store_true")
    args = parser.parse_args()
    mode = "bootstrap" if args.bootstrap else "reconcile" if args.reconcile else "incremental"

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Catalogue sync already running; skipping this invocation", flush=True)
            return 0
        try:
            run(mode)
            return 0
        except (requests.RequestException, RuntimeError, OSError, ValueError) as exc:
            print(f"ERROR: catalogue v2 sync failed: {exc}", file=sys.stderr, flush=True)
            return 1
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


if __name__ == "__main__":
    raise SystemExit(main())
