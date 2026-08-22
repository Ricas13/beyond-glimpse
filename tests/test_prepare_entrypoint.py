import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "prepare_entrypoint.py"
spec = importlib.util.spec_from_file_location("prepare_entrypoint", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PrepareEntrypointTests(unittest.TestCase):
    def test_rewrites_original_media_cron_lines_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "entrypoint.sh"
            path.write_text("#!/bin/bash\n" + module.OLD_JELLYFIN + "\n" + module.OLD_EMBY + "\n", encoding="utf-8")
            self.assertTrue(module.prepare(path))
            first = path.read_text(encoding="utf-8")
            self.assertIn('INCREMENTAL_SYNC=\\"${INCREMENTAL_SYNC:-true}\\"', first)
            self.assertIn('FULL_RECONCILE_HOURS=\\"${FULL_RECONCILE_HOURS:-24}\\"', first)
            self.assertIn('SYNC_OVERLAP_SECONDS=\\"${SYNC_OVERLAP_SECONDS:-300}\\"', first)
            self.assertIn('PAGE_SIZE=\\"${PAGE_SIZE:-500}\\"', first)
            self.assertNotIn('--token', first)
            self.assertFalse(module.prepare(path))
            self.assertEqual(first, path.read_text(encoding="utf-8"))

    def test_upgrades_previous_beyond_glimpse_cron_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "entrypoint.sh"
            path.write_text("#!/bin/bash\n" + module.PREVIOUS_JELLYFIN + "\n" + module.PREVIOUS_EMBY + "\n", encoding="utf-8")
            self.assertTrue(module.prepare(path))
            content = path.read_text(encoding="utf-8")
            self.assertIn('INCREMENTAL_SYNC=\\"${INCREMENTAL_SYNC:-true}\\"', content)
            self.assertIn('STATE_DIR=\\"/app/state/jellyfin\\"', content)
            self.assertIn('STATE_DIR=\\"/app/state/emby\\"', content)


if __name__ == "__main__":
    unittest.main()
