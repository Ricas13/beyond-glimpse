import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "jellyfin_data_fetcher.py"
spec = importlib.util.spec_from_file_location("jellyfin_data_fetcher", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
JellyfinDataFetcher = module.JellyfinDataFetcher


class JellyfinDataFetcherTests(unittest.TestCase):
    def make_fetcher(self, server_type="jellyfin", **kwargs):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        fetcher = JellyfinDataFetcher(
            "http://example.test:8096",
            "secret-token",
            output_dir=tempdir.name,
            server_type=server_type,
            **kwargs,
        )
        return fetcher, Path(tempdir.name)

    def test_jellyfin_uses_modern_mediabrowser_authorization(self):
        fetcher, _ = self.make_fetcher("jellyfin")
        auth = fetcher.session.headers.get("Authorization", "")
        self.assertIn('MediaBrowser Token="secret-token"', auth)
        self.assertNotIn("X-Emby-Token", fetcher.session.headers)

    def test_emby_keeps_legacy_token_header(self):
        fetcher, _ = self.make_fetcher("emby")
        self.assertEqual(fetcher.session.headers.get("X-Emby-Token"), "secret-token")
        self.assertNotIn("Authorization", fetcher.session.headers)

    def test_image_tag_skips_unchanged_artwork_without_http_request(self):
        fetcher, root = self.make_fetcher()
        poster = root / "posters" / "movies" / "abc.jpg"
        poster.write_bytes(b"existing")
        key = fetcher.image_key(poster)
        fetcher.image_state[key] = {"tag": "tag-1", "maxWidth": 500, "quality": 82}
        fetcher.session.get = Mock(side_effect=AssertionError("HTTP should not be called"))

        self.assertTrue(fetcher.sync_image("abc", "Primary", "tag-1", poster, 500))
        fetcher.session.get.assert_not_called()

    def test_changed_image_is_downloaded_with_resizing_parameters(self):
        fetcher, root = self.make_fetcher()
        poster = root / "posters" / "movies" / "abc.jpg"
        response = Mock()
        response.raise_for_status.return_value = None
        response.iter_content.return_value = [b"new-image"]
        fetcher.session.get = Mock(return_value=response)

        self.assertTrue(fetcher.sync_image("abc", "Primary", "tag-2", poster, 500))
        self.assertEqual(poster.read_bytes(), b"new-image")
        _, kwargs = fetcher.session.get.call_args
        self.assertEqual(kwargs["params"]["tag"], "tag-2")
        self.assertEqual(kwargs["params"]["maxWidth"], 500)
        self.assertEqual(kwargs["params"]["quality"], 82)
        self.assertEqual(kwargs["params"]["format"], "jpg")

    def test_series_counts_do_not_make_per_series_api_calls(self):
        fetcher, _ = self.make_fetcher()
        fetcher.session.get = Mock(side_effect=AssertionError("No API call expected"))
        info = fetcher.process_media_item(
            {
                "Id": "series-1",
                "Name": "Example",
                "EpisodeCount": 24,
                "ChildCount": 3,
            },
            "tvshow",
        )
        self.assertEqual(info["leafCount"], 24)
        self.assertEqual(info["childCount"], 3)
        fetcher.session.get.assert_not_called()

    def test_atomic_json_write_replaces_target(self):
        fetcher, root = self.make_fetcher()
        target = root / "movies.json"
        target.write_text('[{"old":true}]\n', encoding="utf-8")
        fetcher.atomic_write_json(target, [{"new": True}])
        self.assertEqual(target.read_text(encoding="utf-8"), '[{"new":true}]\n')

    def test_state_is_stored_outside_public_output_tree(self):
        fetcher, root = self.make_fetcher()
        self.assertFalse(fetcher.image_state_file.is_relative_to(root))
        self.assertFalse(fetcher.catalog_db_file.is_relative_to(root))

    def test_backdrops_are_disabled_by_default(self):
        fetcher, _ = self.make_fetcher()
        self.assertFalse(fetcher.download_backdrops)

    def test_legacy_public_state_can_be_loaded_for_migration(self):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        output = root / "data" / "jellyfin"
        output.mkdir(parents=True)
        legacy_state = output / "image-state.json"
        legacy_state.write_text('{"posters/movies/a.jpg":{"tag":"old"}}', encoding="utf-8")

        fetcher = JellyfinDataFetcher(
            "http://example.test:8096",
            "secret-token",
            output_dir=output,
            server_type="jellyfin",
        )
        self.assertIn("posters/movies/a.jpg", fetcher.image_state)

    def test_incremental_fetch_uses_min_date_last_saved_without_total_count(self):
        fetcher, _ = self.make_fetcher(page_size=2)
        calls = []

        def request_json(path, params=None):
            calls.append((path, dict(params or {})))
            return {"Items": [{"Id": "1"}]}

        fetcher.request_json = request_json
        items = fetcher.fetch_library_content(
            "user-1",
            "lib-1",
            "movie",
            min_date_last_saved="2026-08-22T10:00:00Z",
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(calls[0][0], "/Items")
        self.assertEqual(calls[0][1]["UserId"], "user-1")
        self.assertEqual(calls[0][1]["MinDateLastSaved"], "2026-08-22T10:00:00Z")
        self.assertEqual(calls[0][1]["EnableTotalRecordCount"], "false")

    def test_first_state_requires_full_reconcile(self):
        fetcher, _ = self.make_fetcher()
        connection = fetcher.open_catalog_db()
        self.addCleanup(connection.close)
        mode, reason = fetcher.choose_sync_mode(
            connection,
            "user-1",
            [{"id": "lib-1", "name": "Movies", "media_type": "movie"}],
            datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(mode, "full")
        self.assertIn("schema", reason)

    def test_due_reconciliation_forces_full_sync(self):
        fetcher, _ = self.make_fetcher(full_reconcile_hours=24)
        connection = fetcher.open_catalog_db()
        self.addCleanup(connection.close)
        now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
        libraries = [{"id": "lib-1", "name": "Movies", "media_type": "movie"}]
        fetcher.set_meta(connection, "schema_version", module.CATALOG_SCHEMA_VERSION)
        fetcher.set_meta(connection, "config_signature", fetcher.config_signature("user-1", libraries))
        fetcher.set_meta(connection, "watermark", fetcher.format_api_datetime(now - timedelta(hours=1)))
        fetcher.set_meta(connection, "last_full_reconcile", fetcher.format_api_datetime(now - timedelta(hours=25)))
        connection.commit()
        mode, _ = fetcher.choose_sync_mode(connection, "user-1", libraries, now)
        self.assertEqual(mode, "full")

    def test_incremental_sync_updates_only_changed_rows_and_uses_overlap(self):
        fetcher, _ = self.make_fetcher(sync_overlap_seconds=300)
        connection = fetcher.open_catalog_db()
        self.addCleanup(connection.close)

        old_entry = {
            "id": "movie-1",
            "library_id": "lib-1",
            "media_type": "movie",
            "media_json": json.dumps({"id": "movie-1", "title": "Old"}, separators=(",", ":")),
            "poster_tag": None,
            "backdrop_tag": None,
        }
        fetcher.upsert_catalog_entry(connection, old_entry)
        connection.commit()

        captured = []
        changed_item = {"Id": "movie-1", "Name": "New", "ImageTags": {}, "BackdropImageTags": []}
        new_item = {"Id": "movie-2", "Name": "Second", "ImageTags": {}, "BackdropImageTags": []}

        def fetch_content(user_id, library_id, media_type, min_date_last_saved=None):
            captured.append(min_date_last_saved)
            return [changed_item, new_item]

        fetcher.fetch_library_content = fetch_content
        watermark = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
        changed = fetcher.run_incremental_sync(
            connection,
            "user-1",
            [{"id": "lib-1", "name": "Movies", "media_type": "movie"}],
            watermark,
        )
        self.assertEqual(changed, 2)
        self.assertEqual(captured, ["2026-08-22T11:55:00Z"])
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM items").fetchone()[0], 2)
        title = json.loads(connection.execute("SELECT media_json FROM items WHERE id='movie-1'").fetchone()[0])["title"]
        self.assertEqual(title, "New")

    def test_full_reconcile_removes_deleted_items(self):
        fetcher, _ = self.make_fetcher()
        connection = fetcher.open_catalog_db()
        self.addCleanup(connection.close)
        for item_id in ("movie-1", "movie-2"):
            fetcher.upsert_catalog_entry(
                connection,
                {
                    "id": item_id,
                    "library_id": "lib-1",
                    "media_type": "movie",
                    "media_json": json.dumps({"id": item_id, "title": item_id}, separators=(",", ":")),
                    "poster_tag": None,
                    "backdrop_tag": None,
                },
            )
        connection.commit()

        fetcher.fetch_library_content = lambda *args, **kwargs: [
            {"Id": "movie-1", "Name": "Still here", "ImageTags": {}, "BackdropImageTags": []}
        ]
        fetcher.run_full_reconcile(
            connection,
            "user-1",
            [{"id": "lib-1", "name": "Movies", "media_type": "movie"}],
        )
        ids = [row[0] for row in connection.execute("SELECT id FROM items ORDER BY id")]
        self.assertEqual(ids, ["movie-1"])

    def test_catalogue_state_scales_to_large_synthetic_library(self):
        fetcher, _ = self.make_fetcher()
        connection = fetcher.open_catalog_db()
        self.addCleanup(connection.close)
        connection.execute("BEGIN")
        for index in range(10_000):
            item_id = f"movie-{index:05d}"
            fetcher.upsert_catalog_entry(
                connection,
                {
                    "id": item_id,
                    "library_id": "lib-1",
                    "media_type": "movie",
                    "media_json": json.dumps({"id": item_id, "title": f"Movie {index}"}, separators=(",", ":")),
                    "poster_tag": f"tag-{index}",
                    "backdrop_tag": None,
                },
            )
        connection.commit()
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM items").fetchone()[0], 10_000)
        self.assertEqual(len(fetcher.public_catalogue(connection, "movie")), 10_000)


if __name__ == "__main__":
    unittest.main()
