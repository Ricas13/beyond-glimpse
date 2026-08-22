#!/usr/bin/env python3

import argparse
import json
import os
import pwd
import grp
import sys
from datetime import datetime
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


APP_NAME = "Beyond Glimpse"
APP_VERSION = "1.0"
DEFAULT_PAGE_SIZE = 500
DEFAULT_POSTER_MAX_WIDTH = 500
DEFAULT_BACKDROP_MAX_WIDTH = 1280
DEFAULT_IMAGE_QUALITY = 82
DEFAULT_REQUEST_TIMEOUT = 60


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
        download_backdrops=True,
        request_timeout=DEFAULT_REQUEST_TIMEOUT,
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
        self.image_state_file = self.output_dir / "image-state.json"
        self.image_state = {}
        self.expected_image_keys = set()

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

    def load_image_state(self):
        if not self.image_state_file.exists():
            return {}
        try:
            data = json.loads(self.image_state_file.read_text(encoding="utf-8"))
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

    def fetch_library_content(self, user_id, library_id, media_type):
        all_items = []
        start_index = 0
        include_type = "Movie" if media_type == "movie" else "Series"
        fields = (
            "Overview,Genres,People,Studios,DateCreated,RunTimeTicks,ProviderIds,"
            "ImageTags,BackdropImageTags,RecursiveItemCount,Taglines"
        )

        while True:
            data = self.request_json(
                f"/Users/{user_id}/Items",
                params={
                    "ParentId": library_id,
                    "StartIndex": start_index,
                    "Limit": self.page_size,
                    "Recursive": "true",
                    "Fields": fields,
                    "IncludeItemTypes": include_type,
                    "EnableTotalRecordCount": "true",
                },
            )
            items = data.get("Items", [])
            if not items:
                break
            all_items.extend(items)
            print(f"  Fetched {len(items)} items (offset: {start_index})")

            total = data.get("TotalRecordCount")
            start_index += len(items)
            if len(items) < self.page_size or (isinstance(total, int) and start_index >= total):
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
            # Jellyfin can return these counts on the series DTO. Using them avoids
            # two extra API requests per series, which is critical for large libraries.
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

    def fetch_and_save_data(self):
        print(f"Starting {self.server_type.title()} data fetch at {datetime.now().isoformat(timespec='seconds')}")
        print(f"Server URL: {self.jellyfin_url}")
        print(
            f"Image cache: posters <= {self.poster_max_width}px, "
            f"backdrops <= {self.backdrop_max_width}px, quality {self.image_quality}"
        )

        user_id = self.get_user_id()
        print(f"Using user ID: {user_id}")
        libraries = self.fetch_libraries()
        print(f"Found {len(libraries)} libraries")

        movies_data = []
        tvshows_data = []

        for library in libraries:
            library_id = library.get("ItemId") or library.get("Id")
            library_type = library.get("CollectionType")
            library_name = library.get("Name")

            if self.is_library_excluded(library_name, library_id):
                print(f"Skipping excluded library: {library_name}")
                continue
            if library_type not in ("movies", "tvshows"):
                continue
            if not library_id:
                print(f"Warning: skipping library without an ID: {library_name}")
                continue

            media_type = "movie" if library_type == "movies" else "tvshow"
            print(f"Processing library: {library_name} ({media_type})")
            items = self.fetch_library_content(user_id, library_id, media_type)
            print(f"Found {len(items)} items in {library_name}")

            for index, item in enumerate(items, start=1):
                media_info = self.process_media_item(item, media_type)
                item_id = media_info["id"]
                if not item_id:
                    continue

                if index == 1 or index % 250 == 0 or index == len(items):
                    print(f"  Processing {index}/{len(items)}")

                poster_tag = (item.get("ImageTags") or {}).get("Primary")
                if poster_tag:
                    poster_path = self.output_dir / "posters" / f"{media_type}s" / f"{item_id}.jpg"
                    self.sync_image(item_id, "Primary", poster_tag, poster_path, self.poster_max_width)

                backdrop_tags = item.get("BackdropImageTags") or []
                if self.download_backdrops and backdrop_tags:
                    backdrop_path = self.output_dir / "backdrops" / f"{media_type}s" / f"{item_id}.jpg"
                    self.sync_image(
                        item_id,
                        "Backdrop",
                        backdrop_tags[0],
                        backdrop_path,
                        self.backdrop_max_width,
                        image_index=0,
                    )

                if media_type == "movie":
                    movies_data.append(media_info)
                else:
                    tvshows_data.append(media_info)

        # Only replace the public catalogue once every metadata request succeeded.
        self.atomic_write_json(self.output_dir / "movies.json", movies_data)
        self.atomic_write_json(self.output_dir / "tvshows.json", tvshows_data)
        self.prune_stale_images()
        self.atomic_write_json(self.image_state_file, self.image_state, compact=False)

        print(f"Completed sync: {len(movies_data)} movies, {len(tvshows_data)} TV shows")


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

    # Scheduled Emby jobs explicitly export EMBY_EXCLUDE_LIBRARIES in the
    # existing entrypoint, even though cron does not inherit Docker env vars.
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
    parser.add_argument("--no-backdrops", action="store_true", default=not env_bool("DOWNLOAD_BACKDROPS", True))
    args = parser.parse_args()

    if not args.url:
        parser.error("server URL is required (--url or JELLYFIN_URL/EMBY_URL)")
    if not args.token:
        parser.error("API token is required (--token or JELLYFIN_TOKEN/EMBY_TOKEN)")

    server_type = args.server_type or detect_server_type(args.url)
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
    )

    try:
        fetcher.fetch_and_save_data()
    except (requests.RequestException, RuntimeError, OSError, ValueError) as exc:
        print(f"ERROR: sync failed; existing catalogue files were preserved: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
