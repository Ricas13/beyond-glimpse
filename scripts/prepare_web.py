#!/usr/bin/env python3

import argparse
import re
from pathlib import Path


START_MARKER = "        // Initialize on page load\n        loadMedia();"
START_REPLACEMENT = """        // Initialize after the Beyond Glimpse runtime has replaced the upstream hot paths.
        window.__startBeyondGlimpseMedia = () => {
            if (window.__beyondGlimpseMediaStarted) return;
            window.__beyondGlimpseMediaStarted = true;
            loadMedia();
        };
        window.addEventListener('beyond-glimpse:ready', window.__startBeyondGlimpseMedia, { once: true });
        setTimeout(window.__startBeyondGlimpseMedia, 3000);"""

SCRIPT_TAG = '    <script src="/large-library.js?v=6"></script>'
OLD_SCRIPT_TAGS = (
    '    <script src="/large-library.js?v=1"></script>',
    '    <script src="/large-library.js?v=2"></script>',
    '    <script src="/large-library.js?v=3"></script>',
    '    <script src="/large-library.js?v=4"></script>',
    '    <script src="/large-library.js?v=5"></script>',
)
LIBRARY_BROWSE_TAG = '    <script src="/library-browse.js?v=1"></script>'
TV_EPISODES_TAG = '    <script src="/tv-episodes.js?v=1"></script>'
STARTUP_STATUS_TAG = '    <script src="/startup-status.js?v=3"></script>'
OLD_STARTUP_TAGS = (
    '    <script src="/startup-status.js?v=1"></script>',
    '    <script src="/startup-status.js?v=2"></script>',
)

# Strip any prior Beyond Glimpse external runtime tags before placing the
# canonical set immediately before the *real* closing body tag. Glimpse's
# source contains an HTML comment that literally mentions "</body>" before the
# actual closing tag, so first-occurrence string replacement is unsafe. Older
# broken pages can have these tags embedded in the middle of that comment, so
# this deliberately does not anchor the match to a line boundary.
RUNTIME_TAG_RE = re.compile(
    r'[ \t]*<script src="/(?:large-library\.js\?v=\d+|library-browse\.js\?v=\d+|tv-episodes\.js\?v=\d+|startup-status\.js\?v=\d+)"></script>[ \t]*(?:\r?\n)?'
)

# v2.0.1 briefly used an inline function-toString shim. Remove it during
# upgrades; v2.0.2+ detects the primary server directly from the themed title.
LEGACY_HINT_RE = re.compile(
    r"\s*<script>\s*\(\(\) => \{\s*window\.__beyondGlimpseServerHintShim = true;.*?</script>\s*",
    re.DOTALL,
)

JS_HINT_OLD = """    function hintedServerType() {
        for (const type of ['jellyfin', 'plex', 'emby']) {
            if (originalLoadSource.includes(`data/${type}/movies.json`) ||
                originalLoadSource.includes(`data/${type}/tvshows.json`)) return type;
        }
        const path = window.location.pathname.toLowerCase();
        if (path.startsWith('/jellyfin/')) return 'jellyfin';
        if (path.startsWith('/plex/')) return 'plex';
        if (path.startsWith('/emby/')) return 'emby';
        return null;
    }"""

JS_HINT_NEW = """    function hintedServerType() {
        // The entrypoint already themes the document title with the configured
        // primary server (for example \"Beyond Glimpse - Jellyfin\"). Read that
        // explicit, credential-free hint first instead of guessing from inherited
        // Glimpse source code.
        const title = String(document.title || '').toLowerCase();
        for (const type of ['jellyfin', 'plex', 'emby']) {
            if (title.includes(type)) return type;
        }
        for (const type of ['jellyfin', 'plex', 'emby']) {
            if (originalLoadSource.includes(`data/${type}/movies.json`) ||
                originalLoadSource.includes(`data/${type}/tvshows.json`)) return type;
        }
        const path = window.location.pathname.toLowerCase();
        if (path.startsWith('/jellyfin/')) return 'jellyfin';
        if (path.startsWith('/plex/')) return 'plex';
        if (path.startsWith('/emby/')) return 'emby';
        return null;
    }"""

JS_BOOTSTRAP_OLD = """                await loadApiGenres();
                updateGenreUI(activeTabType());
                await resetApiQuery(document.querySelector('.search-input')?.value || '');
                return;"""

JS_BOOTSTRAP_NEW = """                // Render the first catalogue page immediately. Genre aggregation is
                // useful navigation metadata, but it must never block first paint.
                await resetApiQuery(document.querySelector('.search-input')?.value || '');
                loadApiGenres()
                    .then(() => updateGenreUI(activeTabType()))
                    .catch(error => console.warn('Could not load catalogue genres:', error));
                return;"""


def prepare_runtime_js(path: Path) -> bool:
    if not path.exists():
        return False
    source = path.read_text(encoding="utf-8")
    changed = False

    if JS_HINT_OLD in source:
        source = source.replace(JS_HINT_OLD, JS_HINT_NEW, 1)
        changed = True
    elif JS_HINT_NEW not in source:
        raise RuntimeError("Could not find Beyond Glimpse server detection block")

    if JS_BOOTSTRAP_OLD in source:
        source = source.replace(JS_BOOTSTRAP_OLD, JS_BOOTSTRAP_NEW, 1)
        changed = True
    elif JS_BOOTSTRAP_NEW not in source:
        raise RuntimeError("Could not find Beyond Glimpse Jellyfin bootstrap block")

    if changed:
        path.write_text(source, encoding="utf-8")
    return changed


def place_runtime_tags(source: str) -> str:
    source = RUNTIME_TAG_RE.sub("", source)
    body_close = source.rfind("</body>")
    if body_close < 0:
        raise RuntimeError("Could not find final </body> in Glimpse index")

    before = source[:body_close].rstrip()
    after = source[body_close:]
    return (
        f"{before}\n\n{SCRIPT_TAG}\n{LIBRARY_BROWSE_TAG}\n"
        f"{TV_EPISODES_TAG}\n{STARTUP_STATUS_TAG}\n{after}"
    )


def prepare(path: Path) -> bool:
    original = path.read_text(encoding="utf-8")
    source = original

    source = LEGACY_HINT_RE.sub("\n", source, count=1)

    if START_MARKER in source:
        source = source.replace(START_MARKER, START_REPLACEMENT, 1)
    elif START_REPLACEMENT not in source:
        raise RuntimeError("Could not find Glimpse loadMedia initialization marker")

    # Always normalize external runtime placement. This also repairs v2.0.0-
    # v2.0.2 pages where the tags were accidentally injected into Glimpse's
    # comment that mentions the literal text "</body>".
    source = place_runtime_tags(source)

    changed = source != original
    if changed:
        path.write_text(source, encoding="utf-8")

    js_changed = prepare_runtime_js(path.parent / "large-library.js")
    return changed or js_changed


def main():
    parser = argparse.ArgumentParser(description="Prepare Glimpse web UI for Beyond Glimpse production runtime")
    parser.add_argument("path", nargs="?", default="/app/web/index.html")
    args = parser.parse_args()
    path = Path(args.path)
    changed = prepare(path)
    print(f"Prepared {path}" if changed else f"Already prepared: {path}")


if __name__ == "__main__":
    main()
