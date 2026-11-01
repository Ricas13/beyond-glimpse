import json
import re
import subprocess
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

    def test_theme_activation_requires_entrypoint_class(self):
        js = (ROOT / "web" / "jellyfin-ui.js").read_text(encoding="utf-8")
        self.assertIn("document.documentElement.classList.contains('jf-ui')", js)
        self.assertNotIn("String(document.title", js)
        self.assertNotIn("document.documentElement.classList.add('jf-ui')", js)

    def test_same_library_selection_can_navigate_from_other_tab(self):
        js = (ROOT / "web" / "library-browse.js").read_text(encoding="utf-8")
        self.assertIn("const changed = selected[type] !== id;", js)
        self.assertIn("if (activeTab !== targetTab && typeof switchTab", js)

    def test_service_worker_precaches_the_assets(self):
        sw = (ROOT / "web" / "sw.js").read_text(encoding="utf-8")
        self.assertIn("/jellyfin-ui.js?v=6", sw)
        self.assertIn("/jellyfin-ui.css?v=4", sw)

    def test_sort_query_honours_the_selected_direction(self):
        runtime = (ROOT / "web" / "large-library.js").read_text(encoding="utf-8")
        self.assertIn("sortDirection[method]", runtime)

    def test_roulette_button_is_hidden_in_the_jellyfin_skin(self):
        css = (ROOT / "web" / "jellyfin-ui.css").read_text(encoding="utf-8")
        self.assertIn("html.jf-ui .roulette-button { display: none !important; }", css)

    def test_library_order_prefixes_and_natural_sort_in_both_interfaces(self):
        names = [
            "In Cinemas", "__TV - DE", "_Movies 10", "_Movies 2",
            "__Movies - DE", "_Movies", "日本語", "_école", "___",
            "_", "__", "_Movies"
        ]
        expected = [
            "_", "_école", "_Movies", "_Movies", "_Movies 2", "_Movies 10",
            "__", "__Movies - DE", "__TV - DE", "___"
        ]
        # Run the real comparator code in Node, not a Python approximation.
        for filename, boundary in (("jellyfin-ui.js", "function el("),
                                   ("library-browse.js", "function normalizeType(")):
            source = (ROOT / "web" / filename).read_text(encoding="utf-8")
            snippet = source.split("    const nameCollator =", 1)[1].split(boundary, 1)[0]
            code = "const nameCollator =" + snippet + (
                "\nconst input = " + json.dumps([
                    {"name": name, "id": str(index)} for index, name in enumerate(names)
                ]) + ";\nprocess.stdout.write(JSON.stringify(input.sort(compareLibraries)));"
            )
            output = subprocess.run(["node", "-e", code], capture_output=True,
                                    text=True, check=True).stdout
            ordered = json.loads(output)
            # Unprefixed libraries come first (in either order), then "_" libraries,
            # then "__" and deeper, as requested for the drawer.
            self.assertCountEqual([item["name"] for item in ordered[:2]],
                                  ["In Cinemas", "日本語"], filename)
            self.assertEqual([item["name"] for item in ordered[2:]], expected, filename)
            # Two identically named libraries retain their original stable order.
            self.assertEqual([item["id"] for item in ordered
                              if item["name"] == "_Movies"], ["5", "11"], filename)

    def test_trailer_feature_is_removed(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        # It embedded a third-party iframe that the app's CSP blocks.
        self.assertNotIn("tvtime.dev", html)
        self.assertNotIn("watch-trailer-btn\">", html)
        self.assertNotIn("trailer-modal-overlay\">", html)

    def test_poster_cards_are_keyboard_operable(self):
        runtime = (ROOT / "web" / "large-library.js").read_text(encoding="utf-8")
        self.assertIn("mediaItem.tabIndex = 0", runtime)
        self.assertIn("event.key === 'Enter'", runtime)

    def test_phone_menu_lists_libraries(self):
        source = (ROOT / "web" / "jellyfin-ui.js").read_text(encoding="utf-8")
        self.assertIn("jf-mlibs", source)
        self.assertIn("renderMobileLibraries(libraries, current, state)", source)
        self.assertIn("list.scrollTop = scrollTop", source)
        self.assertIn("if (live.open) closeLive()", source)

    def test_roulette_retry_keeps_working_without_trailer_actions(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("modalActions = document.createElement('div')", html)
        self.assertIn("modalActions.appendChild(tryAgainBtn)", html)
        self.assertNotIn(".watch-trailer-btn", html)

    def test_keyboard_modals_restore_focus_without_repeat_activation(self):
        runtime = (ROOT / "web" / "large-library.js").read_text(encoding="utf-8")
        self.assertIn("event.target !== mediaItem || event.repeat", runtime)
        self.assertIn("lastCardActivator = mediaItem", runtime)
        self.assertIn("lastCardActivator.isConnected", runtime)
        self.assertIn("document.querySelector('.modal-close')?.focus()", runtime)


if __name__ == "__main__":
    unittest.main()
