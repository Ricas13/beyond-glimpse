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
POSTER_WIDTH = max(64, min(1000, int(os.environ.get("POSTER_PROXY_MAX_WIDTH", "320"))))
POSTER_QUALITY = max(30, min(95, int(os.environ.get("POSTER_PROXY_QUALITY", "72"))))


class CatalogueHandler(BaseHTTPRequestHandler):
    server_version = "BeyondGlimpseCatalogue/2"
    jellyfin = None

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
            if parsed.path.startswith("/api/item/"):
                item_id = unquote(parsed.path[len("/api/item/"):])
                return self.handle_item(item_id)
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

    def handle_items(self, query):
        media_type = self.media_type(query)
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
                },
            )
        finally:
            connection.close()

    def handle_genres(self, query):
        media_type = self.media_type(query)
        connection = open_db()
        try:
            rows = connection.execute(
                """
                SELECT g.genre, COUNT(*) AS count
                FROM item_genres g
                JOIN items i ON i.id=g.item_id
                WHERE i.media_type=?
                GROUP BY g.genre
                ORDER BY g.genre COLLATE NOCASE
                """,
                (media_type,),
            ).fetchall()
            return self.send_json(
                200,
                {"genres": [{"name": row["genre"], "count": row["count"]} for row in rows]},
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
