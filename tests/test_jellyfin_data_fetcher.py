import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "jellyfin_data_fetcher.py"
spec = importlib.util.spec_from_file_location("jellyfin_data_fetcher", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
JellyfinDataFetcher = module.JellyfinDataFetcher


class JellyfinDataFetcherTests(unittest.TestCase):
    def make_fetcher(self, server_type="jellyfin"):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        fetcher = JellyfinDataFetcher(
            "http://example.test:8096",
            "secret-token",
            output_dir=tempdir.name,
            server_type=server_type,
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


if __name__ == "__main__":
    unittest.main()
