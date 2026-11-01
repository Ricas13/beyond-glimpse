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
        self.assertIn("/jellyfin-ui.js?v=5", sw)
        self.assertIn("/jellyfin-ui.css?v=4", sw)

    def test_sort_query_honours_the_selected_direction(self):
        runtime = (ROOT / "web" / "large-library.js").read_text(encoding="utf-8")
        self.assertIn("sortDirection[method]", runtime)

    def test_roulette_button_is_hidden_in_the_jellyfin_skin(self):
        css = (ROOT / "web" / "jellyfin-ui.css").read_text(encoding="utf-8")
        self.assertIn("html.jf-ui .roulette-button { display: none !important; }", css)

    def test_libraries_respect_underscore_prefixes_then_alphabetical(self):
        for name in ("jellyfin-ui.js", "library-browse.js"):
            source = (ROOT / "web" / name).read_text(encoding="utf-8")
            self.assertIn("underscoreRank", source, name)
            self.assertIn("numeric: true", source, name)
            self.assertIn("p{L}", source, name)

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
