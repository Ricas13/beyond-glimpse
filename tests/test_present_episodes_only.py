import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import catalogue_service as service  # noqa: E402
import catalogue_present_only as present  # noqa: E402
from catalogue_core import get_meta, open_db  # noqa: E402


class PresentEpisodesOnlyTests(unittest.TestCase):
    def test_virtual_and_missing_episode_placeholders_are_rejected(self):
        original = getattr(service, "_present_only_original_episode_from_api", None)
        service._present_only_original_episode_from_api = lambda item: {"id": item.get("Id")}
        try:
            self.assertIsNone(present.present_episode_from_api({"Id": "1", "IsMissing": True}))
            self.assertIsNone(present.present_episode_from_api({"Id": "2", "IsVirtualItem": True}))
            self.assertIsNone(present.present_episode_from_api({"Id": "3", "LocationType": "Virtual"}))
            self.assertEqual(
                present.present_episode_from_api({"Id": "4", "LocationType": "FileSystem"}),
                {"id": "4"},
            )
            # STRM-backed items are real Jellyfin media items and must remain visible.
            self.assertEqual(
                present.present_episode_from_api({"Id": "5", "Path": "/media/show/S01E01.strm"}),
                {"id": "5"},
            )
        finally:
            if original is None:
                delattr(service, "_present_only_original_episode_from_api")
            else:
                service._present_only_original_episode_from_api = original

    def test_episode_requests_add_server_side_missing_filter(self):
        source = (SCRIPTS / "catalogue_present_only.py").read_text(encoding="utf-8")
        self.assertIn('effective["IsMissing"] = "false"', source)
        self.assertIn('effective.get("IncludeItemTypes") == "Episode"', source)

    def test_old_episode_cache_is_invalidated_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "catalogue-v2.db"
            with patch.dict(os.environ, {"CATALOGUE_DB": str(db)}, clear=False):
                connection = open_db()
                try:
                    service.ensure_tv_cache_schema(connection)
                    connection.execute(
                        "INSERT INTO season_episode_cache(season_id,series_id,payload_json,fetched_at) VALUES(?,?,?,?)",
                        ("season-1", "series-1", '{"episodes":[{"id":"virtual"}]}', 1),
                    )
                    connection.commit()
                finally:
                    connection.close()

                self.assertTrue(present.invalidate_old_episode_cache_once())
                connection = open_db()
                try:
                    self.assertEqual(
                        connection.execute("SELECT COUNT(*) FROM season_episode_cache").fetchone()[0],
                        0,
                    )
                    self.assertEqual(
                        get_meta(connection, "episode_visibility_policy"),
                        present.EPISODE_VISIBILITY_POLICY,
                    )
                finally:
                    connection.close()

                self.assertFalse(present.invalidate_old_episode_cache_once())


if __name__ == "__main__":
    unittest.main()
