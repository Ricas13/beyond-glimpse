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


def activate_ultralight_mode():
    # Force one clean reconciliation on upgrade because the public export format
    # changes from full records to compact indexes + detail shards.
    base.CATALOG_SCHEMA_VERSION = "2-ultralight"
    base.APP_VERSION = "1.2"
    base.JellyfinDataFetcher.build_catalog_entry = ultralight_build_catalog_entry
    base.JellyfinDataFetcher.populate_expected_image_keys = ultralight_populate_expected_image_keys
    base.JellyfinDataFetcher.write_public_catalogue = ultralight_write_public_catalogue


if __name__ == "__main__":
    activate_ultralight_mode()
    base.main()
