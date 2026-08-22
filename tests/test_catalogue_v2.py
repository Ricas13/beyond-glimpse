import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import catalogue_core as core  # noqa: E402
import catalogue_service as service  # noqa: E402
import catalogue_sync as sync  # noqa: E402


class FakeBootstrapClient:
    def __init__(self):
        self.calls = []

    def resolve_user_id(self, connection=None):
        if connection is not None:
            core.set_meta(connection, "user_id", "user-1")
            connection.commit()
        return "user-1"

    def eligible_libraries(self):
        return [{"id": "lib-1", "name": "Movies", "media_type": "movie"}]

    def get_server_time(self):
        return 1_700_000_000

    def light_params(self, user_id, **kwargs):
        params = dict(kwargs)
        params["UserId"] = user_id
        return params

    def get_json(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        start = int((params or {}).get("start_index", (params or {}).get("StartIndex", 0)))
        if start:
            return {"Items": []}
        return {
            "Items": [
                {
                    "Id": "movie-1",
                    "Name": "Alpha Film",
                    "ProductionYear": 2026,
                    "DateCreated": "2026-08-22T12:00:00Z",
                    "Genres": ["Drama", "Mystery"],
                    "ImageTags": {"Primary": "tag-a"},
                },
                {
                    "Id": "movie-2",
                    "Name": "Beta Film",
                    "ProductionYear": 2025,
                    "DateCreated": "2026-08-21T12:00:00Z",
                    "Genres": ["Comedy"],
                    "ImageTags": {},
                },
            ]
        }


class CatalogueV2Tests(unittest.TestCase):
    def make_db(self):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        path = Path(tempdir.name) / "catalogue-v2.db"
        connection = core.open_db(path)
        self.addCleanup(connection.close)
        return connection, path

    def test_schema_uses_wal_search_genres_and_lazy_details(self):
        connection, _ = self.make_db()
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
        }
        self.assertIn("items", tables)
        self.assertIn("item_genres", tables)
        self.assertIn("item_details", tables)
        self.assertIn(core.get_meta(connection, "fts_enabled"), {"0", "1"})
        journal = connection.execute("PRAGMA journal_mode").fetchone()[0].lower()
        self.assertEqual(journal, "wal")

    def test_light_item_contains_no_rich_metadata(self):
        record = core.light_item_from_api(
            {
                "Id": "x",
                "Name": "Example",
                "ProductionYear": 2026,
                "DateCreated": "2026-08-22T12:00:00Z",
                "Genres": ["Drama"],
                "Overview": "This must not enter the browse row",
                "People": [{"Type": "Actor", "Name": "Someone"}],
                "ImageTags": {"Primary": "tag"},
            },
            "lib",
            "movie",
            1,
        )
        self.assertEqual(record["title"], "Example")
        self.assertEqual(record["poster_tag"], "tag")
        self.assertNotIn("Overview", record)
        self.assertNotIn("People", record)
        self.assertNotIn("summary", record)
        self.assertNotIn("actors", record)

    def test_bootstrap_indexes_lightweight_rows_and_marks_ready(self):
        connection, _ = self.make_db()
        client = FakeBootstrapClient()
        with patch.dict(os.environ, {"CATALOGUE_BOOTSTRAP_PAGE_SIZE": "1000"}, clear=False):
            count = sync.bootstrap(client, connection)
        self.assertEqual(count, 2)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM items").fetchone()[0], 2)
        self.assertEqual(core.get_meta(connection, "bootstrap_complete"), "1")
        self.assertEqual(core.get_meta(connection, "sync_state"), "ready")
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM item_details").fetchone()[0], 0)
        genres = {
            row[0]
            for row in connection.execute("SELECT genre FROM item_genres WHERE item_id='movie-1'")
        }
        self.assertEqual(genres, {"Drama", "Mystery"})

    def test_paginated_api_never_returns_more_than_requested(self):
        connection, path = self.make_db()
        for index in range(35):
            record = core.light_item_from_api(
                {
                    "Id": f"movie-{index:03d}",
                    "Name": f"Movie {index:03d}",
                    "ProductionYear": 2026,
                    "Genres": ["Drama" if index % 2 else "Comedy"],
                },
                "lib",
                "movie",
                1,
            )
            core.upsert_light_item(connection, record)
        connection.commit()

        handler = object.__new__(service.CatalogueHandler)
        handler.send_json = lambda status, payload, **kwargs: (status, payload)
        with patch.dict(os.environ, {"CATALOGUE_DB": str(path)}, clear=False):
            status, payload = handler.handle_items({"type": ["movie"], "limit": ["10"], "offset": ["0"]})
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["items"]), 10)
        self.assertTrue(payload["hasMore"])
        self.assertEqual(payload["nextOffset"], 10)

    def test_server_side_search_and_genre_filter(self):
        connection, path = self.make_db()
        for item_id, title, genre in (
            ("1", "Alien Arrival", "Science Fiction"),
            ("2", "Quiet Drama", "Drama"),
        ):
            record = core.light_item_from_api(
                {"Id": item_id, "Name": title, "Genres": [genre]},
                "lib", "movie", 1,
            )
            core.upsert_light_item(connection, record)
        connection.commit()
        handler = object.__new__(service.CatalogueHandler)
        handler.send_json = lambda status, payload, **kwargs: (status, payload)
        with patch.dict(os.environ, {"CATALOGUE_DB": str(path)}, clear=False):
            status, payload = handler.handle_items(
                {"type": ["movie"], "q": ["Alien"], "genre": ["Science Fiction"]}
            )
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in payload["items"]], ["1"])

    def test_library_filter_scopes_items_and_genres(self):
        connection, path = self.make_db()
        for item_id, title, library_id, genre in (
            ("1", "Library A Drama", "lib-a", "Drama"),
            ("2", "Library B Comedy", "lib-b", "Comedy"),
        ):
            record = core.light_item_from_api(
                {"Id": item_id, "Name": title, "Genres": [genre]},
                library_id, "movie", 1,
            )
            core.upsert_light_item(connection, record)
        connection.commit()

        handler = object.__new__(service.CatalogueHandler)
        handler.send_json = lambda status, payload, **kwargs: (status, payload)
        with patch.dict(os.environ, {"CATALOGUE_DB": str(path)}, clear=False):
            status, payload = handler.handle_items(
                {"type": ["movie"], "library": ["lib-a"], "limit": ["10"]}
            )
            genre_status, genre_payload = handler.handle_genres(
                {"type": ["movie"], "library": ["lib-a"]}
            )

        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in payload["items"]], ["1"])
        self.assertEqual(payload["library"], "lib-a")
        self.assertEqual(genre_status, 200)
        self.assertEqual(genre_payload["genres"], [{"name": "Drama", "count": 1}])

    def test_library_endpoint_uses_jellyfin_names_and_sqlite_counts(self):
        connection, path = self.make_db()
        for item_id, library_id in (("1", "lib-a"), ("2", "lib-a"), ("3", "lib-b")):
            record = core.light_item_from_api(
                {"Id": item_id, "Name": f"Movie {item_id}", "Genres": []},
                library_id, "movie", 1,
            )
            core.upsert_light_item(connection, record)
        connection.commit()

        handler = object.__new__(service.CatalogueHandler)
        handler.send_json = lambda status, payload, **kwargs: (status, payload)
        libraries = [
            {"id": "lib-b", "name": "Kids Movies", "media_type": "movie"},
            {"id": "lib-a", "name": "Main Movies", "media_type": "movie"},
            {"id": "tv-a", "name": "Main TV", "media_type": "tvshow"},
        ]
        with patch.object(service.CatalogueHandler, "cached_libraries", return_value=libraries):
            with patch.dict(os.environ, {"CATALOGUE_DB": str(path)}, clear=False):
                status, payload = handler.handle_libraries({"type": ["movie"]})

        self.assertEqual(status, 200)
        self.assertEqual(payload["total"], 3)
        self.assertEqual(
            [(entry["id"], entry["name"], entry["count"]) for entry in payload["libraries"]],
            [("lib-b", "Kids Movies", 1), ("lib-a", "Main Movies", 2)],
        )

    def test_rich_detail_parser_is_separate_from_browse_row(self):
        detail = core.detail_from_api(
            {
                "Overview": "Synopsis",
                "CommunityRating": 8.1,
                "Studios": [{"Name": "Studio"}],
                "People": [
                    {"Type": "Actor", "Name": "Actor One", "Role": "Lead"},
                    {"Type": "Director", "Name": "Director"},
                ],
                "RunTimeTicks": 7_200_000_000,
                "OfficialRating": "15",
                "Taglines": ["Tagline"],
            },
            "movie",
        )
        self.assertEqual(detail["summary"], "Synopsis")
        self.assertEqual(detail["actors"][0]["name"], "Actor One")
        self.assertEqual(detail["studio"], "Studio")
        self.assertGreater(detail["duration"], 0)

    def test_poster_service_code_requires_catalogue_id_and_exact_tag(self):
        source = (ROOT / "scripts" / "catalogue_service.py").read_text(encoding="utf-8")
        self.assertIn("SELECT poster_tag FROM items WHERE id=?", source)
        self.assertIn('row["poster_tag"] != tag', source)
        proxy = (ROOT / "scripts" / "configure_poster_proxy.py").read_text(encoding="utf-8")
        self.assertNotIn("MediaBrowser Token", proxy)
        self.assertIn("/internal/poster/", proxy)

    def test_library_browse_runtime_is_server_side_and_bounded(self):
        source = (ROOT / "web" / "library-browse.js").read_text(encoding="utf-8")
        self.assertIn("/api/libraries", source)
        self.assertIn("url.searchParams.set('library'", source)
        self.assertNotIn("JELLYFIN_TOKEN", source)
        self.assertNotIn("JELLYFIN_URL", source)

    def test_scheduler_defaults_to_ten_minutes(self):
        source = (ROOT / "scripts" / "catalogue_scheduler.py").read_text(encoding="utf-8")
        self.assertIn("DEFAULT_INTERVAL = 600", source)


if __name__ == "__main__":
    unittest.main()
