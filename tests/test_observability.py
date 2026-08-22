import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, relative_path):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync_runner = load_module("sync_runner", "scripts/sync_runner.py")
status_module = load_module("status_module", "scripts/status.py")


class SyncRunnerTests(unittest.TestCase):
    def test_parses_sync_summary_lines(self):
        status = {}
        sync_runner.parse_sync_line(status, "Sync mode: incremental (metadata watermark is current)\n")
        sync_runner.parse_sync_line(status, "Incremental sync changed 17 catalogue records\n")
        sync_runner.parse_sync_line(status, "Removed 3 stale cached images\n")
        sync_runner.parse_sync_line(status, "Completed incremental sync: 44742 movies, 7676 TV shows\n")
        self.assertEqual(status["mode"], "incremental")
        self.assertEqual(status["reason"], "metadata watermark is current")
        self.assertEqual(status["changedRecords"], 17)
        self.assertEqual(status["staleImagesRemoved"], 3)
        self.assertEqual(status["movies"], 44742)
        self.assertEqual(status["tvShows"], 7676)

    def test_child_environment_isolates_multi_server_credentials(self):
        with patch.dict(
            os.environ,
            {
                "JELLYFIN_URL": "http://jellyfin",
                "JELLYFIN_TOKEN": "jf-secret",
                "EMBY_URL": "http://emby",
                "EMBY_TOKEN": "emby-secret",
            },
            clear=False,
        ):
            emby_env = sync_runner.child_environment("emby")
            jellyfin_env = sync_runner.child_environment("jellyfin")
        self.assertNotIn("JELLYFIN_URL", emby_env)
        self.assertNotIn("JELLYFIN_TOKEN", emby_env)
        self.assertEqual(emby_env["EMBY_URL"], "http://emby")
        self.assertNotIn("EMBY_URL", jellyfin_env)
        self.assertNotIn("EMBY_TOKEN", jellyfin_env)
        self.assertEqual(jellyfin_env["JELLYFIN_URL"], "http://jellyfin")

    def test_reads_private_catalog_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            db = sqlite3.connect(state / "catalog.db")
            db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute(
                "CREATE TABLE items(id TEXT PRIMARY KEY, library_id TEXT, media_type TEXT, media_json TEXT, poster_tag TEXT, backdrop_tag TEXT)"
            )
            db.executemany(
                "INSERT INTO meta(key, value) VALUES(?, ?)",
                [
                    ("schema_version", "1"),
                    ("watermark", "2026-08-22T12:00:00Z"),
                    ("last_full_reconcile", "2026-08-22T06:00:00Z"),
                ],
            )
            db.executemany(
                "INSERT INTO items(id, library_id, media_type, media_json) VALUES(?, 'lib', ?, '{}')",
                [("m1", "movie"), ("m2", "movie"), ("s1", "tvshow")],
            )
            db.commit()
            db.close()

            snapshot = sync_runner.read_catalog_state(state)
            self.assertEqual(snapshot["movies"], 2)
            self.assertEqual(snapshot["tvShows"], 1)
            self.assertEqual(snapshot["watermark"], "2026-08-22T12:00:00Z")
            self.assertGreater(snapshot["catalogDbBytes"], 0)


class OperatorStatusTests(unittest.TestCase):
    def test_collect_reports_storage_without_exposing_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_root = root / "data"
            state_root = root / "state"
            data_dir = data_root / "jellyfin"
            state_dir = state_root / "jellyfin"
            (data_dir / "posters" / "movies").mkdir(parents=True)
            (data_dir / "backdrops" / "movies").mkdir(parents=True)
            state_dir.mkdir(parents=True)
            (data_dir / "posters" / "movies" / "1.jpg").write_bytes(b"poster")
            (data_dir / "movies.json").write_text("[]\n", encoding="utf-8")
            (data_dir / "tvshows.json").write_text("[]\n", encoding="utf-8")
            (state_dir / "catalog.db").write_bytes(b"db")
            (state_dir / "sync-status.json").write_text(
                json.dumps({"state": "success", "mode": "incremental", "movies": 10, "tvShows": 3}),
                encoding="utf-8",
            )

            result = status_module.collect("jellyfin", data_root=data_root, state_root=state_root)
            self.assertEqual(result["posterFiles"], 1)
            self.assertEqual(result["posterBytes"], 6)
            self.assertEqual(result["movies"], 10)
            self.assertNotIn("token", json.dumps(result).lower())


class TraefikDeploymentTests(unittest.TestCase):
    def test_traefik_compose_does_not_publish_host_port(self):
        compose = (ROOT / "docker-compose.traefik.yml").read_text(encoding="utf-8")
        self.assertNotIn("ports:", compose)
        self.assertIn('expose:\n      - "80"', compose)
        self.assertIn("traefik.http.services.beyond-glimpse.loadbalancer.server.port=80", compose)
        self.assertIn("loadbalancer.healthcheck.path=/healthz", compose)
        self.assertIn("traefik.docker.network=${TRAEFIK_NETWORK:-media_net}", compose)

    def test_nginx_and_docker_expose_healthcheck(self):
        nginx = (ROOT / "config" / "nginx.conf").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("location = /healthz", nginx)
        self.assertIn("service\\\":\\\"beyond-glimpse", nginx.replace('"', '\\"'))
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn("http://127.0.0.1/healthz", dockerfile)

    def test_env_file_is_ignored_but_example_is_trackable(self):
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".env", ignore)
        self.assertIn("!.env.traefik.example", ignore)


if __name__ == "__main__":
    unittest.main()
