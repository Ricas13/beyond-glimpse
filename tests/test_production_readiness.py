import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


initial_sync = load_module("initial_sync", "scripts/initial_sync.py")


class ProductionReadinessTests(unittest.TestCase):
    def test_version_and_dependency_are_pinned(self):
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        core = (ROOT / "scripts" / "catalogue_core.py").read_text(encoding="utf-8")
        # APP_VERSION is the Jellyfin client/protocol version and may remain on
        # the compatible minor line for a browser-only patch release.
        self.assertRegex(core, r'APP_VERSION = "\d+\.\d+\.\d+"')
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(requirements, ["requests==2.34.2"])
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("pip install --no-cache-dir -r /app/requirements.txt", dockerfile)
        self.assertIn("COPY VERSION /app/VERSION", dockerfile)
        self.assertIn("catalogue_service.py", dockerfile)
        self.assertIn("catalogue_sync.py", dockerfile)

    def test_initial_sync_commands_keep_tokens_out_of_argv(self):
        with patch.dict(
            os.environ,
            {
                "JELLYFIN_URL": "http://jellyfin:8096",
                "JELLYFIN_TOKEN": "secret-token",
                "EMBY_URL": "http://emby:8096",
                "EMBY_TOKEN": "other-secret",
                "PLEX_URL": "http://plex:32400",
                "PLEX_TOKEN": "plex-secret",
            },
            clear=False,
        ):
            commands = [initial_sync.command_for(server) for server in ("jellyfin", "emby", "plex")]
        flattened = json.dumps(commands)
        self.assertNotIn("secret-token", flattened)
        self.assertNotIn("other-secret", flattened)
        self.assertNotIn("plex-secret", flattened)
        self.assertNotIn("--token", flattened)
        self.assertIn("catalogue_sync.py", flattened)
        self.assertIn("--bootstrap", flattened)
        self.assertNotIn("ultralight_jellyfin.py", json.dumps(initial_sync.command_for("jellyfin")))

    def test_initial_sync_status_is_atomic_and_publicly_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = initial_sync.WEB_STATUS
            try:
                initial_sync.WEB_STATUS = Path(tmp) / "catalogue-status.json"
                initial_sync.atomic_status("syncing", message="Preparing catalogue")
                payload = json.loads(initial_sync.WEB_STATUS.read_text(encoding="utf-8"))
                self.assertEqual(payload["state"], "syncing")
                self.assertEqual(payload["message"], "Preparing catalogue")
                self.assertNotIn("token", json.dumps(payload).lower())
                self.assertFalse((Path(tmp) / ".catalogue-status.json.tmp").exists())
            finally:
                initial_sync.WEB_STATUS = old

    def test_supervisor_starts_web_and_api_before_one_shot_sync(self):
        config = (ROOT / "config" / "supervisord.conf").read_text(encoding="utf-8")
        self.assertIn("[program:catalogue-api]", config)
        self.assertIn("catalogue_service.py", config)
        self.assertIn("[program:initial-sync]", config)
        self.assertIn("/app/scripts/initial_sync.py", config)
        self.assertIn("[program:catalogue-scheduler]", config)
        self.assertIn("catalogue_scheduler.py", config)
        self.assertIn("priority=10", config)
        self.assertIn("priority=15", config)
        self.assertIn("priority=30", config)
        self.assertIn("priority=40", config)
        self.assertIn("autorestart=false", config)

    def test_browser_startup_helper_can_expose_partial_catalogue(self):
        runtime = (ROOT / "web" / "startup-status.js").read_text(encoding="utf-8")
        self.assertIn("hasUsableCatalogue", runtime)
        self.assertIn("tryLoadPartialCatalogue", runtime)
        self.assertIn("/api/status", runtime)
        self.assertIn("await loadMedia()", runtime)
        self.assertIn("cache: 'no-store'", runtime)
        self.assertNotIn("innerHTML", runtime)

    def test_readme_is_beyond_glimpse_not_old_install_guide(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertTrue(readme.startswith("# Beyond Glimpse"))
        self.assertIn("docker-compose.traefik.yml", readme)
        self.assertIn("smoke_test.py", readme)
        self.assertIn("256 MiB", readme)
        self.assertNotIn("curl -o Glimpse/docker-compose.yml", readme)
        self.assertNotIn("MD5 Checksum Verification", readme)


if __name__ == "__main__":
    unittest.main()
