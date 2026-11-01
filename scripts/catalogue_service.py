#!/usr/bin/env python3

import json
import os
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import requests

from catalogue_core import (
    JellyfinClient,
    detail_from_api,
    get_meta,
    open_db,
    public_item,
    safe_fts_query,
)


HOST = os.environ.get("CATALOGUE_API_HOST", "127.0.0.1")
PORT = int(os.environ.get("CATALOGUE_API_PORT", "8091"))
MAX_PAGE_SIZE = 120
DEFAULT_PAGE_SIZE = 60
DETAIL_TTL_SECONDS = max(300, int(os.environ.get("DETAIL_CACHE_TTL_SECONDS", str(7 * 86400))))
CHANNEL_CACHE_TTL_SECONDS = max(15, int(os.environ.get("CHANNEL_CACHE_TTL_SECONDS", "60")))
MAX_CHANNELS = 20000
EPISODE_CACHE_TTL_SECONDS = max(300, int(os.environ.get("EPISODE_CACHE_TTL_SECONDS", str(6 * 3600))))
POSTER_WIDTH = max(64, min(1000, int(os.environ.get("POSTER_PROXY_MAX_WIDTH", "320"))))
POSTER_QUALITY = max(30, min(95, int(os.environ.get("POSTER_PROXY_QUALITY", "72"))))
LIBRARY_CACHE_TTL_SECONDS = max(60, int(os.environ.get("LIBRARY_CACHE_TTL_SECONDS", "3600")))
MAX_API_THREADS = max(4, min(64, int(os.environ.get("MAX_API_THREADS", "24"))))


def int_or_none(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_channel_number(value):
    """Sort key for channel numbers such as '7', '12.1' or ''. Numbered first."""
    text = str(value or "").strip()
    try:
        return (0, float(text), text)
    except ValueError:
        return (1, 0.0, text.casefold())


def channel_from_api(item):
    if not isinstance(item, dict):
        return None
    channel_id = str(item.get("Id") or "")
    # Match the Nginx route and never allow path/control characters in an
    # upstream-provided identifier that will become part of a public image URL.
    if not re.fullmatch(r"[0-9A-Za-z-]{1,128}", channel_id):
        return None
    program = item.get("CurrentProgram") or {}
    if not isinstance(program, dict):
        program = {}
    now = None
    if program.get("Name"):
        now = {
            "title": str(program.get("Name") or ""),
            "episode": str(program.get("EpisodeTitle") or ""),
            "start": str(program.get("StartDate") or ""),
            "end": str(program.get("EndDate") or ""),
        }
    image_tags = item.get("ImageTags") or {}
    logo_tag = str(image_tags.get("Primary") or "") if isinstance(image_tags, dict) else ""
    if not re.fullmatch(r"[0-9A-Za-z._~-]{1,256}", logo_tag):
        logo_tag = ""
    return {
        "id": channel_id,
        "name": str(item.get("Name") or ""),
        "number": str(item.get("Number") or item.get("ChannelNumber") or ""),
        "kind": "radio" if str(item.get("ChannelType") or "").casefold() == "radio" else "tv",
        "logoTag": logo_tag,
        "now": now,
    }


def season_from_api(item):
    season_id = str(item.get("Id") or "")
    if not season_id:
        return None
    index_number = int_or_none(item.get("IndexNumber"))
    name = str(item.get("Name") or "").strip()
    if not name:
        if index_number == 0:
            name = "Specials"
        elif index_number is not None:
            name = f"Season {index_number}"
        else:
            name = "Season"
    return {
        "id": season_id,
        "name": name,
        "indexNumber": index_number,
    }


def episode_from_api(item):
    episode_id = str(item.get("Id") or "")
    if not episode_id:
        return None
    runtime_ticks = int_or_none(item.get("RunTimeTicks")) or 0
    return {
        "id": episode_id,
        "name": str(item.get("Name") or ""),
        "episodeNumber": int_or_none(item.get("IndexNumber")),
        "seasonNumber": int_or_none(item.get("ParentIndexNumber")),
        "runtime": runtime_ticks // 10000,
        "airDate": str(item.get("PremiereDate") or ""),
        "overview": str(item.get("Overview") or ""),
    }


def present_season_ids(client, user_id, season_ids, workers=8):
    """Which of ``season_ids`` hold at least one present episode.

    Each season gets a tiny parallel probe (a handful of rows) instead of
    paging through every episode of the series, so the cost no longer grows
    with show length. A failed probe keeps the season visible rather than
    hiding something that may be real.
    """
    ids = list(season_ids)
    if not ids:
        return set()

    # requests.Session is not guaranteed thread-safe. Each pool thread gets its
    # own JellyfinClient/session; the present-only get_json wrapper still applies.
    # Lightweight fake clients in the unit tests can be passed through unchanged.
    worker_local = threading.local()
    worker_clients = []
    worker_clients_lock = threading.Lock()

    def probe_client():
        if not isinstance(client, JellyfinClient):
            return client
        local = getattr(worker_local, "client", None)
        if local is None:
            local = JellyfinClient(url=client.url, token=client.token, timeout=client.timeout)
            worker_local.client = local
            with worker_clients_lock:
                worker_clients.append(local)
        return local

    def probe(season_id):
        local = probe_client()
        offset = 0
        # With an older Jellyfin build, ExcludeLocationTypes can be ignored.
        # Check further small pages when the first five are only placeholders.
        # After four full pages we cannot prove absence: keep the season rather
        # than incorrectly hiding real episodes beyond this bounded scan.
        for _ in range(4):
            try:
                data = local.get_json(
                    "/Items",
                    params={
                        "ParentId": season_id,
                        "Recursive": "false",
                        "IncludeItemTypes": "Episode",
                        "StartIndex": offset,
                        "Limit": 5,
                        "EnableTotalRecordCount": "false",
                        "EnableUserData": "false",
                        "EnableImages": "false",
                        "UserId": user_id,
                    },
                )
                if not isinstance(data, dict) or not isinstance(data.get("Items"), list):
                    return season_id, True
                items = data["Items"]
                # Looked up at call time so the present-only filter applies.
                if any(episode_from_api(item) is not None for item in items):
                    return season_id, True
            except (requests.RequestException, ValueError, TypeError, KeyError, AttributeError):
                # An invalid season id or a failed/malformed response is unknown,
                # not evidence that the season has no available episodes.
                return season_id, True
            if not items:
                return season_id, False
            # A Jellyfin server may cap pages below the requested Limit.
            # Keep looking from the returned row count rather than assuming a
            # short page means the end of the season.
            offset += len(items)
        return season_id, True

    try:
        with ThreadPoolExecutor(max_workers=max(1, min(8, workers, len(ids)))) as pool:
            return {season_id for season_id, present in pool.map(probe, ids) if present}
    finally:
        for local in worker_clients:
            local.session.close()


def season_sort_key(season):
    index_number = season.get("indexNumber")
    if index_number == 0:
        return (1, 10_000, season.get("name", "").casefold())
    if index_number is None:
        return (1, 9_999, season.get("name", "").casefold())
    return (0, index_number, season.get("name", "").casefold())


def episode_sort_key(episode):
    number = episode.get("episodeNumber")
    return (number is None, number if number is not None else 99_999, episode.get("name", "").casefold())


def ensure_tv_cache_schema(connection):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS series_season_cache (
            series_id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            fetched_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS season_episode_cache (
            season_id TEXT PRIMARY KEY,
            series_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            fetched_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_season_episode_cache_series
            ON season_episode_cache(series_id);
        """
    )
    connection.commit()


class BoundedCatalogueServer(ThreadingHTTPServer):
    """Bound in-flight handlers so upstream stalls cannot spawn unlimited threads."""
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, *args, **kwargs):
        self.request_slots = threading.BoundedSemaphore(MAX_API_THREADS)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.request_slots.acquire(blocking=False):
            try:
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Connection: close\r\n"
                    b"Retry-After: 1\r\n"
                    b"Content-Length: 0\r\n\r\n"
                )
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.request_slots.release()


class CatalogueHandler(BaseHTTPRequestHandler):
    server_version = "BeyondGlimpseCatalogue/2"
    jellyfin = None
    library_cache = {"fetched_at": 0, "items": []}
    channel_cache = {"fetched_at": 0, "channels": [], "available": False}
    channel_lock = threading.Lock()

    def log_message(self, fmt, *args):
        print(f"catalogue-api: {self.address_string()} - {fmt % args}", flush=True)

    def send_json(self, status, payload, *, cache_control="no-store"):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, status, body, content_type, *, cache_control="public, max-age=2592000, immutable"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/healthz":
                return self.send_json(200, {"status": "ok", "service": "catalogue-api"})
            if parsed.path == "/api/status":
                return self.handle_status()
            if parsed.path == "/api/items":
                return self.handle_items(parse_qs(parsed.query))
            if parsed.path == "/api/genres":
                return self.handle_genres(parse_qs(parsed.query))
            if parsed.path == "/api/channels":
                return self.handle_channels(parse_qs(parsed.query))
            if parsed.path == "/api/libraries":
                return self.handle_libraries(parse_qs(parsed.query))
            if parsed.path.startswith("/api/item/"):
                suffix = unquote(parsed.path[len("/api/item/"):])
                if suffix.endswith("/seasons"):
                    series_id = suffix[:-len("/seasons")]
                    return self.handle_seasons(series_id)
                if suffix.endswith("/episodes"):
                    series_id = suffix[:-len("/episodes")]
                    return self.handle_episodes(series_id, parse_qs(parsed.query))
                return self.handle_item(suffix)
            if parsed.path.startswith("/internal/channel-logo/"):
                return self.handle_channel_logo(parsed.path)
            if parsed.path.startswith("/internal/poster/"):
                return self.handle_poster(parsed.path)
            return self.send_json(404, {"error": "not found"})
        except BrokenPipeError:
            return
        except Exception as exc:
            print(f"catalogue-api ERROR {parsed.path}: {exc}", flush=True)
            return self.send_json(500, {"error": "catalogue request failed"})

    def handle_status(self):
        connection = open_db(readonly=True)
        try:
            movies = connection.execute("SELECT COUNT(*) FROM items WHERE media_type='movie'").fetchone()[0]
            tvshows = connection.execute("SELECT COUNT(*) FROM items WHERE media_type='tvshow'").fetchone()[0]
            payload = {
                "state": get_meta(connection, "sync_state", "starting"),
                "bootstrapComplete": get_meta(connection, "bootstrap_complete", "0") == "1",
                "movies": movies,
                "tvShows": tvshows,
                "progressItems": int(get_meta(connection, "progress_items", "0") or 0),
                "progressLibrary": get_meta(connection, "progress_library", ""),
                "lastBootstrap": int(get_meta(connection, "last_bootstrap", "0") or 0),
                "lastIncremental": int(get_meta(connection, "last_incremental", "0") or 0),
                "lastReconcile": int(get_meta(connection, "last_reconcile", "0") or 0),
                "search": "fts5" if get_meta(connection, "fts_enabled", "0") == "1" else "like",
                "jellyfinConfigured": self.jellyfin is not None,
            }
            return self.send_json(200, payload)
        finally:
            connection.close()

    @staticmethod
    def media_type(query):
        value = (query.get("type") or ["movie"])[0]
        return "tvshow" if value in {"tvshow", "tvshows", "series"} else "movie"

    @staticmethod
    def library_id(query):
        value = ((query.get("library") or [""])[0] or "").strip()
        if len(value) > 128:
            return ""
        return value

    @classmethod
    def cached_libraries(cls):
        if cls.jellyfin is None:
            return []
        now = int(time.time())
        cached_at = int(cls.library_cache.get("fetched_at") or 0)
        cached_items = cls.library_cache.get("items") or []
        if cached_items and now - cached_at < LIBRARY_CACHE_TTL_SECONDS:
            return cached_items
        items = cls.jellyfin.eligible_libraries()
        cls.library_cache = {"fetched_at": now, "items": items}
        return items

    def handle_libraries(self, query):
        media_type = self.media_type(query)
        connection = open_db(readonly=True)
        try:
            try:
                libraries = json.loads(get_meta(connection, "libraries_json", "[]") or "[]")
            except (ValueError, TypeError):
                libraries = []
            if not libraries:
                # No library list has been stored yet (first index still running):
                # fall back to Jellyfin's live list so names can be shown, with
                # counts from whatever has been indexed so far.
                try:
                    libraries = self.cached_libraries()
                except Exception as exc:
                    # Live discovery is a best-effort first-run fallback, never a
                    # prerequisite for serving the existing local catalogue.
                    print(f"catalogue-api library list unavailable: {exc}", flush=True)
                    libraries = []
            if not isinstance(libraries, list):
                libraries = []
            libraries = [
                lib for lib in libraries
                if isinstance(lib, dict) and lib.get("media_type") == media_type
            ]
            settled = get_meta(connection, "bootstrap_complete", "0") == "1"
            counts = {
                str(row["library_id"]): int(row["count"])
                for row in connection.execute(
                    """
                    SELECT library_id, COUNT(*) AS count
                    FROM items
                    WHERE media_type=?
                    GROUP BY library_id
                    """,
                    (media_type,),
                ).fetchall()
            }
            payload = []
            for library in libraries:
                library_id = str(library.get("id") or "")
                if not library_id:
                    continue
                payload.append(
                    {
                        "id": library_id,
                        "name": str(library.get("name") or ""),
                        "type": media_type,
                        "count": counts.get(library_id, 0),
                    }
                )
            payload.sort(key=lambda value: value["name"].casefold())
            return self.send_json(
                200,
                {
                    "type": media_type,
                    "total": sum(counts.values()),
                    "libraries": payload,
                },
                # Counts move while the first index runs; only cache once settled.
                cache_control="public, max-age=300" if settled else "no-store",
            )
        finally:
            connection.close()

    def handle_items(self, query):
        media_type = self.media_type(query)
        library_id = self.library_id(query)
        try:
            limit = max(1, min(MAX_PAGE_SIZE, int((query.get("limit") or [DEFAULT_PAGE_SIZE])[0])))
            offset = max(0, min(1_000_000, int((query.get("offset") or [0])[0])))
        except (TypeError, ValueError):
            return self.send_json(400, {"error": "invalid pagination"})

        search = ((query.get("q") or [""])[0] or "").strip()[:160]
        genre = ((query.get("genre") or [""])[0] or "").strip()[:160]
        sort = ((query.get("sort") or ["title"])[0] or "title").strip().lower()
        order = ((query.get("order") or [""])[0] or "").strip().lower()

        if sort in {"date", "dateadded", "date-added", "added", "recent"}:
            order_sql = "i.date_added ASC, i.id ASC" if order == "asc" else "i.date_added DESC, i.id ASC"
        elif sort in {"year", "release", "released"}:
            order_sql = "i.year ASC, i.sort_title ASC, i.id ASC" if order == "asc" else "i.year DESC, i.sort_title ASC, i.id ASC"
        else:
            order_sql = "i.sort_title DESC, i.id DESC" if order == "desc" else "i.sort_title ASC, i.id ASC"

        where = ["i.media_type = ?"]
        params = [media_type]
        if library_id:
            where.append("i.library_id = ?")
            params.append(library_id)

        connection = open_db(readonly=True)
        try:
            fts_query = safe_fts_query(search) if search else None
            if search and fts_query and get_meta(connection, "fts_enabled", "0") == "1":
                where.append("i.id IN (SELECT id FROM items_fts WHERE items_fts MATCH ?)")
                params.append(fts_query)
            elif search:
                where.append("i.sort_title LIKE ?")
                params.append(f"%{search.casefold()}%")

            if genre and genre.lower() != "all":
                where.append("EXISTS (SELECT 1 FROM item_genres g WHERE g.item_id=i.id AND g.genre=?)")
                params.append(genre)

            sql = "SELECT i.* FROM items i WHERE " + " AND ".join(where) + f" ORDER BY {order_sql} LIMIT ? OFFSET ?"
            rows = connection.execute(sql, (*params, limit + 1, offset)).fetchall()
            has_more = len(rows) > limit
            rows = rows[:limit]
            items = [public_item(row) for row in rows]
            return self.send_json(
                200,
                {
                    "items": items,
                    "offset": offset,
                    "limit": limit,
                    "nextOffset": offset + len(items) if has_more else None,
                    "hasMore": has_more,
                    "type": media_type,
                    "query": search,
                    "genre": genre,
                    "library": library_id,
                },
                cache_control="public, max-age=15",
            )
        finally:
            connection.close()

    def handle_genres(self, query):
        media_type = self.media_type(query)
        library_id = self.library_id(query)
        connection = open_db(readonly=True)
        try:
            where = ["i.media_type=?"]
            params = [media_type]
            if library_id:
                where.append("i.library_id=?")
                params.append(library_id)
            rows = connection.execute(
                """
                SELECT g.genre, COUNT(*) AS count
                FROM item_genres g
                JOIN items i ON i.id=g.item_id
                WHERE """ + " AND ".join(where) + """
                GROUP BY g.genre
                ORDER BY g.genre COLLATE NOCASE
                """,
                params,
            ).fetchall()
            return self.send_json(
                200,
                {
                    "library": library_id,
                    "genres": [{"name": row["genre"], "count": row["count"]} for row in rows],
                },
                cache_control="public, max-age=60",
            )
        finally:
            connection.close()

    def handle_item(self, item_id):
        if not item_id or len(item_id) > 128:
            return self.send_json(404, {"error": "item not found"})
        connection = open_db(initialize=False)
        try:
            row = connection.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            if row is None:
                return self.send_json(404, {"error": "item not found"})
            base = public_item(row)
            detail_row = connection.execute(
                "SELECT detail_json,fetched_at FROM item_details WHERE item_id=?", (item_id,)
            ).fetchone()
            now = int(time.time())
            if detail_row and now - int(detail_row["fetched_at"]) < DETAIL_TTL_SECONDS:
                detail = json.loads(detail_row["detail_json"])
                base.update(detail)
                base["detailCached"] = True
                return self.send_json(200, base, cache_control="private, max-age=60")

            if self.jellyfin is not None:
                try:
                    user_id = self.jellyfin.resolve_user_id(connection)
                    remote = self.jellyfin.fetch_detail(user_id, item_id)
                    if remote:
                        detail = detail_from_api(remote, row["media_type"])
                        connection.execute(
                            """
                            INSERT INTO item_details(item_id,detail_json,fetched_at) VALUES(?,?,?)
                            ON CONFLICT(item_id) DO UPDATE SET
                                detail_json=excluded.detail_json,
                                fetched_at=excluded.fetched_at
                            """,
                            (item_id, json.dumps(detail, ensure_ascii=False, separators=(",", ":")), now),
                        )
                        connection.commit()
                        base.update(detail)
                        base["detailCached"] = False
                        return self.send_json(200, base, cache_control="private, max-age=60")
                except requests.RequestException as exc:
                    print(f"catalogue-api detail fetch failed for {item_id}: {exc}", flush=True)

            if detail_row:
                base.update(json.loads(detail_row["detail_json"]))
                base["detailStale"] = True
            else:
                base["detailUnavailable"] = True
            return self.send_json(200, base)
        finally:
            connection.close()

    @staticmethod
    def series_row(connection, series_id):
        if not series_id or len(series_id) > 128:
            return None
        return connection.execute(
            "SELECT id,title FROM items WHERE id=? AND media_type='tvshow'",
            (series_id,),
        ).fetchone()

    def fetch_seasons_payload(self, connection, series_id):
        cached = connection.execute(
            "SELECT payload_json,fetched_at FROM series_season_cache WHERE series_id=?",
            (series_id,),
        ).fetchone()
        now = int(time.time())
        if cached and now - int(cached["fetched_at"]) < EPISODE_CACHE_TTL_SECONDS:
            return json.loads(cached["payload_json"]), True, False

        stale = json.loads(cached["payload_json"]) if cached else None
        if self.jellyfin is None:
            if stale is not None:
                return stale, True, True
            raise RuntimeError("Jellyfin is not configured")

        try:
            user_id = self.jellyfin.resolve_user_id(connection)
            data = self.jellyfin.get_json(
                "/Items",
                params={
                    "ParentId": series_id,
                    "Recursive": "false",
                    "IncludeItemTypes": "Season",
                    "EnableTotalRecordCount": "false",
                    "EnableUserData": "false",
                    "EnableImages": "false",
                    "UserId": user_id,
                },
            )
            seasons = []
            for item in data.get("Items") or []:
                parsed = season_from_api(item)
                if parsed is not None:
                    seasons.append(parsed)
            present = present_season_ids(self.jellyfin, user_id, [season["id"] for season in seasons])
            seasons = [season for season in seasons if season["id"] in present]
            seasons.sort(key=season_sort_key)
            payload = {"seriesId": series_id, "seasons": seasons}
            connection.execute(
                """
                INSERT INTO series_season_cache(series_id,payload_json,fetched_at) VALUES(?,?,?)
                ON CONFLICT(series_id) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    fetched_at=excluded.fetched_at
                """,
                (series_id, json.dumps(payload, ensure_ascii=False, separators=(",", ":")), now),
            )
            connection.commit()
            return payload, False, False
        except requests.RequestException:
            if stale is not None:
                return stale, True, True
            raise

    def handle_seasons(self, series_id):
        connection = open_db(initialize=False)
        try:
            if self.series_row(connection, series_id) is None:
                return self.send_json(404, {"error": "series not found"})
            try:
                payload, cached, stale = self.fetch_seasons_payload(connection, series_id)
            except RuntimeError:
                return self.send_json(503, {"error": "Jellyfin is not configured"})
            result = dict(payload)
            result["cached"] = cached
            if stale:
                result["stale"] = True
            return self.send_json(200, result, cache_control="private, max-age=60")
        finally:
            connection.close()

    def handle_episodes(self, series_id, query):
        season_id = ((query.get("seasonId") or [""])[0] or "").strip()
        if not season_id or len(season_id) > 128:
            return self.send_json(400, {"error": "seasonId is required"})

        connection = open_db(initialize=False)
        try:
            if self.series_row(connection, series_id) is None:
                return self.send_json(404, {"error": "series not found"})

            try:
                seasons_payload, _, _ = self.fetch_seasons_payload(connection, series_id)
            except RuntimeError:
                return self.send_json(503, {"error": "Jellyfin is not configured"})
            allowed_seasons = {
                str(season.get("id") or "") for season in seasons_payload.get("seasons") or []
            }
            if season_id not in allowed_seasons:
                return self.send_json(404, {"error": "season not found for series"})

            cached = connection.execute(
                """
                SELECT payload_json,fetched_at FROM season_episode_cache
                WHERE season_id=? AND series_id=?
                """,
                (season_id, series_id),
            ).fetchone()
            now = int(time.time())
            if cached and now - int(cached["fetched_at"]) < EPISODE_CACHE_TTL_SECONDS:
                payload = json.loads(cached["payload_json"])
                payload["cached"] = True
                return self.send_json(200, payload, cache_control="private, max-age=60")

            stale = json.loads(cached["payload_json"]) if cached else None
            if self.jellyfin is None:
                if stale is not None:
                    stale["cached"] = True
                    stale["stale"] = True
                    return self.send_json(200, stale)
                return self.send_json(503, {"error": "Jellyfin is not configured"})

            try:
                user_id = self.jellyfin.resolve_user_id(connection)
                data = self.jellyfin.get_json(
                    "/Items",
                    params={
                        "ParentId": season_id,
                        "Recursive": "false",
                        "IncludeItemTypes": "Episode",
                        "Fields": "Overview,RunTimeTicks,PremiereDate",
                        "EnableTotalRecordCount": "false",
                        "EnableUserData": "false",
                        "EnableImages": "false",
                        "UserId": user_id,
                    },
                )
                episodes = []
                for item in data.get("Items") or []:
                    parsed = episode_from_api(item)
                    if parsed is not None:
                        episodes.append(parsed)
                episodes.sort(key=episode_sort_key)
                payload = {
                    "seriesId": series_id,
                    "seasonId": season_id,
                    "episodes": episodes,
                }
                connection.execute(
                    """
                    INSERT INTO season_episode_cache(season_id,series_id,payload_json,fetched_at)
                    VALUES(?,?,?,?)
                    ON CONFLICT(season_id) DO UPDATE SET
                        series_id=excluded.series_id,
                        payload_json=excluded.payload_json,
                        fetched_at=excluded.fetched_at
                    """,
                    (
                        season_id,
                        series_id,
                        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                        now,
                    ),
                )
                connection.commit()
                payload["cached"] = False
                return self.send_json(200, payload, cache_control="private, max-age=60")
            except requests.RequestException:
                if stale is not None:
                    stale["cached"] = True
                    stale["stale"] = True
                    return self.send_json(200, stale)
                raise
        finally:
            connection.close()

    @classmethod
    def load_channels(cls):
        """Return a short-lived list; one caller refreshes while others use stale data.

        Never hold the refresh lock while another HTTP handler waits on Jellyfin:
        a slow upstream should not consume every API worker on a public site.
        """
        now = int(time.time())
        cache = cls.channel_cache
        if cls.jellyfin is None or now - int(cache["fetched_at"]) < CHANNEL_CACHE_TTL_SECONDS:
            return cache
        if not cls.channel_lock.acquire(blocking=False):
            return cls.channel_cache
        try:
            now = int(time.time())
            cache = cls.channel_cache
            if now - int(cache["fetched_at"]) < CHANNEL_CACHE_TTL_SECONDS:
                return cache
            try:
                connection = open_db(initialize=False)
                try:
                    user_id = cls.jellyfin.resolve_user_id(connection)
                finally:
                    connection.close()
                channels, offset = [], 0
                # Bound the number of upstream rows, including invalid entries.
                # This also prevents servers that report inconsistent totals
                # from keeping a request worker busy indefinitely.
                while offset < MAX_CHANNELS:
                    data = cls.jellyfin.get_json(
                        "/LiveTv/Channels",
                        params={
                            "UserId": user_id,
                            "AddCurrentProgram": "true",
                            "EnableImages": "true",
                            "ImageTypeLimit": 1,
                            "EnableImageTypes": "Primary",
                            "EnableUserData": "false",
                            "StartIndex": offset,
                            "Limit": min(1000, MAX_CHANNELS - offset),
                        },
                    )
                    items = data.get("Items") or []
                    if not isinstance(items, list):
                        raise ValueError("Jellyfin returned an invalid channel page")
                    if not items:
                        break
                    for item in items[:MAX_CHANNELS - offset]:
                        channel = channel_from_api(item)
                        if channel is not None:
                            channels.append(channel)
                    offset += min(len(items), MAX_CHANNELS - offset)
                    total = data.get("TotalRecordCount")
                    if isinstance(total, int) and offset >= total:
                        break
                channels.sort(key=lambda c: (parse_channel_number(c["number"]), c["name"].casefold()))
                cls.channel_cache = {"fetched_at": now, "channels": channels, "available": bool(channels)}
            except (requests.RequestException, RuntimeError, ValueError, TypeError, AttributeError, KeyError) as exc:
                print(f"catalogue-api live TV channels unavailable: {exc}", flush=True)
                # Authentication/access loss or removal of Live TV must revoke
                # publicly cached listings instead of exposing the last good one.
                status = (exc.response.status_code
                          if isinstance(exc, requests.HTTPError) and exc.response is not None
                          else None)
                if status in {401, 403, 404}:
                    cls.channel_cache = {"fetched_at": now, "channels": [], "available": False}
                else:
                    # Transient upstream failures retain the previous good list.
                    cls.channel_cache = {
                        "fetched_at": now,
                        "channels": cache["channels"],
                        "available": cache["available"],
                    }
            return cls.channel_cache
        finally:
            cls.channel_lock.release()

    def handle_channels(self, query):
        try:
            limit = max(1, min(MAX_PAGE_SIZE, int((query.get("limit") or [DEFAULT_PAGE_SIZE])[0])))
            offset = max(0, min(1_000_000, int((query.get("offset") or [0])[0])))
        except (TypeError, ValueError):
            return self.send_json(400, {"error": "invalid pagination"})
        search = ((query.get("q") or [""])[0] or "").strip()[:160].casefold()
        kind = ((query.get("kind") or [""])[0] or "").strip().lower()

        cache = self.load_channels()
        channels = cache["channels"]
        if kind in {"tv", "radio"}:
            channels = [c for c in channels if c["kind"] == kind]
        if search:
            channels = [c for c in channels if search in c["name"].casefold() or search in c["number"].casefold()]
        page = channels[offset:offset + limit]
        has_more = offset + len(page) < len(channels)
        return self.send_json(
            200,
            {
                "available": bool(cache["available"]),
                "total": len(channels),
                "offset": offset,
                "limit": limit,
                "hasMore": has_more,
                "nextOffset": offset + len(page) if has_more else None,
                "channels": [
                    {
                        "id": c["id"],
                        "name": c["name"],
                        "number": c["number"],
                        "kind": c["kind"],
                        "logo": f"/channel-logo/{c['id']}/{c['logoTag']}.png" if c["logoTag"] else "",
                        "now": c["now"],
                    }
                    for c in page
                ],
            },
            cache_control="private, max-age=15",
        )

    def handle_channel_logo(self, path):
        suffix = path[len("/internal/channel-logo/"):]
        if not suffix.endswith(".png") or "/" not in suffix:
            return self.send_json(404, {"error": "logo not found"})
        channel_id, tag_file = suffix.split("/", 1)
        tag = tag_file[:-4]
        if (not re.fullmatch(r"[0-9A-Za-z-]{1,128}", channel_id)
                or not re.fullmatch(r"[0-9A-Za-z._~-]{1,256}", tag)):
            return self.send_json(404, {"error": "logo not found"})
        # Only logos of channels Jellyfin actually listed (with the exact tag).
        known = {c["id"]: c["logoTag"] for c in self.channel_cache["channels"]}
        if known.get(channel_id) != tag:
            return self.send_json(404, {"error": "logo not found"})
        if self.jellyfin is None:
            return self.send_json(503, {"error": "Jellyfin is not configured"})
        try:
            body, content_type = self.jellyfin.fetch_poster(channel_id, tag, 240, 90, image_format="png")
        except requests.RequestException:
            return self.send_json(502, {"error": "logo upstream unavailable"})
        # Never serve active image formats such as SVG/HTML from this public,
        # same-origin endpoint, even if Jellyfin returns an unexpected MIME.
        safe_type = content_type.split(";", 1)[0].strip().lower()
        if safe_type not in {"image/png", "image/jpeg", "image/webp", "image/gif", "image/avif"}:
            return self.send_json(502, {"error": "unsupported channel logo format"})
        return self.send_bytes(200, body, safe_type)

    def handle_poster(self, path):
        suffix = path[len("/internal/poster/"):]
        if not suffix.endswith(".jpg") or "/" not in suffix:
            return self.send_json(404, {"error": "poster not found"})
        item_id, tag_file = suffix.split("/", 1)
        tag = tag_file[:-4]
        if not item_id or not tag or len(item_id) > 128 or len(tag) > 256:
            return self.send_json(404, {"error": "poster not found"})

        connection = open_db(readonly=True)
        try:
            row = connection.execute("SELECT poster_tag FROM items WHERE id=?", (item_id,)).fetchone()
            if row is None or not row["poster_tag"] or row["poster_tag"] != tag:
                return self.send_json(404, {"error": "poster not found"})
        finally:
            connection.close()

        if self.jellyfin is None:
            return self.send_json(503, {"error": "Jellyfin is not configured"})
        try:
            body, content_type = self.jellyfin.fetch_poster(item_id, tag, POSTER_WIDTH, POSTER_QUALITY)
        except requests.RequestException:
            return self.send_json(502, {"error": "poster upstream unavailable"})
        return self.send_bytes(200, body, content_type)


def main():
    connection = open_db()
    try:
        ensure_tv_cache_schema(connection)
    finally:
        connection.close()
    if os.environ.get("JELLYFIN_URL") and os.environ.get("JELLYFIN_TOKEN"):
        CatalogueHandler.jellyfin = JellyfinClient()
        state = "with Jellyfin upstream"
    else:
        CatalogueHandler.jellyfin = None
        state = "without Jellyfin upstream"
    server = BoundedCatalogueServer((HOST, PORT), CatalogueHandler)
    print(f"Catalogue API listening on http://{HOST}:{PORT} ({state})", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
