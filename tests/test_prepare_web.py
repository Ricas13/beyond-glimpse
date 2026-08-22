import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "prepare_web.py"
spec = importlib.util.spec_from_file_location("prepare_web", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


RUNTIME_TEMPLATE = module.JS_HINT_OLD + "\n\n" + module.JS_BOOTSTRAP_OLD + "\n"


class PrepareWebTests(unittest.TestCase):
    def make_fixture(self, tmp, *, tag=None, include_legacy_hint=False, body_comment=False):
        path = Path(tmp) / "index.html"
        tag = tag or ''
        legacy = ''
        if include_legacy_hint:
            legacy = """<script>
    (() => {
        window.__beyondGlimpseServerHintShim = true;
        const original = window.loadMedia;
    })();
    </script>\n"""
        comment = ''
        if body_comment:
            comment = '<!-- Add this at the end of the body, just before the closing </body> tag -->\n'
        path.write_text(
            "<html><head><title>Beyond Glimpse - Jellyfin</title></head><body><script>\n"
            "        async function loadMedia() { await fetch('data/movies.json'); }\n"
            "        // Initialize on page load\n"
            "        loadMedia();\n"
            "</script>\n"
            + legacy
            + comment
            + tag
            + "\n</body></html>",
            encoding="utf-8",
        )
        (Path(tmp) / "large-library.js").write_text(RUNTIME_TEMPLATE, encoding="utf-8")
        return path

    def test_injects_production_bootstrap_and_patches_runtime_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_fixture(tmp)
            self.assertTrue(module.prepare(path))
            first = path.read_text(encoding="utf-8")
            runtime = (Path(tmp) / "large-library.js").read_text(encoding="utf-8")

            self.assertIn("__startBeyondGlimpseMedia", first)
            self.assertIn('/large-library.js?v=6', first)
            self.assertIn('/startup-status.js?v=3', first)
            self.assertNotIn("__beyondGlimpseServerHintShim", first)
            self.assertIn("const title = String(document.title || '').toLowerCase();", runtime)
            self.assertIn("await resetApiQuery", runtime)
            self.assertIn("loadApiGenres()", runtime)
            self.assertLess(runtime.index("await resetApiQuery"), runtime.index("loadApiGenres()"))

            self.assertFalse(module.prepare(path))
            second = path.read_text(encoding="utf-8")
            self.assertEqual(first, second)
            self.assertEqual(second.count('/large-library.js?v=6'), 1)
            self.assertEqual(second.count('/startup-status.js?v=3'), 1)

    def test_runtime_tags_are_after_literal_body_comment_and_before_real_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_fixture(tmp, body_comment=True)
            self.assertTrue(module.prepare(path))
            content = path.read_text(encoding="utf-8")

            comment_end = content.index('tag -->') + len('tag -->')
            runtime_pos = content.index('/large-library.js?v=6')
            startup_pos = content.index('/startup-status.js?v=3')
            real_body_close = content.rfind('</body>')

            self.assertLess(comment_end, runtime_pos)
            self.assertLess(runtime_pos, startup_pos)
            self.assertLess(startup_pos, real_body_close)
            self.assertEqual(content.count('/large-library.js?v=6'), 1)
            self.assertEqual(content.count('/startup-status.js?v=3'), 1)

    def test_repairs_tags_previously_injected_inside_body_comment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_fixture(tmp, body_comment=True)
            broken = path.read_text(encoding="utf-8").replace(
                '</body> tag -->',
                f'{module.OLD_SCRIPT_TAGS[-1]}\n{module.OLD_STARTUP_TAGS[-1]}\n</body> tag -->',
                1,
            )
            path.write_text(broken, encoding="utf-8")

            self.assertTrue(module.prepare(path))
            content = path.read_text(encoding="utf-8")
            real_body_close = content.rfind('</body>')
            self.assertEqual(content.count('/large-library.js?v=6'), 1)
            self.assertEqual(content.count('/startup-status.js?v=3'), 1)
            self.assertLess(content.index('/large-library.js?v=6'), real_body_close)
            self.assertNotIn('/large-library.js?v=5', content)
            self.assertNotIn('/startup-status.js?v=2', content)

    def test_upgrades_previous_runtime_tags_and_removes_v201_shim(self):
        for old_tag in module.OLD_SCRIPT_TAGS:
            with self.subTest(old_tag=old_tag), tempfile.TemporaryDirectory() as tmp:
                path = self.make_fixture(tmp, tag=old_tag, include_legacy_hint=True)
                self.assertTrue(module.prepare(path))
                content = path.read_text(encoding="utf-8")
                self.assertIn('/large-library.js?v=6', content)
                self.assertIn('/startup-status.js?v=3', content)
                self.assertNotIn("__beyondGlimpseServerHintShim", content)
                self.assertNotIn(old_tag, content)

    def test_server_detection_is_credential_free_and_title_based(self):
        self.assertNotIn("TOKEN", module.JS_HINT_NEW)
        self.assertNotIn("JELLYFIN_URL", module.JS_HINT_NEW)
        self.assertIn("document.title", module.JS_HINT_NEW)
        self.assertIn("jellyfin", module.JS_HINT_NEW)
        self.assertIn("plex", module.JS_HINT_NEW)
        self.assertIn("emby", module.JS_HINT_NEW)
        self.assertNotIn("Function.prototype.toString", module.JS_HINT_NEW)


if __name__ == "__main__":
    unittest.main()
