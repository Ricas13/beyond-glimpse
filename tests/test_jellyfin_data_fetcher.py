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

    def test_state_is_stored_outside_public_output_tree(self):
        fetcher, root = self.make_fetcher()
        self.assertFalse(fetcher.image_state_file.is_relative_to(root))
        self.assertEqual(fetcher.image_state_file.name, "image-state.json")

    def test_backdrops_are_disabled_by_default(self):
        fetcher, _ = self.make_fetcher()
        self.assertFalse(fetcher.download_backdrops)

    def test_legacy_public_state_is_migrated_then_removed(self):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        output = root / "data" / "jellyfin"
        output.mkdir(parents=True)
        legacy_state = output / "image-state.json"
        legacy_checksums = output / "checksums.pkl"
        legacy_state.write_text('{"posters/movies/a.jpg":{"tag":"old"}}', encoding="utf-8")
        legacy_checksums.write_bytes(b"legacy")

        fetcher = JellyfinDataFetcher(
            "http://example.test:8096",
            "secret-token",
            output_dir=output,
            server_type="jellyfin",
        )
        self.assertIn("posters/movies/a.jpg", fetcher.image_state)
        fetcher.atomic_write_json(fetcher.image_state_file, fetcher.image_state, compact=False)
        for legacy_path in (fetcher.legacy_image_state_file, fetcher.legacy_checksums_file):
            legacy_path.unlink(missing_ok=True)
        self.assertTrue(fetcher.image_state_file.exists())
        self.assertFalse(legacy_state.exists())
        self.assertFalse(legacy_checksums.exists())


if __name__ == "__main__":
    unittest.main()
