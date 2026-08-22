import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import jellyfin_data_fetcher as base  # noqa: E402
import ultralight_jellyfin as ultra  # noqa: E402
import configure_poster_proxy as proxy  # noqa: E402


class UltraLightCatalogueTests(unittest.TestCase):
    def test_rich_session_does_not_repeat_read_timeouts(self):
        fake = SimpleNamespace(server_type="jellyfin", jellyfin_token="secret")
        session = ultra.ultralight_build_session(fake)
        self.assertEqual(session.get_adapter("https://").max_retries.read, 0)
        self.assertEqual(session.get_adapter("https://").max_retries.connect, 3)

    def test_full_sync_starts_small_and_halves_page_after_read_timeout(self):
        calls = []

        def request_json(path, params=None):
            calls.append((path, dict(params or {})))
            if len(calls) == 1:
                raise base.requests.exceptions.ReadTimeout("read timed out")
            return {"Items": [{"Id": "movie-1"}]}

        fake = SimpleNamespace(
            page_size=500,
            server_type="jellyfin",
            request_json=request_json,
        )
        items = ultra.ultralight_fetch_library_content(fake, "user-1", "lib-1", "movie")
        self.assertEqual([item["Id"] for item in items], ["movie-1"])
        self.assertEqual([call[1]["Limit"] for call in calls], [100, 50])
        self.assertEqual([call[1]["StartIndex"] for call in calls], [0, 0])

    def test_incremental_rich_fetch_keeps_configured_page_size(self):
        calls = []

        def request_json(path, params=None):
            calls.append((path, dict(params or {})))
            return {"Items": []}

        fake = SimpleNamespace(
            page_size=500,
            server_type="jellyfin",
            request_json=request_json,
        )
        ultra.ultralight_fetch_library_content(
            fake,
            "user-1",
            "lib-1",
            "movie",
            min_date_last_saved="2026-08-22T10:00:00Z",
        )
        self.assertEqual(calls[0][1]["Limit"], 500)
        self.assertEqual(calls[0][1]["MinDateLastSaved"], "2026-08-22T10:00:00Z")

    def test_catalog_entry_does_not_download_poster(self):
        calls = []
        fake = SimpleNamespace(
            download_backdrops=False,
            process_media_item=lambda item, media_type: {
                "id": item["Id"],
                "title": item["Name"],
                "year": 2026,
                "addedAt": 1,
                "genres": [],
                "actors": [],
            },
            sync_image=lambda *args, **kwargs: calls.append((args, kwargs)),
        )
        entry = ultra.ultralight_build_catalog_entry(
            fake,
            {"Id": "ab1234", "Name": "Test", "ImageTags": {"Primary": "poster-v1"}},
            "movie",
            "library-1",
        )
        self.assertEqual(entry["poster_tag"], "poster-v1")
        self.assertEqual(calls, [])

    def test_public_index_is_compact_and_details_are_sharded(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            fetcher = base.JellyfinDataFetcher.__new__(base.JellyfinDataFetcher)
            fetcher.output_dir = output
            fetcher.www_data_uid = None
            fetcher.www_data_gid = None

            connection = sqlite3.connect(":memory:")
            connection.execute(
                """
                CREATE TABLE items (
                    id TEXT PRIMARY KEY,
                    library_id TEXT,
                    media_type TEXT,
                    media_json TEXT,
                    poster_tag TEXT,
                    backdrop_tag TEXT
                )
                """
            )
            movie = {
                "id": "ab1234",
                "title": "Huge Movie",
                "year": 2026,
                "addedAt": 123,
                "genres": ["Drama"],
                "summary": "A very long synopsis that should not be in the startup index.",
                "actors": [{"name": "Actor", "role": "Role"}],
                "contentRating": "15",
                "duration": 7200000,
            }
            connection.execute(
                "INSERT INTO items VALUES (?, ?, ?, ?, ?, ?)",
                ("ab1234", "lib", "movie", json.dumps(movie), "tag123", None),
            )
            connection.commit()

            movies, tv = ultra.ultralight_write_public_catalogue(fetcher, connection)
            self.assertEqual((movies, tv), (1, 0))

            index = json.loads((output / "movies.json").read_text(encoding="utf-8"))
            self.assertEqual(set(index[0]), {"id", "title", "year", "addedAt", "genres", "posterTag"})
            self.assertNotIn("summary", index[0])
            self.assertNotIn("actors", index[0])

            shard = json.loads((output / "details" / "movies" / "ab.json").read_text(encoding="utf-8"))
            self.assertEqual(shard["ab1234"]["summary"], movie["summary"])
            self.assertEqual(shard["ab1234"]["actors"][0]["name"], "Actor")

    def test_proxy_configuration_is_bounded_and_tag_versioned(self):
        config = proxy.build_config("http://jellyfin:8096", "abcdef123456", 320, 72)
        self.assertIn("proxy_cache poster_cache;", config)
        self.assertIn("$poster_image_tag", config)
        self.assertIn("maxWidth=320", config)
        self.assertIn("quality=72", config)
        self.assertIn("MediaBrowser Token=\"abcdef123456\"", config)

    def test_proxy_rejects_base_path_urls(self):
        with self.assertRaises(ValueError):
            proxy.build_config("https://example.com/jellyfin", "abcdef", 320, 72)

    def test_web_runtime_uses_lazy_details_and_proxy_posters(self):
        runtime = (ROOT / "web" / "large-library.js").read_text(encoding="utf-8")
        self.assertIn("/details/${plural}/${shard}.json", runtime)
        self.assertIn("/poster/${encodeURIComponent(item.id)}", runtime)
        self.assertIn("window.__beyondGlimpseUltraLight = true", runtime)

    def test_service_worker_never_caches_catalogue_or_posters(self):
        worker = (ROOT / "web" / "sw.js").read_text(encoding="utf-8")
        self.assertIn("url.pathname.startsWith('/data/')", worker)
        self.assertIn("url.pathname.startsWith('/poster/')", worker)
        self.assertNotIn("staleWhileRevalidate", worker)
        self.assertNotIn("DYNAMIC_CACHE", worker)

    def test_nginx_poster_cache_has_hard_limit(self):
        nginx = (ROOT / "config" / "nginx.conf").read_text(encoding="utf-8")
        self.assertIn("max_size=256m", nginx)
        self.assertIn("include /etc/nginx/poster-proxy.inc;", nginx)


if __name__ == "__main__":
    unittest.main()
