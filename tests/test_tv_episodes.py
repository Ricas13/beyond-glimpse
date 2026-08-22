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


class FakeChildrenClient:
    def __init__(self):
        self.calls = []

    def resolve_user_id(self, connection=None):
        return "user-1"

    def get_json(self, path, params=None):
        params = dict(params or {})
        self.calls.append((path, params))
        item_type = params.get("IncludeItemTypes")
        if item_type == "Season":
            return {
                "Items": [
                    {"Id": "season-specials", "Name": "Specials", "IndexNumber": 0},
                    {"Id": "season-2", "Name": "Season 2", "IndexNumber": 2},
                    {"Id": "season-1", "Name": "Season 1", "IndexNumber": 1},
                ]
            }
        if item_type == "Episode":
            return {
                "Items": [
                    {
                        "Id": "episode-2",
                        "Name": "Second",
                        "IndexNumber": 2,
                        "ParentIndexNumber": 1,
                        "RunTimeTicks": 2_700_000_000,
                        "PremiereDate": "2026-08-02T00:00:00Z",
                        "Overview": "Second overview",
                    },
                    {
                        "Id": "episode-1",
                        "Name": "Pilot",
                        "IndexNumber": 1,
                        "ParentIndexNumber": 1,
                        "RunTimeTicks": 3_000_000_000,
                        "PremiereDate": "2026-08-01T00:00:00Z",
                        "Overview": "Pilot overview",
                    },
                ]
            }
        raise AssertionError(params)


class TvEpisodeTests(unittest.TestCase):
    def make_db(self):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        path = Path(tempdir.name) / "catalogue-v2.db"
        connection = core.open_db(path)
        record = core.light_item_from_api(
            {"Id": "series-1", "Name": "Example Series", "Genres": ["Drama"]},
            "tv-lib",
            "tvshow",
            1,
        )
        core.upsert_light_item(connection, record)
        connection.commit()
        connection.close()
        return path

    def make_handler(self, jellyfin):
        handler = object.__new__(service.CatalogueHandler)
        handler.jellyfin = jellyfin
        handler.send_json = lambda status, payload, **kwargs: (status, payload)
        return handler

    def test_parsers_keep_children_compact(self):
        season = service.season_from_api({"Id": "s1", "Name": "Season 1", "IndexNumber": 1})
        self.assertEqual(season, {"id": "s1", "name": "Season 1", "indexNumber": 1})

        episode = service.episode_from_api(
            {
                "Id": "e1",
                "Name": "Pilot",
                "IndexNumber": 1,
                "ParentIndexNumber": 1,
                "RunTimeTicks": 600_000_000,
                "PremiereDate": "2026-08-01T00:00:00Z",
                "Overview": "Overview",
                "People": [{"Name": "Must not leak"}],
            }
        )
        self.assertEqual(episode["episodeNumber"], 1)
        self.assertEqual(episode["seasonNumber"], 1)
        self.assertEqual(episode["runtime"], 60_000)
        self.assertEqual(episode["overview"], "Overview")
        self.assertNotIn("People", episode)

    def test_seasons_are_sorted_regular_first_specials_last(self):
        seasons = [
            {"id": "sp", "name": "Specials", "indexNumber": 0},
            {"id": "s2", "name": "Season 2", "indexNumber": 2},
            {"id": "s1", "name": "Season 1", "indexNumber": 1},
        ]
        seasons.sort(key=service.season_sort_key)
        self.assertEqual([season["id"] for season in seasons], ["s1", "s2", "sp"])

    def test_seasons_and_episodes_are_cached_and_scoped_to_series(self):
        path = self.make_db()
        client = FakeChildrenClient()
        handler = self.make_handler(client)

        with patch.dict(os.environ, {"CATALOGUE_DB": str(path)}, clear=False):
            status, seasons = handler.handle_seasons("series-1")
            self.assertEqual(status, 200)
            self.assertEqual([item["id"] for item in seasons["seasons"]], ["season-1", "season-2", "season-specials"])

            status, episodes = handler.handle_episodes("series-1", {"seasonId": ["season-1"]})
            self.assertEqual(status, 200)
            self.assertEqual([item["id"] for item in episodes["episodes"]], ["episode-1", "episode-2"])

            upstream_calls = len(client.calls)
            status, cached_seasons = handler.handle_seasons("series-1")
            status2, cached_episodes = handler.handle_episodes("series-1", {"seasonId": ["season-1"]})
            self.assertEqual((status, status2), (200, 200))
            self.assertTrue(cached_seasons["cached"])
            self.assertTrue(cached_episodes["cached"])
            self.assertEqual(len(client.calls), upstream_calls)

            status, payload = handler.handle_episodes("series-1", {"seasonId": ["not-this-series"]})
            self.assertEqual(status, 404)
            self.assertEqual(payload["error"], "season not found for series")

    def test_episode_cache_tables_are_persistent_sqlite_state(self):
        path = self.make_db()
        connection = core.open_db(path)
        service.ensure_tv_cache_schema(connection)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        connection.close()
        self.assertIn("series_season_cache", tables)
        self.assertIn("season_episode_cache", tables)

    def test_browser_defaults_to_season_one_and_uses_safe_dom_rendering(self):
        source = (ROOT / "web" / "tv-episodes.js").read_text(encoding="utf-8")
        self.assertIn("Number(season.indexNumber) === 1", source)
        self.assertIn("/seasons", source)
        self.assertIn("/episodes?seasonId=", source)
        self.assertNotIn("innerHTML", source)
        self.assertIn("textContent", source)


if __name__ == "__main__":
    unittest.main()
