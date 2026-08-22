#!/usr/bin/env python3

import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


APP_NAME = "Beyond Glimpse"
APP_VERSION = "2.0.0"
DEFAULT_DB_PATH = "/app/state/jellyfin/catalogue-v2.db"
LIGHT_FIELDS = "DateCreated,Genres"
DETAIL_FIELDS = (
    "Overview,Genres,People,Studios,RunTimeTicks,RecursiveItemCount,"
    "ChildCount,Taglines,PremiereDate,OfficialRating,CommunityRating,DateCreated"
)


def utc_now_epoch():
    return int(time.time())


def parse_date_epoch(value):
    if not value:
        return 0
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return 0


def json_compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def db_path():
    return Path(os.environ.get("CATALOGUE_DB", DEFAULT_DB_PATH))


def open_db(path=None, *, readonly=False):
    path = Path(path or db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    if readonly and path.exists():
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    else:
        connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA foreign_keys=ON")
    if not readonly:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        ensure_schema(connection)
    return connection


def ensure_schema(connection):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY,
            library_id TEXT NOT NULL,
            media_type TEXT NOT NULL CHECK(media_type IN ('movie','tvshow')),
            title TEXT NOT NULL,
            sort_title TEXT NOT NULL,
            year INTEGER,
            date_added INTEGER NOT NULL DEFAULT 0,
            genres_json TEXT NOT NULL DEFAULT '[]',
            poster_tag TEXT,
            generation INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_items_type_title
            ON items(media_type, sort_title, id);
        CREATE INDEX IF NOT EXISTS idx_items_type_added
            ON items(media_type, date_added DESC, id);
        CREATE INDEX IF NOT EXISTS idx_items_type_year
            ON items(media_type, year DESC, sort_title, id);
        CREATE INDEX IF NOT EXISTS idx_items_library
            ON items(library_id, media_type);
        CREATE INDEX IF NOT EXISTS idx_items_generation
            ON items(generation);

        CREATE TABLE IF NOT EXISTS item_genres (
            item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
            genre TEXT NOT NULL,
            PRIMARY KEY(item_id, genre)
        );
        CREATE INDEX IF NOT EXISTS idx_item_genres_genre
            ON item_genres(genre, item_id);

        CREATE TABLE IF NOT EXISTS item_details (
            item_id TEXT PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
            detail_json TEXT NOT NULL,
            fetched_at INTEGER NOT NULL
        );
        """
    )
    try:
        connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS items_fts
            USING fts5(id UNINDEXED, title, genres, tokenize='unicode61 remove_diacritics 2')
            """
        )
        set_meta(connection, "fts_enabled", "1")
    except sqlite3.OperationalError:
        set_meta(connection, "fts_enabled", "0")
    connection.commit()


def get_meta(connection, key, default=None):
    row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(connection, key, value):
    connection.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def light_item_from_api(item, library_id, media_type, generation):
    item_id = str(item.get("Id") or "")
    if not item_id:
        return None
    genres = [str(value) for value in (item.get("Genres") or []) if value]
    poster_tag = (item.get("ImageTags") or {}).get("Primary")
    title = str(item.get("Name") or "")
    year = item.get("ProductionYear")
    try:
        year = int(year) if year not in (None, "") else None
    except (TypeError, ValueError):
        year = None
    return {
        "id": item_id,
        "library_id": str(library_id),
        "media_type": media_type,
        "title": title,
        "sort_title": title.casefold(),
        "year": year,
        "date_added": parse_date_epoch(item.get("DateCreated")),
        "genres": genres,
        "genres_json": json_compact(genres),
        "poster_tag": str(poster_tag) if poster_tag else None,
        "generation": int(generation),
        "updated_at": utc_now_epoch(),
    }


def upsert_light_item(connection, record):
    connection.execute(
        """
        INSERT INTO items(
            id,library_id,media_type,title,sort_title,year,date_added,
            genres_json,poster_tag,generation,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            library_id=excluded.library_id,
            media_type=excluded.media_type,
            title=excluded.title,
            sort_title=excluded.sort_title,
            year=excluded.year,
            date_added=excluded.date_added,
            genres_json=excluded.genres_json,
            poster_tag=excluded.poster_tag,
            generation=excluded.generation,
            updated_at=excluded.updated_at
        """,
        (
            record["id"], record["library_id"], record["media_type"], record["title"],
            record["sort_title"], record["year"], record["date_added"],
            record["genres_json"], record["poster_tag"], record["generation"],
            record["updated_at"],
        ),
    )
    connection.execute("DELETE FROM item_genres WHERE item_id = ?", (record["id"],))
    if record["genres"]:
        connection.executemany(
            "INSERT OR IGNORE INTO item_genres(item_id,genre) VALUES(?,?)",
            ((record["id"], genre) for genre in record["genres"]),
        )
    if get_meta(connection, "fts_enabled", "0") == "1":
        connection.execute("DELETE FROM items_fts WHERE id = ?", (record["id"],))
        connection.execute(
            "INSERT INTO items_fts(id,title,genres) VALUES(?,?,?)",
            (record["id"], record["title"], " ".join(record["genres"])),
        )


def delete_items(connection, item_ids):
    ids = [str(value) for value in item_ids if value]
    if not ids:
        return 0
    if get_meta(connection, "fts_enabled", "0") == "1":
        connection.executemany("DELETE FROM items_fts WHERE id = ?", ((value,) for value in ids))
    connection.executemany("DELETE FROM items WHERE id = ?", ((value,) for value in ids))
    return len(ids)


def public_item(row):
    genres = []
    try:
        genres = json.loads(row["genres_json"] or "[]")
    except (TypeError, json.JSONDecodeError):
        pass
    return {
        "id": row["id"],
        "title": row["title"],
        "year": row["year"] or "",
        "addedAt": row["date_added"] or 0,
        "genres": genres,
        "posterTag": row["poster_tag"] or "",
    }


def detail_from_api(item, media_type):
    studios = item.get("Studios") or []
    actors = []
    for person in item.get("People") or []:
        if person.get("Type") == "Actor" and len(actors) < 6:
            actors.append({"name": person.get("Name", ""), "role": person.get("Role", "")})

    result = {
        "summary": item.get("Overview", ""),
        "rating": item.get("CommunityRating", ""),
        "studio": studios[0].get("Name", "") if studios else "",
        "actors": actors,
        "contentRating": item.get("OfficialRating", ""),
        "originallyAvailableAt": item.get("PremiereDate", ""),
    }
    if media_type == "movie":
        result["duration"] = int(item.get("RunTimeTicks") or 0) // 10000
        result["tagline"] = (item.get("Taglines") or [""])[0]
    else:
        result["leafCount"] = item.get("EpisodeCount") or item.get("RecursiveItemCount") or 0
        result["childCount"] = item.get("ChildCount") or 0
    return result


def safe_fts_query(value):
    tokens = re.findall(r"[\w]+", value or "", flags=re.UNICODE)
    if not tokens:
        return None
    return " AND ".join(f'"{token.replace(chr(34), "")}"*' for token in tokens[:8])


class JellyfinClient:
    def __init__(self, url=None, token=None, timeout=None):
        self.url = (url or os.environ.get("JELLYFIN_URL", "")).rstrip("/")
        self.token = token or os.environ.get("JELLYFIN_TOKEN", "")
        self.timeout = max(5, int(timeout or os.environ.get("REQUEST_TIMEOUT", "60")))
        if not self.url or not self.token:
            raise RuntimeError("JELLYFIN_URL and JELLYFIN_TOKEN are required")
        self.session = requests.Session()
        retries = Retry(
            total=2,
            connect=2,
            read=0,
            status=2,
            backoff_factor=0.4,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=12, pool_maxsize=12)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers["Authorization"] = (
            f'MediaBrowser Token="{self.token}", Client="{APP_NAME}", '
            f'Device="Server", DeviceId="beyond-glimpse-v2", Version="{APP_VERSION}"'
        )
        self.session.headers["Accept"] = "application/json"

    def get_json(self, path, params=None):
        response = self.session.get(
            f"{self.url}{path}", params=params, timeout=(10, self.timeout)
        )
        response.raise_for_status()
        return response.json()

    def get_server_time(self):
        try:
            response = self.session.get(
                f"{self.url}/System/Info/Public", timeout=(10, self.timeout)
            )
            response.raise_for_status()
            header = response.headers.get("Date")
            if header:
                from email.utils import parsedate_to_datetime
                parsed = parsedate_to_datetime(header)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return int(parsed.timestamp())
        except (requests.RequestException, TypeError, ValueError):
            pass
        return utc_now_epoch()

    def resolve_user_id(self, connection=None):
        configured = os.environ.get("JELLYFIN_USER_ID", "").strip()
        if configured:
            if connection is not None:
                set_meta(connection, "user_id", configured)
                connection.commit()
            return configured
        if connection is not None:
            cached = get_meta(connection, "user_id")
            if cached:
                return cached
        users = self.get_json("/Users")
        if not users:
            raise RuntimeError("Jellyfin returned no users")
        user_id = str(users[0].get("Id") or "")
        if not user_id:
            raise RuntimeError("Jellyfin user response did not contain an Id")
        if connection is not None:
            set_meta(connection, "user_id", user_id)
            connection.commit()
        print(
            "Warning: JELLYFIN_USER_ID is not configured; using the first Jellyfin user.",
            flush=True,
        )
        return user_id

    def eligible_libraries(self):
        excluded = {
            value.strip()
            for value in os.environ.get("JELLYFIN_EXCLUDE_LIBRARIES", "").split(",")
            if value.strip()
        }
        result = []
        for library in self.get_json("/Library/VirtualFolders"):
            library_id = str(library.get("ItemId") or library.get("Id") or "")
            collection_type = library.get("CollectionType")
            name = str(library.get("Name") or "")
            if not library_id or collection_type not in {"movies", "tvshows"}:
                continue
            if library_id in excluded or name in excluded:
                continue
            result.append(
                {
                    "id": library_id,
                    "name": name,
                    "media_type": "movie" if collection_type == "movies" else "tvshow",
                }
            )
        return result

    def light_params(self, user_id, *, library_id=None, media_type=None, start_index=0, limit=1000, min_saved=None, ids=None):
        params = {
            "StartIndex": int(start_index),
            "Limit": int(limit),
            "Recursive": "true",
            "Fields": LIGHT_FIELDS,
            "EnableTotalRecordCount": "false",
            "EnableUserData": "false",
            "EnableImages": "true",
            "ImageTypeLimit": 1,
            "EnableImageTypes": "Primary",
            "UserId": user_id,
        }
        if library_id:
            params["ParentId"] = library_id
        if media_type:
            params["IncludeItemTypes"] = "Movie" if media_type == "movie" else "Series"
        if min_saved:
            params["MinDateLastSaved"] = min_saved
        if ids:
            params["Ids"] = ",".join(ids)
            params.pop("Recursive", None)
            params.pop("ParentId", None)
        return params

    def fetch_detail(self, user_id, item_id):
        params = {
            "Ids": item_id,
            "UserId": user_id,
            "Fields": DETAIL_FIELDS,
            "EnableTotalRecordCount": "false",
            "EnableUserData": "false",
            "EnableImages": "false",
            "Limit": 1,
        }
        data = self.get_json("/Items", params=params)
        items = data.get("Items") or []
        return items[0] if items else None

    def fetch_poster(self, item_id, tag, max_width, quality):
        response = self.session.get(
            f"{self.url}/Items/{item_id}/Images/Primary",
            params={
                "tag": tag,
                "maxWidth": int(max_width),
                "quality": int(quality),
                "format": "jpg",
            },
            headers={"Accept": "image/jpeg,image/*"},
            timeout=(10, self.timeout),
        )
        response.raise_for_status()
        return response.content, response.headers.get("Content-Type", "image/jpeg")
