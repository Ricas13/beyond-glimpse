#!/usr/bin/env python3

import json

import jellyfin_data_fetcher as base


DETAILS_FIELDS = (
    "summary",
    "rating",
    "studio",
    "actors",
    "duration",
    "contentRating",
    "leafCount",
    "childCount",
    "originallyAvailableAt",
    "tagline",
)
FULL_ITEM_FIELDS = (
    "Overview,Genres,People,Studios,DateCreated,RunTimeTicks,ProviderIds,"
    "ImageTags,BackdropImageTags,RecursiveItemCount,Taglines"
)
ID_RECONCILE_PAGE_SIZE = 2000
ID_DETAIL_BATCH_SIZE = 200

# Preserve the proven base incremental implementation and state machine. The
# ultra-light layer only changes the periodic deletion reconciliation path.
ORIGINAL_CHOOSE_SYNC_MODE = base.JellyfinDataFetcher.choose_sync_mode
ORIGINAL_RUN_INCREMENTAL_SYNC = base.JellyfinDataFetcher.run_incremental_sync
ORIGINAL_FINALIZE_STATE = base.JellyfinDataFetcher.finalize_state


def shard_key(item_id):
    value = str(item_id or "").lower()
    if len(value) >= 2 and all(ch in "0123456789abcdef" for ch in value[:2]):
        return value[:2]
    return "zz"


def ultralight_build_catalog_entry(self, item, media_type, library_id):
    media_info = self.process_media_item(item, media_type)
    item_id = media_info["id"]
    if not item_id:
        return None

    poster_tag = (item.get("ImageTags") or {}).get("Primary")
    backdrop_tags = item.get("BackdropImageTags") or []
    backdrop_tag = backdrop_tags[0] if backdrop_tags else None

    # Posters are deliberately not downloaded here. The browser requests a
    # tag-versioned /poster/<id>/<tag>.jpg URL, which Nginx fetches from Jellyfin
    # only when needed and stores in a hard-bounded proxy cache.
    if self.download_backdrops and backdrop_tag:
        backdrop_path = self.output_dir / "backdrops" / f"{media_type}s" / f"{item_id}.jpg"
        self.sync_image(
            item_id,
            "Backdrop",
            backdrop_tag,
            backdrop_path,
            self.backdrop_max_width,
            image_index=0,
        )

    return {
        "id": item_id,
        "library_id": str(library_id),
        "media_type": media_type,
        "media_json": json.dumps(media_info, ensure_ascii=False, separators=(",", ":")),
        "poster_tag": poster_tag,
        "backdrop_tag": backdrop_tag,
    }


def ultralight_populate_expected_image_keys(self, connection):
    self.expected_image_keys = set()
    if not self.download_backdrops:
        return

    for item_id, media_type, backdrop_tag in connection.execute(
        "SELECT id, media_type, backdrop_tag FROM items"
    ):
        if backdrop_tag:
            path = self.output_dir / "backdrops" / f"{media_type}s" / f"{item_id}.jpg"
            self.expected_image_keys.add(self.image_key(path))


def compact_index_item(media, poster_tag):
    return {
        "id": media.get("id", ""),
        "title": media.get("title", ""),
        "year": media.get("year", ""),
        "addedAt": media.get("addedAt", 0),
        "genres": media.get("genres") or [],
        "posterTag": poster_tag or "",
    }


def compact_detail_item(media):
    result = {"id": media.get("id", "")}
    for key in DETAILS_FIELDS:
        value = media.get(key)
        if value not in (None, "", [], 0):
            result[key] = value
    return result


def write_detail_shards(self, media_type, details):
    plural = f"{media_type}s"
    root = self.output_dir / "details" / plural
    root.mkdir(parents=True, exist_ok=True)
    self.set_permissions(root)

    grouped = {}
    for detail in details:
        grouped.setdefault(shard_key(detail.get("id")), []).append(detail)

    expected = set()
    for shard, items in grouped.items():
        path = root / f"{shard}.json"
        payload = {item["id"]: item for item in items}
        self.atomic_write_json(path, payload)
        expected.add(path.name)

    for path in root.glob("*.json"):
        if path.name not in expected:
            path.unlink(missing_ok=True)


def ultralight_write_public_catalogue(self, connection):
    counts = {}
    for media_type in ("movie", "tvshow"):
        rows = connection.execute(
            "SELECT media_json, poster_tag FROM items WHERE media_type = ? ORDER BY id",
            (media_type,),
        )
        index = []
        details = []
        for media_json, poster_tag in rows:
            media = json.loads(media_json)
            index.append(compact_index_item(media, poster_tag))
            details.append(compact_detail_item(media))

        filename = "movies.json" if media_type == "movie" else "tvshows.json"
        self.atomic_write_json(self.output_dir / filename, index)
        write_detail_shards(self, media_type, details)
        counts[media_type] = len(index)

    return counts["movie"], counts["tvshow"]


def ultralight_choose_sync_mode(self, connection, user_id, allowed_libraries, sync_started_at):
    mode, reason = ORIGINAL_CHOOSE_SYNC_MODE(
        self,
        connection,
        user_id,
        allowed_libraries,
        sync_started_at,
    )
    self._id_reconcile_due = False

    # The base engine historically used a full metadata rebuild to detect
    # deletions every N hours. Keep all genuine full-sync reasons intact, but
    # convert only that periodic maintenance pass into a cheap ID inventory.
    if mode == "full" and reason.startswith("periodic ") and "deletion reconciliation is due" in reason:
        self._id_reconcile_due = True
        return (
            "incremental",
            reason.replace("deletion reconciliation", "ID-only deletion reconciliation"),
        )
    return mode, reason


def fetch_library_ids(self, user_id, library_id, media_type):
    all_ids = []
    start_index = 0
    include_type = "Movie" if media_type == "movie" else "Series"
    page_size = max(self.page_size, ID_RECONCILE_PAGE_SIZE)

    while True:
        params = {
            "ParentId": library_id,
            "StartIndex": start_index,
            "Limit": page_size,
            "Recursive": "true",
            "IncludeItemTypes": include_type,
            "EnableTotalRecordCount": "false",
            "EnableImages": "false",
            "EnableUserData": "false",
            "UserId": user_id,
        }
        data = self.request_json("/Items", params=params)
        items = data.get("Items", [])
        if not items:
            break
        all_ids.extend(str(item["Id"]) for item in items if item.get("Id"))
        start_index += len(items)
        # Do not stop merely because Jellyfin returned fewer rows than requested:
        # servers may cap page sizes below our 2,000-ID preference. The next empty
        # page is the reliable end-of-inventory signal.

    return all_ids


def fetch_items_by_ids(self, user_id, item_ids):
    item_ids = list(dict.fromkeys(str(value) for value in item_ids if value))
    if not item_ids:
        return []

    results = []
    for offset in range(0, len(item_ids), ID_DETAIL_BATCH_SIZE):
        batch = item_ids[offset : offset + ID_DETAIL_BATCH_SIZE]
        params = {
            "Ids": ",".join(batch),
            "UserId": user_id,
            "Fields": FULL_ITEM_FIELDS,
            "EnableTotalRecordCount": "false",
            "EnableUserData": "false",
            "EnableImages": "true",
            "Limit": len(batch),
        }
        data = self.request_json("/Items", params=params)
        results.extend(data.get("Items", []))
    return results


def run_id_reconciliation(self, connection, user_id, allowed_libraries):
    seen = {}
    total_seen = 0
    for library in allowed_libraries:
        ids = fetch_library_ids(
            self,
            user_id,
            library["id"],
            library["media_type"],
        )
        total_seen += len(ids)
        for item_id in ids:
            seen[item_id] = (str(library["id"]), library["media_type"])

    existing = {
        str(item_id): (str(library_id), media_type)
        for item_id, library_id, media_type in connection.execute(
            "SELECT id, library_id, media_type FROM items"
        )
    }

    deleted_ids = sorted(set(existing) - set(seen))
    new_ids = set(seen) - set(existing)
    moved_ids = {
        item_id
        for item_id in set(seen).intersection(existing)
        if seen[item_id] != existing[item_id]
    }
    refresh_ids = sorted(new_ids | moved_ids)

    # Fetch full metadata only for inventory entries absent from the local
    # catalogue or whose library/type changed. Usually this list is empty.
    refreshed_entries = []
    if refresh_ids:
        returned = fetch_items_by_ids(self, user_id, refresh_ids)
        returned_ids = set()
        for item in returned:
            item_id = str(item.get("Id", ""))
            target = seen.get(item_id)
            if not item_id or target is None:
                continue
            returned_ids.add(item_id)
            library_id, media_type = target
            entry = self.build_catalog_entry(item, media_type, library_id)
            if entry:
                refreshed_entries.append(entry)

        missing = set(refresh_ids) - returned_ids
        if missing:
            print(f"Warning: ID reconciliation could not refetch {len(missing)} inventory items")

    changed_count = 0
    connection.execute("BEGIN IMMEDIATE")
    try:
        if deleted_ids:
            connection.executemany("DELETE FROM items WHERE id = ?", ((item_id,) for item_id in deleted_ids))
            changed_count += len(deleted_ids)

        for entry in refreshed_entries:
            if self.upsert_catalog_entry(connection, entry):
                changed_count += 1
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    print(
        "ID reconciliation: "
        f"{total_seen} current IDs, {len(deleted_ids)} deleted, "
        f"{len(new_ids)} new, {len(moved_ids)} moved"
    )
    return changed_count


def ultralight_run_incremental_sync(self, connection, user_id, allowed_libraries, watermark):
    changed_count = ORIGINAL_RUN_INCREMENTAL_SYNC(
        self,
        connection,
        user_id,
        allowed_libraries,
        watermark,
    )
    if getattr(self, "_id_reconcile_due", False):
        changed_count += run_id_reconciliation(self, connection, user_id, allowed_libraries)
    return changed_count


def ultralight_finalize_state(self, connection, user_id, allowed_libraries, sync_started_at, mode):
    ORIGINAL_FINALIZE_STATE(
        self,
        connection,
        user_id,
        allowed_libraries,
        sync_started_at,
        mode,
    )
    if getattr(self, "_id_reconcile_due", False):
        timestamp = self.format_api_datetime(sync_started_at)
        self.set_meta(connection, "last_full_reconcile", timestamp)
        self.set_meta(connection, "last_id_reconcile", timestamp)
        connection.commit()


def activate_ultralight_mode():
    # Schema v2 is the compact-index/detail-shard format introduced by PR #7.
    # The ID-only reconciliation changes maintenance behavior, not public schema,
    # so upgrades do not force another full metadata rebuild.
    base.CATALOG_SCHEMA_VERSION = "2-ultralight"
    base.APP_VERSION = "1.3"
    base.JellyfinDataFetcher.build_catalog_entry = ultralight_build_catalog_entry
    base.JellyfinDataFetcher.populate_expected_image_keys = ultralight_populate_expected_image_keys
    base.JellyfinDataFetcher.write_public_catalogue = ultralight_write_public_catalogue
    base.JellyfinDataFetcher.choose_sync_mode = ultralight_choose_sync_mode
    base.JellyfinDataFetcher.run_incremental_sync = ultralight_run_incremental_sync
    base.JellyfinDataFetcher.finalize_state = ultralight_finalize_state


if __name__ == "__main__":
    activate_ultralight_mode()
    base.main()
