import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "prepare_entrypoint.py"
spec = importlib.util.spec_from_file_location("prepare_entrypoint", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture(*lines):
    return "#!/bin/bash\n" + "\n".join(lines) + "\n" + module.PROXY_MARKER + "\n"


class PrepareEntrypointTests(unittest.TestCase):
    def test_rewrites_original_media_cron_lines_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "entrypoint.sh"
            path.write_text(fixture(module.OLD_JELLYFIN, module.OLD_EMBY), encoding="utf-8")
            self.assertTrue(module.prepare(path))
            first = path.read_text(encoding="utf-8")
            self.assertIn('INCREMENTAL_SYNC=\\"${INCREMENTAL_SYNC:-true}\\"', first)
            self.assertIn('FULL_RECONCILE_HOURS=\\"${FULL_RECONCILE_HOURS:-24}\\"', first)
            self.assertIn('PAGE_SIZE=\\"${PAGE_SIZE:-500}\\"', first)
            self.assertIn('/app/scripts/ultralight_jellyfin.py --output /app/data/jellyfin', first)
            self.assertIn('/app/scripts/sync_runner.py --server-type emby', first)
            self.assertIn('/app/scripts/configure_poster_proxy.py', first)
            self.assertNotIn('--token', first)
            self.assertFalse(module.prepare(path))
            self.assertEqual(first, path.read_text(encoding="utf-8"))

    def test_upgrades_previous_observability_cron_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "entrypoint.sh"
            path.write_text(fixture(module.CURRENT_JELLYFIN, module.NEW_EMBY), encoding="utf-8")
            self.assertTrue(module.prepare(path))
            content = path.read_text(encoding="utf-8")
            self.assertIn('/app/scripts/ultralight_jellyfin.py', content)
            self.assertNotIn('/app/scripts/jellyfin_data_fetcher.py --output /app/data/jellyfin >>', content)

    def test_wraps_initial_sync_without_token_command_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "entrypoint.sh"
            path.write_text(
                fixture(
                    module.OLD_JELLYFIN,
                    module.OLD_EMBY,
                    module.INITIAL_JELLYFIN_OLD,
                    module.INITIAL_EMBY_OLD,
                ),
                encoding="utf-8",
            )
            self.assertTrue(module.prepare(path))
            content = path.read_text(encoding="utf-8")
            self.assertIn(module.INITIAL_JELLYFIN_NEW, content)
            self.assertIn(module.INITIAL_EMBY_NEW, content)
            self.assertNotIn('jellyfin_data_fetcher.py --url "$JELLYFIN_URL" --token', content)
            self.assertNotIn('jellyfin_data_fetcher.py --url "$EMBY_URL" --token', content)


if __name__ == "__main__":
    unittest.main()
