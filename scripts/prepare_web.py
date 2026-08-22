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

SCRIPT_TAG = '    <script src="/large-library.js?v=5"></script>'
OLD_SCRIPT_TAGS = (
    '    <script src="/large-library.js?v=1"></script>',
    '    <script src="/large-library.js?v=2"></script>',
    '    <script src="/large-library.js?v=3"></script>',
    '    <script src="/large-library.js?v=4"></script>',
)
STARTUP_STATUS_TAG = '    <script src="/startup-status.js?v=2"></script>'
OLD_STARTUP_TAG = '    <script src="/startup-status.js?v=1"></script>'

# v2.0.1 briefly used an inline function-toString shim. Remove it during
# upgrades; v2.0.2 detects the primary server directly from the themed title.
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


def prepare(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    changed = False

    cleaned, count = LEGACY_HINT_RE.subn("\n", source, count=1)
    if count:
        source = cleaned
        changed = True

    if START_MARKER in source:
        source = source.replace(START_MARKER, START_REPLACEMENT, 1)
        changed = True
    elif START_REPLACEMENT not in source:
        raise RuntimeError("Could not find Glimpse loadMedia initialization marker")

    if SCRIPT_TAG not in source:
        replaced = False
        for old in OLD_SCRIPT_TAGS:
            if old in source:
                source = source.replace(old, SCRIPT_TAG, 1)
                changed = True
                replaced = True
                break
        if not replaced:
            if "</body>" not in source:
                raise RuntimeError("Could not find </body> in Glimpse index")
            source = source.replace("</body>", f"{SCRIPT_TAG}\n</body>", 1)
            changed = True

    if OLD_STARTUP_TAG in source:
        source = source.replace(OLD_STARTUP_TAG, STARTUP_STATUS_TAG, 1)
        changed = True
    elif STARTUP_STATUS_TAG not in source:
        if SCRIPT_TAG not in source:
            raise RuntimeError("Could not find Beyond Glimpse runtime script tag")
        source = source.replace(SCRIPT_TAG, f"{SCRIPT_TAG}\n{STARTUP_STATUS_TAG}", 1)
        changed = True

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
