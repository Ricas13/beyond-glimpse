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

    def test_season_and_episode_requests_exclude_virtual_items(self):
        seen = []
        original = service.JellyfinClient.get_json
        had_policy = getattr(service, "_present_only_policy_installed", False)

        def recorder(client, path, params=None):
            seen.append(dict(params or {}))
            return {"Items": []}

        service.JellyfinClient.get_json = recorder
        service._present_only_policy_installed = False
        try:
            present.install_present_episode_policy()
            client = service.JellyfinClient("http://jf", "token")
            client.get_json("/Items", params={"IncludeItemTypes": "Season", "ParentId": "s"})
            client.get_json("/Items", params={"IncludeItemTypes": "Episode", "ParentId": "x"})
            client.get_json("/Items", params={"IncludeItemTypes": "Movie"})
        finally:
            # Undo every monkeypatch so other tests see the pristine service.
            if not had_policy:
                service.episode_from_api = service._present_only_original_episode_from_api
                service.season_from_api = service._present_only_original_season_from_api
                del service._present_only_original_episode_from_api
                del service._present_only_original_season_from_api
                service._present_only_policy_installed = False
            service.JellyfinClient.get_json = original
        season, episode, movie = seen
        self.assertEqual(season["ExcludeLocationTypes"], "Virtual")
        self.assertNotIn("IsMissing", season)
        self.assertEqual(episode["ExcludeLocationTypes"], "Virtual")
        self.assertEqual(episode["IsMissing"], "false")
        self.assertNotIn("ExcludeLocationTypes", movie)

    def test_placeholder_seasons_are_rejected(self):
        service._present_only_original_season_from_api = lambda item: {"id": item.get("Id")}
        try:
            self.assertIsNone(present.present_season_from_api({"Id": "1", "LocationType": "Virtual"}))
            self.assertIsNone(present.present_season_from_api({"Id": "2", "IsMissing": True}))
            self.assertEqual(present.present_season_from_api({"Id": "3"}), {"id": "3"})
        finally:
            del service._present_only_original_season_from_api

    def test_seasons_without_present_episodes_are_dropped(self):
        class Client:
            def get_json(self, path, params=None):
                if params["ParentId"] == "empty":
                    return {"Items": []}
                return {"Items": [{"Id": "e1"}]}

        self.assertEqual(
            service.present_season_ids(Client(), "u", ["s1", "empty", "s3"]), {"s1", "s3"}
        )

    def test_failed_probe_keeps_the_season_visible(self):
        import requests

        class Client:
            def get_json(self, path, params=None):
                if params["ParentId"] == "flaky":
                    raise requests.ConnectionError("boom")
                return {"Items": []}

        self.assertEqual(service.present_season_ids(Client(), "u", ["flaky", "empty"]), {"flaky"})

    def test_virtual_first_page_does_not_hide_a_real_later_episode(self):
        calls = []
        original = service.episode_from_api

        class Client:
            def get_json(self, path, params=None):
                calls.append(params["StartIndex"])
                if params["StartIndex"] == 0:
                    return {"Items": [{"Id": f"v{i}", "LocationType": "Virtual"} for i in range(5)]}
                return {"Items": [{"Id": "real", "LocationType": "FileSystem"}]}

        try:
            service.episode_from_api = lambda item: (
                None if item.get("LocationType") == "Virtual" else original(item)
            )
            self.assertEqual(service.present_season_ids(Client(), "u", ["s"]), {"s"})
        finally:
            service.episode_from_api = original
        self.assertEqual(calls, [0, 5])

    def test_only_virtual_episodes_are_hidden_when_the_page_ends(self):
        original = service.episode_from_api

        class Client:
            def get_json(self, path, params=None):
                if params["StartIndex"] > 0:
                    return {"Items": []}
                return {"Items": [{"Id": "v", "LocationType": "Virtual"}]}

        try:
            service.episode_from_api = lambda item: (
                None if item.get("LocationType") == "Virtual" else original(item)
            )
            self.assertEqual(service.present_season_ids(Client(), "u", ["s"]), set())
        finally:
            service.episode_from_api = original

    def test_bounded_unknown_probe_preserves_season(self):
        calls = []
        original = service.episode_from_api

        class Client:
            def get_json(self, path, params=None):
                calls.append(params["StartIndex"])
                return {"Items": [{"Id": f"v{i}", "LocationType": "Virtual"} for i in range(5)]}

        try:
            service.episode_from_api = lambda item: (
                None if item.get("LocationType") == "Virtual" else original(item)
            )
            self.assertEqual(service.present_season_ids(Client(), "u", ["s"]), {"s"})
        finally:
            service.episode_from_api = original
        self.assertEqual(calls, [0, 5, 10, 15])

    def test_parallel_jellyfin_probes_use_worker_sessions(self):
        original = service.JellyfinClient.get_json
        session_ids = []

        def recorder(self, path, params=None):
            session_ids.append(id(self.session))
            return {"Items": [{"Id": "real"}]}

        service.JellyfinClient.get_json = recorder
        caller = service.JellyfinClient("http://jf", "token")
        try:
            self.assertEqual(
                len(service.present_season_ids(caller, "u", [f"s{i}" for i in range(20)])), 20
            )
            self.assertEqual(len(session_ids), 20)
            self.assertNotIn(id(caller.session), session_ids)
        finally:
            service.JellyfinClient.get_json = original
            caller.session.close()

    def test_probe_requests_are_small_and_do_not_scan_the_series(self):
        calls = []

        class Client:
            def get_json(self, path, params=None):
                calls.append(dict(params))
                return {"Items": [{"Id": "e"}]}

        service.present_season_ids(Client(), "u", [f"s{i}" for i in range(20)])
        self.assertEqual(len(calls), 20)
        for params in calls:
            self.assertEqual(params["Limit"], 5)
            self.assertEqual(params["Recursive"], "false")
            self.assertEqual(params["EnableTotalRecordCount"], "false")
            self.assertTrue(params["ParentId"].startswith("s"))

    def test_no_seasons_means_no_probes(self):
        class Client:
            def get_json(self, path, params=None):
                raise AssertionError("must not be called")

        self.assertEqual(service.present_season_ids(Client(), "u", []), set())

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
                    connection.execute(
                        "INSERT INTO series_season_cache(series_id,payload_json,fetched_at) VALUES(?,?,?)",
                        ("series-1", '{"seasons":[{"id":"virtual-season"}]}', 1),
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
                        connection.execute("SELECT COUNT(*) FROM series_season_cache").fetchone()[0],
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
