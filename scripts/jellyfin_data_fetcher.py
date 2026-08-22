#!/usr/bin/env python3

import argparse
import fcntl
import json
import os
import pwd
import grp
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


APP_NAME = "Beyond Glimpse"
APP_VERSION = "1.1"
CATALOG_SCHEMA_VERSION = "1"
DEFAULT_PAGE_SIZE = 500
DEFAULT_POSTER_MAX_WIDTH = 500
DEFAULT_BACKDROP_MAX_WIDTH = 1280
DEFAULT_IMAGE_QUALITY = 82
DEFAULT_REQUEST_TIMEOUT = 60
DEFAULT_FULL_RECONCILE_HOURS = 24
DEFAULT_SYNC_OVERLAP_SECONDS = 300


class JellyfinDataFetcher:
    def __init__(
        self,
        jellyfin_url,
        jellyfin_token,
        output_dir="data/jellyfin",
        page_size=DEFAULT_PAGE_SIZE,
        excluded_libraries=None,
        server_type="jellyfin",
        user_id=None,
        poster_max_width=DEFAULT_POSTER_MAX_WIDTH,
        backdrop_max_width=DEFAULT_BACKDROP_MAX_WIDTH,
        image_quality=DEFAULT_IMAGE_QUALITY,
        download_backdrops=False,
        request_timeout=DEFAULT_REQUEST_TIMEOUT,
        state_dir=None,
        incremental_sync=None,
        full_reconcile_hours=DEFAULT_FULL_RECONCILE_HOURS,
        sync_overlap_seconds=DEFAULT_SYNC_OVERLAP_SECONDS,
        force_full_sync=False,
    ):
        self.jellyfin_url = jellyfin_url.rstrip("/")
        self.jellyfin_token = jellyfin_token
        self.output_dir = Path(output_dir)
        self.page_size = max(1, int(page_size))
        self.excluded_libraries = set(excluded_libraries or [])
        self.server_type = server_type.lower()
        self.user_id = user_id
        self.poster_max_width = max(1, int(poster_max_width))
        self.backdrop_max_width = max(1, int(backdrop_max_width))
        self.image_quality = max(1, min(100, int(image_quality)))
        self.download_backdrops = bool(download_backdrops)
        self.request_timeout = max(1, int(request_timeout))
        self.incremental_sync = self.server_type == "jellyfin" if incremental_sync is None else bool(incremental_sync)
        self.full_reconcile_hours = max(1, int(full_reconcile_hours))
        self.sync_overlap_seconds = max(0, int(sync_overlap_seconds))
        self.force_full_sync = bool(force_full_sync)

        if state_dir:
            self.state_dir = Path(state_dir)
        elif self.output_dir.parent.name == "data":
            self.state_dir = self.output_dir.parent.parent / "state" / self.output_dir.name
        else:
            self.state_dir = self.output_dir.parent / f".{self.output_dir.name}-state"

        self.image_state_file = self.state_dir / "image-state.json"
        self.catalog_db_file = self.state_dir / "catalog.db"
        self.sync_lock_file = self.state_dir / "sync.lock"
        self.legacy_image_state_file = self.output_dir / "image-state.json"
        self.legacy_checksums_file = self.output_dir / "checksums.pkl"
        self.image_state = {}
        self.expected_image_keys = set()
        self._lock_handle = None

        try:
            self.www_data_uid = pwd.getpwnam("www-data").pw_uid
            self.www_data_gid = grp.getgrnam("www-data").gr_gid
        except KeyError:
            self.www_data_uid = self.www_data_gid = None

        self.setup_directories()
        self.image_state = self.load_image_state()
        self.session = self.build_session()

    def build_session(self):
        session = requests.Session()
        retries = Retry(
            total=3,
            connect=3,
            read=3,
            status=3,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=20, pool_maxsize=20)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        if self.server_type == "emby":
            session.headers["X-Emby-Token"] = self.jellyfin_token
        else:
            session.headers["Authorization"] = (
                f'MediaBrowser Token="{self.jellyfin_token}", '
                f'Client="{APP_NAME}", Device="Server", '
                f'DeviceId="beyond-glimpse-sync", Version="{APP_VERSION}"'
            )
        return session

    def setup_directories(self):
        for directory in (
            self.output_dir,
            self.output_dir / "posters" / "movies",
            self.output_dir / "posters" / "tvshows",
            self.output_dir / "backdrops" / "movies",
            self.output_dir / "backdrops" / "tvshows",
            self.state_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
            self.set_permissions(directory)

    def set_permissions(self, path):
        if self.www_data_uid is None or self.www_data_gid is None:
            return
        try:
            os.chown(path, self.www_data_uid, self.www_data_gid)
        except (PermissionError, FileNotFoundError):
            pass

    def acquire_sync_lock(self):
        self._lock_handle = self.sync_lock_file.open("a+")
        try:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._lock_handle.close()
            self._lock_handle = None
            raise RuntimeError("another Beyond Glimpse sync is already running") from exc

    def release_sync_lock(self):
        if self._lock_handle is None:
            return
        try:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._lock_handle.close()
            self._lock_handle = None

    def load_image_state(self):
        source = self.image_state_file
        if not source.exists() and self.legacy_image_state_file.exists():
            source = self.legacy_image_state_file
            print(f"Migrating image state from public data path: {source}")
        if not source.exists():
            return {}
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Warning: ignoring invalid image state: {exc}")
            return {}

    def atomic_write_json(self, path, data, *, compact=True):
        path = Path(path)
        tmp_path = path.with_name(f".{path.name}.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            if compact:
                json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
            else:
                json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        self.set_permissions(path)

    def request_json(self, path, params=None):
        response = self.session.get(
            f"{self.jellyfin_url}{path}",
            params=params,
            headers={"Accept": "application/json"},
            timeout=self.request_timeout,
        )
        response.raise_for_status()
        return response.json()

    def get_server_time(self):
        try:
            response = self.session.get(
                f"{self.jellyfin_url}/System/Info/Public",
                headers={"Accept": "application/json"},
                timeout=self.request_timeout,
            )
            response.raise_for_status()
            header = response.headers.get("Date")
            if header:
                parsed = parsedate_to_datetime(header)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc)
        except (requests.RequestException, TypeError, ValueError) as exc:
            print(f"Warning: could not read server clock; using local UTC clock: {exc}")
        return datetime.now(timezone.utc)

    @staticmethod
    def format_api_datetime(value):
        return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    @staticmethod
    def parse_state_datetime(value):
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def is_library_excluded(self, library_name, library_id):
        if not self.excluded_libraries:
            return False
        return library_name in self.excluded_libraries or str(library_id) in self.excluded_libraries

    def get_user_id(self):
        if self.user_id:
            return self.user_id

        users = self.request_json("/Users")
        if not users:
            raise RuntimeError("No Jellyfin/Emby users were returned by /Users")

        selected = users[0]["Id"]
        print(
            "Warning: no explicit user ID configured; using the first server user. "
            "Set JELLYFIN_USER_ID/EMBY_USER_ID or --user-id to make this deterministic."
        )
        return selected

    def fetch_libraries(self):
        libraries = self.request_json("/Library/VirtualFolders")
        if not isinstance(libraries, list):
            raise RuntimeError("Unexpected response from /Library/VirtualFolders")
        return libraries

    def allowed_libraries(self, libraries):
        allowed = []
        for library in libraries:
            library_id = library.get("ItemId") or library.get("Id")
            library_type = library.get("CollectionType")
            library_name = library.get("Name")
            if not library_id or library_type not in ("movies", "tvshows"):
                continue
            if self.is_library_excluded(library_name, library_id):
                print(f"Skipping excluded library: {library_name}")
                continue
            allowed.append(
                {
                    "id": str(library_id),
                    "name": library_name or "",
                    "media_type": "movie" if library_type == "movies" else "tvshow",
                }
            )
        return allowed

    def fetch_library_content(self, user_id, library_id, media_type, min_date_last_saved=None):
        all_items = []
        start_index = 0
        include_type = "Movie" if media_type == "movie" else "Series"
        fields = (
            "Overview,Genres,People,Studios,DateCreated,RunTimeTicks,ProviderIds,"
            "ImageTags,BackdropImageTags,RecursiveItemCount,Taglines"
        )

        while True:
            params = {
                "ParentId": library_id,
                "StartIndex": start_index,
                "Limit": self.page_size,
                "Recursive": "true",
                "Fields": fields,
                "IncludeItemTypes": include_type,
                "EnableTotalRecordCount": "false",
            }
            if min_date_last_saved:
                params["MinDateLastSaved"] = min_date_last_saved

            if self.server_type == "jellyfin":
                params["UserId"] = user_id
                item_path = "/Items"
            else:
                item_path = f"/Users/{user_id}/Items"
            data = self.request_json(item_path, params=params)
            items = data.get("Items", [])
            if not items:
                break
            all_items.extend(items)
            print(f"  Fetched {len(items)} items (offset: {start_index})")
            start_index += len(items)
            if len(items) < self.page_size:
                break

        return all_items

    def image_key(self, output_path):
        return output_path.relative_to(self.output_dir).as_posix()

    def sync_image(self, item_id, image_type, image_tag, output_path, max_width, image_index=None):
        output_path = Path(output_path)
        key = self.image_key(output_path)
        self.expected_image_keys.add(key)
        fingerprint = {
            "tag": image_tag,
            "maxWidth": int(max_width),
            "quality": self.image_quality,
        }

        if output_path.exists() and self.image_state.get(key) == fingerprint:
            return True

        image_path = f"/Items/{item_id}/Images/{image_type}"
        if image_index is not None:
            image_path += f"/{image_index}"

        params = {
            "tag": image_tag,
            "maxWidth": int(max_width),
            "quality": self.image_quality,
            "format": "jpg",
        }
        tmp_path = output_path.with_name(f".{output_path.name}.tmp")

        try:
            response = self.session.get(
                f"{self.jellyfin_url}{image_path}",
                params=params,
                headers={"Accept": "image/jpeg,image/*"},
                stream=True,
                timeout=self.request_timeout,
            )
            response.raise_for_status()
            with tmp_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, output_path)
            self.set_permissions(output_path)
            self.image_state[key] = fingerprint
            return True
        except (requests.RequestException, OSError) as exc:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            print(f"Warning: image sync failed for {item_id} {image_type}: {exc}")
            return False

    def process_media_item(self, item, media_type):
        added_at = 0
        date_created = item.get("DateCreated")
        if date_created:
            try:
                added_at = int(datetime.fromisoformat(date_created.replace("Z", "+00:00")).timestamp())
            except (TypeError, ValueError):
                pass

        media_info = {
            "id": str(item.get("Id", "")),
            "title": item.get("Name", ""),
            "year": item.get("ProductionYear", ""),
            "summary": item.get("Overview", ""),
            "rating": item.get("CommunityRating", ""),
            "studio": "",
            "addedAt": added_at,
            "updatedAt": added_at,
            "genres": list(item.get("Genres") or []),
            "actors": [],
        }

        studios = item.get("Studios") or []
        if studios:
            media_info["studio"] = studios[0].get("Name", "")

        for person in item.get("People") or []:
            if person.get("Type") == "Actor" and len(media_info["actors"]) < 3:
                media_info["actors"].append({"name": person.get("Name", ""), "role": person.get("Role", "")})

        if media_type == "movie":
            runtime_ticks = item.get("RunTimeTicks") or 0
            media_info.update(
                {
                    "duration": runtime_ticks // 10000,
                    "contentRating": item.get("OfficialRating", ""),
                    "originallyAvailableAt": item.get("PremiereDate", ""),
                    "tagline": (item.get("Taglines") or [""])[0],
                }
            )
        else:
            episode_count = item.get("EpisodeCount")
            if episode_count is None:
                episode_count = item.get("RecursiveItemCount") or 0
            season_count = item.get("ChildCount") or 0
            media_info.update(
                {
                    "leafCount": episode_count,
                    "childCount": season_count,
                    "contentRating": item.get("OfficialRating", ""),
                    "originallyAvailableAt": item.get("PremiereDate", ""),
                }
            )

        return media_info

    def build_catalog_entry(self, item, media_type, library_id):
        media_info = self.process_media_item(item, media_type)
        item_id = media_info["id"]
        if not item_id:
            return None

        poster_tag = (item.get("ImageTags") or {}).get("Primary")
        backdrop_tags = item.get("BackdropImageTags") or []
        backdrop_tag = backdrop_tags[0] if backdrop_tags else None

        if poster_tag:
            poster_path = self.output_dir / "posters" / f"{media_type}s" / f"{item_id}.jpg"
            self.sync_image(item_id, "Primary", poster_tag, poster_path, self.poster_max_width)

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

    def open_catalog_db(self):
        connection = sqlite3.connect(self.catalog_db_file, timeout=max(5, self.request_timeout))
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                id TEXT PRIMARY KEY,
                library_id TEXT NOT NULL,
                media_type TEXT NOT NULL,
                media_json TEXT NOT NULL,
                poster_tag TEXT,
                backdrop_tag TEXT
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_items_library_id ON items(library_id)")
        connection.commit()
        self.set_permissions(self.catalog_db_file)
        return connection

    @staticmethod
    def get_meta(connection, key):
        row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    @staticmethod
    def set_meta(connection, key, value):
        connection.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )

    def config_signature(self, user_id, allowed_libraries):
        payload = {
            "schema": CATALOG_SCHEMA_VERSION,
            "server_type": self.server_type,
            "server_url": self.jellyfin_url,
            "user_id": str(user_id),
            "libraries": sorted((library["id"], library["media_type"]) for library in allowed_libraries),
            "poster_max_width": self.poster_max_width,
            "backdrop_max_width": self.backdrop_max_width,
            "image_quality": self.image_quality,
            "download_backdrops": self.download_backdrops,
        }
        return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    def choose_sync_mode(self, connection, user_id, allowed_libraries, sync_started_at):
        if self.force_full_sync:
            return "full", "forced by configuration"
        if not self.incremental_sync:
            return "full", "incremental sync disabled"
        if self.get_meta(connection, "schema_version") != CATALOG_SCHEMA_VERSION:
            return "full", "catalog state is new or schema changed"

        expected_signature = self.config_signature(user_id, allowed_libraries)
        if self.get_meta(connection, "config_signature") != expected_signature:
            return "full", "server/user/library/artwork configuration changed"

        watermark = self.parse_state_datetime(self.get_meta(connection, "watermark"))
        if watermark is None:
            return "full", "no valid incremental watermark"

        last_full = self.parse_state_datetime(self.get_meta(connection, "last_full_reconcile"))
        if last_full is None:
            return "full", "no previous full reconciliation"
        if sync_started_at - last_full >= timedelta(hours=self.full_reconcile_hours):
            return "full", f"periodic {self.full_reconcile_hours}h deletion reconciliation is due"

        return "incremental", "metadata watermark is current"

    @staticmethod
    def upsert_catalog_entry(connection, entry):
        existing = connection.execute(
            "SELECT library_id, media_type, media_json, poster_tag, backdrop_tag FROM items WHERE id = ?",
            (entry["id"],),
        ).fetchone()
        new_values = (
            entry["library_id"],
            entry["media_type"],
            entry["media_json"],
            entry["poster_tag"],
            entry["backdrop_tag"],
        )
        changed = existing != new_values
        connection.execute(
            """
            INSERT INTO items(id, library_id, media_type, media_json, poster_tag, backdrop_tag)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                library_id = excluded.library_id,
                media_type = excluded.media_type,
                media_json = excluded.media_json,
                poster_tag = excluded.poster_tag,
                backdrop_tag = excluded.backdrop_tag
            """,
            (entry["id"],) + new_values,
        )
        return changed

    def populate_expected_image_keys(self, connection):
        self.expected_image_keys = set()
        for item_id, media_type, poster_tag, backdrop_tag in connection.execute(
            "SELECT id, media_type, poster_tag, backdrop_tag FROM items"
        ):
            if poster_tag:
                path = self.output_dir / "posters" / f"{media_type}s" / f"{item_id}.jpg"
                self.expected_image_keys.add(self.image_key(path))
            if self.download_backdrops and backdrop_tag:
                path = self.output_dir / "backdrops" / f"{media_type}s" / f"{item_id}.jpg"
                self.expected_image_keys.add(self.image_key(path))

    def prune_stale_images(self):
        removed = 0
        image_roots = (self.output_dir / "posters", self.output_dir / "backdrops")
        for root in image_roots:
            if not root.exists():
                continue
            for path in root.rglob("*.jpg"):
                key = self.image_key(path)
                if key not in self.expected_image_keys:
                    try:
                        path.unlink()
                        removed += 1
                    except OSError as exc:
                        print(f"Warning: could not remove stale image {path}: {exc}")

        self.image_state = {
            key: value for key, value in self.image_state.items() if key in self.expected_image_keys
        }
        if removed:
            print(f"Removed {removed} stale cached images")

    def public_catalogue(self, connection, media_type):
        rows = connection.execute(
            "SELECT media_json FROM items WHERE media_type = ? ORDER BY id",
            (media_type,),
        )
        return [json.loads(row[0]) for row in rows]

    def write_public_catalogue(self, connection):
        movies = self.public_catalogue(connection, "movie")
        tvshows = self.public_catalogue(connection, "tvshow")
        self.atomic_write_json(self.output_dir / "movies.json", movies)
        self.atomic_write_json(self.output_dir / "tvshows.json", tvshows)
        return len(movies), len(tvshows)

    def run_full_reconcile(self, connection, user_id, allowed_libraries):
        changed = False
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("DELETE FROM items")
            for library in allowed_libraries:
                print(f"Full reconcile: {library['name']} ({library['media_type']})")
                items = self.fetch_library_content(user_id, library["id"], library["media_type"])
                print(f"Found {len(items)} items in {library['name']}")
                for index, item in enumerate(items, start=1):
                    entry = self.build_catalog_entry(item, library["media_type"], library["id"])
                    if entry:
                        self.upsert_catalog_entry(connection, entry)
                    if index == 1 or index % 250 == 0 or index == len(items):
                        print(f"  Processing {index}/{len(items)}")
            connection.commit()
            changed = True
        except Exception:
            connection.rollback()
            raise
        return changed

    def run_incremental_sync(self, connection, user_id, allowed_libraries, watermark):
        min_date = watermark - timedelta(seconds=self.sync_overlap_seconds)
        min_date_text = self.format_api_datetime(min_date)
        print(f"Incremental metadata window starts at {min_date_text}")
        changed_count = 0

        connection.execute("BEGIN IMMEDIATE")
        try:
            for library in allowed_libraries:
                items = self.fetch_library_content(
                    user_id,
                    library["id"],
                    library["media_type"],
                    min_date_last_saved=min_date_text,
                )
                print(f"Changed candidates in {library['name']}: {len(items)}")
                for item in items:
                    entry = self.build_catalog_entry(item, library["media_type"], library["id"])
                    if entry and self.upsert_catalog_entry(connection, entry):
                        changed_count += 1
            connection.commit()
        except Exception:
            connection.rollback()
            raise

        return changed_count

    def finalize_state(self, connection, user_id, allowed_libraries, sync_started_at, mode):
        self.set_meta(connection, "schema_version", CATALOG_SCHEMA_VERSION)
        self.set_meta(connection, "config_signature", self.config_signature(user_id, allowed_libraries))
        self.set_meta(connection, "watermark", self.format_api_datetime(sync_started_at))
        if mode == "full":
            self.set_meta(connection, "last_full_reconcile", self.format_api_datetime(sync_started_at))
        connection.commit()

    def cleanup_legacy_state(self):
        for legacy_path in (self.legacy_image_state_file, self.legacy_checksums_file):
            if legacy_path != self.image_state_file:
                try:
                    legacy_path.unlink(missing_ok=True)
                except OSError as exc:
                    print(f"Warning: could not remove legacy state file {legacy_path}: {exc}")

    def fetch_and_save_data(self):
        self.acquire_sync_lock()
        try:
            print(f"Starting {self.server_type.title()} data fetch at {datetime.now().isoformat(timespec='seconds')}")
            print(f"Server URL: {self.jellyfin_url}")
            print(
                f"Image cache: posters <= {self.poster_max_width}px, "
                f"backdrops {'enabled' if self.download_backdrops else 'disabled'}, quality {self.image_quality}"
            )

            user_id = self.get_user_id()
            print(f"Using user ID: {user_id}")
            libraries = self.fetch_libraries()
            allowed = self.allowed_libraries(libraries)
            print(f"Found {len(allowed)} eligible libraries")
            sync_started_at = self.get_server_time() if self.server_type == "jellyfin" else datetime.now(timezone.utc)

            connection = self.open_catalog_db()
            try:
                mode, reason = self.choose_sync_mode(connection, user_id, allowed, sync_started_at)
                print(f"Sync mode: {mode} ({reason})")
                previous_watermark = self.parse_state_datetime(self.get_meta(connection, "watermark"))

                if mode == "full":
                    catalog_changed = self.run_full_reconcile(connection, user_id, allowed)
                else:
                    changed_count = self.run_incremental_sync(connection, user_id, allowed, previous_watermark)
                    catalog_changed = changed_count > 0
                    print(f"Incremental sync changed {changed_count} catalogue records")

                self.populate_expected_image_keys(connection)
                self.prune_stale_images()

                if mode == "full" or catalog_changed:
                    movie_count, tv_count = self.write_public_catalogue(connection)
                else:
                    movie_count = connection.execute("SELECT COUNT(*) FROM items WHERE media_type='movie'").fetchone()[0]
                    tv_count = connection.execute("SELECT COUNT(*) FROM items WHERE media_type='tvshow'").fetchone()[0]
                    print("No exported metadata changed; public catalogue JSON left untouched")

                self.finalize_state(connection, user_id, allowed, sync_started_at, mode)
                self.atomic_write_json(self.image_state_file, self.image_state, compact=False)
                self.cleanup_legacy_state()
                print(f"Completed {mode} sync: {movie_count} movies, {tv_count} TV shows")
            finally:
                connection.close()
        finally:
            self.release_sync_lock()


def env_bool(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def detect_server_type(url):
    configured = os.environ.get("SERVER_TYPE")
    if configured:
        return configured.lower()

    emby_url = os.environ.get("EMBY_URL", "").rstrip("/")
    if emby_url and url.rstrip("/") == emby_url:
        return "emby"

    if "EMBY_EXCLUDE_LIBRARIES" in os.environ and "JELLYFIN_EXCLUDE_LIBRARIES" not in os.environ:
        return "emby"
    return "jellyfin"


def main():
    default_url = os.environ.get("JELLYFIN_URL") or os.environ.get("EMBY_URL", "")
    default_token = os.environ.get("JELLYFIN_TOKEN") or os.environ.get("EMBY_TOKEN", "")
    default_output = os.environ.get("OUTPUT_DIR", "data/jellyfin")
    default_page_size = int(os.environ.get("PAGE_SIZE", str(DEFAULT_PAGE_SIZE)))
    excluded_libraries_str = os.environ.get("EMBY_EXCLUDE_LIBRARIES") or os.environ.get(
        "JELLYFIN_EXCLUDE_LIBRARIES", ""
    )
    excluded_libraries = [value.strip() for value in excluded_libraries_str.split(",") if value.strip()]

    parser = argparse.ArgumentParser(description="Fetch Jellyfin/Emby media data and optimized artwork")
    parser.add_argument("--url", default=default_url)
    parser.add_argument("--token", default=default_token)
    parser.add_argument("--output", default=default_output)
    parser.add_argument("--page-size", type=int, default=default_page_size)
    parser.add_argument("--exclude-libraries", nargs="*", default=excluded_libraries)
    parser.add_argument("--server-type", choices=("jellyfin", "emby"), default=None)
    parser.add_argument("--user-id", default=os.environ.get("JELLYFIN_USER_ID") or os.environ.get("EMBY_USER_ID"))
    parser.add_argument("--poster-max-width", type=int, default=int(os.environ.get("POSTER_MAX_WIDTH", DEFAULT_POSTER_MAX_WIDTH)))
    parser.add_argument("--backdrop-max-width", type=int, default=int(os.environ.get("BACKDROP_MAX_WIDTH", DEFAULT_BACKDROP_MAX_WIDTH)))
    parser.add_argument("--image-quality", type=int, default=int(os.environ.get("IMAGE_QUALITY", DEFAULT_IMAGE_QUALITY)))
    parser.add_argument("--request-timeout", type=int, default=int(os.environ.get("REQUEST_TIMEOUT", DEFAULT_REQUEST_TIMEOUT)))
    parser.add_argument("--state-dir", default=os.environ.get("STATE_DIR"))
    parser.add_argument("--no-backdrops", action="store_true", default=not env_bool("DOWNLOAD_BACKDROPS", False))
    parser.add_argument("--full-sync", action="store_true", default=env_bool("FORCE_FULL_SYNC", False))
    parser.add_argument("--no-incremental", action="store_true", default=not env_bool("INCREMENTAL_SYNC", True))
    parser.add_argument(
        "--full-reconcile-hours",
        type=int,
        default=int(os.environ.get("FULL_RECONCILE_HOURS", DEFAULT_FULL_RECONCILE_HOURS)),
    )
    parser.add_argument(
        "--sync-overlap-seconds",
        type=int,
        default=int(os.environ.get("SYNC_OVERLAP_SECONDS", DEFAULT_SYNC_OVERLAP_SECONDS)),
    )
    args = parser.parse_args()

    if not args.url:
        parser.error("server URL is required (--url or JELLYFIN_URL/EMBY_URL)")
    if not args.token:
        parser.error("API token is required (--token or JELLYFIN_TOKEN/EMBY_TOKEN)")

    server_type = args.server_type or detect_server_type(args.url)
    incremental_sync = not args.no_incremental if server_type == "jellyfin" else False
    fetcher = JellyfinDataFetcher(
        args.url,
        args.token,
        args.output,
        page_size=args.page_size,
        excluded_libraries=args.exclude_libraries,
        server_type=server_type,
        user_id=args.user_id,
        poster_max_width=args.poster_max_width,
        backdrop_max_width=args.backdrop_max_width,
        image_quality=args.image_quality,
        download_backdrops=not args.no_backdrops,
        request_timeout=args.request_timeout,
        state_dir=args.state_dir,
        incremental_sync=incremental_sync,
        full_reconcile_hours=args.full_reconcile_hours,
        sync_overlap_seconds=args.sync_overlap_seconds,
        force_full_sync=args.full_sync,
    )

    try:
        fetcher.fetch_and_save_data()
    except (requests.RequestException, RuntimeError, OSError, ValueError, sqlite3.Error) as exc:
        print(f"ERROR: sync failed; existing catalogue files were preserved: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
