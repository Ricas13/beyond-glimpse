import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "prepare_entrypoint.py"
spec = importlib.util.spec_from_file_location("prepare_entrypoint", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PrepareEntrypointTests(unittest.TestCase):
    def test_rewrites_media_cron_lines_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "entrypoint.sh"
            path.write_text(
                "#!/bin/bash\n"
                + module.OLD_JELLYFIN
                + "\n"
                + module.OLD_EMBY
                + "\n",
                encoding="utf-8",
            )
            self.assertTrue(module.prepare(path))
            first = path.read_text(encoding="utf-8")
            self.assertIn('DOWNLOAD_BACKDROPS=\\"${DOWNLOAD_BACKDROPS:-false}\\"', first)
            self.assertIn('STATE_DIR=\\"/app/state/jellyfin\\"', first)
            self.assertIn('STATE_DIR=\\"/app/state/emby\\"', first)
            self.assertNotIn('--token', first)

            self.assertFalse(module.prepare(path))
            self.assertEqual(first, path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
