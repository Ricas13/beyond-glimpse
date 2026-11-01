import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class JellyfinUiAssetTests(unittest.TestCase):
    def test_every_stylesheet_rule_is_scoped_to_the_jellyfin_class(self):
        css = (ROOT / "web" / "jellyfin-ui.css").read_text(encoding="utf-8")
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
        # Walk top-level rule preludes (media blocks are descended into).
        depth, buffer, preludes = 0, "", []
        for char in css:
            if char == "{":
                preludes.append((depth, buffer.strip()))
                buffer, depth = "", depth + 1
            elif char == "}":
                buffer, depth = "", depth - 1
            elif char == ";" and depth > 0:
                buffer = ""
            else:
                buffer += char
        for depth, prelude in preludes:
            if prelude.startswith("@media"):
                continue
            for selector in prelude.split(","):
                self.assertTrue(
                    selector.strip().startswith("html.jf-ui"),
                    f"unscoped selector would leak into Plex/Emby: {selector.strip()!r}",
                )

    def test_entrypoint_enables_class_only_in_jellyfin_theme(self):
        source = (ROOT / "config" / "entrypoint.sh").read_text(encoding="utf-8")
        theme = source[source.index("apply_jellyfin_theme()"):source.index("apply_emby_theme()")]
        self.assertIn('class="jf-ui"', theme)
        self.assertNotIn("jf-ui", source[source.index("apply_emby_theme()"):])

    def test_service_worker_precaches_the_assets(self):
        sw = (ROOT / "web" / "sw.js").read_text(encoding="utf-8")
        self.assertIn("/jellyfin-ui.js?v=1", sw)
        self.assertIn("/jellyfin-ui.css?v=1", sw)


if __name__ == "__main__":
    unittest.main()
