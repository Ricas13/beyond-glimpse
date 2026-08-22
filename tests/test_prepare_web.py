import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "prepare_web.py"
spec = importlib.util.spec_from_file_location("prepare_web", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PrepareWebTests(unittest.TestCase):
    def test_injects_production_bootstrap_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.html"
            path.write_text(
                "<html><body><script>\n"
                "        // Initialize on page load\n"
                "        loadMedia();\n"
                "</script></body></html>",
                encoding="utf-8",
            )
            self.assertTrue(module.prepare(path))
            first = path.read_text(encoding="utf-8")
            self.assertIn("__startBeyondGlimpseMedia", first)
            self.assertIn('/large-library.js?v=3', first)
            self.assertIn('/startup-status.js?v=2', first)
            self.assertLess(first.index('/large-library.js?v=3'), first.index('/startup-status.js?v=2'))

            self.assertFalse(module.prepare(path))
            second = path.read_text(encoding="utf-8")
            self.assertEqual(first, second)
            self.assertEqual(second.count('/large-library.js?v=3'), 1)
            self.assertEqual(second.count('/startup-status.js?v=2'), 1)

    def test_upgrades_previous_runtime_tags(self):
        for old_tag in module.OLD_SCRIPT_TAGS:
            with self.subTest(old_tag=old_tag), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "index.html"
                path.write_text(
                    "<html><body><script>\n"
                    + module.START_REPLACEMENT
                    + "\n</script>\n"
                    + old_tag
                    + "\n"
                    + module.OLD_STARTUP_TAG
                    + "\n</body></html>",
                    encoding="utf-8",
                )
                self.assertTrue(module.prepare(path))
                content = path.read_text(encoding="utf-8")
                self.assertIn('/large-library.js?v=3', content)
                self.assertIn('/startup-status.js?v=2', content)
                self.assertNotIn(old_tag, content)


if __name__ == "__main__":
    unittest.main()
