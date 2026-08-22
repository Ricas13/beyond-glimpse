#!/usr/bin/env python3

import json
import os
import time
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
EPISODE_CACHE_TTL_SECONDS = max(300, int(os.environ.get("EPISODE_CACHE_TTL_SECONDS", str(6 * 3600))))
POSTER_WIDTH = max(64, min(1000, int(os.environ.get("POSTER_PROXY_MAX_WIDTH", "320"))))
POSTER_QUALITY = max(30, min(95, int(os.environ.get("POSTER_PROXY_QUALITY", "72"))))
LIBRARY_CACHE_TTL_SECONDS = max(60, int(os.environ.get("LIBRARY_CACHE_TTL_SECONDS", "3600")))


def int_or_none(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


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


class CatalogueHandler(BaseHTTPRequestHandler):
    server_version = "BeyondGlimpseCatalogue/2"
    jellyfin = None
    library_cache = {"fetched_at": 0, "items": []}

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
            if parsed.path.startswith("/internal/poster/"):
                return self.handle_poster(parsed.path)
            return self.send_json(404, {"error": "not found"})
        except BrokenPipeError:
            return
        except Exception as exc:
            print(f"catalogue-api ERROR {parsed.path}: {exc}", flush=True)
            return self.send_json(500, {"error": "catalogue request failed"})

    def handle_status(self):
        connection = open_db()
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
        libraries = [
            library for library in self.cached_libraries()
            if library.get("media_type") == media_type
        ]
        connection = open_db()
        try:
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
                cache_control="public, max-age=300",
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

        search = ((query.get("q") or [""])[0] or "").strip()
        genre = ((query.get("genre") or [""])[0] or "").strip()
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

        connection = open_db()
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
            )
        finally:
            connection.close()

    def handle_genres(self, query):
        media_type = self.media_type(query)
        library_id = self.library_id(query)
        connection = open_db()
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
        connection = open_db()
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
        ensure_tv_cache_schema(connection)
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
        connection = open_db()
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

        connection = open_db()
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

            ensure_tv_cache_schema(connection)
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

    def handle_poster(self, path):
        suffix = path[len("/internal/poster/"):]
        if not suffix.endswith(".jpg") or "/" not in suffix:
            return self.send_json(404, {"error": "poster not found"})
        item_id, tag_file = suffix.split("/", 1)
        tag = tag_file[:-4]
        if not item_id or not tag or len(item_id) > 128 or len(tag) > 256:
            return self.send_json(404, {"error": "poster not found"})

        connection = open_db()
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
    if os.environ.get("JELLYFIN_URL") and os.environ.get("JELLYFIN_TOKEN"):
        CatalogueHandler.jellyfin = JellyfinClient()
        state = "with Jellyfin upstream"
    else:
        CatalogueHandler.jellyfin = None
        state = "without Jellyfin upstream"
    server = ThreadingHTTPServer((HOST, PORT), CatalogueHandler)
    server.daemon_threads = True
    print(f"Catalogue API listening on http://{HOST}:{PORT} ({state})", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
